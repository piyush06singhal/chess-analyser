"""Stockfish implementation of the :class:`ChessEngine` abstraction.

Manages a persistent UCI subprocess with:
- configuration via ``ARGUS_STOCKFISH_PATH`` with auto-detection fallback
  (never a hardcoded path in code)
- request timeouts (engine killed, lazy restart on the next call)
- recovery from engine crashes
- structured error handling for invalid FENs and unparseable responses

The engine is serialized behind a lock. Blocking subprocess I/O must run off
the event loop (the API service wraps calls in ``asyncio.to_thread``).
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

import chess

from argus.analysis.engine.base import (
    AnalyzedPosition,
    ChessEngine,
    EngineLine,
    MoveComparison,
    compute_cp_loss,
    flip_score,
)
from argus.chess_core.fen import validate_fen
from argus.chess_core.models import Game
from argus.shared.errors import (
    EngineError,
    EngineResponseError,
    EngineTimeoutError,
    EngineUnavailableError,
    InvalidFenError,
    InvalidMoveError,
)
from argus.shared.logging import get_logger

logger = get_logger(__name__)

_COMMON_LOCATIONS = (
    "/opt/homebrew/bin/stockfish",
    "/usr/local/bin/stockfish",
    "/usr/games/stockfish",
    "/usr/bin/stockfish",
)


def locate_stockfish(configured_path: str | None = None) -> str | None:
    """Find a Stockfish binary.

    Order: configured path -> ``stockfish`` on PATH -> common install locations.
    Returns the first executable file found, or ``None``.
    """
    candidates: list[str] = []
    if configured_path:
        candidates.append(configured_path)
    on_path = shutil.which("stockfish")
    if on_path:
        candidates.append(on_path)
    candidates.extend(_COMMON_LOCATIONS)
    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
    return None


@dataclass
class StockfishSettings:
    """Engine configuration. Values come from environment/config, not code."""

    path: str | None = None
    depth: int = 14
    multipv: int = 3
    timeout_seconds: float = 30.0
    threads: int = 1
    hash_mb: int = 256


class StockfishEngine(ChessEngine):
    """UCI Stockfish engine with timeouts and crash recovery."""

    def __init__(self, settings: StockfishSettings | None = None) -> None:
        self._settings = settings or StockfishSettings()
        self._path = locate_stockfish(self._settings.path)
        self._version: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    # --- lifecycle -------------------------------------------------------------

    def info(self) -> dict:
        path = self._path or locate_stockfish(self._settings.path)
        if path is None:
            return {
                "available": False,
                "engine": "stockfish",
                "path": None,
                "version": None,
                "running": False,
                "reason": "Stockfish binary not found; set ARGUS_STOCKFISH_PATH or install Stockfish",
            }
        return {
            "available": True,
            "engine": "stockfish",
            "path": path,
            "version": self._version,
            "running": self._process is not None and self._process.poll() is None,
        }

    def close(self) -> None:
        """Stop the engine process."""
        self._terminate()

    def _start(self) -> None:
        if self._path is None:
            raise EngineUnavailableError(
                "Stockfish binary not found; set ARGUS_STOCKFISH_PATH or install Stockfish"
            )
        try:
            self._process = subprocess.Popen(
                [self._path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise EngineUnavailableError(
                f"Failed to start Stockfish at '{self._path}': {exc}"
            ) from exc

        handshake_lines: list[str] = []
        self._send("uci")
        if not self._read_until(lambda line: line.startswith("uciok"), collect=handshake_lines):
            self._terminate()
            raise EngineResponseError("Stockfish did not answer 'uciok' during handshake")
        for line in handshake_lines:
            if line.startswith("id name "):
                self._version = line.removeprefix("id name ").strip()
                break
        self._send(f"setoption name Threads value {self._settings.threads}")
        self._send(f"setoption name Hash value {self._settings.hash_mb}")
        self._send("isready")
        if not self._read_until(lambda line: line == "readyok"):
            self._terminate()
            raise EngineResponseError("Stockfish did not answer 'readyok' during handshake")
        logger.info("Stockfish started [path=%s version=%s]", self._path, self._version)

    def _ensure_started(self) -> None:
        if self._process is None or self._process.poll() is not None:
            self._terminate()
            self._start()

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            try:
                process.kill()
            except Exception:
                pass

    # --- UCI communication -------------------------------------------------------

    def _send(self, command: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise EngineError("Stockfish process is not running")
        try:
            process.stdin.write(command + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise EngineError(f"Failed to communicate with Stockfish: {exc}") from exc

    def _read_until(
        self,
        predicate: Callable[[str], bool],
        *,
        collect: list[str] | None = None,
        nudge_command: str | None = "isready",
        nudge_after_seconds: float = 0.5,
    ) -> bool:
        """Read engine output until ``predicate`` matches or the timeout elapses.

        Stockfish buffers stdout when it is a pipe: pending output (including
        terminal markers such as ``uciok``/``readyok``/``bestmove``) can sit
        unflushed until new input arrives. While waiting, a harmless
        ``isready`` nudge is therefore sent at intervals to flush the buffer.
        Inserted ``readyok`` lines never match ``uciok``/``bestmove``
        predicates and are skipped by the info-line parser.

        Raises:
            EngineError: when the engine process dies mid-read (crash).
        """
        process = self._process
        if process is None or process.stdout is None:
            raise EngineError("Stockfish process is not running")
        deadline = time.monotonic() + self._settings.timeout_seconds
        next_nudge = time.monotonic() + nudge_after_seconds
        fd = process.stdout.fileno()
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return False
            if nudge_command is not None and now >= next_nudge:
                self._send(nudge_command)
                next_nudge = time.monotonic() + nudge_after_seconds
            wait = min(remaining, max(0.05, next_nudge - time.monotonic()))
            try:
                ready, _, _ = select.select([fd], [], [], wait)
            except (OSError, ValueError):
                ready = [fd]
            if ready:
                line = process.stdout.readline()
                if not line:  # EOF — engine crashed
                    raise EngineError("Stockfish process terminated unexpectedly")
                line = line.strip()
                if collect is not None:
                    collect.append(line)
                if predicate(line):
                    return True

    # --- analysis primitives ------------------------------------------------------

    def analyze_position(
        self, fen: str, *, depth: int | None = None, multipv: int | None = None
    ) -> AnalyzedPosition:
        validation = validate_fen(fen)
        if not validation.is_valid:
            raise InvalidFenError(
                f"Invalid FEN: {'; '.join(validation.errors)}",
                details={"fen": fen, "errors": validation.errors},
            )
        board = chess.Board(validation.fen)
        if board.legal_moves.count() == 0:
            # Terminal position (checkmate/stalemate): the engine has nothing
            # to search. Report the known outcome instead of an engine call —
            # `bestmove (none)` on such positions is not an error.
            reason = "checkmate" if board.is_checkmate() else "stalemate"
            return AnalyzedPosition(
                fen=validation.fen,
                depth=0,
                multipv=multipv or self._settings.multipv,
                best_move_uci=None,
                best_move_san=None,
                lines=[],
                is_terminal=True,
                terminal_reason=reason,
                engine="stockfish",
                engine_version=self._version,
            )
        depth_used = depth or self._settings.depth
        multipv_used = multipv or self._settings.multipv
        multipv_used = max(1, min(multipv_used, board.legal_moves.count()))

        with self._lock:
            self._ensure_started()
            raw_lines: list[str] = []
            try:
                self._send(f"setoption name MultiPV value {multipv_used}")
                self._send(f"position fen {validation.fen}")
                self._send(f"go depth {depth_used}")
                got_bestmove = self._read_until(
                    lambda line: line.startswith("bestmove"), collect=raw_lines
                )
            except EngineError:
                self._terminate()  # force a clean restart on the next call
                raise
            if not got_bestmove:
                self._terminate()
                raise EngineTimeoutError(
                    f"Stockfish timed out after {self._settings.timeout_seconds}s "
                    f"(depth {depth_used})"
                )

        bestmove_uci: str | None = None
        for line in raw_lines:
            if line.startswith("bestmove "):
                tokens = line.split()
                if len(tokens) >= 2 and tokens[1] not in ("(none)", "(None)"):
                    bestmove_uci = tokens[1]
                break

        lines = self._parse_info_lines(raw_lines, multipv=multipv_used)
        if not lines and bestmove_uci is None:
            raise EngineResponseError(
                "Stockfish returned no parsable analysis", details={"fen": validation.fen}
            )

        best_line = lines[0] if lines else None
        best_move_uci = bestmove_uci or (best_line.move_uci if best_line else None)
        best_move_san = self._san_for(board, best_move_uci)
        for line in lines:
            if line.move_san is None:
                line.move_san = self._san_for(board, line.move_uci)

        return AnalyzedPosition(
            fen=validation.fen,
            depth=max((line.depth for line in lines), default=depth_used),
            multipv=multipv_used,
            best_move_uci=best_move_uci,
            best_move_san=best_move_san,
            lines=lines,
            engine="stockfish",
            engine_version=self._version,
        )

    def compare_moves(
        self, fen: str, moves: list[str], *, depth: int | None = None
    ) -> list[MoveComparison]:
        """Compare candidate moves against the engine's best in the position.

        A single MultiPV analysis covers the requested moves when they are
        among the top lines; moves outside them fall back to a per-move
        before/after analysis with perspective flipping.
        """
        if not moves:
            raise InvalidMoveError("No moves to compare")
        validation = validate_fen(fen)
        if not validation.is_valid:
            raise InvalidFenError(
                f"Invalid FEN: {'; '.join(validation.errors)}",
                details={"fen": fen, "errors": validation.errors},
            )
        board = chess.Board(validation.fen)

        parsed_moves: dict[str, chess.Move] = {}
        for move_uci in moves:
            text = move_uci.strip()
            try:
                move = chess.Move.from_uci(text)
            except ValueError as exc:
                raise InvalidMoveError(f"Invalid UCI move '{move_uci}': {exc}") from exc
            if move not in board.legal_moves:
                raise InvalidMoveError(
                    f"Illegal move '{text}' in position", details={"fen": validation.fen}
                )
            parsed_moves[text] = move

        depth_used = depth or self._settings.depth
        multipv_used = max(1, min(len(parsed_moves), board.legal_moves.count()))
        analysis = self.analyze_position(validation.fen, depth=depth_used, multipv=multipv_used)

        lines_by_move = {line.move_uci: line for line in analysis.lines}
        best_line = analysis.lines[0] if analysis.lines else None
        best_cp = best_line.cp if best_line else None
        best_mate = best_line.mate if best_line else None

        comparisons: list[MoveComparison] = []
        for move_uci, move in parsed_moves.items():
            played_line = lines_by_move.get(move_uci)
            if played_line is not None:
                played_cp, played_mate = played_line.cp, played_line.mate
            else:
                board_copy = board.copy(stack=False)
                board_copy.push(move)
                after = self.analyze_position(board_copy.fen(), depth=depth_used, multipv=1)
                after_line = after.lines[0] if after.lines else None
                played_cp, played_mate = (
                    flip_score(after_line.cp, after_line.mate) if after_line else (None, None)
                )
            comparisons.append(
                MoveComparison(
                    fen=validation.fen,
                    played_move_uci=move_uci,
                    played_move_san=board.san(move),
                    best_move_uci=best_line.move_uci if best_line else None,
                    best_move_san=analysis.best_move_san,
                    played_cp=played_cp,
                    best_cp=best_cp,
                    centipawn_loss=compute_cp_loss(best_cp, best_mate, played_cp, played_mate),
                    is_best_move=bool(analysis.best_move_uci and move_uci == analysis.best_move_uci),
                    depth=analysis.depth,
                )
            )
        return comparisons

    # --- helpers -------------------------------------------------------------------

    @staticmethod
    def _parse_info_lines(raw_lines: list[str], *, multipv: int) -> list[EngineLine]:
        """Parse ``info ... score ... pv ...`` output.

        Keeps the deepest completed line per MultiPV index; partial
        lowerbound/upperbound search updates are skipped.
        """
        by_index: dict[int, EngineLine] = {}
        for raw in raw_lines:
            if not raw.startswith("info"):
                continue
            tokens = raw.split()
            if not {"depth", "multipv", "score", "pv"} <= set(tokens):
                continue
            if "lowerbound" in tokens or "upperbound" in tokens:
                continue
            try:
                depth = int(tokens[tokens.index("depth") + 1])
                index = int(tokens[tokens.index("multipv") + 1])
                score_kind = tokens[tokens.index("score") + 1]
                score_value = int(tokens[tokens.index("score") + 2])
                pv = tokens[tokens.index("pv") + 1 :]
            except (IndexError, ValueError):
                continue
            if score_kind not in ("cp", "mate") or not pv or not 1 <= index <= multipv:
                continue
            entry = EngineLine(
                index=index,
                depth=depth,
                move_uci=pv[0],
                cp=score_value if score_kind == "cp" else None,
                mate=score_value if score_kind == "mate" else None,
                pv=pv,
            )
            previous = by_index.get(index)
            if previous is None or depth >= previous.depth:
                by_index[index] = entry
        return [by_index[index] for index in sorted(by_index)]

    @staticmethod
    def _san_for(board: chess.Board, move_uci: str | None) -> str | None:
        if not move_uci:
            return None
        try:
            return board.san(chess.Move.from_uci(move_uci))
        except (ValueError, AssertionError):
            return None
