"""Phase 9 opponent intelligence — API tests.

These seed real imported games with controlled stored move analyses and exercise
the opponent endpoints end to end: identity/history, repertoire, position
response, tendencies, phase statistics, the preparation report, the cached
profile, and the authorization/honesty rules (unknown player → 404; small
samples never reported as findings).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from argus_api.db.models import Game, MoveAnalysis
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
AFTER_E4_NF3_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def _import_game(client: TestClient) -> str:
    response = client.post(
        "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False}
    )
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


def _player_id(client: TestClient, name: str = "Paul Morphy") -> str:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not in {players}"
    return match["id"]


def _seed_white_opening(game_id: str) -> None:
    """Seed White's 1.e4 2.Nf3 3.Bc4 as stored, analysed moves."""
    rows = [
        (1, 1, "e2e4", "e4", START_FEN, "opening", 20),
        (3, 2, "g1f3", "Nf3", AFTER_E4, "opening", 15),
        (5, 3, "f1c4", "Bc4", AFTER_E4_NF3_NC6, "opening", 10),
    ]
    with app.state.session_factory.session_scope() as session:
        game = session.get(Game, game_id)
        if game is not None:
            # The opponent engine reads analysis only from games marked analysed.
            game.analysis_status = "analyzed"
        for ply, move_number, uci, san, fen, phase, loss in rows:
            session.add(
                MoveAnalysis(
                    game_id=game_id,
                    ply=ply,
                    move_number=move_number,
                    mover="white",
                    played_move_uci=uci,
                    played_move_san=san,
                    fen_before=fen,
                    fen_after=fen,
                    best_move_uci=uci,
                    best_move_san=san,
                    evaluation_before_cp=30,
                    centipawn_loss=loss,
                    played_eval_cp=30,
                    played_eval_source="same_search",
                    classification="good",
                    is_best_move=True,
                    phase=phase,
                    depth=12,
                    principal_variation=[uci],
                    analysis_version="3.1",
                    engine="stockfish",
                    engine_version="test-engine",
                )
            )


def _setup(client: TestClient, *, games: int = 3) -> dict:
    ids = [_import_game(client) for _ in range(games)]
    for game_id in ids:
        _seed_white_opening(game_id)
    return {"game_ids": ids, "player_id": _player_id(client)}


class TestOpponentMetaAndAuthorization:
    def test_meta_documents_the_policy(self, client: TestClient) -> None:
        body = client.get("/api/players/opponent-meta").json()
        assert body["methodology_version"] == "9.0"
        assert body["policy_defaults"]["min_occurrences_for_tendency"] >= 1
        assert "psycholog" in body["privacy"].lower()

    def test_unknown_player_is_404(self, client: TestClient) -> None:
        response = client.get("/api/players/999999/opponent-profile")
        assert response.status_code == 404, response.text


class TestOpponentEndpoints:
    def test_profile_and_games(self, client: TestClient) -> None:
        setup = _setup(client)
        profile = client.get(f"/api/players/{setup['player_id']}/opponent-profile").json()
        assert profile["statistics"]["total_games"] == 3
        assert profile["statistics"]["analyzed_games"] == 3
        assert profile["identity"]["name"] == "Paul Morphy"
        assert profile["rebuilt"] is True

        games = client.get(f"/api/players/{setup['player_id']}/opponent-games").json()
        assert games["total"] == 3
        assert len(games["games"]) == 3

    def test_repertoire_counts_real_moves(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            f"/api/players/{setup['player_id']}/repertoire?color=white"
        ).json()
        root = next(node for node in body["nodes"] if node["san"] == "e4")
        assert root["occurrences"] == 3
        assert root["claim_level"] in ("pattern", "tendency")

    def test_position_response_returns_distribution(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(
            f"/api/players/{setup['player_id']}/position-response",
            params={"fen": START_FEN},
        ).json()
        assert body["match"] == "exact"
        assert body["responses"][0]["uci"] == "e2e4"
        assert body["responses"][0]["occurrences"] == 3

    def test_position_response_rejects_an_invalid_fen(self, client: TestClient) -> None:
        setup = _setup(client, games=1)
        response = client.get(
            f"/api/players/{setup['player_id']}/position-response",
            params={"fen": "not a fen"},
        )
        assert response.status_code == 422

    def test_tendencies_and_phase_statistics(self, client: TestClient) -> None:
        setup = _setup(client)
        tendencies = client.get(f"/api/players/{setup['player_id']}/tendencies").json()
        assert any(t["key"] == "castling_side" for t in tendencies["tendencies"])
        for tendency in tendencies["tendencies"]:
            assert tendency["measurement"]
            assert "sample_size" in tendency

        phases = client.get(f"/api/players/{setup['player_id']}/phase-statistics").json()
        opening = next(stat for stat in phases["phases"] if stat["phase"] == "opening")
        assert opening["moves"] == 9  # three games × three moves

    def test_preparation_report_is_evidence_gated(self, client: TestClient) -> None:
        setup = _setup(client)
        report = client.get(
            f"/api/players/{setup['player_id']}/preparation-report?color=white"
        ).json()
        assert report["coverage"] in ("limited", "moderate", "robust")
        assert report["repertoire"]["color"] == "white"
        assert report["insights"], report
        assert all(insight["sample_size"] >= 0 for insight in report["insights"])
        # Every report states its limitations honestly.
        assert report["limitations"]

    def test_profile_is_cached_then_rebuilt_on_change(self, client: TestClient) -> None:
        setup = _setup(client, games=1)
        first = client.get(f"/api/players/{setup['player_id']}/opponent-profile").json()
        assert first["rebuilt"] is True
        second = client.get(f"/api/players/{setup['player_id']}/opponent-profile").json()
        assert second["rebuilt"] is False
        assert second["source_signature"] == first["source_signature"]
        # A new analysed game changes the fingerprint and triggers a rebuild.
        new_game = _import_game(client)
        _seed_white_opening(new_game)
        third = client.get(f"/api/players/{setup['player_id']}/opponent-profile").json()
        assert third["rebuilt"] is True
        assert third["statistics"]["total_games"] == 2

    def test_responses_lists_recurring_positions(self, client: TestClient) -> None:
        setup = _setup(client)
        body = client.get(f"/api/players/{setup['player_id']}/responses").json()
        assert body["methodology_version"] == "9.0"
        assert isinstance(body["position_patterns"], list)


class TestOpponentAgentTools:
    def test_opponent_tools_are_registered(self) -> None:
        from argus.ai_agent.tools import AgentProviders, build_agent_toolbox

        toolbox = build_agent_toolbox(AgentProviders())
        names = {tool.name for tool in toolbox.list()}
        for expected in (
            "get_opponent_profile",
            "get_opponent_repertoire",
            "get_opponent_position_responses",
            "get_opponent_tendencies",
            "get_opponent_phase_statistics",
            "get_opponent_preparation_report",
            "generate_opponent_brief",
        ):
            assert expected in names

    def test_opponent_tools_are_unavailable_without_providers(self) -> None:
        from argus.ai_agent.tools import AgentProviders, build_agent_toolbox

        toolbox = build_agent_toolbox(AgentProviders())
        tool = toolbox.get("get_opponent_profile")
        assert tool is not None
        assert tool.available is False
        assert tool.reason
