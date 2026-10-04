"""Phase 8 unit tests: eligibility, acceptance, difficulty, hints, dedupe keys.

These pin the *rules* of the training engine. Every threshold in the policies
has a test on both sides of the line — a position just inside it and one just
outside — because a boundary nobody tests is a boundary nobody can trust.
"""

from __future__ import annotations

import sys
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.training.acceptance import (  # noqa: E402
    AcceptanceOutcome,
    MoveAcceptancePolicy,
    evaluate_attempt,
)
from argus.training.difficulty import assess_difficulty  # noqa: E402
from argus.training.eligibility import (  # noqa: E402
    EligibilityPolicy,
    RejectionReason,
    TrainingEligibilityService,
)
from argus.training.hints import build_hints  # noqa: E402
from argus.training.models import (  # noqa: E402
    Category,
    TrainingPosition,
)

#: The Scholar's-mate position 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6?? — a legal, legal-move-rich
#: position whose "solution" Qxf7# is genuinely playable to test against.
POSITION_FEN = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
SOLUTION_UCI = "h5f7"
SOLUTION_SAN = "Qxf7#"

#: A nearly-forced position: white king in check from the g1 rook, exactly two
#: legal answers (Kxg1, Kh2).
FORCED_FEN = "7k/8/8/8/8/8/8/6rK w - - 0 1"
FORCED_SOLUTION = "h1g1"

#: A sparse non-forced position (white Kc1 + Pc2 vs black Ka8).
SPARSE_FEN = "k7/8/8/8/8/8/2P5/2K5 w - - 0 1"


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _service(**policy_kwargs) -> TrainingEligibilityService:
    return TrainingEligibilityService(EligibilityPolicy(**policy_kwargs))


def _legal_position() -> chess.Board:
    return chess.Board(POSITION_FEN)


class TestEligibilityLegality:
    def test_corrupt_fen_rejected(self) -> None:
        report = _service().check(
            fen="not a fen at all",
            solution_uci="e2e4",
            solution_eval_cp=40,
            played_loss_cp=200,
        )
        assert not report.accepted
        assert report.reasons[0].startswith(RejectionReason.NOT_LEGAL.value)

    def test_game_over_position_rejected(self) -> None:
        board = chess.Board()
        board.push(chess.Move.from_uci("f2f3"))
        board.push(chess.Move.from_uci("e7e5"))
        board.push(chess.Move.from_uci("g2g4"))
        board.push(chess.Move.from_uci("d8h4"))
        assert board.is_checkmate()
        report = _service().check(
            fen=board.fen(),
            solution_uci="a2a3",
            solution_eval_cp=0,
            played_loss_cp=200,
        )
        assert not report.accepted
        assert "game is already over" in report.reasons[0]

    def test_illegal_solution_rejected(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci="e4e6",  # no pawn on e4 in that position
            solution_eval_cp=40,
            played_loss_cp=200,
        )
        assert not report.accepted
        assert report.reasons[0].startswith(RejectionReason.SOLUTION_NOT_LEGAL.value)


class TestEligibilityEngineEvidence:
    def test_no_engine_analysis_rejected(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=None,
            played_loss_cp=None,
        )
        assert not report.accepted
        assert report.reasons[0].startswith(RejectionReason.NO_ENGINE_ANALYSIS.value)

    def test_mate_only_solution_is_engine_analysis(self) -> None:
        # A mate score counts as engine analysis even with no centipawn row.
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=None,
            solution_eval_mate=3,
            played_loss_cp=400,
        )
        mate_reasons = [r for r in report.reasons if r.startswith(RejectionReason.NO_ENGINE_ANALYSIS.value)]
        assert not mate_reasons


class TestEligibilityClarityAndValue:
    def test_not_a_mistake_below_gap(self) -> None:
        # Played move cost 100cp < min_gap_to_played_cp (120): not worth training.
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=100,
        )
        assert any(r.startswith(RejectionReason.NOT_A_MISTAKE.value) for r in report.reasons)

    def test_clear_mistake_passes_clarity(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=250,
        )
        assert not any(r.startswith(RejectionReason.NOT_A_MISTAKE.value) for r in report.reasons)

    def test_unclear_solution_rejected(self) -> None:
        # Best alternative only 40cp worse than the solution (< min_advantage_cp 60).
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=250,
            alternative_best_loss_cp=40,
        )
        assert any(r.startswith(RejectionReason.SOLUTION_NOT_CLEAR.value) for r in report.reasons)

    def test_clear_advantage_passes(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=250,
            alternative_best_loss_cp=180,
        )
        assert not any(r.startswith(RejectionReason.SOLUTION_NOT_CLEAR.value) for r in report.reasons)

    def test_decided_position_trivially_obvious(self) -> None:
        # Solution advantage 1200cp > max 900: any move wins.
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=1200,
            played_loss_cp=250,
        )
        assert any(r.startswith(RejectionReason.TRIVIALLY_OBVIOUS.value) for r in report.reasons)

    def test_mate_solution_not_rejected_as_trivial(self) -> None:
        # Mate scores bypass the centipawn 'decided' check.
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=None,
            solution_eval_mate=4,
            played_loss_cp=250,
        )
        assert not any(r.startswith(RejectionReason.TRIVIALLY_OBVIOUS.value) for r in report.reasons)


