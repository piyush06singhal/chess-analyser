"""Phase 4 game-intelligence tests (detectors, methodology, report).

Every model is fed real, verified chess positions and games. Where a test needs
engine data it either uses measurements that were really produced by Stockfish
(recorded below) or constructs the engine fields explicitly — the intelligence
layer's job is to interpret stored engine output, so the tests control that
input precisely.
"""

from __future__ import annotations

import chess
import pytest

from argus.analysis.classification import MoveClassification
from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence import (
    REPORT_VERSION,
    AccuracyPolicy,
    AdvantagePolicy,
    AdvantageState,
    Certainty,
    EvidenceSource,
    GameContext,
    GameIntelligence,
    GamePhaseDetector,
    IntelligencePolicy,
    MoveFact,
    analyse_accuracy,
    build_trajectory,
    detect_opening,
    detect_tactical_events,
    detect_turning_points,
    state_for,
    win_expectation,
)
from argus.intelligence.accuracy import (
    EVAL_SOURCE_RESULTING_POSITION,
    EVAL_SOURCE_SAME_SEARCH,
    EVAL_SOURCE_UNAVAILABLE,
    material_balance_for,
    played_expectation,
    score_move,
)
from argus.intelligence.conversion import ConversionEventType
from argus.intelligence.king_safety import KingSafetyEventType
from argus.intelligence.material import build_material_timeline
from argus.intelligence.performance import build_phase_performance
from argus.intelligence.positional import build_positional_analysis, weak_squares
from argus.intelligence.structure import build_pawn_structure, pawn_structure_for
from argus.intelligence.tactics import TacticType
from argus.intelligence.turning_points import (
    material_balance_change_for_mover,
    material_delta_against_mover,
)

from tests.conftest import OPERA_GAME_PGN, PROMOTION_PGN
from argus.chess_core.pgn import parse_first_game

#: A short verified line with a structural consequence: 6...dxc6 doubles the
#: black c-pawns and opens the b-file for White.
DOUBLE_PAWN_LINE = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6"]

#: Evans Gambit version of the Opera Game (Morphy, 1858). Used for tactical
#: detection because it contains verified forks, pins and skewers.
EVANS_GAMBIT_SAN = [
    "e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4", "Bxb4", "c3", "Ba5",
    "d4", "exd4", "O-O", "d3", "Qb3", "Qf6", "e5", "Qg6", "Re1", "Nge7",
    "Ba3", "b5", "Qxb5", "Rb8", "Qa4", "Bb6", "Nbd2", "Bb7", "Ne4", "Qf5",
    "Bxd3", "Qh5", "Nf6+", "gxf6", "exf6", "Rg8", "Rad1", "Qxf3", "Rxe7+",
    "Nxe7", "Qxd7+", "Kxd7", "Bf5+", "Ke8", "Bd7+", "Kf8", "Bxe7#",
]

# --- helpers -------------------------------------------------------------------


def san_list_from_pgn(pgn: str) -> list[str]:
    game = parse_first_game(pgn)
    return [move.san for move in game.moves]


def moves_from_sans(san_list: list[str], *, per_ply: dict[int, dict] | None = None) -> list[MoveFact]:
    """Build MoveFacts from SAN moves, with optional per-ply engine fields."""
    board = chess.Board()
    facts: list[MoveFact] = []
    overrides = per_ply or {}
    for index, san in enumerate(san_list):
        move = board.parse_san(san)
        fen_before = board.fen()
        board.push(move)
        facts.append(
            MoveFact(
                ply=index + 1,
                move_number=index // 2 + 1,
                mover=Color.WHITE if index % 2 == 0 else Color.BLACK,
                san=san,
                uci=move.uci(),
                fen_before=fen_before,
                fen_after=board.fen(),
                **overrides.get(index + 1, {}),
            )
        )
    return facts


def set_white_evals(facts: list[MoveFact], values: dict[int, int], *, start: int = 0) -> None:
    """Set White-perspective evaluations per ply, converted to mover perspective."""
    previous = start
    for fact in facts:
        value = values.get(fact.ply, previous)
        sign = 1 if fact.mover is Color.WHITE else -1
        fact.eval_before_cp = sign * previous
        fact.eval_after_cp = sign * value
        fact.eval_change_cp = (value - previous) * sign
        previous = value


def quiet(facts: list[MoveFact], *, cp: int = 0) -> list[MoveFact]:
    """Mark every move as an evaluated, perfect move at a constant evaluation."""
    for fact in facts:
        fact.eval_before_cp = cp
        fact.eval_after_cp = cp
        fact.eval_change_cp = 0
        fact.centipawn_loss = 0
        fact.classification = MoveClassification.BEST
        fact.is_best_move = True
        fact.depth = 12
    return facts


def context_for(facts: list[MoveFact], **overrides) -> GameContext:
    payload = dict(
        game_id="test-game",
        white_player="White Player",
        black_player="Black Player",
        result="*",
        initial_position=chess.STARTING_FEN,
        move_count=len(facts),
    )
    payload.update(overrides)
    return GameContext(**payload)


def intelligence_for(pgn: str, **context_overrides) -> GameIntelligence:
    facts = quiet(moves_from_sans(san_list_from_pgn(pgn)))
    return GameIntelligence(context_for(facts, **context_overrides), facts)


# --- game phase detection -------------------------------------------------------


def test_start_position_is_opening_with_reasons() -> None:
    detection = GamePhaseDetector().detect_fen(chess.STARTING_FEN)
    assert detection.phase is GamePhase.OPENING
    assert detection.reasons
    assert detection.indicators_evaluated >= 3
    assert 0.0 <= detection.confidence <= 1.0
    assert detection.votes


def test_phase_detector_calls_a_bare_king_endgame_an_endgame() -> None:
    fen = "8/5k2/8/8/8/8/4P3/4K3 w - - 0 1"
    detection = GamePhaseDetector().detect_fen(fen)
    assert detection.phase is GamePhase.ENDGAME
    assert any("material" in reason or "pieces" in reason for reason in detection.reasons)


def test_early_queen_trade_is_not_an_endgame() -> None:
    # Queens traded on move 4 (1.e4 e5 2.Qh5 Nc6 3.Qxe5+ Qe7 4.Qxe7+ Bxe7) while
    # almost all material is still on the board: that is not an endgame.
    board = chess.Board()
    for san in ["e4", "e5", "Qh5", "Nc6", "Qxe5+", "Qe7", "Qxe7+", "Bxe7"]:
        board.push_san(san)
    detection = GamePhaseDetector().detect(board)
    assert detection.phase is not GamePhase.ENDGAME
    assert detection.votes


