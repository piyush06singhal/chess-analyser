"""Intelligence benchmarks (§10–§15): classification, accuracy, phases, claims.

These suites are engine-free and deterministic. They test the *rules* — the
classification bands, the accuracy formula, the phase detector, the sample-size
gates — against hand-computable cases, including the false-positive and
small-sample cases the phase specifically calls out.
"""

from __future__ import annotations

from argus.analysis.classification import (
    MoveClassification,
    MoveClassificationInput,
    MoveClassificationPolicy,
    classify_move,
)
from argus.analysis.features.extractor import extract_position_features_from_fen
from argus.analysis.phase import GamePhase, classify_position_fen
from argus.evaluation.results import SuiteResult, check
from argus.intelligence.accuracy import ACCURACY_DISCLAIMER, win_expectation
from argus.opponent_intelligence import policy as opponent_policy
from argus.player_intelligence.policy import Coverage, PlayerInsightPolicy


def _classify(cpl: int | None, **kwargs) -> MoveClassification | None:
    return classify_move(MoveClassificationInput(centipawn_loss=cpl, **kwargs))


def move_classification_suite(context) -> SuiteResult:
    """Classification respects its bands, handles mate, and does not over-fire."""
    policy = MoveClassificationPolicy()
    checks = []

    bands = [
        ("best.zero", 0, MoveClassification.BEST),
        ("best.within", policy.best_max_cp_loss, MoveClassification.BEST),
        ("excellent", policy.best_max_cp_loss + 1, MoveClassification.EXCELLENT),
        ("good", policy.excellent_max_cp_loss + 1, MoveClassification.GOOD),
        ("inaccurate", policy.good_max_cp_loss + 1, MoveClassification.INACCURATE),
        ("mistake", policy.inaccurate_max_cp_loss + 1, MoveClassification.MISTAKE),
        ("blunder", policy.mistake_max_cp_loss + 1, MoveClassification.BLUNDER),
    ]
    for name, cpl, expected in bands:
        actual = _classify(cpl)
        checks.append(
            check(f"band.{name}", actual == expected, detail=f"cpl={cpl} -> {actual}")
        )

    # An unclassified move must stay unclassified, never guessed.
    checks.append(
        check(
            "unavailable evaluation is not classified",
            _classify(None) is None,
            detail="cp loss None -> None",
        )
    )

    # A small engine difference is not a mistake — the false-positive guard.
    checks.append(
        check(
            "small loss is never called a mistake",
            _classify(12) not in {MoveClassification.MISTAKE, MoveClassification.BLUNDER},
            detail=f"12cp -> {_classify(12)}",
            critical=True,
        )
    )

    # Brilliancy requires positive proof: best move, a sacrifice, and a gap.
    brilliant_no_sacrifice = classify_move(
        MoveClassificationInput(centipawn_loss=0, is_best_move=True, second_best_gap=200)
    )
    brilliant_with_proof = classify_move(
        MoveClassificationInput(
            centipawn_loss=0, is_best_move=True, sacrifices_material=True, second_best_gap=200
        )
    )
    checks.append(
        check(
            "brilliant requires a sacrifice",
            brilliant_no_sacrifice != MoveClassification.BRILLIANT
            and brilliant_with_proof == MoveClassification.BRILLIANT,
            detail=f"no-sac={brilliant_no_sacrifice}, with-proof={brilliant_with_proof}",
        )
    )

    return SuiteResult(
        suite="move_classification",
        title="Move classification",
        checks=checks,
    )


def accuracy_suite(context) -> SuiteResult:
    """The accuracy metric is documented, bounded, deterministic and honest."""
    checks = []

    # Win expectation: bounded, symmetric in the sense a positive eval favours the
    # side it belongs to, and mate handled exactly.
    level = win_expectation(0, None)
    checks.append(check("win expectation is 0.5 at level", abs(level - 0.5) < 1e-9, detail=f"{level}"))
    checks.append(
        check(
            "win expectation is bounded",
            all(0.0 <= win_expectation(cp, None) <= 1.0 for cp in (-2000, -300, 0, 300, 2000)),
        )
    )
    checks.append(
        check(
            "mate handled exactly",
            win_expectation(None, 3) == 1.0 and win_expectation(9999, -2) == 0.0,
            detail="mate>0 -> 1.0, mate<0 -> 0.0",
        )
    )
    # Monotonic: a bigger advantage is never a smaller expectation.
    values = [win_expectation(cp, None) for cp in (-500, -100, 0, 100, 500)]
    checks.append(check("win expectation is monotonic", values == sorted(values)))

    # The methodology and disclaimer travel with the metric.
    checks.append(
        check(
            "disclaimer states it is not another site's metric",
            "not Chess.com" in ACCURACY_DISCLAIMER and "not Lichess" in ACCURACY_DISCLAIMER,
        )
    )

    # The documented formula: loss = max(0, E_before - E_after) / max(E_before, 0.5),
    # accuracy = 100 * (1 - loss). A small drop must score higher than a large one,
    # and the score must stay inside [0, 100].
    def accuracy_for(before_cp: int, after_cp: int) -> float:
        before = win_expectation(before_cp, None)
        after = win_expectation(after_cp, None)
        loss = max(0.0, before - after) / max(before, 0.5)
        return 100.0 * (1.0 - min(1.0, loss))

    small_drop = accuracy_for(100, 80)
    large_drop = accuracy_for(100, -300)
    no_drop = accuracy_for(50, 50)
    checks.append(check("a move played best scores 100", abs(no_drop - 100.0) < 1e-9, detail=f"{no_drop}"))
    checks.append(
        check(
            "a small drop scores higher than a large one",
            small_drop > large_drop,
            detail=f"small={small_drop:.1f}, large={large_drop:.1f}",
        )
    )
    checks.append(
        check(
            "the accuracy score is bounded",
            0.0 <= large_drop <= 100.0 and 0.0 <= small_drop <= 100.0,
        )
    )

    return SuiteResult(
        suite="accuracy",
        title="Accuracy methodology",
        checks=checks,
    )


