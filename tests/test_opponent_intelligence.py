"""Phase 9 opponent intelligence — package-level tests (no database, no engine).

The core is deterministic and engine-free, so these tests build real
``OpponentGameInput`` fixtures and assert the aggregations directly: repertoire
counts and shares, response distributions, evidence-gated tendencies, phase
statistics, and the honesty rules (sample sizes travel with every claim; below a
gate, the claim level is ``insufficient``).
"""

from __future__ import annotations

from argus.opponent_intelligence import (
    ClaimLevel,
    Coverage,
    OpponentGameInput,
    OpponentInsightPolicy,
    OpponentIntelligenceService,
    OpponentMoveInput,
    PlayerIdentity,
)
from argus.player_intelligence.models import GameOutcome

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
AFTER_E4_NF3_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def _white_moves(opening: str = "e4") -> list[OpponentMoveInput]:
    """Three legal White opening moves: 1.e4 2.Nf3 3.Bc4 (or a variant 1st move)."""
    return [
        OpponentMoveInput(
            ply=1, move_number=1, color="white", san=opening, uci="e2e4", fen_before=START_FEN,
            phase="opening",
        ),
        OpponentMoveInput(
            ply=3, move_number=2, color="white", san="Nf3", uci="g1f3", fen_before=AFTER_E4,
            phase="opening",
        ),
        OpponentMoveInput(
            ply=5, move_number=3, color="white", san="Bc4", uci="f1c4",
            fen_before=AFTER_E4_NF3_NC6, phase="opening",
        ),
    ]


def _game(game_id: str, *, color: str = "white", outcome: GameOutcome = GameOutcome.WIN, **kw) -> OpponentGameInput:
    return OpponentGameInput(
        game_id=game_id,
        color=color,
        opponent_name="Rival",
        result="1-0" if outcome is GameOutcome.WIN else "0-1",
        outcome=outcome,
        analysis_status="analyzed",
        moves=kw.pop("moves", _white_moves()),
        **kw,
    )


def _identity() -> PlayerIdentity:
    return PlayerIdentity(player_id=1, name="Rival")


class TestRepertoire:
    def test_root_choice_is_counted_across_games(self) -> None:
        games = [_game(f"g{i}") for i in range(3)]
        profile = OpponentIntelligenceService().repertoire(games, "white")
        assert profile.analyzed_games == 3
        # The 1.e4 node is a genuine pattern at 3/3 with a share above the gate.
        root = next(node for node in profile.nodes if node.san == "e4")
        assert root.occurrences == 3
        assert root.share == 1.0
        assert root.claim_level in (ClaimLevel.PATTERN, ClaimLevel.TENDENCY)

    def test_small_sample_stays_an_observation(self) -> None:
        service = OpponentIntelligenceService(
            OpponentInsightPolicy(min_games_for_repertoire_insight=5)
        )
        profile = service.repertoire([_game("g1", color="white")], "white")
        root = next(node for node in profile.nodes if node.san == "e4")
        # One game is below the repertoire gate: counted, but not claimed.
        assert root.claim_level is ClaimLevel.OBSERVATION

    def test_no_games_means_no_repertoire_and_says_so(self) -> None:
        profile = OpponentIntelligenceService().repertoire([], "white")
        assert profile.nodes == []
        assert profile.coverage is Coverage.INSUFFICIENT
        assert "will not invent" in profile.note

    def test_repertoire_distinguishes_the_two_colours(self) -> None:
        black_moves = [
            OpponentMoveInput(
                ply=2, move_number=1, color="black", san="e5", uci="e7e5",
                fen_before="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                phase="opening",
            )
        ]
        games = [
            _game("w1"),
            OpponentGameInput(
                game_id="b1",
                color="black",
                opponent_name="Rival",
                result="0-1",
                outcome=GameOutcome.WIN,
                analysis_status="analyzed",
                moves=black_moves,
            ),
        ]
        service = OpponentIntelligenceService()
        white = service.repertoire(games, "white")
        black = service.repertoire(games, "black")
        assert white.analyzed_games == 1
        assert black.analyzed_games == 1
        assert any(node.san == "e5" for node in black.nodes)
        assert not any(node.san == "e5" for node in white.nodes)


