"""Lichess source tests.

Same discipline as the Chess.com suite: the client is exercised against a mocked
HTTP transport carrying payloads in the shape the real Lichess API returns
(NDJSON, epoch-millisecond timestamps, mixed variants), and the route tests drive
the real API surface with the network client replaced, which is where the
idempotent import behaviour lives.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from argus.importing.lichess import (
    LichessClient,
    _game_from_payload,
    month_bounds_ms,
    months_for_profile,
    normalize_username,
)
from argus.shared.errors import (
    SourcePlayerNotFoundError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceUnavailableError,
    ValidationError,
)

from tests.conftest import OPERA_GAME_PGN

LICHESS_PGN = OPERA_GAME_PGN.replace(
    '[Event "Paris Opera House"]',
    '[Event "rated blitz game"]\n[Site "https://lichess.org/abcd1234"]\n'
    '[UTCDate "2024.11.02"]\n[WhiteElo "1900"]\n[BlackElo "1850"]',
)

_GAME_URL = "https://lichess.org/abcd1234"


def _game_payload(url_id: str = "abcd1234") -> dict:
    return {
        "id": url_id,
        "rated": True,
        "variant": "standard",
        "speed": "blitz",
        "perf": "blitz",
        "status": "resign",
        "createdAt": 1_730_563_200_000,
        "winner": "white",
        "pgn": LICHESS_PGN,
        "clock": {"initial": 300, "increment": 3, "totalTime": 420},
        "players": {
            "white": {"user": {"name": "Morphy", "id": "morphy"}, "rating": 1900},
            "black": {"user": {"name": "DukeKarl", "id": "dukekarl"}, "rating": 1850},
        },
        "opening": {"eco": "C41", "name": "Philidor Defense"},
    }


# --- pure helpers ---------------------------------------------------------------


def test_normalize_username_accepts_handles_and_rejects_paths() -> None:
    assert normalize_username("  @Thibault ") == "thibault"
    assert normalize_username("Duke_Karl-2") == "duke_karl-2"
    for bad in ("", "   ", "a", "../../etc/passwd", "hi karu", "hi/karu"):
        with pytest.raises(ValidationError):
            normalize_username(bad)


def test_month_bounds_cover_a_utc_calendar_month() -> None:
    since, until = month_bounds_ms(2024, 11)
    assert datetime.fromtimestamp(since / 1000, tz=UTC) == datetime(2024, 11, 1, tzinfo=UTC)
    assert datetime.fromtimestamp(until / 1000, tz=UTC) == datetime(2024, 12, 1, tzinfo=UTC)
    # December must roll into the next year.
    since, until = month_bounds_ms(2024, 12)
    assert datetime.fromtimestamp(until / 1000, tz=UTC) == datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        month_bounds_ms(2024, 13)


def test_months_run_back_from_now_and_stop_at_account_creation() -> None:
    now = datetime(2025, 1, 15, tzinfo=UTC)
    months = months_for_profile("x", datetime(2024, 10, 2, tzinfo=UTC), months=6, now=now)
    assert [(m.year, m.month) for m in months] == [
        (2025, 1),
        (2024, 12),
        (2024, 11),
        (2024, 10),
    ]
    assert months[0].label == "January 2025"
    # Bounded by the requested window, newest first.
    assert len(months_for_profile("x", datetime(2010, 1, 1, tzinfo=UTC), months=2, now=now)) == 2
    # An unknown creation date must not be guessed into a longer history.
    assert len(months_for_profile("x", None, months=6, now=now)) == 1


def test_game_payload_maps_players_result_and_opening() -> None:
    game = _game_from_payload(_game_payload())
    assert game is not None
    assert game.url == _GAME_URL
    assert game.white_username == "Morphy"
    assert game.black_rating == 1850
    assert game.result == "1-0"
    assert game.time_class == "blitz"
    assert game.time_control == "300+3"
    assert game.eco_code == "C41"
    assert game.opening_name == "Philidor Defense"
    assert game.played_at is not None and game.played_at.year == 2024
    assert game.is_standard is True
    assert game.move_count_estimate == 33


def test_variant_payload_is_not_standard_chess() -> None:
    payload = _game_payload()
    payload["variant"] = "chess960"
    game = _game_from_payload(payload)
    assert game is not None and game.is_standard is False


@pytest.mark.parametrize(
    ("winner", "status", "expected"),
    [
        ("black", "resign", "0-1"),
        (None, "stalemate", "1/2-1/2"),
        (None, "draw", "1/2-1/2"),
        (None, "aborted", None),
    ],
)
def test_result_is_derived_from_status_when_the_pgn_has_none(
    winner: str | None, status: str, expected: str | None
) -> None:
    payload = _game_payload()
    payload["winner"] = winner
    payload["status"] = status
    payload["pgn"] = LICHESS_PGN.replace('[Result "1-0"]\n', "")
    game = _game_from_payload(payload)
    assert game is not None
    assert game.result == expected


def test_unusable_payloads_are_dropped_rather_than_half_read() -> None:
    assert _game_from_payload({"id": "abcd1234", "pgn": "   "}) is None
    assert _game_from_payload({"pgn": LICHESS_PGN}) is None
    assert _game_from_payload({}) is None


# --- client behaviour (mocked transport) ----------------------------------------


def _client(handler) -> LichessClient:
    return LichessClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _ndjson(*payloads: dict) -> httpx.Response:
    import json

    body = "\n".join(json.dumps(payload) for payload in payloads)
    return httpx.Response(200, text=body, headers={"Content-Type": "application/x-ndjson"})


def test_profile_is_parsed_including_only_speeds_with_games() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "thibault",
                "username": "thibault",
                "title": "LM",
                "url": "https://lichess.org/@/thibault",
                "createdAt": 1_291_430_400_000,
                "seenAt": 1_730_563_200_000,
                "count": {"all": 23_878, "win": 10_000, "loss": 9_000, "draw": 4_878},
                "playTime": {"total": 4_000_000, "tv": 100},
                "perfs": {
                    "blitz": {"games": 100, "rating": 1734},
                    "bullet": {"games": 0, "rating": 1500},
                },
            },
        )

    client = _client(handler)
    profile = client.get_profile("Thibault")
    assert profile.username == "thibault"
    assert profile.title == "LM"
    assert profile.total_games == 23_878
    assert profile.play_time_seconds == 4_000_000
    assert profile.created_at is not None and profile.created_at.year == 2010
    # A speed with zero games is not reported as a rating.
    assert profile.ratings == {"blitz": 1734}


def test_month_games_reads_ndjson_and_counts_variants_apart() -> None:
    variant = _game_payload("variant1")
    variant["variant"] = "chess960"
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept", "")
        return _ndjson(_game_payload(), variant, {"unreadable": "line"})

    month = _client(handler).get_month_games("morphy", 2024, 11, limit=5)
    assert [g.url for g in month.games] == [_GAME_URL]
    assert month.standard_count == 1
    assert month.variant_count == 1
    # The month window, the cap and the JSON PGN request are all real params.
    assert "since=" in captured["url"] and "until=" in captured["url"]
    assert "max=5" in captured["url"] and "pgnInJson=true" in captured["url"]
    assert captured["accept"] == "application/x-ndjson"


def test_month_games_can_filter_by_time_class_and_flags_truncation() -> None:
    rapid = _game_payload("rapid1234")
    rapid["speed"] = "rapid"

    def handler(request: httpx.Request) -> httpx.Response:
        return _ndjson(_game_payload(), rapid)

    month = _client(handler).get_month_games("morphy", 2024, 11, time_class="rapid", limit=1)
    assert [g.url for g in month.games] == ["https://lichess.org/rapid1234"]
    assert month.truncated is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, SourcePlayerNotFoundError),
        (429, SourceRateLimitedError),
        (500, SourceUnavailableError),
    ],
)
def test_http_failures_map_to_typed_errors(status: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    with pytest.raises(expected):
        _client(handler).get_profile("ghost")


def test_connection_and_unreadable_payloads_are_distinct() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(SourceUnavailableError):
        _client(boom).get_profile("ghost")

    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(SourceResponseError):
        _client(garbage).get_profile("ghost")


def test_invalid_month_is_rejected_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be issued")

    with pytest.raises(ValidationError):
        _client(handler).get_month_games("morphy", 2024, 13)


# --- routes ---------------------------------------------------------------------


class FakeLichessClient:
    """Stands in for the network client so route behaviour can be tested."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> FakeLichessClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_profile(self, username: str) -> object:
        from argus.importing.lichess import LichessProfile

        if username.lower() == "ghost":
            raise SourcePlayerNotFoundError("Lichess has no player named 'ghost'.")
        return LichessProfile(
            username="thibault",
            player_id="thibault",
            title="LM",
            created_at=datetime(2024, 11, 5, tzinfo=UTC),
            total_games=23_878,
            ratings={"blitz": 1734},
        )

    def get_month_games(self, username: str, year: int, month: int, **kwargs: object):
        from argus.importing.lichess import LichessGame, LichessMonthGames

        game = LichessGame(
            url=_GAME_URL,
            game_id="abcd1234",
            pgn=LICHESS_PGN,
            variant="standard",
            time_class="blitz",
            white_username="Morphy",
            black_username="DukeKarl",
            white_rating=1900,
            black_rating=1850,
            result="1-0",
            eco_code="C41",
        )
        return LichessMonthGames(
            username=username,
            year=year,
            month=month,
            games=[game],
            standard_count=1,
            variant_count=0,
        )


