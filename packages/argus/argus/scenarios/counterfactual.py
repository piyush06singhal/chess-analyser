"""Counterfactual scenario engine.

Given a position, the move that was actually played, and an alternative move,
this builds two played-out continuations and the measured difference between
them. It is the mechanism behind both "why not this move?" and "what if I had
played ...?".

Three properties are non-negotiable here:

* **Nothing is simulated.** Every ply in a continuation is a move the engine
  actually chose, and every score is a score the engine actually returned. There
  is no heuristic that "estimates" where a line goes.
* **The original game is never modified.** A branch is an immutable value object
  built from a FEN; the stored game is read-only input.
* **The comparison is like-for-like or it says so.** When both moves fall inside
  one MultiPV search, their scores are directly comparable and recorded as
  ``same_search``. When a move falls outside it, its score comes from a separate
  search and is recorded as ``resulting_position`` — a small difference between
  the two is not a real difference, and the branch says that rather than
  pretending otherwise.
"""

from __future__ import annotations

import chess
from argus.analysis.engine.base import ChessEngine, to_cp

from argus.scenarios.candidates import resolve_move
from argus.scenarios.models import (
    ContinuationPly,
    EvidenceRef,
    ScenarioBranch,
    ScenarioType,
)
from argus.scenarios.policy import (
    DEFAULT_MULTIPV,
    clamp_depth,
    clamp_multipv,
    clamp_plies_ahead,
)
from argus.scenarios.positions import (
    PositionComparisonService,
    engine_metrics_from,
    position_facts,
)
from argus.shared.errors import InvalidMoveError

__all__ = ["CounterfactualAnalyzer"]


class CounterfactualAnalyzer:
    """Build alternative continuations and measure the difference."""

    def __init__(self, engine: ChessEngine, comparisons: PositionComparisonService | None = None) -> None:
        self.engine = engine
        self.comparisons = comparisons or PositionComparisonService(engine)

    def branch(
        self,
        fen: str,
        alternative_move: str,
        *,
        actual_move: str | None = None,
        scenario_type: ScenarioType = ScenarioType.COUNTERFACTUAL_MOVE,
        plies_ahead: int | None = None,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        compare_resulting_positions: bool = True,
    ) -> ScenarioBranch:
        """Build one branch: the actual line and the alternative line, measured.

        Raises:
            InvalidFenError: when the FEN cannot be parsed.
            InvalidMoveError: when the alternative move is not legal here. Callers
                that want to present the refusal as a result catch it and return
                "that move is not legal in this position" instead of evaluating a
                move that cannot be played.
        """
        facts = position_facts(fen)
        board = chess.Board(facts.fen)
        alternative, reason = resolve_move(board, alternative_move)
        if alternative is None:
            raise InvalidMoveError(
                reason or f"'{alternative_move}' is not a legal move in this position",
                details={"fen": facts.fen, "move": alternative_move},
            )
        actual: chess.Move | None = None
        if actual_move:
            actual, actual_reason = resolve_move(board, actual_move)
            if actual is None:
                raise InvalidMoveError(
                    actual_reason or f"'{actual_move}' is not a legal move in this position",
                    details={"fen": facts.fen, "move": actual_move},
                )

        effective_depth, depth_clamped = clamp_depth(depth)
        effective_plies, plies_clamped = clamp_plies_ahead(plies_ahead)
        width, _ = clamp_multipv(multipv if multipv is not None else DEFAULT_MULTIPV)

        search = self.engine.analyze_position(
            facts.fen, depth=effective_depth, multipv=max(width, 1), movetime_ms=movetime_ms
        )
        config = self.comparisons.engine_config(
            depth=effective_depth, multipv=max(width, 1), movetime_ms=movetime_ms
        )

        alt_cp, alt_mate, alt_source, alt_pv = _score_move(
            self.engine,
            board,
            alternative,
            search,
            depth=effective_depth,
            movetime_ms=movetime_ms,
        )
        actual_cp = actual_mate = None
        actual_source = "unavailable"
        actual_pv: list[str] = []
        if actual is not None:
            actual_cp, actual_mate, actual_source, actual_pv = _score_move(
                self.engine,
                board,
                actual,
                search,
                depth=effective_depth,
                movetime_ms=movetime_ms,
            )

        alt_continuation, alt_resulting_fen = self._play_line(
            board,
            alternative,
            plies_ahead=effective_plies,
            depth=effective_depth,
            movetime_ms=movetime_ms,
        )
        actual_continuation: list[ContinuationPly] = []
        actual_resulting_fen: str | None = None
        if actual is not None:
            actual_continuation, actual_resulting_fen = self._play_line(
                board,
                actual,
                plies_ahead=effective_plies,
                depth=effective_depth,
                movetime_ms=movetime_ms,
            )

        comparison = None
        if compare_resulting_positions and actual_resulting_fen and alt_resulting_fen:
            comparison = self.comparisons.compare(
                actual_resulting_fen, alt_resulting_fen, depth=effective_depth, movetime_ms=movetime_ms
            )

        alt_value = to_cp(alt_cp, alt_mate)
        actual_value = to_cp(actual_cp, actual_mate)
        change = (
            None
            if alt_value is None or actual_value is None
            else alt_value - actual_value
        )

        notes: list[str] = []
        if alt_source != "same_search":
            notes.append(
                "The alternative move was outside the MultiPV window; its score comes "
                "from a separate search of the resulting position."
            )
        if actual_move and actual_source != "same_search":
            notes.append(
                "The played move was outside the MultiPV window; its score comes from a "
                "separate search of the resulting position."
            )
        if depth_clamped:
            notes.append(f"Requested depth was clamped to {effective_depth}.")
        if plies_clamped:
            notes.append(f"Continuation length was clamped to {effective_plies} plies.")
        if actual_move is None:
            notes.append("No played move was supplied, so only the alternative line is shown.")

        return ScenarioBranch(
            scenario_type=scenario_type,
            source_fen=facts.fen,
            alternative_move_uci=alternative.uci(),
            alternative_move_san=board.san(alternative),
            actual_move_uci=actual.uci() if actual else None,
            actual_move_san=board.san(actual) if actual else None,
            engine_config=config,
            actual_continuation=actual_continuation,
            alternative_continuation=alt_continuation,
            actual_eval_cp=actual_cp,
            alternative_eval_cp=alt_cp,
            actual_eval_source=actual_source,
            alternative_eval_source=alt_source,
            evaluation_change_cp=change,
            comparison=comparison,
            moves_played=len(alt_continuation),
            plies_requested=effective_plies,
            truncated=len(alt_continuation) < effective_plies,
            evidence=[
                EvidenceRef(
                    kind="engine",
                    detail=f"MultiPV root search of {facts.fen} at {config.label()}",
                    uci=alternative.uci(),
                ),
                EvidenceRef(
                    kind="engine",
                    detail=(
                        f"Continuation: {effective_plies} ply/plies of engine best moves "
                        f"from the position after {board.san(alternative)}"
                    ),
                    uci=alternative.uci(),
                ),
                EvidenceRef(kind="board", detail="material and structure read from the branch positions"),
            ],
            notes=notes,
        )

    def _play_line(
        self,
        board: chess.Board,
        first_move: chess.Move,
        *,
        plies_ahead: int,
        depth: int,
        movetime_ms: int | None,
    ) -> tuple[list[ContinuationPly], str]:
        """Follow the engine's own line for at most ``plies_ahead`` plies.

        The first ply is the move under consideration; every later ply is the
        engine's best move in the position it reached, re-searched rather than
        read from the earlier PV, so each recorded score belongs to the position
        it is attached to.
        """
        working = board.copy(stack=False)
        plies: list[ContinuationPly] = []
        root = self.engine.analyze_position(
            working.fen(), depth=depth, multipv=1, movetime_ms=movetime_ms
        )
        first_line = next((entry for entry in root.lines if entry.move_uci == first_move.uci()), None)
        plies.append(
            _continuation_ply(
                working,
                first_move,
                cp=first_line.cp if first_line else None,
                mate=first_line.mate if first_line else None,
                ply=1,
            )
        )
        working.push(first_move)
        while len(plies) < max(1, plies_ahead):
            if working.is_game_over() or not list(working.legal_moves):
                break
            analysis = self.engine.analyze_position(
                working.fen(), depth=depth, multipv=1, movetime_ms=movetime_ms
            )
            if not analysis.lines:
                break
            best_uci = analysis.best_move_uci or analysis.lines[0].move_uci
            try:
                move = chess.Move.from_uci(best_uci)
            except ValueError:
                break
            if move not in working.legal_moves:
                break
            line = analysis.lines[0]
            plies.append(
                _continuation_ply(
                    working, move, cp=line.cp, mate=line.mate, ply=len(plies) + 1
                )
            )
            working.push(move)
        return plies, working.fen()


