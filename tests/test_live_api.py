"""Phase 12 API tests: live games end to end over HTTP and the WebSocket.

These run the real stack — the real database, the real service, the real routes
and a real WebSocket handshake — because the properties that keep live play fair
are exactly the ones unit tests cannot check:

* the client sends an *intent*, never a position, clock, result or version that
  is trusted;
* authorization precedes every read and every action, and a refused action
  changes nothing;
* a competitive game can never receive an engine move, at any surface;
* a client that missed events can retrieve exactly the difference, or is told to
  resync;
* the end of a game reaches the library so the Phase 3–11 pipeline can run.

The point is not that the board can move a piece; it is that only the server
decides that it may.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import OPERA_GAME_PGN

#: A second, short real game, so the tests have three distinct players to seat.
SECOND_PGN = """[Event "Live API test"]
[Site "?"]
[Date "2026.01.01"]
[Round "1"]
[White "Live Tester One"]
[Black "Live Tester Two"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _import(client: TestClient, pgn: str) -> None:
    response = client.post("/api/games/import", json={"pgn_text": pgn, "run_analysis": False})
    assert response.status_code == 200, response.text


def _players(client: TestClient) -> dict[str, int]:
    entries = client.get("/api/players").json()["players"]
    return {entry["name"]: entry["id"] for entry in entries}


def _seed(client: TestClient) -> dict[str, int]:
    """Import two real games and return the named player ids."""
    _import(client, OPERA_GAME_PGN)
    _import(client, SECOND_PGN)
    return _players(client)


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "player_id": overrides.pop("player_id"),
        "mode": "private_match",
        "colour": "white",
        "time_control": "10+5",
    }
    payload.update(overrides)
    response = client.post("/api/live/games", json=payload)
    assert response.status_code == 201, response.text
    # Every mutation returns the same {events, state} envelope; the state is what
    # a client renders.
    body = response.json()
    assert "state" in body and "events" in body
    return body["state"]


def _state(client: TestClient, game_id: str, player_id: int) -> dict:
    response = client.get(f"/api/live/games/{game_id}", params={"player_id": player_id})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# methodology, metrics, creation
# ---------------------------------------------------------------------------


class TestMethodAndMetrics:
    def test_method_describes_modes_states_and_the_fair_play_rule(self, client: TestClient) -> None:
        body = client.get("/api/live/method").json()
        assert "private_match" in body["modes"] or "private_match" in str(body["modes"])
        assert body["analysis_modes"]
        # The fair-play statement is part of the contract, not a comment.
        assert body.get("fair_play") or body.get("fairplay")

    def test_metrics_are_counts_only(self, client: TestClient) -> None:
        body = client.get("/api/live/metrics").json()
        assert "counters" in body
        assert body["counters"].get("live_games_created", 0) >= 0
        # No game content ever appears in the metrics: only counters and gauges.
        assert set(body) <= {"counters", "engine", "connections", "note"}
        assert "current_fen" not in body and "pgn" not in body