@pytest.fixture()
def lichess_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import argus_api.routes.sources as sources_routes

    monkeypatch.setattr(sources_routes, "LichessClient", FakeLichessClient)


def test_lichess_lookup_reports_calendar_months_honestly(
    lichess_client, client: TestClient
) -> None:
    body = client.get("/api/sources/lichess/thibault?months=3").json()
    assert body["source"] == "lichess"
    assert body["profile"]["username"] == "thibault"
    assert body["profile"]["ratings"] == {"blitz": 1734}
    # Lichess publishes no archive index — the payload must say so.
    assert body["months_are_published"] is False
    assert [m["label"] for m in body["months"]][0].endswith("2026")
    assert "calendar months" in body["note"]


def test_lichess_missing_player_is_a_404_not_a_500(lichess_client, client: TestClient) -> None:
    response = client.get("/api/sources/lichess/ghost")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "source_player_not_found"


def test_lichess_month_listing_imports_nothing(lichess_client, client: TestClient) -> None:
    body = client.get("/api/sources/lichess/thibault/games?year=2024&month=11").json()
    assert body["count"] == 1
    assert body["imported_count"] == 0
    assert body["games"][0]["already_imported"] is False
    assert body["games"][0]["ply_count"] == 33
    # Browsing must not touch the library.
    assert client.get("/api/games").json()["count"] == 0