def _score_move(
    engine: ChessEngine,
    board: chess.Board,
    move: chess.Move,
    search,  # AnalyzedPosition from the shared root search
    *,
    depth: int,
    movetime_ms: int | None,
) -> tuple[int | None, int | None, str, list[str]]:
    """Score one move: from the shared search when possible, else on its own."""
    line = next((entry for entry in search.lines if entry.move_uci == move.uci()), None)
    if line is not None:
        return line.cp, line.mate, "same_search", list(line.pv)
    after = board.copy(stack=False)
    after.push(move)
    analysis = engine.analyze_position(after.fen(), depth=depth, multipv=1, movetime_ms=movetime_ms)
    metrics = engine_metrics_from(after.fen(), analysis)
    cp = None if metrics.cp_white is None else -metrics.cp_white
    mate = None if metrics.mate is None else -metrics.mate
    return cp, mate, "resulting_position", list(metrics.pv)


def _continuation_ply(
    board_before: chess.Board,
    move: chess.Move,
    *,
    cp: int | None,
    mate: int | None,
    ply: int,
) -> ContinuationPly:
    """One recorded ply, with the score expressed from the mover's perspective.

    ``board_before`` is the position *before* the move and is left unchanged, so
    the caller stays in control of how far the line has advanced.
    """
    mover_is_white = board_before.turn == chess.WHITE
    after = board_before.copy(stack=False)
    after.push(move)
    value = to_cp(cp, mate)
    return ContinuationPly(
        ply=ply,
        move_number=board_before.fullmove_number,
        color="white" if mover_is_white else "black",
        uci=move.uci(),
        san=board_before.san(move),
        fen_before=board_before.fen(),
        fen_after=after.fen(),
        cp=cp,
        mate=mate,
        cp_white=None if value is None else (value if mover_is_white else -value),
        is_best_in_search=True,
    )
