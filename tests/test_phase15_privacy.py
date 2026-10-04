"""Cross-account privacy (§6, §28): two callers, one library, enforced isolation.

These are the tests the previous audit said could not be written — "there are no
accounts". API keys now provide a real caller identity, and game ownership makes
the boundary exercisable end to end: caller A imports a game, caller B must not
see it, and B's read returns 404 (absent, not forbidden).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from argus_api.config import get_settings
from argus_api.main import app
from argus_api.rate_limit import LIMITER
from argus_api.services.resource_gate import GATE
from tests.conftest import OPERA_GAME_PGN

#: Two callers with distinct keys. Roles are irrelevant to ownership; the caller
#: id is what the policy keys off.
KEY_A = "alice-key:alice:operator"
KEY_B = "bob-key:bob:operator"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("ARGUS_API_KEYS", f"{KEY_A},{KEY_B}")
    get_settings.cache_clear()
    LIMITER.reset()
    GATE.reset()
    return TestClient(app)


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def _import(client: TestClient, key: str | None, pgn: str = OPERA_GAME_PGN) -> str:
    headers = _headers(key) if key else {}
    response = client.post(
        "/api/games/import",
        json={"pgn_text": pgn, "run_analysis": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


class TestCrossAccountPrivacy:
    def test_a_keyless_request_is_refused(self, monkeypatch):
        with _client(monkeypatch) as client:
            assert client.get("/api/games").status_code == 401

    def test_a_game_is_private_to_its_importer(self, monkeypatch):
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")

            # Alice sees her game.
            assert client.get(f"/api/games/{game_id}", headers=_headers("alice-key")).status_code == 200

            # Bob does not — and it is *absent*, not forbidden, so the API never
            # confirms the game exists.
            bob_read = client.get(f"/api/games/{game_id}", headers=_headers("bob-key"))
            assert bob_read.status_code == 404
            assert bob_read.json()["error"]["code"] == "not_found"

    def test_the_library_list_is_scoped_per_caller(self, monkeypatch):
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")

            alice_library = client.get("/api/games", headers=_headers("alice-key")).json()
            bob_library = client.get("/api/games", headers=_headers("bob-key")).json()

            assert game_id in {game["id"] for game in alice_library["games"]}
            assert game_id not in {game["id"] for game in bob_library["games"]}

    def test_bob_cannot_read_alices_analysis_or_moves(self, monkeypatch):
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")
            for path in (
                f"/api/analysis/games/{game_id}",
                f"/api/analysis/games/{game_id}/moves",
                f"/api/analysis/games/{game_id}/progress",
                f"/api/analysis/games/{game_id}/critical-moments",
                f"/api/games/{game_id}/positions",
                f"/api/games/{game_id}/status",
            ):
                assert client.get(path, headers=_headers("bob-key")).status_code == 404, path
                assert client.get(path, headers=_headers("alice-key")).status_code == 200, path

    def test_the_report_and_intelligence_surfaces_are_scoped(self, monkeypatch):
        """Every game-reading surface inherits the one authorization decision."""
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")
            for path in (
                f"/api/intelligence/games/{game_id}/report/status",
                f"/api/intelligence/games/{game_id}/report",
                f"/api/intelligence/games/{game_id}/summary",
                f"/api/intelligence/games/{game_id}/accuracy",
                f"/api/training/games/{game_id}",
                f"/api/graph/games/{game_id}",
            ):
                assert client.get(path, headers=_headers("bob-key")).status_code == 404, path

    def test_bob_cannot_read_alices_graph_or_update_it(self, monkeypatch):
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")
            # The graph explorer must not reveal another caller's game...
            assert client.get(
                f"/api/graph/games/{game_id}", headers=_headers("bob-key")
            ).status_code == 404
            # ...and Bob may not materialize graph nodes for it either.
            assert client.post(
                f"/api/graph/games/{game_id}/update", headers=_headers("bob-key")
            ).status_code == 404

    def test_bob_cannot_delete_or_analyse_alices_game(self, monkeypatch):
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")

            delete = client.delete(f"/api/games/{game_id}", headers=_headers("bob-key"))
            assert delete.status_code == 404
            # The game still exists for Alice.
            assert client.get(f"/api/games/{game_id}", headers=_headers("alice-key")).status_code == 200

            analyze = client.post(
                f"/api/analysis/games/{game_id}", headers=_headers("bob-key")
            )
            assert analyze.status_code == 404

    def test_authorization_is_enforced_before_the_handler_runs(self, monkeypatch):
        """A denied request must not have a side effect (nothing was deleted)."""
        with _client(monkeypatch) as client:
            game_id = _import(client, "alice-key")
            client.delete(f"/api/games/{game_id}", headers=_headers("bob-key"))
            # Alice's game survived Bob's attempt.
            assert client.get(f"/api/games/{game_id}", headers=_headers("alice-key")).status_code == 200

    def test_open_mode_still_sees_the_whole_library(self, monkeypatch):
        """With no keys, behaviour is unchanged: every game is shared."""
        monkeypatch.delenv("ARGUS_API_KEYS", raising=False)
        get_settings.cache_clear()
        LIMITER.reset()
        GATE.reset()
        with TestClient(app) as client:
            game_id = _import(client, None)  # no header needed in open mode
            assert client.get(f"/api/games/{game_id}").status_code == 200
            library = client.get("/api/games").json()
            assert game_id in {game["id"] for game in library["games"]}
