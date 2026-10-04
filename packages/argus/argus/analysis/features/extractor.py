"""Raw feature extraction from python-chess boards.

Pure, deterministic, engine-free: every feature is computed from the board
state alone so results are reproducible across runs and cheap to compute for
every position of a game.
"""

from __future__ import annotations

import chess

from argus.analysis.features.models import RawPositionFeatures

# Standard piece values in centipawns. Used for material balance only —
# engine evaluations remain the authoritative strength signal.
PIECE_VALUES_CP: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

CENTER_SQUARES = (chess.D4, chess.E4, chess.D5, chess.E5)

_ORIGINAL_SQUARES = {
    chess.WHITE: {
        chess.KNIGHT: (chess.B1, chess.G1),
        chess.BISHOP: (chess.C1, chess.F1),
        chess.ROOK: (chess.A1, chess.H1),
        chess.QUEEN: (chess.D1,),
    },
    chess.BLACK: {
        chess.KNIGHT: (chess.B8, chess.G8),
        chess.BISHOP: (chess.C8, chess.F8),
        chess.ROOK: (chess.A8, chess.H8),
        chess.QUEEN: (chess.D8,),
    },
}

_PHASE_PIECE_VALUES = {
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        PIECE_VALUES_CP[piece.piece_type]
        for piece in board.piece_map().values()
        if piece.color == color
    )


def _mobility(board: chess.Board, color: chess.Color) -> int:
    if board.turn == color:
        return board.legal_moves.count()
    flipped = board.copy(stack=False)
    flipped.turn = color
    return flipped.legal_moves.count()


def _pawn_shield(board: chess.Board, color: chess.Color) -> int:
    """Count pawns on the up-to-three squares directly in front of the king."""
    king_square = board.king(color)
    if king_square is None:
        return 0
    file = chess.square_file(king_square)
    rank = chess.square_rank(king_square)
    forward = 1 if color == chess.WHITE else -1
    shield = 0
    for df in (-1, 0, 1):
        f = file + df
        r = rank + forward
        if 0 <= f <= 7 and 0 <= r <= 7:
            piece = board.piece_at(chess.square(f, r))
            if piece is not None and piece.piece_type == chess.PAWN and piece.color == color:
                shield += 1
    return shield


def _is_castled(board: chess.Board, color: chess.Color) -> bool:
    """Heuristic: king on the c/g file of its home rank with no castling
    rights remaining for that side."""
    king_square = board.king(color)
    if king_square is None:
        return False
    home_rank = 0 if color == chess.WHITE else 7
    if chess.square_rank(king_square) != home_rank:
        return False
    if chess.square_file(king_square) not in (2, 6):  # c-file, g-file
        return False
    rights = board.clean_castling_rights()
    if color == chess.WHITE:
        return not (rights & (chess.BB_A1 | chess.BB_H1))
    return not (rights & (chess.BB_A8 | chess.BB_H8))


def _pawn_files(board: chess.Board, color: chess.Color) -> dict[int, list[int]]:
    """Map file -> list of ranks for the side's pawns."""
    files: dict[int, list[int]] = {}
    for square in chess.SquareSet(board.pawns & board.occupied_co[color]):
        files.setdefault(chess.square_file(square), []).append(chess.square_rank(square))
    return files


def pawn_file_map(board: chess.Board, color: chess.Color) -> dict[int, list[int]]:
    """Public accessor: map file -> ranks of the side's pawns.

    Exposed so the game-intelligence layer can reuse the exact same pawn-file
    logic instead of re-deriving it (single source of pawn-structure truth).
    """
    return _pawn_files(board, color)


def _isolated_pawns(pawn_files: dict[int, list[int]]) -> int:
    return sum(
        1
        for file, ranks in pawn_files.items()
        if file - 1 not in pawn_files and file + 1 not in pawn_files
    )


def _doubled_pawns(pawn_files: dict[int, list[int]]) -> int:
    return sum(max(0, len(ranks) - 1) for ranks in pawn_files.values())


def _passed_pawns(
    board: chess.Board, color: chess.Color, pawn_files: dict[int, list[int]]
) -> int:
    """Pawns with no opposing pawn ahead on the same or adjacent files."""
    opponent_pawns = chess.SquareSet(board.pawns & board.occupied_co[not color])
    direction = 1 if color == chess.WHITE else -1
    count = 0
    for file, ranks in pawn_files.items():
        for rank in ranks:
            blocked = False
            for df in (-1, 0, 1):
                f = file + df
                if not 0 <= f <= 7:
                    continue
                for step in range(1, 8):
                    r = rank + direction * step
                    if not 0 <= r <= 7:
                        break
                    if chess.square(f, r) in opponent_pawns:
                        blocked = True
                        break
                if blocked:
                    break
            if not blocked:
                count += 1
    return count