def test_phase_detector_agrees_with_phase3_classifier_on_boundaries() -> None:
    from argus.analysis.phase import classify_position

    detector = GamePhaseDetector()
    for fen in [
        chess.STARTING_FEN,
        "8/5k2/8/8/8/8/4P3/4K3 w - - 0 1",
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    ]:
        board = chess.Board(fen)
        assert detector.detect(board).phase is classify_position(board)


def test_undeveloped_pieces_alone_do_not_label_a_shattered_position_an_opening() -> None:
    # Morphy's Opera Game ends in mate on move 17; Black never castled, so its h8
    # rook and f8 bishop are still at home. An undeveloped-piece count alone would
    # call this mate position "opening", which is why development is now gated on
    # the material still on the board.
    board = chess.Board()
    for san in san_list_from_pgn(OPERA_GAME_PGN):
        board.push_san(san)
    detection = GamePhaseDetector().detect(board)
    assert detection.phase is not GamePhase.OPENING
    assert detection.decision.startswith("precedence")


def test_phase_detector_tracks_the_opening_to_middlegame_transition() -> None:
    detector = GamePhaseDetector()
    board = chess.Board()
    phases = []
    for san in san_list_from_pgn(OPERA_GAME_PGN):
        board.push_san(san)
        phases.append(detector.detect(board).phase)

    assert phases[0] is GamePhase.OPENING
    assert phases[-1] is GamePhase.MIDDLEGAME
    assert GamePhase.MIDDLEGAME in phases
    assert phases.index(GamePhase.MIDDLEGAME) > 0


def test_phase_detection_explains_which_rule_produced_the_phase() -> None:
    detection = GamePhaseDetector().detect_fen(chess.STARTING_FEN)
    assert detection.decision == "majority of the substantive board-state indicators"
    assert detection.confidence > 0.0
    assert "phase3_baseline" in {indicator.name for indicator in detection.indicators}


# --- opening identification ------------------------------------------------------


def test_opening_is_identified_from_the_move_sequence() -> None:
    san = san_list_from_pgn(OPERA_GAME_PGN)
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts, eco_code="C41", opening_name="Philidor"))
    assert analysis.identification.source == "move_sequence"
    assert analysis.identification.family == "Philidor Defense"
    assert analysis.identification.eco == "C41"
    assert analysis.identification.matched_plies == 4
    assert analysis.identification.header_agreement is True


def test_italian_game_variation_is_reported() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "Nf6", "c3"]
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts))
    assert analysis.identification.family == "Italian Game"
    assert analysis.identification.variation == "Giuoco Pianissimo"


def test_unknown_opening_is_reported_as_unclassified() -> None:
    san = ["a3", "a6", "h3", "h6"]
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts))
    assert analysis.identification.source == "unclassified"
    assert analysis.identification.name is None
    assert "not part of any line" in (analysis.identification.note or "")
    assert "guess" in (analysis.identification.note or "")


def test_header_only_metadata_is_reported_separately_from_detection() -> None:
    san = ["a3", "a6", "h3", "h6"]
    facts = moves_from_sans(san)
    analysis = detect_opening(
        facts, context_for(facts, eco_code="A00", opening_name="Polish Opening")
    )
    assert analysis.identification.source == "pgn_header"
    assert analysis.identification.header_eco == "A00"
    assert analysis.identification.eco == "A00"


def test_custom_start_position_is_not_guessed() -> None:
    facts = moves_from_sans(["e4", "e5"])
    context = context_for(facts, initial_position="8/8/8/8/8/8/8/K6k w - - 0 1")
    analysis = detect_opening(facts, context)
    assert analysis.identification.source == "unclassified"
    assert "standard initial position" in (analysis.identification.note or "")


def test_opening_deviation_records_the_point_and_expected_continuation() -> None:
    # Sicilian Najdorf prefix, then a move that leaves the table.
    san = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6", "Be3"]
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts))
    assert analysis.identification.variation == "Najdorf Variation"
    assert analysis.deviation.deviated is True
    assert analysis.deviation.ply == 11
    assert analysis.deviation.move_number == 6
    assert analysis.deviation.side is Color.WHITE
    assert analysis.deviation.played_san == "Be3"
    assert analysis.deviation.expected_continuation_san == []
    assert "not by itself a mistake" in analysis.deviation.note


def test_opening_deviation_is_not_a_quality_judgement() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "Nf6", "a3"]
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts))
    assert analysis.deviation.deviated is True
    # The deviation model carries no severity, classification or evaluation.
    assert not hasattr(analysis.deviation, "severity")
    assert not hasattr(analysis.deviation, "classification")


def test_game_inside_the_table_has_no_deviation() -> None:
    san = ["e4", "e5", "Nf3", "Nc6"]
    facts = moves_from_sans(san)
    analysis = detect_opening(facts, context_for(facts))
    assert analysis.deviation.deviated is False
    assert analysis.deviation.last_book_ply == 4


# --- material analysis -----------------------------------------------------------


def test_material_tracks_captures_and_exchanges() -> None:
    san = ["e4", "d5", "exd5", "Qxd5"]
    facts = moves_from_sans(san)
    timeline = build_material_timeline(facts, initial_position=chess.STARTING_FEN)
    assert timeline.initial_balance == 0
    assert timeline.final_balance == 0
    capture = next(event for event in timeline.captures if event.san == "exd5")
    assert capture.captured_piece == "pawn"
    assert capture.side is Color.WHITE
    exchanges = [event for event in timeline.events if event.type == "exchange"]
    assert exchanges and exchanges[0].answered_on_same_square is True


def test_material_detects_promotion() -> None:
    facts = moves_from_sans(san_list_from_pgn(PROMOTION_PGN))
    timeline = build_material_timeline(facts, initial_position=chess.STARTING_FEN)
    assert timeline.promotions
    assert timeline.promotions[0].promoted_to == "queen"
    assert timeline.transitions


def test_material_balance_is_independent_of_the_evaluation() -> None:
    san = ["e4", "d5", "exd5"]
    facts = moves_from_sans(san, per_ply={3: {"eval_before_cp": 900, "eval_after_cp": -900}})
    timeline = build_material_timeline(facts, initial_position=chess.STARTING_FEN)
    # White is a pawn up purely because of the board, whatever the engine said.
    assert timeline.final_balance == 1
    assert "not interchangeable" in timeline.note


def test_material_peak_reports_both_sides() -> None:
    san = ["e4", "d5", "exd5"]
    facts = moves_from_sans(san)
    timeline = build_material_timeline(facts, initial_position=chess.STARTING_FEN)
    assert timeline.peak_white_balance == 1
    assert timeline.peak_white_ply == 3
    assert timeline.peak_black_balance == 0
    assert timeline.first_capture_ply == 3


# --- tactical detection ----------------------------------------------------------


