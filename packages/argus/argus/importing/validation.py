"""Structured PGN validation.

Unlike :func:`argus.chess_core.pgn.validate_pgn` (which answers a simple
valid/invalid question), this module reports *why* a PGN is rejected — with a
category, the offending game, and (where determinable) the move number and ply.
Errors are always user-presentable strings; raw parser exceptions are never
propagated to clients.
"""

from __future__ import annotations

import io

import chess
import chess.pgn

from argus.importing.base import IssueType, ValidationIssue, ValidationReport

DEFAULT_MAX_PLIES = 1000
_VALID_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


def _issue(
    issue_type: IssueType,
    message: str,
    *,
    game_index: int | None = None,
    move_number: int | None = None,
    ply: int | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        type=issue_type,
        message=message,
        game_index=game_index,
        move_number=move_number,
        ply=ply,
    )


def validate_pgn_detailed(
    pgn_text: str, *, max_plies: int | None = DEFAULT_MAX_PLIES
) -> ValidationReport:
    """Validate every game in a PGN payload and collect structured issues.

    Checks performed: emptiness, readability, header-only games, illegal move
    sequences, overlong games, and result/checkmate consistency. Returns a
    report rather than raising.
    """
    if not (pgn_text or "").strip():
        return ValidationReport(
            is_valid=False,
            game_count=0,
            issues=[_issue(IssueType.EMPTY_PGN, "PGN text is empty")],
        )

    stream = io.StringIO(pgn_text)
    issues: list[ValidationIssue] = []
    valid_games = 0
    total_plies = 0
    index = -1

    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception as exc:  # noqa: BLE001 — the reader raises arbitrary errors on malformed input
            issues.append(
                _issue(IssueType.MALFORMED_PGN, f"Could not read PGN: {exc}")
            )
            break
        if game is None:
            break

        index += 1
        game_issues, plies = _inspect_game(game, index=index, max_plies=max_plies)
        if game_issues:
            issues.extend(game_issues)
            continue
        valid_games += 1
        total_plies += plies

    if valid_games == 0 and not issues:
        issues.append(
            _issue(IssueType.NO_GAMES, "No games found in the provided PGN text")
        )

    return ValidationReport(
        is_valid=not issues and valid_games > 0,
        game_count=valid_games,
        ply_count=total_plies,
        issues=issues,
    )


# python-chess fills the seven-tag roster with placeholders, so an absent
# header is indistinguishable from "?" unless we exclude the placeholders.
_PLACEHOLDER_HEADERS = {"?", "????.??.??", "*", ""}


def _has_meaningful_headers(game: chess.pgn.Game) -> bool:
    return any(
        value not in _PLACEHOLDER_HEADERS for value in game.headers.values()
    )


def _inspect_game(
    game: chess.pgn.Game, *, index: int, max_plies: int | None
) -> tuple[list[ValidationIssue], int]:
    """Return (issues, plies) for one parsed game."""
    issues: list[ValidationIssue] = []
    board = game.board()

    if not game.variations:
        if not _has_meaningful_headers(game):
            # Free text with no PGN structure at all.
            issues.append(
                _issue(
                    IssueType.MALFORMED_PGN,
                    "Could not find a chess game with moves in the provided text",
                    game_index=index,
                )
            )
        else:
            issues.append(
                _issue(
                    IssueType.INCOMPLETE_GAME,
                    "Game contains headers but no moves",
                    game_index=index,
                )
            )
        return issues, 0

    ply = 0
    for move in game.mainline_moves():
        if max_plies is not None and ply >= max_plies:
            issues.append(
                _issue(
                    IssueType.GAME_TOO_LONG,
                    f"Game exceeds the maximum supported length of {max_plies} plies",
                    game_index=index,
                    move_number=board.fullmove_number,
                    ply=ply + 1,
                )
            )
            return issues, ply
        if not board.is_legal(move):
            issues.append(
                _issue(
                    IssueType.ILLEGAL_MOVE,
                    f"Illegal move at move {board.fullmove_number} (ply {ply + 1})",
                    game_index=index,
                    move_number=board.fullmove_number,
                    ply=ply + 1,
                )
            )
            return issues, ply
        board.push(move)
        ply += 1

    # python-chess stops the main line at the first unparseable move; report it
    # with the move number of the position where parsing halted.
    if game.errors:
        issues.append(
            _issue(
                IssueType.ILLEGAL_MOVE,
                f"Unreadable or illegal move at move {board.fullmove_number}: "
                f"{game.errors[0]}",
                game_index=index,
                move_number=board.fullmove_number,
                ply=ply + 1,
            )
        )
        return issues, ply

    result_header = game.headers.get("Result", "*")
    if result_header not in _VALID_RESULTS:
        issues.append(
            _issue(
                IssueType.RESULT_MISMATCH,
                f"Unsupported result value {result_header!r}",
                game_index=index,
            )
        )
        return issues, ply

    if board.is_checkmate():
        mate_winner = "1-0" if board.turn == chess.BLACK else "0-1"
        if result_header in {"1-0", "0-1"} and result_header != mate_winner:
            issues.append(
                _issue(
                    IssueType.RESULT_MISMATCH,
                    f"Result {result_header} contradicts the checkmate on the board "
                    f"({mate_winner})",
                    game_index=index,
                    move_number=board.fullmove_number,
                    ply=ply,
                )
            )
            return issues, ply

    return issues, ply
