"""API tests for the Phase 4 intelligence endpoints.

Engine-backed tests (which need a real analysis run first) are marked ``engine``;
the honest-degradation behaviour that needs no engine is unmarked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.intelligence import REPORT_VERSION
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN


def _import(client: TestClient, pgn: str = OPERA_GAME_PGN) -> str:
    response = client.post("/api/games/import", json={"pgn_text": pgn, "run_analysis": False})
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


class TestIntelligenceWithoutAnalysis:
    def test_report_requires_stored_analysis(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        for path in (
            "report",
            "summary",
            "trajectory",
            "critical-moments",
            "tactical-events",
            "positional-events",
            "phase-analysis",
            "material-timeline",
            "accuracy",
            "forecast",
            "player-statistics",
            "move-analysis",
            "tools",
        ):
            response = client.get(f"/api/intelligence/games/{game_id}/{path}")
            assert response.status_code == 409, f"{path}: {response.status_code}"
            body = response.json()
            assert body["error"]["code"] == "analysis_required"
            assert "Run the Stockfish analysis first" in body["error"]["message"]

    def test_missing_game_is_404(self, client: TestClient) -> None:
        assert client.get("/api/intelligence/games/nope/summary").status_code == 404
        assert client.post("/api/intelligence/games/nope/report").status_code == 404

    def test_report_status_is_honest_without_analysis(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        body = client.get(f"/api/intelligence/games/{game_id}/report/status").json()
        assert body["has_report"] is False
        assert body["has_analysis"] is False
        assert body["stored_move_analyses"] == 0
        assert body["analysis_status"] == "ready"


@pytest.mark.engine
class TestIntelligenceWithAnalysis:
    @pytest.fixture()
    def analyzed_game(self, client: TestClient) -> str:
        game_id = _import(client)
        start = client.post(
            f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 2}
        )
        assert start.status_code == 202, start.text
        progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert progress["status"] == "completed"
        return game_id

    def test_report_generation_and_persistence(self, client: TestClient, analyzed_game: str) -> None:
        generated = client.post(f"/api/intelligence/games/{analyzed_game}/report")
        assert generated.status_code == 200, generated.text
        report = generated.json()
        assert report["report_version"] == REPORT_VERSION
        assert report["provenance"]["engine_calls_made_by_intelligence_layer"] == 0
        assert report["provenance"]["engine_version"]
        assert report["provenance"]["moves_in_game"] == 33
        assert report["summary"]["moves"] == 33
        assert report["evidence_policy"]

        stored = client.get(f"/api/intelligence/games/{analyzed_game}/report").json()
        assert stored["source"] == "stored"
        assert stored["report"]["report_version"] == REPORT_VERSION
        assert stored["report"]["game_id"] == analyzed_game

        status = client.get(f"/api/intelligence/games/{analyzed_game}/report/status").json()
        assert status["has_report"] is True
        assert status["has_analysis"] is True

    def test_forecast_is_engine_derived_and_carries_its_method(self, client: TestClient, analyzed_game: str) -> None:
        body = client.get(f"/api/intelligence/games/{analyzed_game}/forecast").json()
        assert body["source"] == "argus_interpretation"
        assert body["evaluated_plies"] > 0
        assert body["final"] is not None
        total = body["final"]["white"] + body["final"]["draw"] + body["final"]["black"]
        assert abs(total - 1.0) < 0.01
        assert "300" in body["methodology"]
        assert "not a trained model" in body["disclaimer"]
        assert body["peak_white"] is not None

    def test_report_is_deterministic(self, client: TestClient, analyzed_game: str) -> None:
        first = client.post(f"/api/intelligence/games/{analyzed_game}/report").json()
        second = client.post(f"/api/intelligence/games/{analyzed_game}/report").json()
        # Everything except the generation timestamp must be reproducible.
        for payload in (first, second):
            payload.pop("generated_at")
        assert first == second

    def test_report_sections_are_evidenced(self, client: TestClient, analyzed_game: str) -> None:
        report = client.post(f"/api/intelligence/games/{analyzed_game}/report").json()
        assert report["opening"]["identification"]["source"] in {
            "move_sequence",
            "pgn_header",
            "unclassified",
        }
        assert report["phases"]["phase_by_ply"]
        assert report["trajectory"]["trajectory"]["points"]
        assert report["material"]["timeline"]["snapshots"]
        assert report["accuracy"]["analysis"]["methodology"]
        assert "not Chess.com" in report["accuracy"]["analysis"]["disclaimer"]
        assert report["key_lessons"]
        for lesson in report["key_lessons"]:
            assert lesson["statement"]
            assert lesson["source"] in {
                "engine_fact",
                "argus_derived_feature",
                "argus_interpretation",
            }
        for entry in report["critical_moments"]["timeline"]:
            assert entry["ply"] >= 1
            assert entry["kind"]
        for error in report["error_categories"]["analysis"]["errors"]:
            assert error["basis"]
            assert error["evidence"]

    def test_timeline_plies_exist_in_the_stored_positions(
        self, client: TestClient, analyzed_game: str
    ) -> None:
        report = client.post(f"/api/intelligence/games/{analyzed_game}/report").json()
        positions = client.get(f"/api/games/{analyzed_game}/positions").json()["positions"]
        available = {position["ply"] for position in positions}
        for entry in report["critical_moments"]["timeline"]:
            assert entry["ply"] in available

    def test_tool_endpoints_return_the_same_data_as_the_report(
        self, client: TestClient, analyzed_game: str
    ) -> None:
        report = client.post(f"/api/intelligence/games/{analyzed_game}/report").json()

        summary = client.get(f"/api/intelligence/games/{analyzed_game}/summary").json()
        assert summary["result"] == report["summary"]["result"]
        assert summary["facts"]

        trajectory = client.get(f"/api/intelligence/games/{analyzed_game}/trajectory").json()
        assert len(trajectory["trajectory"]["points"]) == len(
            report["trajectory"]["trajectory"]["points"]
        )
        assert trajectory["advantage_thresholds"]

        material = client.get(f"/api/intelligence/games/{analyzed_game}/material-timeline").json()
        assert len(material["snapshots"]) == 33

        accuracy = client.get(f"/api/intelligence/games/{analyzed_game}/accuracy").json()
        # Every scored move says where its comparison came from, and the counts
        # account for exactly the scored moves — no more, no fewer.
        for side in ("white", "black"):
            row = accuracy[side]
            assert row["exact_scores"] + row["approximate_scores"] == row["scored_moves"]
        assert {move["evaluation_source"] for move in accuracy["moves"]} <= {
            "same_search",
            "resulting_position",
            "unavailable",
        }
        # The breakdown slices the same scored moves three ways.
        breakdown = accuracy["breakdown"]
        assert breakdown is not None
        for side in ("white", "black"):
            slices = breakdown[side]
            assert slices["side"] == side
            for dimension in ("by_phase", "by_classification", "by_material_state"):
                assert slices[dimension], f"{side}.{dimension} has no measured slice"
                assert all(group["scored_moves"] > 0 for group in slices[dimension])

        phases = client.get(f"/api/intelligence/games/{analyzed_game}/phase-analysis").json()
        assert phases["final_phase"] in {"opening", "middlegame", "endgame"}
        assert phases["performance"]["phase_move_counts"]

        tactical = client.get(f"/api/intelligence/games/{analyzed_game}/tactical-events").json()
        assert "events" in tactical and "confirmed_count" in tactical

        positional = client.get(f"/api/intelligence/games/{analyzed_game}/positional-events").json()
        assert "events" in positional

        criticals = client.get(f"/api/intelligence/games/{analyzed_game}/critical-moments").json()
        assert "timeline" in criticals

        stats = client.get(f"/api/intelligence/games/{analyzed_game}/player-statistics").json()
        assert stats["scope"] == "single_game"
        assert "never inferred from one game" in stats["note"]
        assert stats["white"]["moves"] > 0

    def test_move_analysis_endpoint(self, client: TestClient, analyzed_game: str) -> None:
        everything = client.get(f"/api/intelligence/games/{analyzed_game}/move-analysis").json()
        assert everything["count"] == 33
        single = client.get(
            f"/api/intelligence/games/{analyzed_game}/move-analysis", params={"ply": 1}
        ).json()
        assert single["ply"] == 1
        assert single["san"] == "e4"
        missing = client.get(
            f"/api/intelligence/games/{analyzed_game}/move-analysis", params={"ply": 999}
        )
        assert missing.status_code == 404

    def test_tools_endpoint_lists_the_agent_surface(
        self, client: TestClient, analyzed_game: str
    ) -> None:
        body = client.get(f"/api/intelligence/games/{analyzed_game}/tools").json()
        assert "get_game_summary" in body["tools"]
        assert "get_player_game_statistics" in body["tools"]
        assert "not implemented here" in body["note"]

    def test_refresh_regenerates_instead_of_serving_the_stored_copy(
        self, client: TestClient, analyzed_game: str
    ) -> None:
        client.post(f"/api/intelligence/games/{analyzed_game}/report")
        refreshed = client.get(
            f"/api/intelligence/games/{analyzed_game}/report", params={"refresh": "true"}
        ).json()
        assert refreshed["source"] == "generated"
        assert refreshed["report"]["report_version"] == REPORT_VERSION

    def test_scholars_mate_game_reports_a_forced_mate(
        self, client: TestClient
    ) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        client.post(f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 2})
        report = client.post(f"/api/intelligence/games/{game_id}/report").json()
        trajectory = report["trajectory"]["trajectory"]
        assert any(point["mate_white"] is not None for point in trajectory["points"]), (
            "a mate score must be surfaced as a mate distance, never as centipawns"
        )
        # Mate must never be rendered as a plain centipawn number in the display.
        for point in trajectory["points"]:
            if point["mate_white"] is not None:
                assert point["evaluation_display"].startswith("#")


class TestReportPersistenceModel:
    def test_report_row_is_not_created_without_analysis(self, client: TestClient) -> None:
        game_id = _import(client, SCHOLARS_MATE_PGN)
        assert client.post(f"/api/intelligence/games/{game_id}/report").status_code == 409
        status = client.get(f"/api/intelligence/games/{game_id}/report/status").json()
        assert status["has_report"] is False


class TestReportFreshness:
    """A stored report is reused only while it was built by the current code."""

    def test_report_is_current_predicate(self) -> None:
        from argus_api.services.report_service import report_is_current

        def row(payload: dict | None, version: str) -> SimpleNamespace:
            return SimpleNamespace(payload=payload, report_version=version)

        assert report_is_current(row({"a": 1}, REPORT_VERSION))
        assert not report_is_current(row({"a": 1}, "0.0.1"))
        assert not report_is_current(row(None, REPORT_VERSION))
        assert not report_is_current(None)

    def test_ensure_report_rebuilds_a_stale_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from argus_api.services import report_service

        stale = SimpleNamespace(payload={"stale": True}, report_version="0.0.1")
        monkeypatch.setattr(report_service, "get_game_report", lambda db, gid: stale)
        monkeypatch.setattr(
            report_service, "generate_and_store_report", lambda db, gid: {"fresh": True}
        )
        assert report_service.ensure_report(object(), "g1") == {"fresh": True}

    def test_ensure_report_reuses_a_current_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from argus_api.services import report_service

        current = SimpleNamespace(payload={"fresh": True}, report_version=REPORT_VERSION)
        monkeypatch.setattr(report_service, "get_game_report", lambda db, gid: current)

        def _must_not_rebuild(db: object, gid: str) -> dict:
            raise AssertionError("a current report must not be rebuilt")

        monkeypatch.setattr(report_service, "generate_and_store_report", _must_not_rebuild)
        assert report_service.ensure_report(object(), "g1") == {"fresh": True}

    def test_ensure_report_falls_back_to_the_stale_copy_when_it_cannot_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from argus.shared.errors import AnalysisRequiredError
        from argus_api.services import report_service

        stale = SimpleNamespace(payload={"stale": True}, report_version="0.0.1")
        monkeypatch.setattr(report_service, "get_game_report", lambda db, gid: stale)

        def _cannot(db: object, gid: str) -> dict:
            raise AnalysisRequiredError("analysis is gone")

        monkeypatch.setattr(report_service, "generate_and_store_report", _cannot)
        assert report_service.ensure_report(object(), "g1") == {"stale": True}

    def test_get_report_regenerates_a_stale_snapshot(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from argus_api.routes import intelligence as route

        stale = SimpleNamespace(
            payload={"old": True},
            report_version="0.0.1",
            analysis_version="3.0",
            generated_at=None,
        )
        monkeypatch.setattr(route, "get_game_report", lambda db, gid: stale)
        monkeypatch.setattr(route, "authorize_game", lambda db, gid: None)
        monkeypatch.setattr(
            route,
            "generate_and_store_report",
            lambda db, gid: {
                "report_version": REPORT_VERSION,
                "game_id": gid,
                "provenance": {"analysis_version": "3.1"},
            },
        )
        body = client.get("/api/intelligence/games/abc/report").json()
        assert body["source"] == "generated"
        assert body["report_version"] == REPORT_VERSION

    def test_get_report_serves_a_current_snapshot_without_rebuilding(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from argus_api.routes import intelligence as route

        current = SimpleNamespace(
            payload={"new": True},
            report_version=REPORT_VERSION,
            analysis_version="3.1",
            generated_at=None,
        )
        monkeypatch.setattr(route, "get_game_report", lambda db, gid: current)
        monkeypatch.setattr(route, "authorize_game", lambda db, gid: None)

        def _must_not_rebuild(db: object, gid: str) -> dict:
            raise AssertionError("a current report must be served from the snapshot")

        monkeypatch.setattr(route, "generate_and_store_report", _must_not_rebuild)
        body = client.get("/api/intelligence/games/abc/report").json()
        assert body["source"] == "stored"
        assert body["report"] == {"new": True}