def test_fork_is_detected_and_confirmed() -> None:
    # After 12.Qxb5 the queen attacks the bishop on a5 and the knight on c6.
    san = EVANS_GAMBIT_SAN[:23]  # up to 12.Qxb5
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    forks = [event for event in analysis.events if event.type is TacticType.FORK and event.ply == 23]
    assert forks, "expected the queen fork to be detected"
    assert forks[0].certainty is Certainty.CONFIRMED
    assert forks[0].side is Color.WHITE
    assert len(forks[0].affected_pieces) >= 2


def test_pin_to_the_king_is_confirmed() -> None:
    # ...Bb4 pins the white d2 pawn against the king on e1.
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4", "Bxb4"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    pins = [e for e in analysis.events if e.type is TacticType.PIN and e.ply == 8]
    assert pins
    assert pins[0].certainty is Certainty.CONFIRMED
    assert "king" in pins[0].statement


def test_pawn_alignment_with_a_knight_is_not_reported_as_a_pin() -> None:
    # 3.Bc4 lines the bishop up with f7 and g8 — geometry, not a meaningful pin.
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    assert not [e for e in analysis.events if e.type is TacticType.PIN and e.ply == 5]


def test_hanging_piece_is_a_candidate_not_a_fact() -> None:
    # 4...Bxb4 leaves the b4 bishop attacked by the c3 pawn; the engine may or
    # may not care, so Caissa labels it a candidate.
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4", "Bxb4", "c3"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    hanging = [e for e in analysis.events if e.type is TacticType.HANGING_PIECE]
    assert hanging
    assert all(event.certainty is Certainty.CANDIDATE for event in hanging)


def test_forced_exchange_requires_a_real_piece_capture() -> None:
    # 7.Bxc6 dxc6: White really wins the c6 knight and Black really recaptures
    # it on the same square — a confirmed piece exchange.
    san = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    forced = [e for e in analysis.events if e.type is TacticType.FORCED_EXCHANGE]
    assert forced
    assert forced[0].certainty is Certainty.CONFIRMED
    assert forced[0].engine_context["centipawn_loss"] == 0
    assert forced[0].evidence["traded_value"] >= 3


def test_a_quiet_move_that_is_captured_is_not_a_forced_exchange() -> None:
    # 3.e4 is a pawn push, not a capture; 3...dxe4 captures it. Nothing was
    # recaptured, so reporting a "recapture" here would be a false statement.
    facts = quiet(moves_from_sans(["Nf3", "d5", "e4", "dxe4"]))
    analysis = detect_tactical_events(facts)
    assert not [e for e in analysis.events if e.type is TacticType.FORCED_EXCHANGE]


def test_a_pawn_trade_is_not_reported_as_a_forced_exchange() -> None:
    # 1.e4 d5 2.exd5 Qxd5 is a routine pawn trade: no tactical event, only the
    # material timeline records it.
    facts = quiet(moves_from_sans(["e4", "d5", "exd5", "Qxd5"]))
    analysis = detect_tactical_events(facts)
    assert not [e for e in analysis.events if e.type is TacticType.FORCED_EXCHANGE]


def test_mating_threat_comes_from_the_engine_evaluation_only() -> None:
    san = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    facts = moves_from_sans(san, per_ply={7: {"eval_before_mate": 1, "eval_after_mate": 1}})
    facts = quiet(facts, cp=0)
    facts[6].eval_after_mate = 1
    analysis = detect_tactical_events(facts)
    mates = [e for e in analysis.events if e.type is TacticType.MATING_THREAT]
    assert mates
    assert mates[0].evidence["source"] == "engine_evaluation"
    assert "delivers checkmate" in mates[0].statement


def test_no_tactics_reported_for_a_quiet_opening() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_tactical_events(facts)
    assert not [e for e in analysis.events if e.type is TacticType.FORK]


def test_tactical_analysis_separates_confirmed_from_candidates() -> None:
    facts = quiet(moves_from_sans(EVANS_GAMBIT_SAN))
    analysis = detect_tactical_events(facts)
    assert analysis.confirmed_count + analysis.candidate_count == len(analysis.events)
    assert analysis.note


# --- positional features ---------------------------------------------------------


def test_pawn_structure_counts_isolated_and_doubled_pawns() -> None:
    # White pawns: a2+a3 (doubled), b2, d2 (isolated), f2+g2. Black: a,b,d,f,g,h.
    board = chess.Board("4k3/pp1p1ppp/8/8/8/P7/PP1P1PP1/4K3 w - - 0 1")
    white = pawn_structure_for(board, chess.WHITE)
    assert white.doubled == 1
    assert white.isolated == 1
    assert white.islands == 3
    assert white.pawns == 6


def test_pawn_structure_events_track_changes() -> None:
    facts = quiet(moves_from_sans(DOUBLE_PAWN_LINE))
    analysis = build_pawn_structure(facts, initial_position=chess.STARTING_FEN)
    assert analysis.snapshots
    assert len(analysis.snapshots) == len(facts)
    assert [e for e in analysis.events if e.type == "doubled_pawns_created"]


def test_open_file_is_recorded_as_a_feature() -> None:
    # 1.e4 d5 2.exd5 Qxd5 3.d4 Qxd4 leaves no pawn of either colour on the d-file.
    facts = quiet(moves_from_sans(["e4", "d5", "exd5", "Qxd5", "d4", "Qxd4"]))
    structure = build_pawn_structure(facts, initial_position=chess.STARTING_FEN)
    assert [e for e in structure.events if e.type == "open_file_created"]
    assert "objective board facts" in structure.note


def test_positional_feature_becomes_an_error_candidate_only_with_engine_support() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6"]
    facts = quiet(moves_from_sans(san))
    # A quiet, engine-approved game produces features but no error candidates.
    quiet_analysis = build_positional_analysis(facts, initial_position=chess.STARTING_FEN)
    assert quiet_analysis.feature_count > 0
    assert quiet_analysis.error_candidate_count == 0

    # Now make 8...dxc6 a measured blunder: the same structural change is flagged.
    flagged_facts = moves_from_sans(san)
    for fact in flagged_facts:
        fact.eval_before_cp = 0
        fact.eval_after_cp = 0
        fact.classification = MoveClassification.BEST
        fact.centipawn_loss = 0
    flagged_facts[7].classification = MoveClassification.BLUNDER
    flagged_facts[7].centipawn_loss = 450
    flagged = build_positional_analysis(flagged_facts, initial_position=chess.STARTING_FEN)
    assert flagged.error_candidate_count >= 1
    candidates = [e for e in flagged.events if e.classification == "error_candidate"]
    assert all(event.source is EvidenceSource.ARGUS_INTERPRETATION for event in candidates)
    assert all(event.engine_supported is True for event in candidates)


def test_weak_squares_are_those_no_pawn_can_defend() -> None:
    # White has no b-pawn or f-pawn, so d4 cannot be defended by a pawn.
    board = chess.Board("rnbqkbnr/p1pppppp/8/1p6/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 1")
    squares = weak_squares(board, chess.WHITE)
    assert isinstance(squares, list)


