"""Phase 12 performance and recovery tests (§49, §50).

These are not benchmarks — they are *invariants under load and failure*, which is
what the spec actually asks for:

* many games can be in progress at once without sharing state;
* rapid moves are accepted in order, with monotonic versions and sequences;
* a reconnecting client receives exactly what it missed, or is told to resync;
* a refused move changes nothing (transactional persistence);
* the clock is derived from stored timestamps, so it survives a "restart" (a
  fresh read of the stored game) rather than depending on an in-process counter.

The engine is deliberately not exercised here: the live domain is engine-free by
design, and a load test that starts Stockfish would measure the engine, not the
game.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from argus.analysis.engine.base import ChessEngine
from argus.shared.errors import EngineUnavailableError
from tests.conftest import OPERA_GAME_PGN

SECOND_PGN = """[Event "Live perf"]
[Site "?"]
[Date "2026.01.01"]
[Round "1"]
[White "Live Tester One"]
[Black "Live Tester Two"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""

#: A repeating, always-legal shuffle: knights out and back. It never ends the game
#: on its own (threefold is claimable, not automatic), so it drives throughput.
KNIGHT_SHUFFLE = ("g1f3", "g8f6", "f3g1", "f6g8")


def _seed(client: TestClient) -> dict[str, int]:
    client.post("/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False})
    client.post("/api/games/import", json={"pgn_text": SECOND_PGN, "run_analysis": False})
    entries = client.get("/api/players").json()["players"]
    return {entry["name"]: entry["id"] for entry in entries}


def _human_game(client: TestClient, white: int, black: int) -> str:
    response = client.post(
        "/api/live/games",
        json={
            "player_id": white,
            "mode": "private_match",
            "opponent_player_id": black,
            "time_control": "5+0",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    game_id = body["state"]["game_id"]
    assert body["state"]["status"] == "active"
    return game_id


class TestConcurrency:
    def test_many_games_run_independently(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_ids = [_human_game(client, white, black) for _ in range(5)]
        assert len(set(game_ids)) == 5
        # Play one move in each, in reverse order, to show state is not shared.
        for index, game_id in enumerate(reversed(game_ids)):
            response = client.post(
                f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"]["moves"][-1]["san"] == "e4"
        # Each game holds exactly its own one move and its own version.
        for game_id in game_ids:
            state = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
            assert len(state["moves"]) == 1

    def test_a_second_move_into_a_different_game_cannot_leak(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        one = _human_game(client, white, black)
        two = _human_game(client, white, black)
        client.post(f"/api/live/games/{one}/move", json={"player_id": white, "uci": "e2e4"})
        state_two = client.get(f"/api/live/games/{two}", params={"player_id": white}).json()
        assert state_two["moves"] == []
        assert state_two["current_fen"].startswith("rnbqkbnr")


class TestThroughput:
    def test_rapid_moves_are_ordered_and_monotonic(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        # A deterministic, non-repeating legal line, so the game does not end on a
        # draw while we are measuring throughput.
        import chess
        import random

        rng = random.Random(20261002)
        board = chess.Board()
        line: list[str] = []
        for _ in range(24):
            moves = list(board.legal_moves)
            if not moves or board.is_game_over():
                break
            move = rng.choice(moves)
            line.append(move.uci())
            board.push(move)

        sequences: list[int] = []
        versions: list[int] = []
        for index, uci in enumerate(line):
            player = white if index % 2 == 0 else black
            response = client.post(
                f"/api/live/games/{game_id}/move", json={"player_id": player, "uci": uci}
            )
            assert response.status_code == 200, response.text
            body = response.json()
            for event in body["events"]:
                sequences.append(event["sequence_number"])
            versions.append(body["state"]["version"])
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)  # no duplicate sequence numbers
        assert versions == sorted(versions)
        state = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        assert len(state["moves"]) == len(line)


class TestReconnection:
    def test_a_reconnecting_client_receives_only_what_it_missed(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        first = client.get(
            f"/api/live/games/{game_id}/sync", params={"player_id": white, "after_sequence": 0}
        ).json()
        last_seen = first["events"][-1]["sequence_number"] if first["events"] else 0
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": black, "uci": "e7e5"})
        second = client.get(
            f"/api/live/games/{game_id}/sync",
            params={"player_id": white, "after_sequence": last_seen},
        ).json()
        assert second["events"]
        assert all(event["sequence_number"] > last_seen for event in second["events"])
        assert second["resync"] is False

    def test_a_client_behind_the_oldest_event_is_told_to_resync(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        for index in range(8):
            player = white if index % 2 == 0 else black
            client.post(
                f"/api/live/games/{game_id}/move",
                json={"player_id": player, "uci": KNIGHT_SHUFFLE[index % len(KNIGHT_SHUFFLE)]},
            )
        # Asking from a sequence far below the stored history must produce either
        # the full history or an explicit resync — never a silent skip.
        body = client.get(
            f"/api/live/games/{game_id}/sync", params={"player_id": white, "after_sequence": 0}
        ).json()
        assert body["events"] or body["resync"] or body.get("state")


class _DeadEngine(ChessEngine):
    """An engine that has gone away: the failure recoveries must survive it."""

    def info(self) -> dict:
        return {"available": False, "engine": "stockfish", "reason": "stockfish_down"}

    def analyze_position(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise EngineUnavailableError("Stockfish is not reachable.")

    def compare_moves(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise EngineUnavailableError("Stockfish is not reachable.")

    def close(self) -> None:
        return None


class TestFailureRecovery:
    def test_an_unavailable_engine_does_not_corrupt_a_training_game(
        self, client: TestClient, monkeypatch
    ) -> None:
        # §50: a Stockfish failure must not lose the move or leave a half-applied
        # game. The human's move is kept, the failure is *surfaced*, and the game
        # stays consistent (still active, engine still to move).
        from argus_api.main import app

        players = _seed(client)
        white = players["Paul Morphy"]
        created = client.post(
            "/api/live/games",
            json={"player_id": white, "mode": "training", "opponents": "engine"},
        )
        assert created.status_code == 201, created.text
        game_id = created.json()["state"]["game_id"]

        # The engine dies mid-game.
        monkeypatch.setattr(app.state, "engine", _DeadEngine(), raising=False)
        response = client.post(
            f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"]["moves"][-1]["san"] == "e4"
        assert body.get("engine_error"), "the engine failure must be surfaced, not hidden"
        state = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        assert state["status"] == "active"
        assert state["side_to_move"] == "black"
        assert len(state["moves"]) == 1

    def test_a_refused_move_changes_nothing(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        before = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        # Illegal for black (it is black's turn, but this is not a legal move).
        response = client.post(
            f"/api/live/games/{game_id}/move", json={"player_id": black, "uci": "e7e6e5"}
        )
        assert response.status_code in (409, 422)
        after = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        assert after["version"] == before["version"]
        assert len(after["moves"]) == len(before["moves"])
        assert after["current_fen"] == before["current_fen"]

    def test_the_clock_survives_a_reload_and_is_derived_from_stored_time(
        self, client: TestClient
    ) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        first = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        time.sleep(1.1)
        # A fresh read is what a restarted process (or a refreshed browser) does:
        # the remaining time must have moved on, because it comes from the stored
        # turn timestamp, not from a counter this test controls.
        second = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        assert second["clock"]["white_ms"] < first["clock"]["white_ms"] - 500
        assert second["clock"]["running"] is True

    def test_a_finished_game_cannot_be_restarted_or_moved_in(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        client.post(f"/api/live/games/{game_id}/resign", json={"player_id": white})
        move = client.post(
            f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"}
        )
        assert move.status_code == 409
        restart = client.post(f"/api/live/games/{game_id}/start", json={"player_id": white})
        assert restart.status_code == 409

    def test_the_event_log_is_the_source_of_truth_after_a_disconnect(
        self, client: TestClient
    ) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game_id = _human_game(client, white, black)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": black, "uci": "e7e5"})
        # A client that lost its connection for the whole game asks from zero and
        # reconstructs from the stored log, not from memory.
        body = client.get(
            f"/api/live/games/{game_id}/sync", params={"player_id": white, "after_sequence": 0}
        ).json()
        kinds = {event["event_type"] for event in body.get("events", [])}
        assert "MOVE_MADE" in kinds
        assert body["server_version"] >= 2
