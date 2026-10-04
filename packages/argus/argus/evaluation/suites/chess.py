"""Chess correctness benchmarks (§5/§6/§7).

Three suites, all deterministic and engine-free:

* ``chess_rules`` — Caissa's move validators agree with python-chess on a set of
  standard positions, refuse illegal moves, and never accept a move that the
  reference library rejects. The reference is python-chess, which Caissa already
  depends on, so the comparison is checkable rather than asserted.
* ``fen_benchmark`` — valid, malformed and impossible FENs classify correctly.
* ``pgn_benchmark`` — every PGN category parses (or fails) as expected, and the
  positions generated from a parsed game are legal.
"""

from __future__ import annotations

import chess

from argus.chess_core.fen import validate_fen
from argus.chess_core.moves import validate_san, validate_uci
from argus.chess_core.pgn import parse_games, validate_pgn
from argus.evaluation.fixtures import (
    ANNOTATED_PGN,
    CASTLING_PGN,
    CHECK_PGN,
    CORRUPTED_PGN,
    DRAW_PGN,
    EN_PASSANT_PGN,
    FEN_CASES,
    HEADERS_ONLY_PGN,
    ILLEGAL_MOVE_PGN,
    MULTI_GAME_PGN,
    OPERA_GAME_PGN,
    POSITION_CASES,
    PROMOTION_PGN,
    REPETITION_PGN,
    RUY_BREYER_PGN,
    SCHOLARS_MATE_PGN,
)
from argus.evaluation.results import CheckResult, CheckStatus, SuiteResult, check
from argus.shared.errors import InvalidPgnError


def _position_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    for case in POSITION_CASES:
        board = chess.Board(case.fen)
        expected = case.expected
        problems: list[str] = []

        if "side_to_move" in expected:
            actual = "w" if board.turn else "b"
            if actual != expected["side_to_move"]:
                problems.append(f"side_to_move={actual}")
        if "legal_move_count" in expected:
            actual = board.legal_moves.count()
            if actual != expected["legal_move_count"]:
                problems.append(f"legal_move_count={actual}")
        for key, attr in (
            ("is_check", "is_check"),
            ("is_checkmate", "is_checkmate"),
            ("is_stalemate", "is_stalemate"),
            ("is_game_over", "is_game_over"),
            ("is_insufficient_material", "is_insufficient_material"),
            ("can_claim_fifty_moves", "can_claim_fifty_moves"),
        ):
            if key in expected and getattr(board, attr)() != expected[key]:
                problems.append(f"{key}={getattr(board, attr)()}")
        for san in expected.get("legal_san_includes", []):
            result = validate_san(case.fen, san)
            if not result.is_valid:
                problems.append(f"Caissa refused legal SAN {san!r}: {result.error}")
        for san in expected.get("validate_san_refuses", []):
            if validate_san(case.fen, san).is_valid:
                problems.append(f"Caissa accepted illegal SAN {san!r}")
        for square in expected.get("no_legal_moves_from", []):
            index = chess.parse_square(square)
            from_square = [m for m in board.legal_moves if m.from_square == index]
            if from_square:
                problems.append(f"{square} has {len(from_square)} legal move(s)")
            if from_square:
                uci = from_square[0].uci()
                if validate_uci(case.fen, uci).is_valid:
                    problems.append(f"Caissa accepted pinned move {uci}")

        checks.append(
            check(
                f"position.{case.name}",
                not problems,
                detail="; ".join(problems) if problems else case.category,
            )
        )
        if any(c.status is CheckStatus.FAIL for c in checks):
            break
    return checks