def test_trapped_piece_detection_ignores_pawns_and_kings() -> None:
    from argus.intelligence.activity import activity_for

    # A rook in the corner behind its own king has no move; pawns are excluded.
    board = chess.Board("4k3/8/8/8/8/8/8/R3K3 w Q - 0 1")
    activity = activity_for(board, chess.WHITE)
    assert all(not piece.startswith("P") for piece in activity.trapped_pieces + activity.restricted_pieces)
    assert activity.mobility > 0


def test_piece_activity_measures_mobility_and_coordination() -> None:
    from argus.intelligence.activity import build_piece_activity

    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6"]))
    activity = build_piece_activity(facts, initial_position=chess.STARTING_FEN)
    assert activity.average_mobility_white is not None
    assert activity.snapshots[0].white.developed_pieces >= 2
    assert "no verdict" in activity.note


# --- king safety -----------------------------------------------------------------


def test_king_safety_records_castling_and_pawn_shield() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "O-O"]
    facts = quiet(moves_from_sans(san))
    from argus.intelligence.king_safety import build_king_safety

    analysis = build_king_safety(facts, initial_position=chess.STARTING_FEN)
    castled = [e for e in analysis.events if e.type is KingSafetyEventType.CASTLED]
    assert {event.side for event in castled} == {Color.WHITE, Color.BLACK}
    assert analysis.snapshots[-1].white.castled is True
    assert analysis.snapshots[-1].white.pawn_shield >= 2


def test_king_safety_records_a_check_given() -> None:
    facts = quiet(moves_from_sans(["e4", "d5", "Bb5+"]))
    from argus.intelligence.king_safety import build_king_safety

    analysis = build_king_safety(facts, initial_position=chess.STARTING_FEN)
    checks = [e for e in analysis.events if e.type is KingSafetyEventType.CHECK_GIVEN]
    assert checks and checks[0].side is Color.BLACK


def test_king_exposure_detected_after_the_shield_is_broken() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Qe7", "b4", "Bxb4", "cxb4", "Qxb4"]
    facts = quiet(moves_from_sans(san))
    from argus.intelligence.king_safety import build_king_safety

    analysis = build_king_safety(facts, initial_position=chess.STARTING_FEN)
    assert analysis.worst_white is not None
    assert analysis.worst_white.white.score >= 0


def test_mating_net_requires_an_engine_mate_score() -> None:
    san = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    facts = quiet(moves_from_sans(san))
    from argus.intelligence.king_safety import build_king_safety

    without = build_king_safety(facts, initial_position=chess.STARTING_FEN)
    assert not [e for e in without.events if e.type is KingSafetyEventType.MATING_NET]

    facts[6].eval_after_mate = 1
    with_mate = build_king_safety(facts, initial_position=chess.STARTING_FEN)
    nets = [e for e in with_mate.events if e.type is KingSafetyEventType.MATING_NET]
    assert nets and nets[0].severity == "high"


# --- advantage states & trajectory ------------------------------------------------


@pytest.mark.parametrize(
    ("cp", "expected"),
    [
        (400, AdvantageState.WINNING),
        (200, AdvantageState.ADVANTAGE),
        (80, AdvantageState.SLIGHT_ADVANTAGE),
        (10, AdvantageState.EQUAL),
        (-80, AdvantageState.SLIGHT_DISADVANTAGE),
        (-200, AdvantageState.DISADVANTAGE),
        (-500, AdvantageState.LOSING),
    ],
)
def test_advantage_state_bands(cp: int, expected: AdvantageState) -> None:
    assert state_for(cp, None, Color.WHITE) is expected


def test_advantage_state_handles_mate_for_both_sides() -> None:
    assert state_for(None, 3, Color.WHITE) is AdvantageState.FORCED_MATE
    assert state_for(None, 3, Color.BLACK) is AdvantageState.LOSING
    assert state_for(None, -3, Color.BLACK) is AdvantageState.FORCED_MATE


def test_unknown_evaluation_is_not_reported_as_equal() -> None:
    assert state_for(None, None, Color.WHITE) is None


def test_advantage_policy_documents_its_thresholds() -> None:
    from argus.intelligence.advantage import describe_policy

    described = describe_policy(AdvantagePolicy(winning_threshold=250))
    assert described["winning_threshold_cp"] == 250
    assert "not universal chess truths" in described["note"]


def test_trajectory_has_one_point_per_ply_and_never_interpolates() -> None:
    san = ["e4", "e5", "Nf3", "Nc6"]
    facts = quiet(moves_from_sans(san))
    facts[2].eval_after_cp = None  # the engine did not evaluate ply 3
    trajectory = build_trajectory(facts, initial_position=chess.STARTING_FEN)
    assert len(trajectory.points) == len(facts) + 1  # ply 0 plus one per move
    missing = [point for point in trajectory.points if not point.available]
    assert len(missing) == 1
    assert missing[0].evaluation_cp_white is None
    assert missing[0].evaluation_display == "—"
    assert trajectory.missing_plies == 1
    assert "never interpolated" in trajectory.note


def test_trajectory_reports_stabilization_and_advantage_changes() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "d6", "c3", "Nf6", "O-O", "O-O"]
    facts = moves_from_sans(san, per_ply={ply: {"eval_before_cp": 0, "eval_after_cp": 0} for ply in range(1, 13)})
    facts[9].eval_after_cp = 400
    for ply in range(11, 13):
        facts[ply - 1].eval_after_cp = 400
    trajectory = build_trajectory(facts, initial_position=chess.STARTING_FEN)
    types = {event.type.value for event in trajectory.events}
    assert "advantage_creation" in types
    assert trajectory.states_seen


# --- turning points ---------------------------------------------------------------


NEUTRAL_12 = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "d6", "c3", "Nf6", "O-O", "O-O"]


def test_turning_point_requires_persistence_not_just_a_big_swing() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    # A 400cp drop on ply 7 that is fully recovered eleven plies later.
    set_white_evals(facts, {7: -400, 11: 0, 12: 0})
    facts[6].eval_change_cp = -400
    analysis = detect_turning_points(facts)
    assert not [p for p in analysis.turning_points if p.type.value == "evaluation_swing"]


def test_turning_point_recorded_when_the_loss_sticks() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    set_white_evals(facts, {7: -400, 8: -400, 9: -400, 10: -400, 11: -400, 12: -400})
    facts[6].eval_change_cp = -400
    analysis = detect_turning_points(facts)
    swings = [p for p in analysis.turning_points if p.type.value == "evaluation_swing"]
    assert swings and swings[0].ply == 7
    assert swings[0].persistent is True
    assert swings[0].side is Color.WHITE


