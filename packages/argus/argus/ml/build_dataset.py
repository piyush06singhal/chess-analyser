"""Dataset building: PGN corpus → engine-evaluated feature dataset.

Bridges the analysis pipeline and the ML module: games are parsed with
``argus.chess_core``, analyzed with the real Stockfish pipeline
(``GameAnalyzer`` — the same code path the API uses), and exported as a
documented CSV of per-move rows. No row is synthesized: every feature value
comes from the board, and every label comes from the game's recorded result
or the engine's evaluation at a fixed depth.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from argus.analysis.engine.stockfish import StockfishEngine, StockfishSettings
from argus.analysis.game_analyzer import GameAnalyzer
from argus.chess_core.pgn import parse_games
from argus.chess_core.models import Color

# Feature columns written per move (raw board features + engine evidence).
FEATURE_COLUMNS = [
    "material_balance",
    "mobility_diff",
    "king_safety_diff",
    "isolated_pawns_diff",
    "doubled_pawns_diff",
    "passed_pawns_diff",
    "center_occupied_diff",
    "center_attacked_diff",
    "undeveloped_diff",
    "hanging_diff",
    "total_pieces",
]

ALL_COLUMNS = [
    "game_id",
    "ply",
    "move_number",
    "color",
    "phase",
    "evaluation_cp",
    *FEATURE_COLUMNS,
    "result",
]


@dataclass(frozen=True)
class DatasetBuildResult:
    """Outcome of one dataset build run (for provenance records)."""

    games_parsed: int
    games_failed: int
    rows_written: int
    output_path: Path
    engine_version: str | None
    depth: int
    generated_at: str


def _diff(before, after, attr: str) -> int:  # noqa: ANN001 — RawPositionFeatures
    """Signed (white − black) difference of one feature between two boards."""
    return getattr(after, attr) - getattr(before, attr)


def _evaluation_cp(evaluation) -> int | None:  # noqa: ANN001 — AnalyzedMove
    """Evaluation after the move, from White's perspective (engine-derived)."""
    if evaluation.evaluation_after_cp is None:
        return None
    return (
        evaluation.evaluation_after_cp
        if evaluation.color is Color.WHITE
        else -evaluation.evaluation_after_cp
    )


def build_rows_for_game(game, analysis) -> list[dict[str, str]]:  # noqa: ANN001
    """Flatten one analyzed game into per-move dataset rows.

    Raises:
        ValueError: when the game has no recorded result (labels must be real).
    """
    result = game.result.value
    if result not in ("1-0", "0-1", "1/2-1/2"):
        raise ValueError(
            f"Game '{game.id}' has no decisive/recorded result ('{result}'); "
            "result-label datasets require real results"
        )
    rows: list[dict[str, str]] = []
    for move in analysis.moves:
        fb, fa = move.features_before, move.features_after
        rows.append(
            {
                "game_id": game.id or "",
                "ply": str(move.ply),
                "move_number": str(move.move_number),
                "color": move.color.value,
                "phase": move.phase.value,
                "evaluation_cp": ""
                if _evaluation_cp(move) is None
                else str(_evaluation_cp(move)),
                "material_balance": str(fb.material_balance),
                "mobility_diff": str(_diff(fb, fa, "mobility_white") - _diff(fb, fa, "mobility_black")),
                "king_safety_diff": str(_diff(fb, fa, "king_safety_white") - _diff(fb, fa, "king_safety_black")),
                "isolated_pawns_diff": str(_diff(fb, fa, "isolated_pawns_white") - _diff(fb, fa, "isolated_pawns_black")),
                "doubled_pawns_diff": str(_diff(fb, fa, "doubled_pawns_white") - _diff(fb, fa, "doubled_pawns_black")),
                "passed_pawns_diff": str(_diff(fb, fa, "passed_pawns_white") - _diff(fb, fa, "passed_pawns_black")),
                "center_occupied_diff": str(_diff(fb, fa, "center_occupied_white") - _diff(fb, fa, "center_occupied_black")),
                "center_attacked_diff": str(_diff(fb, fa, "center_attacked_white") - _diff(fb, fa, "center_attacked_black")),
                "undeveloped_diff": str(_diff(fb, fa, "undeveloped_pieces_white") - _diff(fb, fa, "undeveloped_pieces_black")),
                "hanging_diff": str(_diff(fb, fa, "hanging_pieces_white") - _diff(fb, fa, "hanging_pieces_black")),
                "total_pieces": str(fa.total_pieces),
                "result": result,
            }
        )
    return rows


