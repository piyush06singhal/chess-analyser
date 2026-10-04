"""Progressive hints derived ONLY from stored evidence (spec §11).

A hint that says "look for a fork" when the engine line contains no fork is a
hallucination — worse than no hint, because it teaches a false pattern. This
module therefore emits hints only from what is *stored and verifiable*: the
solution move's observable properties, the position's phase, the recorded
tactical tags, the evaluation gap, and the played move's failure.

The sequence is fixed and progressive — vague to specific — and the solution
itself is never included. The final hint stops one step short of the answer,
because the reveal button exists for that.
"""

from __future__ import annotations


import chess

#: Bumped when hint construction changes (stored alongside attempts).
HINT_POLICY_VERSION = "8.0"

#: Maximum hints the policy will ever produce.
MAX_HINTS = 3


def build_hints(
    *,
    fen: str,
    solution_uci: str,
    solution_san: str,
    solution_eval_cp: int | None = None,
    solution_eval_mate: int | None = None,
    played_move_san: str | None = None,
    played_loss_cp: int | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    position_type: str | None = None,
    side_to_move: str | None = None,
) -> list[str]:
    """Build the progressive hint sequence from verifiable evidence only.

    Every sentence is grounded: a tag that was recorded, a property of the
    solution move that can be checked on the board, or a measurement that is
    stored. When evidence is thin, fewer hints are produced — never filler.
    """
    board = chess.Board(fen)
    solution = chess.Move.from_uci(solution_uci)
    tags = list(tags or [])
    side = (side_to_move or ("white" if board.turn == chess.WHITE else "black")).lower()
    hints: list[str] = []

    mover = "White" if side == "white" else "Black"

    # Hint 1 — the strategic frame: what kind of move is being asked for.
    frame_bits: list[str] = []
    if solution_eval_mate is not None and solution_eval_mate > 0:
        frame_bits.append(f"{mover} has a forced win here")
    elif category == "endgame":
        frame_bits.append("this is an endgame decision")
    elif position_type == "find_defense":
        frame_bits.append(f"{mover} is under pressure and must defend accurately")
    elif solution_eval_cp is not None and solution_eval_cp >= 300:
        frame_bits.append(f"{mover} has a strong advantage to press")
    if board.is_check():
        frame_bits.append("you are in check right now")
    if frame_bits:
        hints.append("; ".join(frame_bits) + ".")

    # Hint 2 — the piece and the mechanism, from the solution's own properties.
    piece = board.piece_at(solution.from_square)
    piece_names = {
        chess.PAWN: "pawn",
        chess.KNIGHT: "knight",
        chess.BISHOP: "bishop",
        chess.ROOK: "rook",
        chess.QUEEN: "queen",
        chess.KING: "king",
    }
    piece_name = piece_names.get(piece.piece_type if piece else None)
    if piece_name:
        mechanism_bits: list[str] = []
        if board.gives_check(solution):
            mechanism_bits.append("it involves a check")
        if board.is_capture(solution):
            mechanism_bits.append("it wins material")
        if solution.promotion is not None:
            mechanism_bits.append("a promotion is part of the idea")
        if "mating_pattern" in tags:
            mechanism_bits.append("the line ends in mate")
        elif "fork" in tags or "double_attack" in tags:
            mechanism_bits.append("two targets are attacked at once")
        elif "pin" in tags:
            mechanism_bits.append("a piece cannot move without losing something behind it")
        elif "skewer" in tags:
            mechanism_bits.append("a valuable piece must move and lose something behind it")
        elif "hanging_piece" in tags:
            mechanism_bits.append("an undefended piece can be exploited")
        detail = f" The key move is with the {piece_name}" + (
            f" — {', '.join(mechanism_bits)}." if mechanism_bits else "."
        )
        hints.append(detail.strip())

    # Hint 3 — the contrast with the played move: why the mistake was one.
    if played_move_san and played_loss_cp and played_loss_cp >= 100:
        try:
            played = board.parse_san(played_move_san)
            played_piece = board.piece_at(played.from_square)
            played_name = piece_names.get(played_piece.piece_type if played_piece else None)
            if played_name and played_name != piece_name:
                hints.append(
                    f"the move played in the game ({played_move_san}) was with the "
                    f"{played_name} — the engine's choice uses a different piece"
                )
            elif board.is_capture(played) and not board.is_capture(solution):
                hints.append(
                    f"the move played in the game ({played_move_san}) was a capture — "
                    "the engine's choice is not"
                )
            else:
                hints.append(
                    f"the move played in the game ({played_move_san}) loses "
                    f"{played_loss_cp} centipawns according to the engine"
                )
        except ValueError:
            hints.append(
                f"the move played in the game ({played_move_san}) loses "
                f"{played_loss_cp} centipawns according to the engine"
            )

    # Always finish with the forcing-moves nudge when nothing else filled slot 3
    # — it is the one universally-true, evidence-backed search instruction.
    if len(hints) < MAX_HINTS:
        forcing = sum(
            1 for m in board.legal_moves if board.is_capture(m) or board.gives_check(m)
        )
        if forcing:
            hints.append(
                f"there {'are' if forcing != 1 else 'is'} {forcing} forcing move(s) "
                "(checks or captures) in this position — examine them first"
            )
        else:
            hints.append(
                "no checks or captures are available; look for a quiet improving move"
            )

    # Truncation with intent: vague → specific, never past the answer.
    return hints[:MAX_HINTS]


__all__ = ["HINT_POLICY_VERSION", "MAX_HINTS", "build_hints"]