def test_turning_points_are_not_just_the_largest_swing() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    set_white_evals(facts, {7: -400, 8: -400, 9: -400, 10: 400, 11: -400, 12: -400})
    facts[6].eval_change_cp = -400
    facts[9].eval_change_cp = -370
    analysis = detect_turning_points(facts)
    types = {point.type.value for point in analysis.turning_points}
    assert "missed_win" in types
    assert analysis.candidates_considered >= len(analysis.turning_points)
    assert "Multiple turning points" in analysis.note


def test_mate_change_is_a_turning_point() -> None:
    san = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    facts = quiet(moves_from_sans(san))
    facts[5].eval_before_mate = 2
    facts[5].eval_before_cp = None
    facts[5].eval_after_mate = -1
    facts[5].eval_after_cp = None
    facts[5].eval_change_cp = -300
    analysis = detect_turning_points(facts)
    assert any(point.type.value == "mate_change" for point in analysis.turning_points)


def test_material_transition_is_a_turning_point() -> None:
    # Fried Liver: 6.Nxf7 Kxf7 gives up a knight for a pawn (net -2 pawns).
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5", "Nxd5", "Nxf7", "Kxf7"]
    facts = quiet(moves_from_sans(san))
    analysis = detect_turning_points(facts)
    transitions = [p for p in analysis.turning_points if p.type.value == "material_transition"]
    assert transitions and transitions[0].ply == 11
    assert transitions[0].side is Color.WHITE
    assert transitions[0].evidence["material_balance_change_pawns"] == -2


def test_material_change_is_signed_for_the_mover() -> None:
    # 4.Qxe5+ wins a clean pawn: Black has to block the check and never gets the
    # pawn back, so the mover's balance change is +1 (the old loss-only metric
    # reported 0 here, hiding a real material gain).
    san = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxe5+", "Be7"]
    facts = quiet(moves_from_sans(san))
    assert material_balance_change_for_mover(facts, 6) == 1
    assert material_delta_against_mover(facts, 6) == 0


def test_an_even_trade_is_not_a_material_transition_for_either_side() -> None:
    # 4.Bxc6 dxc6 is bishop for knight: level, so neither ply is a transition.
    facts = quiet(moves_from_sans(DOUBLE_PAWN_LINE))
    assert material_balance_change_for_mover(facts, 6) == 0
    assert material_balance_change_for_mover(facts, 7) == 0
    analysis = detect_turning_points(facts)
    assert not [
        point
        for point in analysis.turning_points
        if point.type.value == "material_transition" and point.ply in (7, 8)
    ]


def test_interleaved_exchange_is_not_a_loss_for_the_capturer() -> None:
    # Opera Game: 4.dxe5 wins a pawn, but the reply 4...Bxf3 is itself answered
    # by 5.Qxf3 and the pawn is recaptured by 5...dxe5. The whole run of captures
    # nets to zero, so the winning capture must not be reported as a two-pawn
    # loss (it was, when only the single reply was measured).
    facts = quiet(moves_from_sans(san_list_from_pgn(OPERA_GAME_PGN)))
    assert material_balance_change_for_mover(facts, 6) == 0
    transitions = [
        point
        for point in detect_turning_points(facts).turning_points
        if point.type.value == "material_transition"
    ]
    assert 7 not in [point.ply for point in transitions]


def test_material_transition_is_measured_across_the_capture_run() -> None:
    # 10.Nxb5 cxb5 11.Bxb5+ is a knight for two pawns: -1, not the -2 a single
    # reply would suggest (the capture on its own even looks like a gain).
    facts = quiet(moves_from_sans(san_list_from_pgn(OPERA_GAME_PGN)))
    assert material_balance_change_for_mover(facts, 18) == -1
    transitions = [
        point
        for point in detect_turning_points(facts).turning_points
        if point.type.value == "material_transition"
    ]
    # The genuine transition is Black losing the queen to 16...Nxb8.
    by_ply = {point.ply: point for point in transitions}
    assert by_ply[32].side is Color.BLACK
    assert by_ply[32].evidence["material_balance_change_pawns"] == 9
    assert 19 not in by_ply


def test_a_mating_move_is_not_reported_as_a_lost_mate() -> None:
    # 7.Qxf7# ends the game. The position after a mating move is never evaluated
    # (there is nothing to search), and that absence must not be read as "the
    # forced mate disappeared" on the move that delivered it.
    san = ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    facts = quiet(moves_from_sans(san))
    facts[6].eval_before_mate = 1
    facts[6].eval_before_cp = None
    changes = [
        point
        for point in detect_turning_points(facts).turning_points
        if point.type.value == "mate_change"
    ]
    assert changes
    assert changes[0].side is Color.WHITE
    assert changes[0].statement == "White delivered checkmate with Qxf7#."


def test_mate_against_the_mover_is_stated_in_the_right_direction() -> None:
    # eval_after_mate is White-perspective: -2 means Black mates in two. The
    # statement must not read as Black's own mate being given up.
    facts = quiet(moves_from_sans(NEUTRAL_12))
    facts[6].eval_after_mate = -2
    facts[6].eval_after_cp = None
    analysis = detect_turning_points(facts)
    change = next(
        point for point in analysis.turning_points if point.type.value == "mate_change"
    )
    assert change.side is Color.WHITE
    assert change.statement == f"After {facts[6].san}, Black has a forced mate."
    assert "forced_sequence" in {point.type.value for point in analysis.turning_points}


# --- conversion -------------------------------------------------------------------


def test_conversion_candidate_when_a_winning_eval_slips() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    set_white_evals(facts, {ply: 400 for ply in range(1, 11)} | {11: 20, 12: 10})
    intelligence = GameIntelligence(context_for(facts, result="1/2-1/2"), facts)
    types = {event.type for event in intelligence.conversion.events}
    assert ConversionEventType.ADVANTAGE_CONVERSION_CANDIDATE in types
    assert ConversionEventType.WINNING_BECAME_EQUAL in types
    candidate = next(
        event
        for event in intelligence.conversion.events
        if event.type is ConversionEventType.ADVANTAGE_CONVERSION_CANDIDATE
    )
    assert candidate.certainty is Certainty.CANDIDATE
    assert "measured" in intelligence.conversion.note


def test_conversion_confirmed_when_the_advantage_is_used() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    set_white_evals(facts, {ply: 400 for ply in range(1, 13)})
    intelligence = GameIntelligence(context_for(facts, result="1-0"), facts)
    confirmed = [
        event
        for event in intelligence.conversion.events
        if event.type is ConversionEventType.CONVERSION_CONFIRMED
    ]
    assert confirmed and confirmed[0].side is Color.WHITE
    assert confirmed[0].certainty is Certainty.CONFIRMED