def build_dataset(
    pgn_paths: list[Path],
    *,
    output_path: Path,
    depth: int,
    multipv: int,
    stockfish_path: str | None,
    timeout_seconds: float,
    max_games: int | None,
) -> DatasetBuildResult:
    """Parse every PGN file, analyze all games, and write the feature CSV."""
    engine = StockfishEngine(
        StockfishSettings(
            path=stockfish_path or None,
            depth=depth,
            multipv=multipv,
            timeout_seconds=timeout_seconds,
        )
    )
    info = engine.info()
    if not info.get("available"):
        from argus.shared.errors import EngineUnavailableError

        raise EngineUnavailableError(
            "Stockfish is required to build an engine-evaluated dataset "
            f"({info.get('reason', 'unavailable')})"
        )

    analyzer = GameAnalyzer(engine)
    all_rows: list[dict[str, str]] = []
    parsed = failed = 0
    for pgn_path in pgn_paths:
        for game in parse_games(pgn_path.read_text(encoding="utf-8")):
            if max_games is not None and parsed >= max_games:
                break
            try:
                rows = build_rows_for_game(game, analyzer.analyze(game))
            except ValueError as exc:
                failed += 1
                print(f"  skipped: {exc}", file=sys.stderr)
                continue
            all_rows.extend(rows)
            parsed += 1
            print(f"  analyzed: {game.white_player.name} vs {game.black_player.name} ({len(rows)} moves)")
    engine.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALL_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    generated_at = datetime.now(timezone.utc).isoformat()
    provenance = output_path.with_suffix(".meta.json")
    provenance.write_text(
        json_provenance(
            {
                "source_files": [str(path) for path in pgn_paths],
                "games_parsed": parsed,
                "games_failed": failed,
                "rows_written": len(all_rows),
                "engine": "stockfish",
                "engine_version": info.get("version"),
                "depth": depth,
                "multipv": multipv,
                "feature_columns": FEATURE_COLUMNS,
                "label_column": "result",
                "generated_at": generated_at,
            }
        ),
        encoding="utf-8",
    )
    return DatasetBuildResult(
        games_parsed=parsed,
        games_failed=failed,
        rows_written=len(all_rows),
        output_path=output_path,
        engine_version=info.get("version"),
        depth=depth,
        generated_at=generated_at,
    )


def json_provenance(payload: dict) -> str:  # noqa: ANN201 — small helper
    import json

    return json.dumps(payload, indent=2)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: `python -m argus.ml.build_dataset ...`."""
    parser = argparse.ArgumentParser(
        description="Build an engine-evaluated per-move dataset from PGN files."
    )
    parser.add_argument("pgn_files", nargs="+", type=Path, help="PGN corpus files")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/positions_dataset.csv"),
        help="Output CSV path (default: data/processed/positions_dataset.csv)",
    )
    parser.add_argument("--depth", type=int, default=12, help="Stockfish depth (default: 12)")
    parser.add_argument("--multipv", type=int, default=1, help="MultiPV (default: 1)")
    parser.add_argument("--stockfish-path", default=None, help="Explicit Stockfish binary path")
    parser.add_argument("--timeout", type=float, default=30.0, help="Engine timeout seconds")
    parser.add_argument("--max-games", type=int, default=None, help="Cap on games analyzed")
    args = parser.parse_args(argv)

    result = build_dataset(
        args.pgn_files,
        output_path=args.output,
        depth=args.depth,
        multipv=args.multipv,
        stockfish_path=args.stockfish_path,
        timeout_seconds=args.timeout,
        max_games=args.max_games,
    )
    print(
        f"Dataset written: {result.output_path} "
        f"[rows={result.rows_written} games={result.games_parsed} skipped={result.games_failed} "
        f"engine={result.engine_version} depth={result.depth}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