class TestPositionResponses:
    def test_exact_match_returns_the_distribution(self) -> None:
        response = OpponentIntelligenceService().responses(
            [_game(f"g{i}") for i in range(3)], START_FEN
        )
        assert response.match == "exact"
        assert response.occurrences == 3
        assert response.responses[0].uci == "e2e4"
        assert response.responses[0].occurrences == 3

    def test_unknown_position_is_no_match_not_a_guess(self) -> None:
        # A position the fixture never reaches, in a distinct piece placement.
        unseen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
        response = OpponentIntelligenceService().responses([_game("g1")], unseen)
        assert response.match == "none"
        assert response.responses == []
        assert "no stored game" in response.sample_note

    def test_small_occurrence_count_is_labelled_as_such(self) -> None:
        response = OpponentIntelligenceService().responses([_game("g1")], START_FEN)
        assert response.claim_level in (ClaimLevel.INSUFFICIENT, ClaimLevel.OBSERVATION)
        assert "observation" in response.sample_note or "occurrence" in response.sample_note


class TestTendenciesAndPhases:
    def test_tendencies_carry_measurements_and_sample_sizes(self) -> None:
        measured = OpponentIntelligenceService().tendencies([_game(f"g{i}") for i in range(3)])
        keys = {tendency.key for tendency in measured}
        assert "castling_side" in keys
        assert "check_frequency" in keys
        for tendency in measured:
            assert tendency.measurement and tendency.value
            assert tendency.sample_size >= 0

    def test_phase_statistics_are_measured_not_imputed(self) -> None:
        games = [
            _game("g1", moves=[
                OpponentMoveInput(
                    ply=1, move_number=1, color="white", san="e4", uci="e2e4",
                    fen_before=START_FEN, phase="opening", centipawn_loss=200,
                    best_move_uci="d2d4", classification="blunder",
                )
            ])
        ]
        stats = OpponentIntelligenceService().phase_statistics(games)
        assert stats.analyzed_games == 1
        opening = next(s for s in stats.phases if s.phase == "opening")
        assert opening.moves == 1
        assert opening.significant_errors == 1
        assert stats.sample_note


class TestPreparationReport:
    def test_report_is_deterministic_and_evidence_gated(self) -> None:
        games = [_game(f"g{i}") for i in range(3)]
        service = OpponentIntelligenceService()
        first = service.preparation_report(games, _identity())
        second = service.preparation_report(games, _identity())
        assert first.model_dump(exclude={"generated_at"}) == second.model_dump(
            exclude={"generated_at"}
        )

    def test_report_never_profiles_psychology(self) -> None:
        report = OpponentIntelligenceService().preparation_report(
            [_game(f"g{i}") for i in range(3)], _identity()
        )
        banned = ("aggressive personality", "emotion", "will win", "predict")
        for insight in report.insights:
            text = f"{insight.title} {insight.statement}".lower()
            assert not any(word in text for word in banned)
            assert insight.sample_size >= 0

    def test_empty_history_is_reported_as_a_limitation(self) -> None:
        report = OpponentIntelligenceService().preparation_report([], _identity())
        assert report.statistics.total_games == 0
        assert any("no stored games" in limitation.lower() for limitation in report.limitations)

    def test_insights_are_ordered_by_confidence(self) -> None:
        report = OpponentIntelligenceService().preparation_report(
            [_game(f"g{i}") for i in range(4)], _identity()
        )
        order = {
            ClaimLevel.TENDENCY: 0,
            ClaimLevel.PATTERN: 1,
            ClaimLevel.OBSERVATION: 2,
            ClaimLevel.INSUFFICIENT: 3,
        }
        levels = [order[insight.claim_level] for insight in report.insights]
        assert levels == sorted(levels)