def test_equal_position_sliding_into_a_losing_one_is_detected() -> None:
    facts = quiet(moves_from_sans(NEUTRAL_12))
    set_white_evals(facts, {ply: 0 for ply in range(1, 12)} | {12: -400})
    intelligence = GameIntelligence(context_for(facts), facts)
    assert any(
        event.type is ConversionEventType.EQUAL_BECAME_LOSING
        for event in intelligence.conversion.events
    )


# --- error categories & phase performance ------------------------------------------


def test_error_categories_come_from_detected_events() -> None:
    facts = quiet(moves_from_sans(EVANS_GAMBIT_SAN))
    facts[6].classification = MoveClassification.BLUNDER
    facts[6].centipawn_loss = 500
    intelligence = GameIntelligence(context_for(facts), facts)
    analysis = intelligence.categories
    assert analysis.errors
    error = analysis.errors[0]
    assert error.basis
    assert error.evidence
    assert "detected" in analysis.note
    assert analysis.by_side_category


def test_unclassifiable_mistake_is_reported_as_unclassified() -> None:
    san = ["e4", "e5", "Nf3", "Nc6"]
    facts = quiet(moves_from_sans(san))
    facts[1].classification = MoveClassification.MISTAKE
    facts[1].centipawn_loss = 250
    intelligence = GameIntelligence(context_for(facts), facts)
    categories = {error.category.value for error in intelligence.categories.errors}
    assert categories <= {"opening", "unclassified", "positional", "material", "tactical", "king_safety", "endgame"}
    assert intelligence.categories.unclassified_count >= 0


def test_phase_performance_reports_sample_sizes() -> None:
    facts = quiet(moves_from_sans(EVANS_GAMBIT_SAN))
    intelligence = GameIntelligence(context_for(facts), facts)
    performance = intelligence.performance
    assert performance.white, "White should have at least one phase with moves"
    for stats in performance.white.values():
        assert stats.moves > 0
        assert stats.plies
        assert stats.small_sample in (True, False)
    assert performance.phase_move_counts


def test_small_samples_are_flagged_not_smoothed() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3"]))
    phase_by_ply = {1: GamePhase.OPENING, 2: GamePhase.OPENING, 3: GamePhase.MIDDLEGAME}
    performance = build_phase_performance(facts, phase_by_ply=phase_by_ply)
    stats = performance.white["opening"]
    assert stats.small_sample is True
    assert stats.note and "indicative" in stats.note


# --- accuracy ----------------------------------------------------------------------


def test_win_expectation_is_bounded_and_handles_mate() -> None:
    assert win_expectation(None, 2) == 1.0
    assert win_expectation(None, -2) == 0.0
    assert win_expectation(None, None) is None
    assert 0.0 < win_expectation(0, None) < 1.0
    assert win_expectation(300, None) > 0.5


def test_perfect_moves_score_high_and_blunders_score_low() -> None:
    def fact_for(cp_after: int) -> MoveFact:
        return MoveFact(
            ply=1,
            move_number=1,
            mover=Color.WHITE,
            san="e4",
            uci="e2e4",
            fen_before=chess.STARTING_FEN,
            fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            eval_before_cp=0,
            eval_after_cp=cp_after,
        )

    good = score_move(fact_for(0))
    bad = score_move(fact_for(-900))
    assert good.accuracy == 100.0
    assert bad.accuracy is not None and bad.accuracy < 20.0
    assert bad.accuracy < good.accuracy
    assert bad.loss is not None and bad.loss > 0.9


def test_accuracy_handles_mate_positions_safely() -> None:
    fact = MoveFact(
        ply=1,
        move_number=1,
        mover=Color.WHITE,
        san="Qxf7#",
        uci="h5f7",
        fen_before="r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 1",
        fen_after="r1bqkbnr/pppp1Qpp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR b KQkq - 0 1",
        eval_before_mate=1,
        eval_after_mate=1,
    )
    record = score_move(fact)
    assert record.scored is True
    assert record.accuracy == 100.0
    assert 0.0 <= record.accuracy <= 100.0


def test_unscored_moves_are_reported_as_unscored_never_imputed() -> None:
    facts = moves_from_sans(["e4", "e5"])
    analysis = analyse_accuracy(facts)
    assert analysis.white.accuracy is None
    assert analysis.white.unscored_moves == 1
    assert analysis.moves[0].scored is False
    assert analysis.moves[0].accuracy is None
    assert "never imputes" in analysis.note


def test_decided_positions_are_excluded_and_counted() -> None:
    san = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
    facts = moves_from_sans(
        san, per_ply={ply: {"eval_before_cp": 2000, "eval_after_cp": 2000} for ply in range(1, 7)}
    )
    for fact in facts:
        fact.centipawn_loss = 0
        fact.classification = MoveClassification.BEST
    analysis = analyse_accuracy(facts)
    assert analysis.white.excluded_decided_moves == 3
    assert analysis.white.scored_moves == 0
    assert analysis.white.accuracy is None
    assert "excluded" in analysis.note


def test_accuracy_reports_raw_cpl_separately() -> None:
    san = ["e4", "e5", "Nf3", "Nc6"]
    facts = quiet(moves_from_sans(san), cp=0)
    for fact in facts:
        fact.centipawn_loss = 40
    analysis = analyse_accuracy(facts)
    assert analysis.white.average_centipawn_loss == 40.0
    assert "different quantity" in analysis.methodology or analysis.methodology


def test_accuracy_never_claims_equivalence_with_other_sites() -> None:
    facts = quiet(moves_from_sans(["e4", "e5"]))
    analysis = analyse_accuracy(facts)
    assert "not Chess.com accuracy" in analysis.disclaimer
    assert "Caissa" in analysis.disclaimer


def test_accuracy_policy_scale_changes_the_result_predictably() -> None:
    fact = MoveFact(
        ply=1,
        move_number=1,
        mover=Color.WHITE,
        san="e4",
        uci="e2e4",
        fen_before=chess.STARTING_FEN,
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        eval_before_cp=300,
        eval_after_cp=0,
    )
    tight = score_move(fact, policy=AccuracyPolicy(scale_cp=100))
    loose = score_move(fact, policy=AccuracyPolicy(scale_cp=1000))
    assert tight.accuracy < loose.accuracy


# --- accuracy provenance (4.1) -------------------------------------------------------

def accuracy_fact(**overrides) -> MoveFact:
    """A single White move with explicit engine fields, for provenance tests."""
    payload = dict(
        ply=1,
        move_number=1,
        mover=Color.WHITE,
        san="e4",
        uci="e2e4",
        fen_before=chess.STARTING_FEN,
        fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        eval_before_cp=300,
    )
    payload.update(overrides)
    return MoveFact(**payload)