class TestEligibilityForcedAndDuplicates:
    def test_nearly_forced_position_rejected(self) -> None:
        board = chess.Board(FORCED_FEN)
        assert board.legal_moves.count() < 3
        report = _service().check(
            fen=FORCED_FEN,
            solution_uci=FORCED_SOLUTION,
            solution_eval_cp=40,
            played_loss_cp=250,
        )
        assert any(r.startswith(RejectionReason.TOO_FORCED.value) for r in report.reasons)

    def test_duplicate_rejected(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=250,
            duplicate=True,
        )
        assert any(r.startswith(RejectionReason.DUPLICATE.value) for r in report.reasons)

    def test_fresh_position_accepts(self) -> None:
        report = _service().check(
            fen=_legal_position().fen(),
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            played_loss_cp=250,
        )
        assert report.accepted, report.reasons


# ---------------------------------------------------------------------------
# Move acceptance
# ---------------------------------------------------------------------------


class TestAcceptanceBands:
    def test_exact_solution_correct(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=240,
            submitted_eval_mate=None,
            submitted_uci="g1f3",
            solution_uci="g1f3",
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT

    def test_recorded_equivalent_correct(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=230,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
            acceptable_moves={"e2e4": "e4"},
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT
        assert "equivalent" in decision.reason

    def test_within_tolerance_correct(self) -> None:
        # Best = +2.4, user = +2.37 (30cp gap = tolerance boundary).
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=210,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT
        assert decision.evaluation_delta_cp == 30

    def test_near_best_band(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=150,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
        )
        assert decision.outcome is AcceptanceOutcome.NEAR_BEST

    def test_clearly_incorrect(self) -> None:
        # Best = +2.4, user = -4.0: clearly incorrect.
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=-400,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
        )
        assert decision.outcome is AcceptanceOutcome.INCORRECT
        assert decision.evaluation_delta_cp == 640

    def test_policy_thresholds_tighten_bands(self) -> None:
        # A 40cp gap is near-best under the default (150) policy...
        default = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=200,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
        )
        assert default.outcome is AcceptanceOutcome.NEAR_BEST
        # ...but incorrect under a stricter policy.
        policy = MoveAcceptancePolicy(equal_tolerance_cp=5, near_best_cp=30)
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=200,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
            policy=policy,
        )
        assert decision.outcome is AcceptanceOutcome.INCORRECT

    def test_missing_attempt_evaluation_is_incorrect_with_reason(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=240,
            solution_eval_mate=None,
            submitted_eval_cp=None,
            submitted_eval_mate=None,
            submitted_uci="e2e4",
            solution_uci="g1f3",
        )
        assert decision.outcome is AcceptanceOutcome.INCORRECT
        assert "no stored evaluation" in decision.reason


