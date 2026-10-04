"""Re-analyzing a game must never be read as two analyses at once.

Running the analysis again at a new ``analysis_version`` writes a second
generation of ``move_analyses`` rows next to the previous one (the unique key is
``game_id + ply + analysis_version``). Reads have to resolve which generation is
current, because returning both is not merely noisy — it reports every move
twice and, on top of that, aborts the run on the unique
``(game_id, ply, reason, analysis_version)`` key of ``critical_positions``.

These tests drive the real repository and the real endpoints on the per-test
database, with two generations of stored rows for one game.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from argus.analysis.classification import MoveClassification
from argus.analysis.critical_positions import CriticalReason, Severity
from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus_api.db.repository import (
    get_critical_positions,
    get_move_analyses,
    latest_analysis_version,
    replace_critical_positions,
    resolve_analysis_version,
    save_move_analysis,
)
from argus_api.main import app

from tests.conftest import OPERA_GAME_PGN

OLD_VERSION = "3.0"
NEW_VERSION = "3.1"


@dataclass
class StoredMove:
    """The duck-typed per-move shape ``save_move_analysis`` persists."""

    ply: int
    move_number: int
    color: Color
    san: str
    uci: str
    fen_before: str
    fen_after: str
    depth: int = 12
    evaluation_before_cp: int | None = None
    evaluation_before_mate: int | None = None
    evaluation_after_cp: int | None = None
    evaluation_after_mate: int | None = None
    evaluation_change_cp: int | None = None
    centipawn_loss: int | None = None
    played_eval_cp: int | None = None
    played_eval_mate: int | None = None
    played_eval_source: str = "unavailable"
    best_move_uci: str | None = None
    best_move_san: str | None = None
    is_best_move: bool = False
    classification: MoveClassification | None = None
    phase: GamePhase | None = None
    principal_variation: list[str] | None = None


@dataclass
class StoredCritical:
    """The duck-typed critical-position shape the repository writes."""

    ply: int
    move_number: int
    color: Color
    san: str
    fen_before: str
    reason: CriticalReason
    severity: Severity
    severity_score: int
    classification: MoveClassification | None = None
    evaluation_before_white: int | None = None
    evaluation_after_white: int | None = None
    swing_cp: int | None = None
    is_mate_related: bool = False
    detail: str | None = None


def import_game(client: TestClient) -> tuple[str, list[dict]]:
    """Import the Opera Game and return its id plus its stored moves."""
    imported = client.post(
        "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
    ).json()
    game_id = imported["game_id"]
    moves = client.get(f"/api/games/{game_id}").json()["moves"]
    return game_id, moves


def write_generation(game_id: str, moves: list[dict], version: str) -> None:
    """Persist one full generation of per-move rows for a game."""
    factory = app.state.session_factory
    with factory.session_scope() as session:
        for move in moves:
            ply = move["ply"]
            mover = Color.WHITE if move["color"] == "white" else Color.BLACK
            save_move_analysis(
                session,
                game_id,
                StoredMove(
                    ply=ply,
                    move_number=move["move_number"],
                    color=mover,
                    san=move["san"],
                    uci=move["uci"],
                    fen_before=move["fen_before"],
                    fen_after=move["fen_after"],
                    evaluation_before_cp=10 * ply,
                    evaluation_after_cp=10 * ply - 5,
                    centipawn_loss=5,
                    played_eval_cp=10 * ply - 5,
                    played_eval_source="same_search" if ply % 2 == 1 else "resulting_position",
                    best_move_uci=move["uci"],
                    best_move_san=move["san"],
                    is_best_move=True,
                    classification=MoveClassification.BEST,
                    phase=GamePhase.OPENING,
                    principal_variation=[move["uci"]],
                ),
                analysis_version=version,
                engine="stockfish",
                engine_version="Stockfish 17.1",
            )


class TestAnalysisGenerations:
    def test_reads_resolve_the_current_generation_only(self, client: TestClient) -> None:
        game_id, moves = import_game(client)
        write_generation(game_id, moves, OLD_VERSION)
        write_generation(game_id, moves, NEW_VERSION)

        with app.state.session_factory.session_scope() as session:
            current = latest_analysis_version(session, game_id)
            rows = get_move_analyses(session, game_id)
            old_rows = get_move_analyses(session, game_id, analysis_version=OLD_VERSION)

        assert current == NEW_VERSION
        # Exactly one row per ply — never both generations at once.
        assert len(rows) == len(moves)
        assert [row.ply for row in rows] == [move["ply"] for move in moves]
        assert {row.analysis_version for row in rows} == {NEW_VERSION}
        # The older generation stays readable when asked for by name.
        assert {row.analysis_version for row in old_rows} == {OLD_VERSION}
        assert len(old_rows) == len(moves)

        # The endpoints that serve moves and progress count the game once.
        served = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert served["count"] == len(moves)
        assert len({row["ply"] for row in served["moves"]}) == len(moves)

        progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert progress["positions_analyzed"] == len(moves)

        # The full stored analysis of the game counts each move once as well.
        analysis = client.get(f"/api/analysis/games/{game_id}").json()
        assert analysis["moves_analyzed"] == len(moves)
        assert analysis["critical_moments_count"] == 0

    def test_the_intelligence_layer_sees_a_single_generation(self, client: TestClient) -> None:
        game_id, moves = import_game(client)
        write_generation(game_id, moves, OLD_VERSION)
        write_generation(game_id, moves, NEW_VERSION)

        status = client.get(f"/api/intelligence/games/{game_id}/report/status").json()
        assert status["stored_move_analyses"] == len(moves)

        body = client.get(f"/api/intelligence/games/{game_id}/move-analysis").json()
        assert body["count"] == len(moves)
        assert len({row["ply"] for row in body["moves"]}) == len(moves)

    def test_critical_positions_are_one_generation_and_deduplicated(self, client: TestClient) -> None:
        game_id, moves = import_game(client)
        write_generation(game_id, moves, OLD_VERSION)
        write_generation(game_id, moves, NEW_VERSION)

        # Two candidates for the same (ply, reason) — exactly what a mixed
        # generation produced before: it must be collapsed, not crash the run.
        candidates = [
            StoredCritical(
                ply=1,
                move_number=1,
                color=Color.WHITE,
                san="e4",
                fen_before=moves[0]["fen_before"],
                reason=CriticalReason.MISTAKE,
                severity=Severity.MEDIUM,
                severity_score=245,
                classification=MoveClassification.MISTAKE,
            ),
            StoredCritical(
                ply=1,
                move_number=1,
                color=Color.WHITE,
                san="e4",
                fen_before=moves[0]["fen_before"],
                reason=CriticalReason.MISTAKE,
                severity=Severity.HIGH,
                severity_score=250,
                classification=MoveClassification.MISTAKE,
            ),
            StoredCritical(
                ply=1,
                move_number=1,
                color=Color.WHITE,
                san="e4",
                fen_before=moves[0]["fen_before"],
                reason=CriticalReason.EVALUATION_SWING,
                severity=Severity.MEDIUM,
                severity_score=245,
                swing_cp=245,
            ),
        ]
        with app.state.session_factory.session_scope() as session:
            written = replace_critical_positions(
                session, game_id, candidates, analysis_version=NEW_VERSION
            )
            stored = get_critical_positions(session, game_id)

        # One row per (ply, reason): the more severe duplicate wins.
        assert written == 2
        assert len(stored) == 2
        assert len({(row.ply, row.reason) for row in stored}) == 2
        mistake = next(row for row in stored if row.reason == CriticalReason.MISTAKE.value)
        assert mistake.severity_score == 250
        assert mistake.severity == Severity.HIGH.value

        served = client.get(f"/api/analysis/games/{game_id}/critical-moments").json()
        assert served["count"] == 2
        assert len({(row["ply"], row["reason"]) for row in served["critical_moments"]}) == 2

    def test_an_unanalyzed_game_reports_no_rows_rather_than_all_versions(
        self, client: TestClient
    ) -> None:
        game_id, _ = import_game(client)
        with app.state.session_factory.session_scope() as session:
            assert latest_analysis_version(session, game_id) is None
            assert get_move_analyses(session, game_id) == []
            assert get_critical_positions(session, game_id) == []


class TestPartialGenerations:
    """An interrupted re-analysis must not shadow the last complete one.

    A re-run that is cancelled part-way leaves a *newer but partial* generation of
    rows next to the older complete one. Reading the newer generation would serve
    half a game as if it were analysed — so reads prefer the newest generation that
    covers every ply, and say when none does.
    """

    def test_a_partial_newer_generation_does_not_shadow_the_complete_one(
        self, client: TestClient
    ) -> None:
        game_id, moves = import_game(client)
        write_generation(game_id, moves, OLD_VERSION)
        # The newer run only got three plies in before it was interrupted.
        write_generation(game_id, moves[:3], NEW_VERSION)

        with app.state.session_factory.session_scope() as session:
            # The newest *written* generation is still the partial one...
            assert latest_analysis_version(session, game_id) == NEW_VERSION
            # ...but reads resolve to the newest *complete* one.
            version, complete = resolve_analysis_version(session, game_id)
            assert version == OLD_VERSION
            assert complete is True
            rows = get_move_analyses(session, game_id)
            assert {row.analysis_version for row in rows} == {OLD_VERSION}
            assert len(rows) == len(moves)
            # Criticals resolve to the same generation as the moves.
            assert get_critical_positions(session, game_id, analysis_version=version) is not None

        served = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert served["analysis_version"] == OLD_VERSION
        assert served["analysis_complete"] is True
        assert served["count"] == len(moves)

    def test_a_partial_only_generation_is_reported_as_incomplete(
        self, client: TestClient
    ) -> None:
        game_id, moves = import_game(client)
        write_generation(game_id, moves[:2], NEW_VERSION)

        with app.state.session_factory.session_scope() as session:
            version, complete = resolve_analysis_version(session, game_id)
        assert version == NEW_VERSION
        assert complete is False

        served = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert served["analysis_version"] == NEW_VERSION
        assert served["analysis_complete"] is False

    def test_the_endpoint_reports_the_stored_version_not_the_code_constant(
        self, client: TestClient
    ) -> None:
        """Rows written by an older run must not be labelled with the current one."""
        game_id, moves = import_game(client)
        write_generation(game_id, moves, OLD_VERSION)
        served = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert served["analysis_version"] == OLD_VERSION
        analysis = client.get(f"/api/analysis/games/{game_id}").json()
        assert analysis["analysis_version"] == OLD_VERSION
        assert analysis["analysis_complete"] is True

    def test_an_unanalysed_game_reports_a_null_version(self, client: TestClient) -> None:
        game_id, _ = import_game(client)
        served = client.get(f"/api/analysis/games/{game_id}/moves").json()
        assert served["analysis_version"] is None
        assert served["analysis_complete"] is False
        assert served["count"] == 0


class TestEngineConfigurationIdempotency:
    """Recording an engine configuration twice (concurrently) must not fail.

    Two analyses can run at once with identical settings. Both check for the row
    before either commits, so the loser hits the unique ``config_hash`` key. That
    is the desired end state — the configuration is recorded — so it must be
    treated as success, not as an error that aborts the whole analysis.
    """

    def test_recording_the_same_configuration_again_is_a_no_op(self) -> None:
        from argus_api.db.models import EngineConfiguration
        from argus_api.db.repository import record_engine_configuration
        from sqlalchemy import func, select

        kwargs = dict(
            config_hash="test-config-hash-idempotent",
            engine="stockfish",
            engine_version="test-engine",
            analysis_version="3.1",
            profile="standard",
            depth=12,
            movetime_ms=None,
            multipv=3,
            threads=1,
            hash_mb=256,
            policy={"probe": True},
        )
        with app.state.session_factory.session_scope() as session:
            record_engine_configuration(session, **kwargs)
            # A second call must not raise even though the row already exists.
            record_engine_configuration(session, **kwargs)
            count = session.execute(
                select(func.count()).select_from(EngineConfiguration).where(
                    EngineConfiguration.config_hash == "test-config-hash-idempotent"
                )
            ).scalar_one()
        assert count == 1