def test_lichess_import_is_idempotent_and_records_provenance(
    lichess_client, client: TestClient
) -> None:
    first = client.post(
        "/api/sources/lichess/import",
        json={"username": "thibault", "year": 2024, "month": 11, "game_urls": [_GAME_URL]},
    ).json()
    assert first["counts"] == {"requested": 1, "imported": 1, "skipped": 0, "failed": 0}
    game_id = first["imported"][0]["game_id"]
    assert first["imported"][0]["queued_for_analysis"] is False

    listed = client.get("/api/games").json()["games"][0]
    assert listed["id"] == game_id
    assert listed["source"] == "lichess"
    assert listed["source_game_id"] == _GAME_URL

    second = client.post(
        "/api/sources/lichess/import",
        json={"username": "thibault", "year": 2024, "month": 11, "game_urls": [_GAME_URL]},
    ).json()
    assert second["counts"] == {"requested": 1, "imported": 0, "skipped": 1, "failed": 0}
    assert second["skipped"][0]["reason"] == "already_imported"
    assert client.get("/api/games").json()["count"] == 1


def test_unknown_site_is_rejected_with_the_real_source_list(client: TestClient) -> None:
    response = client.get("/api/sources/chesssucks/foo")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "unsupported_source"
    # The honest answer to "what about another website": here is what we do read.
    assert {"chess_com", "lichess"} <= set(error["details"]["supported"])
    assert error["details"]["username"] == "foo"


def test_lookup_without_a_username_is_explained_not_a_404(client: TestClient) -> None:
    for source in ("lichess", "chesscom"):
        response = client.get(f"/api/sources/{source}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_lichess_import_of_a_game_outside_the_month_fails_honestly(
    lichess_client, client: TestClient
) -> None:
    body = client.post(
        "/api/sources/lichess/import",
        json={
            "username": "thibault",
            "year": 2024,
            "month": 11,
            "game_urls": ["https://lichess.org/notinmonth"],
        },
    ).json()
    assert body["counts"] == {"requested": 1, "imported": 0, "skipped": 0, "failed": 1}
    assert body["failed"][0]["code"] == "not_in_month"
    assert client.get("/api/games").json()["count"] == 0