class TestCreation:
    def test_a_game_is_created_waiting_for_an_opponent(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        assert game["status"] == "waiting"
        seats = game["seats"]
        # The creator is seated; the other seat is open to whoever joins.
        assert seats["white"] == "human"
        assert seats["black"] == "open"

    def test_an_unknown_player_cannot_create_a_game(self, client: TestClient) -> None:
        _seed(client)
        response = client.post(
            "/api/live/games", json={"player_id": 999999, "mode": "private_match"}
        )
        assert response.status_code == 404

    def test_a_known_opponent_seats_both_and_readies_the_game(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(
            client,
            player_id=players["Paul Morphy"],
            opponent_player_id=players["Live Tester One"],
        )
        # Two seats filled means there is nobody left to wait for, so it is ready
        # (and, because a ready game is startable, it is started).
        assert game["status"] in ("ready", "active")

    def test_the_open_game_limit_is_refused_cleanly(self, client: TestClient) -> None:
        # §45: a player may not farm unlimited live games. The refusal is a 422
        # with a stable code, not a 500 — the limit exists to protect the server.
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        for _ in range(8):
            _create(client, player_id=white, opponent_player_id=black)
        response = client.post(
            "/api/live/games",
            json={"player_id": white, "mode": "private_match", "opponent_player_id": black},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "live_game_limit"

    def test_an_engine_opponent_is_allowed_only_in_training(self, client: TestClient) -> None:
        players = _seed(client)
        response = client.post(
            "/api/live/games",
            json={
                "player_id": players["Paul Morphy"],
                "mode": "private_match",
                "opponents": "engine",
            },
        )
        # A competitive game cannot quietly seat an engine.
        assert response.status_code == 422, response.text

    def test_a_training_game_may_seat_the_engine(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(
            client, player_id=players["Paul Morphy"], mode="training", opponents="engine"
        )
        seat_kinds = set(game["seats"].values())
        assert "engine" in seat_kinds
        # A ready training game starts itself.
        assert game["analysis_mode"] != "no_analysis"


# ---------------------------------------------------------------------------
# authorization and privacy
# ---------------------------------------------------------------------------


class TestPrivacy:
    def test_a_stranger_cannot_read_a_private_game(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        response = client.get(
            f"/api/live/games/{game['game_id']}",
            params={"player_id": players["Live Tester One"]},
        )
        assert response.status_code == 404

    def test_a_public_game_is_readable_by_a_spectator(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"], visibility="public")
        response = client.get(
            f"/api/live/games/{game['game_id']}",
            params={"player_id": players["Live Tester One"]},
        )
        assert response.status_code == 200
        assert response.json()["viewer"] == "spectator"

    def test_a_spectator_cannot_move(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(
            client,
            player_id=players["Paul Morphy"],
            opponent_player_id=players["Live Tester One"],
            visibility="public",
        )
        response = client.post(
            f"/api/live/games/{game['game_id']}/move",
            json={"player_id": players["Live Tester Two"], "uci": "e2e4"},
        )
        assert response.status_code == 404

    def test_joining_without_an_invitation_is_refused(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        response = client.post(
            f"/api/live/games/{game['game_id']}/join",
            json={"player_id": players["Live Tester One"]},
        )
        assert response.status_code == 404

    def test_a_valid_invitation_lets_a_named_player_join(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        token = client.get(
            f"/api/live/games/{game['game_id']}/invite",
            params={"player_id": players["Paul Morphy"]},
        ).json()["invite_token"]
        response = client.post(
            f"/api/live/games/{game['game_id']}/join",
            json={"player_id": players["Live Tester One"], "invite_token": token},
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"]["status"] == "ready"

    def test_rotating_an_invitation_invalidates_the_old_one(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        old = client.get(
            f"/api/live/games/{game['game_id']}/invite",
            params={"player_id": players["Paul Morphy"]},
        ).json()["invite_token"]
        rotated = client.post(
            f"/api/live/games/{game['game_id']}/invite/rotate",
            json={"player_id": players["Paul Morphy"]},
        ).json()["invite_token"]
        assert rotated != old
        stale = client.post(
            f"/api/live/games/{game['game_id']}/join",
            json={"player_id": players["Live Tester One"], "invite_token": old},
        )
        assert stale.status_code == 404

    def test_the_owner_controls_visibility(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(client, player_id=players["Paul Morphy"])
        response = client.post(
            f"/api/live/games/{game['game_id']}/visibility",
            json={"player_id": players["Paul Morphy"], "visibility": "unlisted"},
        )
        assert response.status_code == 200
        assert response.json()["visibility"] == "unlisted"


# ---------------------------------------------------------------------------
# the move pipeline
# ---------------------------------------------------------------------------


def _started_game(client: TestClient) -> tuple[str, int, int]:
    """A started competitive game: (game_id, white_id, black_id)."""
    players = _seed(client)
    game = _create(
        client,
        player_id=players["Paul Morphy"],
        opponent_player_id=players["Live Tester One"],
    )
    game_id = game["game_id"]
    if game["status"] != "active":
        client.post(f"/api/live/games/{game_id}/start", json={"player_id": players["Paul Morphy"]})
    return game_id, players["Paul Morphy"], players["Live Tester One"]


class TestMovePipeline:
    def test_a_legal_move_is_applied_and_returns_the_new_state(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        response = client.post(
            f"/api/live/games/{game_id}/move",
            json={"player_id": white, "uci": "e2e4"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"]["moves"][-1]["san"] == "e4"
        assert body["state"]["side_to_move"] == "black"
        assert body["state"]["version"] >= 1

    def test_an_illegal_move_is_refused_and_changes_nothing(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        before = _state(client, game_id, white)["version"]
        response = client.post(
            f"/api/live/games/{game_id}/move",
            json={"player_id": white, "uci": "a1a8"},
        )
        assert response.status_code == 422
        assert _state(client, game_id, white)["version"] == before

    def test_moving_out_of_turn_is_refused(self, client: TestClient) -> None:
        game_id, _, black = _started_game(client)
        response = client.post(
            f"/api/live/games/{game_id}/move",
            json={"player_id": black, "uci": "e7e5"},
        )
        assert response.status_code == 409

    def test_a_stale_version_is_refused_and_reports_the_current_one(self, client: TestClient) -> None:
        game_id, white, black = _started_game(client)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        stale = client.post(
            f"/api/live/games/{game_id}/move",
            json={"player_id": black, "uci": "e7e5", "expected_version": 0},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["details"]["current_version"] >= 1

    def test_the_server_ignores_a_client_supplied_position(self, client: TestClient) -> None:
        # There is no FEN field on the request at all; extra keys are ignored and
        # the board is rebuilt from stored state.
        game_id, white, _ = _started_game(client)
        response = client.post(
            f"/api/live/games/{game_id}/move",
            json={"player_id": white, "uci": "e2e4", "fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
        )
        assert response.status_code == 200
        assert response.json()["state"]["current_fen"].startswith("rnbqkbnr")


# ---------------------------------------------------------------------------
# draws, resignation, clock
# ---------------------------------------------------------------------------


class TestEndings:
    def test_a_move_declines_a_pending_draw_and_then_resignation_ends_it(
        self, client: TestClient
    ) -> None:
        game_id, white, black = _started_game(client)
        client.post(f"/api/live/games/{game_id}/draw/offer", json={"player_id": white})
        assert _state(client, game_id, white)["draw_offer"] == "white"
        # A move is a decline.
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        assert _state(client, game_id, white)["draw_offer"] is None
        resigned = client.post(f"/api/live/games/{game_id}/resign", json={"player_id": black})
        assert resigned.status_code == 200
        assert resigned.json()["state"]["status"] == "resigned"
        assert resigned.json()["state"]["result"] == "1-0"

    def test_accepting_an_opponent_draw_ends_it(self, client: TestClient) -> None:
        game_id, white, black = _started_game(client)
        client.post(f"/api/live/games/{game_id}/draw/offer", json={"player_id": white})
        accepted = client.post(f"/api/live/games/{game_id}/draw/accept", json={"player_id": black})
        assert accepted.status_code == 200
        assert accepted.json()["state"]["result"] == "1/2-1/2"

    def test_a_clock_is_running_and_derived_from_server_time(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        state = _state(client, game_id, white)
        assert state["status"] == "active"
        clock = state["clock"]
        assert clock["running"] is True
        assert clock["white_ms"] <= state["clock_config"]["base_ms"]


# ---------------------------------------------------------------------------
# sync and PGN
# ---------------------------------------------------------------------------


class TestSyncAndPgn:
    def test_sync_returns_the_events_a_client_missed(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        body = client.get(
            f"/api/live/games/{game_id}/sync", params={"player_id": white, "after_sequence": 0}
        ).json()
        assert body["events"]
        sequences = [event["sequence_number"] for event in body["events"]]
        assert sequences == sorted(sequences)
        assert body["resync"] in (True, False)

    def test_pgn_is_exported_with_the_moves_played(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        body = client.get(f"/api/live/games/{game_id}/pgn", params={"player_id": white}).json()
        assert "e4" in body["pgn"]
        # Standard PGN: no engine evaluation leaks into it.
        assert "eval" not in body["pgn"].lower()


# ---------------------------------------------------------------------------
# the in-game coach and fair play (§20, §54)
# ---------------------------------------------------------------------------


class TestInGameCoach:
    def test_a_competitive_game_never_gets_an_engine_move(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        body = client.post(
            f"/api/live/games/{game_id}/coach",
            json={"player_id": white, "question": "what's the best move?"},
        ).json()
        assert body["kind"] == "hint_only"
        # The reply must not contain a move, an evaluation or engine lines.
        text = str(body).lower()
        assert "best_move_uci" not in text
        assert "analysis" not in body
        assert body["permissions"]["may_give_engine_moves"] is False

    def test_a_sandbox_game_may_receive_engine_analysis(self, client: TestClient) -> None:
        players = _seed(client)
        game = _create(
            client,
            player_id=players["Paul Morphy"],
            mode="sandbox",
            opponents="engine",
        )
        game_id = game["game_id"]
        body = client.post(
            f"/api/live/games/{game_id}/coach",
            json={"player_id": players["Paul Morphy"], "question": "analyse"},
        ).json()
        assert body["kind"] == "analysis_permitted"

    def test_the_coach_refuses_a_stranger(self, client: TestClient) -> None:
        game_id, _, _ = _started_game(client)
        players = _players(client)
        response = client.post(
            f"/api/live/games/{game_id}/coach",
            json={"player_id": players["Live Tester Two"]},
        )
        assert response.status_code == 404

    def test_asking_the_coach_increments_the_coach_counter(self, client: TestClient) -> None:
        before = client.get("/api/live/metrics").json()
        counters_before = before.get("counters", before)
        game_id, white, _ = _started_game(client)
        client.post(f"/api/live/games/{game_id}/coach", json={"player_id": white})
        after = client.get("/api/live/metrics").json()
        counters_after = after.get("counters", after)
        assert counters_after.get("coach_requests", 0) > counters_before.get("coach_requests", 0)


# ---------------------------------------------------------------------------
# real-time analysis (§23–§28)
# ---------------------------------------------------------------------------


def _training_game_vs_engine(client: TestClient) -> tuple[str, int]:
    players = _seed(client)
    game = _create(
        client, player_id=players["Paul Morphy"], mode="training", opponents="engine"
    )
    return game["game_id"], players["Paul Morphy"]


class TestLiveAnalysis:
    def test_a_training_move_produces_stored_analysis_and_a_coach_message(
        self, client: TestClient
    ) -> None:
        game_id, player = _training_game_vs_engine(client)
        moved = client.post(
            f"/api/live/games/{game_id}/move", json={"player_id": player, "uci": "e2e4"}
        )
        assert moved.status_code == 200, moved.text
        # Background tasks run inside the TestClient request, so by now the
        # analysis has been stored on the game's own event stream.
        body = client.get(
            f"/api/live/games/{game_id}/analysis", params={"player_id": player}
        ).json()
        assert body["analysis_permitted"] is True
        assert body["count"] >= 1, body
        # A coach message accompanies a permitted analysis.
        assert body["coach_messages"], body
        first = body["analyses"][0]
        assert first["ply"] == 1
        assert first["analysis"] is not None

    def test_a_competitive_game_stores_no_live_analysis(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        client.post(f"/api/live/games/{game_id}/move", json={"player_id": white, "uci": "e2e4"})
        body = client.get(
            f"/api/live/games/{game_id}/analysis", params={"player_id": white}
        ).json()
        assert body["analysis_permitted"] is False
        assert body["count"] == 0
        assert body["coach_messages"] == []


# ---------------------------------------------------------------------------
# the post-game pipeline (§29–§31)
# ---------------------------------------------------------------------------


class TestPostgame:
    def test_finishing_a_live_game_reaches_the_library_and_extracts_training(
        self, client: TestClient
    ) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game = _create(
            client, player_id=white, opponent_player_id=black, time_control="5+0"
        )
        game_id = game["game_id"]
        # Fool's mate: two real blunders by White, so the analysis has genuine
        # mistakes to turn into exercises for the owner.
        for player, uci in (
            (white, "f2f3"),
            (black, "e7e5"),
            (white, "g2g4"),
            (black, "d8h4"),
        ):
            response = client.post(
                f"/api/live/games/{game_id}/move", json={"player_id": player, "uci": uci}
            )
            assert response.status_code == 200, response.text

        state = client.get(f"/api/live/games/{game_id}", params={"player_id": white}).json()
        assert state["status"] == "finished"
        assert state["result_reason"] == "checkmate"
        # The post-game pipeline ran as a background task: the game reached the
        # library AND its training material was extracted automatically (§29/§31).
        assert state["library_game_id"], "the live game must reach the library"
        positions = client.get(
            "/api/training/positions", params={"player_id": white}
        ).json()
        assert positions["count"] >= 1, positions

    def test_the_library_game_is_analysed(self, client: TestClient) -> None:
        players = _seed(client)
        white, black = players["Paul Morphy"], players["Live Tester One"]
        game = _create(client, player_id=white, opponent_player_id=black, time_control="5+0")
        game_id = game["game_id"]
        for player, uci in ((white, "f2f3"), (black, "e7e5"), (white, "g2g4"), (black, "d8h4")):
            client.post(f"/api/live/games/{game_id}/move", json={"player_id": player, "uci": uci})
        library_id = client.get(
            f"/api/live/games/{game_id}", params={"player_id": white}
        ).json()["library_game_id"]
        assert library_id
        library = client.get(f"/api/games/{library_id}").json()
        assert library["analysis_status"] == "analyzed"


# ---------------------------------------------------------------------------
# the WebSocket event stream
# ---------------------------------------------------------------------------


def _recv_of_type(socket, expected: str, limit: int = 6) -> dict:
    """Read frames until one has the expected type.

    The stream carries real presence events (a connection announces itself), so a
    test must not assume the *next* frame is the reply to its own message.
    """
    for _ in range(limit):
        message = socket.receive_json()
        if message.get("type") == expected:
            return message
    raise AssertionError(f"no '{expected}' frame arrived within {limit} frames")


class TestWebSocket:
    def test_an_unauthorized_socket_is_closed_before_it_is_used(self, client: TestClient) -> None:
        game_id, _, _ = _started_game(client)
        players = _players(client)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/live/games/{game_id}/ws?player_id={players['Live Tester Two']}"
            ):
                pass

    def test_an_authorized_socket_receives_a_sync_first(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(
            f"/api/live/games/{game_id}/ws?player_id={white}"
        ) as socket:
            first = socket.receive_json()
            assert first["type"] == "sync"
            assert "state" in first or "events" in first

    def test_a_ping_is_answered(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(
            f"/api/live/games/{game_id}/ws?player_id={white}"
        ) as socket:
            socket.receive_json()  # initial sync
            socket.send_json({"type": "ping"})
            assert _recv_of_type(socket, "pong")["type"] == "pong"

    def test_actions_are_not_accepted_over_the_socket(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(
            f"/api/live/games/{game_id}/ws?player_id={white}"
        ) as socket:
            socket.receive_json()
            socket.send_json({"type": "move", "uci": "e2e4"})
            reply = _recv_of_type(socket, "error")
            assert reply["error"] == "unsupported_message"

    def test_a_bad_sync_request_is_rejected_not_guessed(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(
            f"/api/live/games/{game_id}/ws?player_id={white}"
        ) as socket:
            socket.receive_json()
            socket.send_json({"type": "sync", "after_sequence": "not a number"})
            assert _recv_of_type(socket, "error")["type"] == "error"

    def test_multiple_sessions_for_one_player_are_detected(self, client: TestClient) -> None:
        # §18: two tabs are two connections, and the server reports both rather
        # than pretending there is one.
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(f"/api/live/games/{game_id}/ws?player_id={white}") as first:
            first.receive_json()
            with client.websocket_connect(
                f"/api/live/games/{game_id}/ws?player_id={white}"
            ) as second:
                second.receive_json()
                state = client.get(
                    f"/api/live/games/{game_id}", params={"player_id": white}
                ).json()
                assert state["session_counts"]["white"] >= 2

    def test_events_are_delivered_in_sequence_to_a_subscriber(self, client: TestClient) -> None:
        game_id, white, _ = _started_game(client)
        with client.websocket_connect(
            f"/api/live/games/{game_id}/ws?player_id={white}"
        ) as socket:
            socket.receive_json()  # initial sync
            # A move made on another "tab" (an ordinary HTTP call) is broadcast
            # to this socket as a sequenced event.
            client.post(
                f"/api/live/games/{game_id}/move",
                json={"player_id": white, "uci": "e2e4"},
            )
            seen: list[int] = []
            for _ in range(4):
                message = socket.receive_json()
                if message["type"] == "event":
                    seen.append(message["event"]["sequence_number"])
                if seen and message["type"] == "event":
                    # The MOVE_MADE event is enough to prove the broadcast path.
                    if message["event"]["event_type"] == "MOVE_MADE":
                        break
            assert seen, "no events were broadcast to the socket"


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


class TestListing:
    def test_a_player_sees_their_own_games(self, client: TestClient) -> None:
        players = _seed(client)
        created = _create(client, player_id=players["Paul Morphy"])
        body = client.get(
            "/api/live/games", params={"player_id": players["Paul Morphy"]}
        ).json()
        ids = [game["game_id"] for game in body["games"]]
        assert created["game_id"] in ids

    def test_an_unknown_live_game_is_not_found(self, client: TestClient) -> None:
        response = client.get("/api/live/games/does-not-exist")
        assert response.status_code == 404