def game_intelligence_suite(context) -> SuiteResult:
    """Phase detection and material reading on known positions (§12)."""
    checks = []

    # Phase: the start position is opening; a bare-kings endgame is endgame.
    start_phase = classify_position_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    endgame_phase = classify_position_fen("8/8/8/4k3/8/8/8/4K3 w - - 0 1")
    checks.append(
        check(
            "start position is the opening",
            start_phase is GamePhase.OPENING,
            detail=str(start_phase),
        )
    )
    checks.append(
        check(
            "bare kings is the endgame",
            endgame_phase is GamePhase.ENDGAME,
            detail=str(endgame_phase),
        )
    )

    # Material: a whole rook up reads as +5 pawns from the right perspective.
    features = extract_position_features_from_fen("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    checks.append(
        check(
            "material balance reads a rook up",
            features.material_balance == 500,
            detail=f"material_balance={features.material_balance}",
        )
    )
    level = extract_position_features_from_fen("8/8/8/4k3/8/8/8/4K3 w - - 0 1")
    checks.append(
        check(
            "a level endgame reads level",
            level.material_balance == 0,
            detail=f"material_balance={level.material_balance}",
        )
    )

    # A position with both sides developed (few undeveloped pieces) and material
    # still on the board is the middlegame — not the opening, not the endgame.
    middlegame_phase = classify_position_fen(
        "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"
    )
    checks.append(
        check(
            "a developed position is the middlegame",
            middlegame_phase is GamePhase.MIDDLEGAME,
            detail=str(middlegame_phase),
        )
    )
    # The same FEN must classify the same way every time.
    checks.append(
        check(
            "phase classification is deterministic",
            classify_position_fen(
                "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"
            )
            is middlegame_phase,
        )
    )

    return SuiteResult(
        suite="game_intelligence",
        title="Game intelligence",
        checks=checks,
    )


def player_intelligence_suite(context) -> SuiteResult:
    """Small samples must not produce strong claims (§14)."""
    policy = PlayerInsightPolicy()
    checks = []

    checks.append(
        check(
            "no profile from a single game",
            not policy.can_profile(1) and policy.can_profile(2),
            detail=f"min_games_for_profile={policy.min_games_for_profile}",
        )
    )
    checks.append(
        check(
            "no tendency from a small sample",
            not policy.can_claim_tendency(5) and policy.can_claim_tendency(20),
            detail=f"min_games_for_tendency={policy.min_games_for_tendency}",
        )
    )
    checks.append(
        check(
            "coverage bands describe the sample",
            policy.coverage_for(1) is Coverage.INSUFFICIENT
            and policy.coverage_for(6) is Coverage.MODERATE
            and policy.coverage_for(30) is Coverage.ROBUST,
            detail="1->insufficient, 6->moderate, 30->robust",
        )
    )
    # A pattern needs occurrences, games, coverage AND consistency.
    checks.append(
        check(
            "a pattern needs every bar",
            not policy.pattern_is_supported(
                occurrences=100, games=100, coverage=0.0, consistency=0.0
            )
            and policy.pattern_is_supported(
                occurrences=4, games=4, coverage=0.5, consistency=0.5
            ),
            critical=True,
        )
    )

    return SuiteResult(
        suite="player_intelligence",
        title="Player intelligence guardrails",
        checks=checks,
    )


def opponent_suite(context) -> SuiteResult:
    """Opponent claims are sample-gated and never predictions (§15)."""
    policy = opponent_policy.DEFAULT_POLICY
    checks = []

    gates = policy.gates()
    checks.append(
        check(
            "four named gates exist",
            set(gates) == {
                "min_games_for_repertoire_insight",
                "min_occurrences_for_tendency",
                "min_positions_for_structure_insight",
                "min_games_for_phase_comparison",
            },
            detail=", ".join(sorted(gates)),
        )
    )
    checks.append(
        check(
            "coverage bands describe the sample",
            opponent_policy.coverage_for(1) is opponent_policy.Coverage.INSUFFICIENT
            and opponent_policy.coverage_for(6) is opponent_policy.Coverage.MODERATE,
            detail="1->insufficient, 6->moderate",
        )
    )
    checks.append(
        check(
            "a tendency needs more share than a pattern",
            policy.tendency_share_for_tendency > policy.repertoire_share_for_pattern,
            detail=(
                f"pattern>={policy.repertoire_share_for_pattern}, "
                f"tendency>={policy.tendency_share_for_tendency}"
            ),
        )
    )
    # The policy exposes thresholds only — there is no field that could be a
    # predicted move or result, which is the structural guarantee.
    forbidden = {"predicted_move", "prediction", "expected_result", "win_probability"}
    checks.append(
        check(
            "no prediction field exists in opponent policy",
            not (forbidden & set(policy.to_dict())),
            detail="policy exposes thresholds, never a prediction",
            critical=True,
        )
    )

    return SuiteResult(
        suite="opponent",
        title="Opponent intelligence guardrails",
        checks=checks,
    )


__all__ = [
    "accuracy_suite",
    "game_intelligence_suite",
    "move_classification_suite",
    "opponent_suite",
    "player_intelligence_suite",
]