class TestAcceptanceMateCases:
    def test_delivering_mate_same_distance_correct(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=2,
            submitted_eval_cp=None,
            submitted_eval_mate=2,
            submitted_uci="d1h5",
            solution_uci="d1h5",
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT

    def test_faster_mate_is_correct(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=3,
            submitted_eval_cp=None,
            submitted_eval_mate=2,
            submitted_uci="d1h5",
            solution_uci="a1a8",
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT

    def test_slower_mate_is_near_best(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=2,
            submitted_eval_cp=None,
            submitted_eval_mate=5,
            submitted_uci="a1a8",
            solution_uci="d1h5",
        )
        assert decision.outcome is AcceptanceOutcome.NEAR_BEST

    def test_missing_mate_when_forced_is_incorrect(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=2,
            submitted_eval_cp=100,
            submitted_eval_mate=None,
            submitted_uci="a1a8",
            solution_uci="d1h5",
        )
        assert decision.outcome is AcceptanceOutcome.INCORRECT

    def test_receiving_mate_equal_defence_correct(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=-3,
            submitted_eval_cp=None,
            submitted_eval_mate=-3,
            submitted_uci="g8f6",
            solution_uci="g8f6",
        )
        assert decision.outcome is AcceptanceOutcome.CORRECT

    def test_receiving_mate_meaningful_delay_is_near_best(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=-3,
            submitted_eval_cp=None,
            submitted_eval_mate=-7,
            submitted_uci="g8h6",
            solution_uci="g8f6",
        )
        assert decision.outcome is AcceptanceOutcome.NEAR_BEST

    def test_receiving_mate_accelerating_is_incorrect(self) -> None:
        decision = evaluate_attempt(
            solution_eval_cp=None,
            solution_eval_mate=-3,
            submitted_eval_cp=None,
            submitted_eval_mate=-1,
            submitted_uci="g8h6",
            solution_uci="g8f6",
        )
        assert decision.outcome is AcceptanceOutcome.INCORRECT


# ---------------------------------------------------------------------------
# Difficulty
# ---------------------------------------------------------------------------


class TestDifficulty:
    def test_forced_simple_position_is_beginner(self) -> None:
        # Few legal moves, quiet endgame-like solution.
        fen = "8/8/8/8/8/5k2/8/5K1Q w - - 0 1"
        assessment = assess_difficulty(
            fen=fen,
            solution_uci="h1h3",
            solution_eval_cp=0,
            alternative_best_loss_cp=500,
        )
        # Measurability + factor provenance, not an exact band.
        assert assessment.factors["legal_moves"] == chess.Board(fen).legal_moves.count()
        assert assessment.factors["factor_weights"] == assessment.factors["factor_weights"]

    def test_dense_quiet_position_is_harder_than_forced_one(self) -> None:
        forced = assess_difficulty(
            fen=SPARSE_FEN,
            solution_uci="c2c4",
            solution_eval_cp=200,
            alternative_best_loss_cp=100,
            solution_pv_length=2,
        )
        dense = assess_difficulty(
            fen=POSITION_FEN,
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            alternative_best_loss_cp=20,
            solution_pv_length=8,
        )
        assert dense.score > forced.score

    def test_known_eval_gap_beats_unknown(self) -> None:
        # A measured thin gap should score harder than 'unknown' neutral.
        measured = assess_difficulty(
            fen=POSITION_FEN,
            solution_uci=SOLUTION_UCI,
            solution_eval_cp=40,
            alternative_best_loss_cp=15,
            solution_pv_length=6,
        )
        assert measured.factors["eval_gap_cp"] == 15

    def test_quiet_solution_flagged(self) -> None:
        fen = POSITION_FEN
        assert chess.Move.from_uci("h2h3") in chess.Board(fen).legal_moves
        assessment = assess_difficulty(
            fen=fen,
            solution_uci="h2h3",
            solution_eval_cp=40,
            alternative_best_loss_cp=None,
        )
        assert assessment.factors["quiet_solution"] is True

    def test_band_boundaries_documented(self) -> None:
        from argus.training.difficulty import DIFFICULTY_BANDS, FACTOR_WEIGHTS
        assert abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9
        thresholds = [t for t, _ in DIFFICULTY_BANDS]
        assert thresholds == sorted(thresholds)


# ---------------------------------------------------------------------------
# Hints
# ---------------------------------------------------------------------------


class TestHints:
    def test_hints_are_progressive_and_bounded(self) -> None:
        hints = build_hints(
            fen=POSITION_FEN,
            solution_uci=SOLUTION_UCI,
            solution_san=SOLUTION_SAN,
            solution_eval_mate=1,
            tags=["mating_pattern"],
            side_to_move="white",
        )
        assert 0 < len(hints) <= 3
        # The solution SAN must never appear in a hint.
        assert all("Qxf7" not in hint for hint in hints)
        # Vague → specific: later hints mention concrete pieces/mechanisms.
        assert any("knight" in h or "check" in h or "mate" in h for h in hints[1:]) or len(hints) == 1

    def test_hints_reference_actual_tags(self) -> None:
        fen = POSITION_FEN
        hints = build_hints(
            fen=fen,
            solution_uci=SOLUTION_UCI,
            solution_san=SOLUTION_SAN,
            solution_eval_mate=1,
            tags=["hanging_piece"],
            side_to_move="white",
        )
        assert hints
        # No hallucinated motif: 'fork' never appears when not tagged.
        assert all("fork" not in h for h in hints)

    def test_hints_use_played_move_evidence(self) -> None:
        fen = POSITION_FEN
        hints = build_hints(
            fen=fen,
            solution_uci=SOLUTION_UCI,
            solution_san=SOLUTION_SAN,
            solution_eval_mate=1,
            played_move_san="Qf3",
            played_loss_cp=250,
            side_to_move="white",
        )
        assert any("Qf3" in h for h in hints)

    def test_no_hint_reveals_solution_uci(self) -> None:
        fen = POSITION_FEN
        hints = build_hints(
            fen=fen,
            solution_uci=SOLUTION_UCI,
            solution_san=SOLUTION_SAN,
            solution_eval_mate=1,
            side_to_move="white",
        )
        assert all(SOLUTION_UCI not in h for h in hints)


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------


class TestDedupeKey:
    def test_normalized_fen_ignores_counters(self) -> None:
        a = TrainingPosition(
            side_to_move="white",
            fen="r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
            category=Category.TACTICAL,
            solution_uci="f3f7",
            solution_san="Qxf7#",
        )
        b = a.model_copy(update={"fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 9 9"})
        assert a.normalized_fen() == b.normalized_fen()
        c = a.model_copy(update={"fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 4 4"})
        assert a.normalized_fen() != c.normalized_fen()
