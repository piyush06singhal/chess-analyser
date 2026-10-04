"""Position extraction and sampling — positions, with the sampling declared.

Every position of every game is a huge and highly redundant dataset: consecutive
positions differ by one move, so a million rows may describe a few thousand games.
Using all of them inflates row counts, distorts class balance, and makes a model
look better than it is. So sampling is a declared choice with a recorded spec,
not an accident of whatever the extraction loop happened to iterate.

Position ids are derived with the importer's own id formula
(:func:`argus.datasets.importer.stable_game_id`), including its per-corpus
occurrence counter, so a ``positions/`` row always joins back to its
``normalized/`` game record. A test asserts the two agree on a real corpus.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Sequence
from pathlib import Path

import chess

from argus.analysis.phase import classify_position_fen
from argus.chess_core.models import Game
from argus.chess_core.pgn import parse_games
from argus.datasets.importer import PARSER_MAX_PLIES, identity_hashes, iter_game_texts, stable_game_id
from argus.datasets.records import PositionRecord
from argus.shared.logging import get_logger

logger = get_logger(__name__)


class SamplingStrategy(str):
    """How positions are chosen from a game."""

    EVERY_PLY = "every_ply"
    EVERY_N_PLIES = "every_n_plies"
    RANDOM_PLIES = "random_plies"
    PHASE_BALANCED = "phase_balanced"
    TACTICAL = "tactical"


STRATEGY_MEANINGS: dict[str, str] = {
    SamplingStrategy.EVERY_PLY: (
        "No sampling. Correct only when the question is about a specific ply: "
        "consecutive rows are almost identical, so they inflate every count."
    ),
    SamplingStrategy.EVERY_N_PLIES: (
        "A fixed stride. Cheap, reproducible, and spread across the whole game."
    ),
    SamplingStrategy.RANDOM_PLIES: (
        "A seeded random subset with a per-game cap. Unbiased, but only reproducible "
        "if the seed is recorded."
    ),
    SamplingStrategy.PHASE_BALANCED: (
        "A stride plus an equal quota per phase, so a long middlegame cannot swamp a "
        "short but important endgame."
    ),
    SamplingStrategy.TACTICAL: (
        "Positions that are plausibly sharp (check, a hanging piece, queens traded "
        "early, a pawn on the seventh). Explicitly NOT a claim that these are the "
        "positions that matter."
    ),
}


class SamplingSpec:
    """A declared sampling choice, recorded with any dataset it produces."""

    def __init__(
        self,
        strategy: str = SamplingStrategy.EVERY_N_PLIES,
        *,
        every_n: int = 4,
        max_per_game: int | None = 40,
        seed: int = 42,
        rationale: str = "",
    ) -> None:
        if every_n < 1:
            raise ValueError("every_n must be at least 1")
        if strategy not in STRATEGY_MEANINGS:
            raise ValueError(
                f"Unknown sampling strategy {strategy!r}; known: "
                + ", ".join(sorted(STRATEGY_MEANINGS))
            )
        self.strategy = strategy
        self.every_n = every_n
        self.max_per_game = max_per_game
        self.seed = seed
        self.rationale = rationale or (
            "Consecutive positions differ by one move, so a stride and a per-game cap "
            "keep the row count from being dominated by redundancy, and stop one very "
            "long game from outweighing the rest of the corpus."
        )

    def describe(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "strategy_meaning": STRATEGY_MEANINGS[self.strategy],
            "every_n": self.every_n,
            "max_per_game": self.max_per_game,
            "seed": self.seed,
            "rationale": self.rationale,
        }


DEFAULT_SAMPLING = SamplingSpec()


def _tactical_flags(fen: str) -> bool:
    """A transparent, board-only 'plausibly sharp' flag."""
    board = chess.Board(fen)
    side = board.turn
    opportunities = (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN)
    hanging = any(
        board.attackers(not side, square)
        and not board.attackers(side, square)
        and (piece := board.piece_at(square))
        and piece.piece_type in opportunities
        for square in chess.SQUARES
    )
    queens_left = len(board.pieces(chess.QUEEN, chess.WHITE) | board.pieces(chess.QUEEN, chess.BLACK))
    queens_traded_early = board.fullmove_number <= 15 and queens_left == 0
    pawn_on_seventh = bool(
        board.pieces(chess.PAWN, chess.WHITE) & chess.BB_RANK_7
        or board.pieces(chess.PAWN, chess.BLACK) & chess.BB_RANK_2
    )
    return bool(board.is_check() or hanging or queens_traded_early or pawn_on_seventh)


def _cap(ordered: list[int], limit: int | None) -> list[int]:
    """Evenly thin an ordered list down to ``limit`` entries."""
    if not limit or len(ordered) <= limit:
        return ordered
    stride = len(ordered) / limit
    return [ordered[int(index * stride)] for index in range(limit)]


def _select_indices(
    candidates: list[tuple[int, str, bool]], spec: SamplingSpec
) -> list[int]:
    """Choose which plies to keep, per the declared strategy."""
    if not candidates:
        return []

    if spec.strategy == SamplingStrategy.EVERY_PLY:
        chosen = [ply for ply, _, _ in candidates]
    elif spec.strategy == SamplingStrategy.EVERY_N_PLIES:
        chosen = [ply for ply, _, _ in candidates if ply % spec.every_n == 0]
    elif spec.strategy == SamplingStrategy.TACTICAL:
        chosen = [ply for ply, _, tactical in candidates if tactical]
    elif spec.strategy == SamplingStrategy.RANDOM_PLIES:
        pool = [ply for ply, _, _ in candidates]
        keep = min(len(pool), spec.max_per_game or len(pool))
        chosen = sorted(random.Random(spec.seed).sample(pool, keep))
    else:  # PHASE_BALANCED
        per_phase: dict[str, list[int]] = {}
        for ply, phase, _ in candidates:
            if ply % max(1, spec.every_n) == 0:
                per_phase.setdefault(phase, []).append(ply)
        quota = max(1, (spec.max_per_game or len(candidates)) // max(1, len(per_phase)))
        chosen = sorted(ply for indices in per_phase.values() for ply in indices[:quota])

    chosen = _cap(chosen, spec.max_per_game)
    # A strategy can select a ply twice; deduplicate while preserving order.
    seen: set[int] = set()
    ordered: list[int] = []
    for ply in chosen:
        if ply not in seen:
            seen.add(ply)
            ordered.append(ply)
    return ordered


def positions_for_game(
    game: Game, *, game_id: str, spec: SamplingSpec | None = None
) -> list[PositionRecord]:
    """Every sampled position of one normalized game."""
    effective = spec or DEFAULT_SAMPLING
    fens: dict[int, str] = {0: game.initial_position}
    sans: dict[int, str] = {}
    candidates: list[tuple[int, str, bool]] = []

    for move in game.moves:
        fens[move.ply] = move.fen_after
        sans[move.ply] = move.san

    for ply in sorted(fens):
        fen = fens[ply]
        phase = classify_position_fen(fen).value
        candidates.append((ply, phase, _tactical_flags(fen)))

    phase_by_ply = {ply: phase for ply, phase, _ in candidates}
    records: list[PositionRecord] = []
    for ply in _select_indices(candidates, effective):
        fen = fens[ply]
        fields = fen.split(" ")
        records.append(
            PositionRecord(
                position_id=f"{game_id}:{ply}",
                game_id=game_id,
                ply=ply,
                move_number=(ply + 1) // 2 if ply else 1,
                side_to_move="white" if ply % 2 == 0 else "black",
                fen=fen,
                phase=phase_by_ply.get(ply),
                move_san=sans.get(ply),
                # Piece placement plus side to move: the same position reached by two
                # different games shares this hash, which is reported rather than hidden.
                position_hash=" ".join(fields[:2]),
            )
        )
    return records


def iter_positions(
    paths: Sequence[str | Path], *, spec: SamplingSpec | None = None
) -> Iterator[PositionRecord]:
    """Stream position rows for a corpus, in the importer's own order and ids."""
    effective = spec or DEFAULT_SAMPLING
    occurrences: dict[str, int] = {}
    for path in sorted(str(item) for item in paths):
        file_path = Path(path)
        if not file_path.is_file():
            continue
        for _index, text in iter_game_texts(file_path):
            try:
                games = parse_games(text, max_plies=PARSER_MAX_PLIES)
            except Exception:  # noqa: BLE001 — malformed input is skipped here, not fatal
                continue
            for game in games:
                moves_hash, metadata_hash = identity_hashes(game)
                key = f"{moves_hash}|{metadata_hash}"
                occurrence = occurrences.get(key, 0)
                occurrences[key] = occurrence + 1
                game_id = stable_game_id(moves_hash, metadata_hash, occurrence)
                yield from positions_for_game(game, game_id=game_id, spec=effective)
        logger.debug("Extracted positions from %s", path)


def build_positions(
    paths: Sequence[str | Path], *, spec: SamplingSpec | None = None
) -> list[PositionRecord]:
    """Materialise position rows for a corpus (streams internally)."""
    return list(iter_positions(paths, spec=spec))


__all__ = [
    "DEFAULT_SAMPLING",
    "STRATEGY_MEANINGS",
    "SamplingSpec",
    "SamplingStrategy",
    "build_positions",
    "iter_positions",
    "positions_for_game",
]