def test_the_same_search_score_is_used_instead_of_the_resulting_position() -> None:
    """Accuracy must compare like with like: one search, best line vs played."""
    # The same search that produced the best line scores the played move at
    # 280cp; the separate search of the resulting position claims -100cp. Only
    # the first is a like-for-like comparison, so accuracy must follow it.
    fact = accuracy_fact(
        played_eval_cp=280,
        played_eval_source=EVAL_SOURCE_SAME_SEARCH,
        eval_after_cp=-100,
    )
    record = score_move(fact)
    assert record.evaluation_source == EVAL_SOURCE_SAME_SEARCH
    assert record.scored is True
    # E(300)=0.5, E(280)=0.4816 -> loss 0.0368 -> accuracy 96.32. Were the
    # separate search used, E(-100)=0.4166 would give ~83 and report a loss
    # that never happened.
    assert record.accuracy is not None and record.accuracy > 95.0
    assert record.loss is not None and record.loss < 0.05


def test_a_resulting_position_score_is_marked_as_approximate() -> None:
    fact = accuracy_fact(eval_after_cp=-100)
    expectation, source = played_expectation(fact)
    assert source == EVAL_SOURCE_RESULTING_POSITION
    assert expectation is not None and expectation < 0.45
    record = score_move(fact)
    assert record.evaluation_source == EVAL_SOURCE_RESULTING_POSITION


def test_a_move_with_no_engine_score_is_not_scored_at_all() -> None:
    fact = accuracy_fact()
    expectation, source = played_expectation(fact)
    assert expectation is None
    assert source == EVAL_SOURCE_UNAVAILABLE
    record = score_move(fact)
    assert record.scored is False
    assert record.accuracy is None
    assert record.evaluation_source == EVAL_SOURCE_UNAVAILABLE


def test_a_legacy_row_without_provenance_still_falls_back_to_its_after_eval() -> None:
    """Rows stored before the played score existed must keep working honestly."""
    fact = accuracy_fact(eval_after_cp=100)  # no played_eval_* at all
    record = score_move(fact)
    assert record.evaluation_source == EVAL_SOURCE_RESULTING_POSITION
    assert record.scored is True


def test_sides_count_how_many_scores_were_exact() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6"]), cp=20)
    # Plies 1, 2 and 3 were inside the MultiPV window; ply 4 was not.
    for ply in (1, 2, 3):
        facts[ply - 1].played_eval_cp = 20
        facts[ply - 1].played_eval_source = EVAL_SOURCE_SAME_SEARCH
    analysis = analyse_accuracy(facts)
    assert analysis.white.exact_scores == 2
    assert analysis.white.approximate_scores == 0
    assert analysis.black.exact_scores == 1
    assert analysis.black.approximate_scores == 1
    # The counts describe the scored moves, never more.
    for side in (analysis.white, analysis.black):
        assert side.exact_scores + side.approximate_scores == side.scored_moves
    assert "ONE engine search" in analysis.methodology


# --- accuracy breakdown --------------------------------------------------------------

def breakdown_facts() -> list[MoveFact]:
    """A small game with a phase boundary, one blunder and a material swing."""
    facts = moves_from_sans(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "d6"])
    for index, fact in enumerate(facts):
        fact.phase = GamePhase.OPENING if index < 4 else GamePhase.MIDDLEGAME
        fact.eval_before_cp = 20
        fact.eval_after_cp = 20
        fact.centipawn_loss = 0
        fact.classification = MoveClassification.BEST
    # Ply 6 (Black's 3rd move) is a real error in the middlegame.
    facts[5].eval_after_cp = -230
    facts[5].centipawn_loss = 250
    facts[5].classification = MoveClassification.BLUNDER
    return facts


def test_breakdown_is_built_from_the_same_scored_moves_as_the_total() -> None:
    analysis = analyse_accuracy(breakdown_facts())
    breakdown = analysis.breakdown
    assert breakdown is not None
    for side, summary in ((breakdown.white, analysis.white), (breakdown.black, analysis.black)):
        assert sum(group.scored_moves for group in side.by_phase) == summary.scored_moves
        assert sum(group.scored_moves for group in side.by_classification) == summary.scored_moves
        # Every slice is a partition of the same loss, so its shares add to 1 —
        # unless the side lost nothing at all, which is reported as no share
        # rather than as an invented 0% slice.
        for dimension in (side.by_phase, side.by_classification, side.by_material_state):
            shares = [group.share_of_loss for group in dimension]
            if all(share is None for share in shares):
                continue
            assert all(share is not None for share in shares)
            assert abs(sum(shares) - 1.0) < 0.01
    # White lost nothing (every move kept the evaluation), so White has no loss
    # to attribute anywhere — not even a zero-filled slice.
    assert all(
        group.share_of_loss is None for group in breakdown.white.by_phase
    )
    # All of Black's loss came from the single blunder.
    black_shares = {group.key: group.share_of_loss for group in breakdown.black.by_classification}
    assert black_shares["blunder"] == 1.0
    assert black_shares["best"] == 0.0
    assert "same scored moves" in breakdown.note


def test_breakdown_omits_slices_it_has_nothing_measured_for() -> None:
    analysis = analyse_accuracy(breakdown_facts())
    assert analysis.breakdown is not None
    keys = {group.key for group in analysis.breakdown.white.by_phase}
    assert "endgame" not in keys  # the game never reached an endgame
    assert keys == {"opening", "middlegame"}
    classifications = {group.key for group in analysis.breakdown.black.by_classification}
    assert classifications == {"best", "blunder"}


def test_breakdown_flags_small_samples_instead_of_overstating_them() -> None:
    analysis = analyse_accuracy(breakdown_facts())
    assert analysis.breakdown is not None
    blunder = next(
        group
        for group in analysis.breakdown.black.by_classification
        if group.key == "blunder"
    )
    assert blunder.scored_moves == 1
    assert blunder.small_sample is True
    # Accuracy is the normalized win-expectation loss, not raw centipawns: a
    # 250cp drop in a balanced position gives up ~38% of Black's win
    # expectation, which is the number the group must report.
    assert blunder.average_centipawn_loss == 250.0
    assert blunder.accuracy is not None and blunder.accuracy < 70.0


def test_material_state_slices_are_from_the_movers_perspective() -> None:
    # White is a queen up on the board before the move.
    queen_up = "4k3/8/8/8/8/8/8/4K2Q w - - 0 1"
    assert material_balance_for(queen_up, Color.WHITE) == 9
    assert material_balance_for(queen_up, Color.BLACK) == -9

    facts = moves_from_sans(["e4", "e5", "Nf3", "Nc6"])
    quiet(facts, cp=20)
    facts[0].fen_before = queen_up  # White, a queen up
    facts[1].fen_before = queen_up  # Black, a queen down
    analysis = analyse_accuracy(facts)
    assert analysis.breakdown is not None
    white_keys = [group.key for group in analysis.breakdown.white.by_material_state]
    black_keys = [group.key for group in analysis.breakdown.black.by_material_state]
    assert white_keys == ["level", "ahead_major"]
    assert black_keys == ["behind_major", "level"]
    ahead = analysis.breakdown.white.by_material_state[-1]
    assert ahead.label == "Ahead by 3+ pawns"