def _backward_pawns(
    board: chess.Board, color: chess.Color, pawn_files: dict[int, list[int]]
) -> int:
    """Simplified backward-pawn heuristic: a pawn is backward when its forward
    square is attacked by an enemy pawn and no friendly pawn on an adjacent
    file can support it."""
    direction = 1 if color == chess.WHITE else -1
    enemy = not color
    count = 0
    for file, ranks in pawn_files.items():
        for rank in ranks:
            forward_rank = rank + direction
            if not 0 <= forward_rank <= 7:
                continue
            forward_square = chess.square(file, forward_rank)
            attackers = board.attackers(enemy, forward_square)
            has_enemy_pawn_attacker = any(
                (piece := board.piece_at(square)) is not None
                and piece.piece_type == chess.PAWN
                for square in attackers
            )
            if not has_enemy_pawn_attacker:
                continue
            supported = False
            for df in (-1, 1):
                adjacent = file + df
                if not 0 <= adjacent <= 7:
                    continue
                for support_rank in pawn_files.get(adjacent, []):
                    behind = support_rank <= rank if direction == 1 else support_rank >= rank
                    if behind:
                        supported = True
                        break
                if supported:
                    break
            if not supported:
                count += 1
    return count


def _center_control(board: chess.Board, color: chess.Color) -> tuple[int, int]:
    occupied = 0
    attacked = 0
    for square in CENTER_SQUARES:
        piece = board.piece_at(square)
        if piece is not None and piece.color == color:
            occupied += 1
        if board.attackers(color, square):
            attacked += 1
    return occupied, attacked


def _undeveloped(board: chess.Board, color: chess.Color) -> int:
    count = 0
    for piece_type, squares in _ORIGINAL_SQUARES[color].items():
        for square in squares:
            piece = board.piece_at(square)
            if piece is not None and piece.piece_type == piece_type and piece.color == color:
                count += 1
    return count


def _hanging_pieces(board: chess.Board, color: chess.Color) -> int:
    """Count the side's pieces that are attacked and not defended."""
    count = 0
    enemy = not color
    for square in chess.SquareSet(board.occupied_co[color]):
        piece = board.piece_at(square)
        if piece is None or piece.piece_type == chess.KING:
            continue
        if board.attackers(enemy, square) and not board.attackers(color, square):
            count += 1
    return count


def extract_position_features(board: chess.Board) -> RawPositionFeatures:
    """Extract raw features for the given position.

    Deterministic: identical boards always produce identical features.
    """
    white_material = _material(board, chess.WHITE)
    black_material = _material(board, chess.BLACK)
    white_pawn_files = _pawn_files(board, chess.WHITE)
    black_pawn_files = _pawn_files(board, chess.BLACK)
    center_white_occupied, center_white_attacked = _center_control(board, chess.WHITE)
    center_black_occupied, center_black_attacked = _center_control(board, chess.BLACK)

    pieces = board.piece_map().values()
    minors = sum(1 for piece in pieces if piece.piece_type in (chess.KNIGHT, chess.BISHOP))
    majors = sum(1 for piece in pieces if piece.piece_type in (chess.ROOK, chess.QUEEN))
    queens = sum(1 for piece in pieces if piece.piece_type == chess.QUEEN)

    return RawPositionFeatures(
        material_balance=white_material - black_material,
        material_white=white_material,
        material_black=black_material,
        mobility_white=_mobility(board, chess.WHITE),
        mobility_black=_mobility(board, chess.BLACK),
        king_safety_white=_pawn_shield(board, chess.WHITE),
        king_safety_black=_pawn_shield(board, chess.BLACK),
        white_in_check=board.turn == chess.WHITE and board.is_check(),
        black_in_check=board.turn == chess.BLACK and board.is_check(),
        white_castled=_is_castled(board, chess.WHITE),
        black_castled=_is_castled(board, chess.BLACK),
        isolated_pawns_white=_isolated_pawns(white_pawn_files),
        isolated_pawns_black=_isolated_pawns(black_pawn_files),
        doubled_pawns_white=_doubled_pawns(white_pawn_files),
        doubled_pawns_black=_doubled_pawns(black_pawn_files),
        passed_pawns_white=_passed_pawns(board, chess.WHITE, white_pawn_files),
        passed_pawns_black=_passed_pawns(board, chess.BLACK, black_pawn_files),
        backward_pawns_white=_backward_pawns(board, chess.WHITE, white_pawn_files),
        backward_pawns_black=_backward_pawns(board, chess.BLACK, black_pawn_files),
        center_occupied_white=center_white_occupied,
        center_occupied_black=center_black_occupied,
        center_attacked_white=center_white_attacked,
        center_attacked_black=center_black_attacked,
        undeveloped_pieces_white=_undeveloped(board, chess.WHITE),
        undeveloped_pieces_black=_undeveloped(board, chess.BLACK),
        hanging_pieces_white=_hanging_pieces(board, chess.WHITE),
        hanging_pieces_black=_hanging_pieces(board, chess.BLACK),
        checkers=len(board.checkers()),
        queens_on_board=queens > 0,
        minor_pieces_on_board=minors,
        major_pieces_on_board=majors,
        total_pieces=board.occupied_co[chess.WHITE].bit_count()
        + board.occupied_co[chess.BLACK].bit_count(),
    )


def extract_position_features_from_fen(fen: str) -> RawPositionFeatures:
    """Extract raw features directly from a FEN string."""
    return extract_position_features(chess.Board(fen))


def phase_piece_material(board: chess.Board) -> int:
    """Non-pawn, non-king material points (N/B=3, R=5, Q=9) for phase logic."""
    return sum(
        _PHASE_PIECE_VALUES[piece.piece_type]
        for piece in board.piece_map().values()
        if piece.piece_type in _PHASE_PIECE_VALUES
    )