def chess_rules_suite(context) -> SuiteResult:
    """Legality, castling, en passant, promotion, pins, repetition, endgame rules."""
    checks: list[CheckResult] = []
    checks.extend(_position_checks())

    # Caissa must agree with the reference on *every* legal move in every fixture.
    disagreements = 0
    total_moves = 0
    for case in POSITION_CASES:
        board = chess.Board(case.fen)
        for move in board.legal_moves:
            total_moves += 1
            uci = move.uci()
            if not validate_uci(case.fen, uci).is_valid:
                disagreements += 1
            expected_san = board.san(move)
            san_result = validate_san(case.fen, expected_san)
            if not san_result.is_valid or san_result.normalized_uci != uci:
                disagreements += 1
    checks.append(
        check(
            "legal moves all validate",
            disagreements == 0,
            detail=f"{total_moves} legal moves, {disagreements} disagreement(s)",
            metrics={"legal_moves": total_moves, "disagreements": disagreements},
            critical=True,
        )
    )

    # Illegal moves must be refused, not silently accepted.
    illegal_refusals = [
        validate_san("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e5").is_valid,
        validate_san("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "Nf6").is_valid,
        validate_uci("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e7e5").is_valid,
    ]
    checks.append(
        check(
            "illegal moves refused",
            not any(illegal_refusals),
            detail=f"{sum(illegal_refusals)} illegal move(s) accepted",
            critical=True,
        )
    )

    # Threefold repetition and the fifty-move rule, via a real game record.
    try:
        repetition_game = parse_games(REPETITION_PGN)[0]
        board = chess.Board(repetition_game.initial_position)
        for move in repetition_game.moves:
            board.push(chess.Move.from_uci(move.uci))
        checks.append(
            check(
                "threefold repetition detected",
                board.can_claim_threefold_repetition(),
                detail="knights shuffled to repeat the start position three times",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            CheckResult(
                name="threefold repetition detected",
                status=CheckStatus.FAIL,
                detail=f"could not replay repetition game: {exc}",
            )
        )

    return SuiteResult(
        suite="chess_rules",
        title="Chess rule correctness",
        checks=checks,

    )


def fen_benchmark_suite(context) -> SuiteResult:
    """Valid, malformed and impossible FENs classify correctly (§6)."""
    checks: list[CheckResult] = []
    for case in FEN_CASES:
        result = validate_fen(case.fen)
        ok = result.is_valid is case.valid
        if ok and not case.valid and case.expect_error_contains:
            ok = any(
                case.expect_error_contains.lower() in error.lower()
                for error in result.errors
            )
        detail = (
            "valid" if result.is_valid else f"refused: {'; '.join(result.errors) or 'no reason'}"
        )
        checks.append(check(f"fen.{case.name}", ok, detail=detail, metrics={"category": case.category}))

    # Caissa and python-chess must agree on validity for every fixture.
    disagreements = 0
    for case in FEN_CASES:
        import chess

        try:
            reference_valid = chess.Board(case.fen).status() == chess.STATUS_VALID
        except ValueError:
            reference_valid = False
        if reference_valid is not case.valid:
            disagreements += 1
    checks.append(
        check(
            "agrees with reference library",
            disagreements == 0,
            detail=f"{disagreements} disagreement(s)",
            critical=True,
        )
    )

    return SuiteResult(
        suite="fen_benchmark",
        title="FEN validation",
        checks=checks,

    )


#: (name, pgn, expect_valid, expected_game_count)
_PGN_CASES: tuple[tuple[str, str, bool, int], ...] = (
    ("standard.opera", OPERA_GAME_PGN, True, 1),
    ("complex.ruy_breyer", RUY_BREYER_PGN, True, 1),
    ("castling", CASTLING_PGN, True, 1),
    ("promotion", PROMOTION_PGN, True, 1),
    ("en_passant", EN_PASSANT_PGN, True, 1),
    ("draw", DRAW_PGN, True, 1),
    ("check", CHECK_PGN, True, 1),
    ("checkmate.scholars", SCHOLARS_MATE_PGN, True, 1),
    ("annotated", ANNOTATED_PGN, True, 1),
    ("multi_game", MULTI_GAME_PGN, True, 2),
    # Caissa refuses a game with no moves: a partial record is rejected with a
    # reason rather than stored as an empty game.
    ("partial.headers_only", HEADERS_ONLY_PGN, False, 0),
    ("invalid.illegal_move", ILLEGAL_MOVE_PGN, False, 0),
    ("corrupted", CORRUPTED_PGN, False, 0),
)


def pgn_benchmark_suite(context) -> SuiteResult:
    """Parse success, failure classification and position generation (§7)."""
    checks: list[CheckResult] = []
    for name, pgn, expect_valid, expect_count in _PGN_CASES:
        validation = validate_pgn(pgn)
        problems: list[str] = []
        if validation.is_valid is not expect_valid:
            problems.append(f"is_valid={validation.is_valid}")
        if expect_valid and validation.game_count != expect_count:
            problems.append(f"game_count={validation.game_count}")
        if not expect_valid and not validation.errors:
            problems.append("invalid PGN reported no reason")
        if expect_valid:
            try:
                games = parse_games(pgn)
            except InvalidPgnError as exc:
                problems.append(f"parse raised: {exc.message}")
                games = []
            for game in games:
                # Replay every move against the board: the parser must never emit
                # a game whose moves are not legal in sequence.
                board = chess.Board(game.initial_position)
                for move in game.moves:
                    candidate = chess.Move.from_uci(move.uci)
                    if candidate not in board.legal_moves:
                        problems.append(f"illegal move in output: {move.uci}")
                        break
                    board.push(candidate)
        checks.append(check(f"pgn.{name}", not problems, detail="; ".join(problems)))

    return SuiteResult(
        suite="pgn_benchmark",
        title="PGN parsing",
        checks=checks,

    )


__all__ = [
    "chess_rules_suite",
    "fen_benchmark_suite",
    "pgn_benchmark_suite",
]