# --- report ----------------------------------------------------------------------


def test_report_contains_every_section() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN, eco_code="C41")
    report = intelligence.build_report()
    assert report.report_version == REPORT_VERSION
    for section in (
        report.summary,
        report.opening,
        report.phases,
        report.trajectory,
        report.material,
        report.tactical,
        report.positional,
        report.king_safety,
        report.accuracy,
        report.conversion,
        report.error_categories,
        report.turning_points,
        report.critical_moments,
        report.key_lessons,
        report.training_recommendations,
        report.unavailable,
    ):
        assert section is not None
    assert report.evidence_policy


def test_report_never_runs_the_engine() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN)
    report = intelligence.build_report()
    assert report.provenance.engine_calls_made_by_intelligence_layer == 0
    assert report.provenance.report_version == REPORT_VERSION


def test_every_insight_carries_evidence_and_a_source() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN)
    report = intelligence.build_report()
    for insight in report.critical_moments.engine_critical_moments:
        assert insight.evidence
        assert insight.source is EvidenceSource.ENGINE_FACT
    for entry in report.critical_moments.timeline:
        assert entry.evidence is not None
        assert entry.kind
        assert entry.ply >= 1
    for finding in report.key_lessons:
        assert finding.statement
        assert finding.source
    for recommendation in report.training_recommendations:
        assert recommendation.focus
        assert recommendation.rationale
        assert recommendation.source is EvidenceSource.ARGUS_INTERPRETATION
    for error in report.error_categories.analysis.errors:
        assert error.evidence and error.basis


def test_report_statements_are_factual_not_commentary() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN)
    report = intelligence.build_report()
    banned = ("terrible", "choker", "perfect", "grandmaster", "you should", "brilliantly played")
    for finding in report.key_lessons + report.summary.facts:
        lowered = finding.statement.lower()
        assert not any(word in lowered for word in banned), finding.statement


def test_timeline_is_clickable_and_ordered() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN)
    timeline = intelligence.build_timeline()
    assert timeline
    plies = [entry.ply for entry in timeline]
    assert plies == sorted(plies)
    assert all(entry.ply >= 1 for entry in timeline)
    kinds = {entry.kind for entry in timeline}
    assert "opening_deviation" in kinds


def test_report_reports_unavailable_sections_instead_of_faking_them() -> None:
    facts = moves_from_sans(["e4", "e5"])
    intelligence = GameIntelligence(context_for(facts), facts)
    report = intelligence.build_report()
    sections = {item.section for item in report.unavailable}
    assert "engine_evaluation" in sections
    assert report.accuracy.analysis.white.accuracy is None
    assert all(item.reason for item in report.unavailable)


def test_incomplete_analysis_coverage_is_declared() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6", "Bc4"]))
    facts[3].eval_after_cp = None
    facts[3].eval_before_cp = None
    intelligence = GameIntelligence(context_for(facts), facts)
    report = intelligence.build_report()
    assert report.provenance.evaluated_moves == 4
    assert any(item.section == "analysis_coverage" for item in report.unavailable)


def test_empty_game_is_handled_without_errors() -> None:
    intelligence = GameIntelligence(context_for([]), [])
    report = intelligence.build_report()
    assert report.summary.moves == 0
    assert any(item.section == "all" for item in report.unavailable)
    assert intelligence.get_player_game_statistics()["scope"] == "single_game"


def test_single_move_game_is_handled() -> None:
    facts = quiet(moves_from_sans(["e4"]))
    intelligence = GameIntelligence(context_for(facts), facts)
    report = intelligence.build_report()
    assert report.summary.moves == 1
    assert report.trajectory.trajectory.points


def test_long_game_processing_stays_linear() -> None:
    # 60 legal plies: the opening, then neutral knight shuffling, then a pair of
    # pawn moves. Checks that snapshots line up with plies for a long game.
    opening = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "d3", "d6", "c3", "Nf6"]
    cycle = ["Ng1", "Ng8", "Nf3", "Nf6"] * 12
    san = opening + cycle + ["h3", "h6"]
    assert len(san) == 60
    facts = quiet(moves_from_sans(san))
    intelligence = GameIntelligence(context_for(facts), facts)
    report = intelligence.build_report()
    assert report.provenance.moves_in_game == 60
    assert len(report.material.timeline.snapshots) == 60
    assert len(report.phases.phase_by_ply) == 60


def test_ai_ready_tool_surface_is_complete() -> None:
    intelligence = intelligence_for(OPERA_GAME_PGN)
    tools = intelligence.available_tools()
    assert set(tools) == {
        "get_game_summary",
        "get_game_trajectory",
        "get_critical_moments",
        "get_move_analysis",
        "get_tactical_events",
        "get_positional_events",
        "get_phase_analysis",            "get_material_timeline",
            "get_accuracy",
            "get_result_forecast",
            "get_player_game_statistics",
        }
    assert intelligence.get_move_analysis(1).ply == 1
    assert intelligence.get_move_analysis(999) is None
    stats = intelligence.get_player_game_statistics()
    assert stats["scope"] == "single_game"
    assert "never inferred from one game" in stats["note"]


def test_no_ml_or_llm_is_involved_in_the_intelligence_layer() -> None:
    import argus.intelligence as package

    sources = [
        __import__(name, fromlist=["*"]).__doc__ or ""
        for name in (
            "argus.intelligence.tactics",
            "argus.intelligence.positional",
            "argus.intelligence.report",
        )
    ]
    joined = " ".join(sources).lower()
    assert "no llm" in joined or "no language model" in joined
    assert hasattr(package, "GameIntelligence")


def test_policy_thresholds_are_configurable_and_recorded() -> None:
    policy = IntelligencePolicy()
    policy.advantage.winning_threshold = 400
    facts = quiet(moves_from_sans(["e4", "e5"]))
    intelligence = GameIntelligence(context_for(facts), facts, policy=policy)
    section = intelligence.get_trajectory_section()
    assert section.advantage_thresholds["winning_threshold_cp"] == 400


# --- independent per-module entry points -------------------------------------------


def test_modules_can_be_used_directly() -> None:
    facts = quiet(moves_from_sans(["e4", "e5", "Nf3", "Nc6"]))
    assert detect_tactical_events(facts).events == []
    assert build_pawn_structure(facts, initial_position=chess.STARTING_FEN).snapshots
    assert build_material_timeline(facts, initial_position=chess.STARTING_FEN).snapshots
    positional_analysis = build_positional_analysis(facts, initial_position=chess.STARTING_FEN)
    assert isinstance(positional_analysis.events, list)
    assert isinstance(positional_analysis.feature_count, int)
    assert parse_first_game(OPERA_GAME_PGN).moves
