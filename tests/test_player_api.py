"""Phase 5 API tests: identity, profile endpoints, caching, rebuild, minimum data.

Engine-backed tests analyse real games with the existing pipeline (depth 6), so
the profiles asserted on here are built from genuine engine output. Player names
in the fixtures are relabelled (headers only, moves untouched) so that two real
games belong to one test player — the chess is real, the identity is the thing
under test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus.player_intelligence import FEATURE_VERSION, METHODOLOGY_VERSION, PROFILE_VERSION

from argus_api.db.models import Player
from argus_api.db.repository import (
    backfill_player_identities,
    get_player_profile_record,
    merge_duplicate_players,
    normalize_identity_key,
    prune_orphan_players,
)
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN

TEST_PLAYER = "Caissa Tester"


def _rename(pgn: str, *, white: str | None = None, black: str | None = None) -> str:
    """Replace/insert PGN player headers (moves are untouched)."""
    lines = pgn.splitlines()
    out: list[str] = []
    seen_white = seen_black = False
    for line in lines:
        if line.startswith("[White "):
            out.append(f'[White "{white}"]' if white else line)
            seen_white = True
        elif line.startswith("[Black "):
            out.append(f'[Black "{black}"]' if black else line)
            seen_black = True
        else:
            out.append(line)
    if white and not seen_white:
        out.insert(0, f'[White "{white}"]')
    if black and not seen_black:
        out.insert(0, f'[Black "{black}"]')
    return "\n".join(out)


def _import_and_analyze(client: TestClient, pgn: str) -> str:
    game_id = client.post("/api/games/import", json={"pgn_text": pgn, "run_analysis": False}).json()[
        "game_id"
    ]
    started = client.post(f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 2})
    assert started.status_code == 202, started.text
    progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
    assert progress["status"] == "completed", progress
    return game_id


def _player_id_for(client: TestClient, name: str) -> str:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not in {players}"
    return match["id"]


class TestPlayerIdentity:
    def test_identity_key_normalizes_case_and_whitespace(self) -> None:
        assert normalize_identity_key("  Piyush1206 ") == "piyush1206"
        assert normalize_identity_key("Piyush1206") == normalize_identity_key("piyush1206")

    def test_backfill_is_idempotent(self, client: TestClient) -> None:
        _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        with app.state.session_factory.session_scope() as session:
            # Force a legacy row with no identity key, then backfill it.
            player = session.query(Player).filter(Player.name == TEST_PLAYER).one()
            player.identity_key = None
            session.commit()
        with app.state.session_factory.session_scope() as session:
            assert backfill_player_identities(session) == 1
            assert backfill_player_identities(session) == 0  # idempotent
        with app.state.session_factory.session_scope() as session:
            player = session.query(Player).filter(Player.name == TEST_PLAYER).one()
            assert player.identity_key == normalize_identity_key(TEST_PLAYER)

    def test_same_player_in_both_colours_is_one_row(self, client: TestClient) -> None:
        _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        _import_and_analyze(client, _rename(SCHOLARS_MATE_PGN, white="Rookie", black=TEST_PLAYER))
        players = client.get("/api/players").json()["players"]
        matches = [entry for entry in players if entry["name"] == TEST_PLAYER]
        assert len(matches) == 1, players
        assert matches[0]["games"] == 2
        assert matches[0]["analyzed_games"] == 2
        assert matches[0]["wins"] == 1 and matches[0]["losses"] == 1

    def test_merge_duplicates_keeps_every_game(self, client: TestClient) -> None:
        game_id = _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        with app.state.session_factory.session_scope() as session:
            keeper = session.query(Player).filter(Player.name == TEST_PLAYER).one()
            keeper.identity_key = normalize_identity_key(TEST_PLAYER)
            # A legacy duplicate spelling with no games of its own.
            session.add(Player(name="CAISSA  TESTER", identity_key=None))
            session.commit()
        with app.state.session_factory.session_scope() as session:
            merged = merge_duplicate_players(session)
        assert merged, "expected one duplicate group to be merged"
        assert merged[0]["kept"]
        # The game is still present and still belongs to the surviving player.
        assert client.get(f"/api/games/{game_id}").status_code == 200
        players = client.get("/api/players").json()["players"]
        tester_rows = [entry for entry in players if normalize_identity_key(entry["name"]) ==
                       normalize_identity_key(TEST_PLAYER)]
        assert len(tester_rows) == 1
        assert tester_rows[0]["games"] == 1

    def test_prune_removes_only_gameless_players(self, client: TestClient) -> None:
        _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        with app.state.session_factory.session_scope() as session:
            orphan = Player(name="Ghost Player", identity_key="ghost player")
            session.add(orphan)
            session.commit()
            orphan_id = orphan.id
        with app.state.session_factory.session_scope() as session:
            removed = prune_orphan_players(session)
        assert any(str(orphan_id) in entry for entry in removed)
        players = client.get("/api/players").json()["players"]
        names = {entry["name"] for entry in players}
        assert "Ghost Player" not in names
        assert TEST_PLAYER in names  # a player with games is never pruned

    def test_list_is_empty_before_any_import(self, client: TestClient) -> None:
        body = client.get("/api/players").json()
        assert body == {"players": [], "count": 0}


@pytest.mark.engine
class TestPlayerProfileApi:
    @pytest.fixture()
    def player_with_two_games(self, client: TestClient) -> tuple[str, str, str]:
        win = _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        loss = _import_and_analyze(
            client, _rename(SCHOLARS_MATE_PGN, white="Rookie", black=TEST_PLAYER)
        )
        return _player_id_for(client, TEST_PLAYER), win, loss

    def test_profile_reports_measured_aggregates(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        player_id, _win, _loss = player_with_two_games
        body = client.get(f"/api/players/{player_id}").json()

        assert body["player_id"] == player_id
        assert body["display_name"] == TEST_PLAYER
        assert body["profile_version"] == PROFILE_VERSION
        assert body["methodology_version"] == METHODOLOGY_VERSION
        assert body["analyzed_games"] == 2
        assert body["imported_games"] == 2
        assert body["excluded_games"] == 0
        assert body["sufficient_data"] is True
        assert body["coverage"] == "limited"

        games = body["games"]
        assert games["wins"] + games["draws"] + games["losses"] == games["analyzed_games"]
        assert games["wins"] == 1 and games["losses"] == 1
        assert games["accuracy_sample"] == 2
        assert games["average_accuracy"] is not None

        # Colour split is present with its own sample sizes.
        colours = {entry["color"]: entry for entry in body["by_color"]}
        assert colours["white"]["games"] == 1
        assert colours["black"]["games"] == 1

        # Every insight carries a claim level, a sample and evidence.
        assert body["insights"]
        for insight in body["insights"]:
            assert insight["claim_level"] in {"insufficient", "observation", "pattern", "tendency"}
            assert insight["methodology_version"] == METHODOLOGY_VERSION
            if insight["claim_level"] in {"pattern", "tendency"}:
                assert insight["evidence"], insight["id"]

        # No tendency may be claimed from two games.
        assert all(i["claim_level"] != "tendency" for i in body["insights"])

    def test_profile_is_cached_then_rebuilt_on_demand(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        player_id, _win, _loss = player_with_two_games
        first = client.get(f"/api/players/{player_id}").json()
        assert first["cache"]["hit"] is False

        second = client.get(f"/api/players/{player_id}").json()
        assert second["cache"]["hit"] is True
        assert second["cache"]["signature"] == first["cache"]["signature"]

        rebuilt = client.post(f"/api/players/{player_id}/rebuild").json()
        assert rebuilt["cache"]["hit"] is False
        assert rebuilt["analyzed_games"] == 2

    def test_snapshot_from_an_older_methodology_is_not_served(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        """A stored reading must not outlive the methodology that produced it."""
        player_id, _win, _loss = player_with_two_games
        first = client.get(f"/api/players/{player_id}").json()
        assert first["cache"]["hit"] is False
        assert first["methodology_version"] == METHODOLOGY_VERSION

        # Pretend the snapshot was computed by an earlier methodology: the games
        # are unchanged, so the input signature alone would still match.
        session_factory = client.app.state.session_factory
        with session_factory.session_scope() as session:
            record = get_player_profile_record(session, int(player_id), profile_version=PROFILE_VERSION)
            assert record is not None
            record.methodology_version = "0.1"
            session.commit()

        second = client.get(f"/api/players/{player_id}").json()
        assert second["cache"]["hit"] is False, "a stale methodology must force a rebuild"
        assert second["methodology_version"] == METHODOLOGY_VERSION

    def test_snapshot_is_persisted_and_marked_when_a_new_game_arrives(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        player_id, _win, _loss = player_with_two_games
        before = client.get(f"/api/players/{player_id}").json()
        assert before["analyzed_games"] == 2
        signature_before = before["cache"]["signature"]

        # A new analyzed game for the same player must invalidate the snapshot.
        _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        after = client.get(f"/api/players/{player_id}").json()
        assert after["analyzed_games"] == 3
        assert after["cache"]["signature"] != signature_before
        assert after["cache"]["hit"] is False

        # Stored profiles are visible through the profile table.
        from argus_api.db.models import PlayerProfileRecord

        with app.state.session_factory.session_scope() as session:
            rows = session.query(PlayerProfileRecord).all()
            assert rows and rows[0].payload["player_id"] == player_id
            assert rows[0].coverage in {"insufficient", "limited", "moderate", "robust"}

    def test_unanalyzed_games_are_counted_not_dropped(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        player_id, _win, _loss = player_with_two_games
        # Import a third game for the same player but never analyse it.
        client.post(
            "/api/games/import",
            json={"pgn_text": _rename(OPERA_GAME_PGN, white=TEST_PLAYER), "run_analysis": False},
        )
        body = client.get(f"/api/players/{player_id}").json()
        assert body["imported_games"] == 3
        assert body["analyzed_games"] == 2
        assert body["excluded_games"] == 1
        assert len(body["excluded_game_ids"]) == 1

    def test_insights_evidence_and_features_endpoints(
        self, client: TestClient, player_with_two_games: tuple[str, str, str]
    ) -> None:
        player_id, _win, _loss = player_with_two_games

        insights = client.get(f"/api/players/{player_id}/insights").json()
        assert insights["player_id"] == player_id
        assert insights["coverage"] == "limited"
        assert insights["insights"]

        first_id = insights["insights"][0]["id"]
        evidence = client.get(f"/api/players/{player_id}/evidence", params={"insight_id": first_id}).json()
        assert len(evidence["insights"]) == 1
        for ref in evidence["insights"][0]["evidence"]:
            # Evidence is a real game + ply the UI can open.
            assert client.get(f"/api/games/{ref['game_id']}").status_code == 200
            assert ref["ply"] >= 0

        features = client.get(f"/api/players/{player_id}/features").json()
        assert features["games_analyzed"] == 2
        assert features["feature_version"] == FEATURE_VERSION
        assert features["data_range"][0] is not None
        for feature in features["features"]:
            assert feature["definition"]
            assert feature["user_specific"] is True
            assert feature["training_eligible"] is False

    def test_unknown_player_is_404(self, client: TestClient) -> None:
        assert client.get("/api/players/99999").status_code == 404
        assert client.post("/api/players/99999/rebuild").status_code == 404

    def test_one_analyzed_game_is_an_honest_insufficient_state(self, client: TestClient) -> None:
        _import_and_analyze(client, _rename(OPERA_GAME_PGN, white=TEST_PLAYER))
        player_id = _player_id_for(client, TEST_PLAYER)
        body = client.get(f"/api/players/{player_id}").json()

        assert body["analyzed_games"] == 1
        assert body["sufficient_data"] is False
        assert body["coverage"] == "insufficient"
        assert body["insights"] == []
        assert body["chess_dna"]["dimensions"] == []
        assert any("Not enough analyzed games" in note for note in body["notes"])
        # The games section is deliberately empty rather than zero-filled.
        assert body["games"]["analyzed_games"] == 0
        assert body["games"]["average_accuracy"] is None
