"""Chess.com source tests.

The client is exercised against a mocked HTTP transport carrying payloads in the
shape the real Published-Data API returns, so the parsing, filtering and error
mapping are verified without depending on the network. The route tests drive the
real API surface with the network client replaced, which is where the idempotent
import behaviour actually lives.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from argus.importing.chesscom import (
    ChessComClient,
    _game_from_payload,
    _pgn_headers,
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

CHESSCOM_PGN = OPERA_GAME_PGN.replace(
    '[Event "Paris Opera House"]',
    '[Event "Live Chess"]\n[Site "https://www.chess.com/game/live/123456789"]\n'
    '[ECOUrl "https://www.chess.com/openings/Philidor-Defense"]',
)


def _game_payload(url: str = "https://www.chess.com/game/live/123456789") -> dict:
    return {
        "url": url,
        "uuid": "abc-123",
        "pgn": CHESSCOM_PGN,
        "time_class": "blitz",
        "time_control": "300",
        "rated": True,
        "rules": "chess",
        "end_time": 1_700_000_000,
        "white": {"username": "Morphy", "rating": 1900, "result": "win"},
        "black": {"username": "DukeKarl", "rating": 1850, "result": "checkmated"},
    }


# --- pure helpers ---------------------------------------------------------------


def test_normalize_username_accepts_handles_and_rejects_paths() -> None:
    assert normalize_username("  @Hikaru ") == "hikaru"
    assert normalize_username("Duke_Karl-2") == "duke_karl-2"
    for bad in ("", "   ", "a", "../../etc/passwd", "hi karu", "hi/karu"):
        with pytest.raises(ValidationError):
            normalize_username(bad)


def test_pgn_headers_reads_only_the_tag_section() -> None:
    headers = _pgn_headers(CHESSCOM_PGN)
    assert headers["Event"] == "Live Chess"
    assert headers["ECO"] == "C41"
    assert headers["Result"] == "1-0"


def test_game_payload_maps_ratings_result_and_eco() -> None:
    game = _game_from_payload(_game_payload())
    assert game is not None
    assert game.white_username == "Morphy"
    assert game.black_rating == 1850
    assert game.eco_code == "C41"
    assert game.result == "1-0"
    assert game.time_class == "blitz"
    assert game.end_time is not None and game.end_time.year == 2023
    assert game.is_standard is True
    assert game.move_count_estimate == 33


def test_unusable_payload_is_dropped_rather_than_half_read() -> None:
    assert _game_from_payload({"url": "https://x", "pgn": ""}) is None
    assert _game_from_payload({"pgn": CHESSCOM_PGN}) is None
    assert _game_from_payload({}) is None


def test_variants_are_not_standard_games() -> None:
    payload = _game_payload()
    payload["rules"] = "chess960"
    game = _game_from_payload(payload)
    assert game is not None and game.is_standard is False


# --- client behaviour (mocked transport) ----------------------------------------


def _client(handler) -> ChessComClient:
    return ChessComClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _archive_payload(games: list[dict]) -> dict:
    return {"games": games}


def test_profile_and_archives_are_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/games/archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/hikaru/games/2024/01",
                        "https://api.chess.com/pub/player/hikaru/games/2024/02",
                        "not-a-month",
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "username": "Hikaru",
                "player_id": 15448422,
                "status": "premium",
                "joined": 1389043258,
                "followers": 1_000_000,
            },
        )

    client = _client(handler)
    profile = client.get_profile("Hikaru")
    assert profile.username == "Hikaru"
    assert profile.joined is not None
    assert profile.is_closed is False

    archives = client.list_archives("hikaru")
    assert [(a.year, a.month) for a in archives] == [(2024, 1), (2024, 2)]
    assert archives[0].label == "January 2024"
    assert [a.label for a in client.get_recent_months("hikaru", months=1)] == ["February 2024"]


def test_month_games_filters_variants_and_reports_them() -> None:
    variant = _game_payload("https://www.chess.com/game/live/999")
    variant["rules"] = "chess960"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_archive_payload([_game_payload(), variant]))

    month = _client(handler).get_month_games("morphy", 2024, 1)
    assert [g.url for g in month.games] == ["https://www.chess.com/game/live/123456789"]
    assert month.standard_count == 1
    assert month.variant_count == 1
    assert month.truncated is False


def test_month_games_can_filter_by_time_class() -> None:
    rapid = _game_payload("https://www.chess.com/game/live/222")
    rapid["time_class"] = "rapid"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_archive_payload([_game_payload(), rapid]))

    month = _client(handler).get_month_games("morphy", 2024, 1, time_class="rapid")
    assert [g.url for g in month.games] == ["https://www.chess.com/game/live/222"]
    assert month.standard_count == 1


def test_limit_keeps_the_newest_games_and_flags_truncation() -> None:
    games = [_game_payload(f"https://www.chess.com/game/live/{n}") for n in range(1, 6)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_archive_payload(games))

    month = _client(handler).get_month_games("morphy", 2024, 1, limit=2)
    assert [g.url for g in month.games] == [
        "https://www.chess.com/game/live/4",
        "https://www.chess.com/game/live/5",
    ]
    assert month.truncated is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, SourcePlayerNotFoundError),
        (403, SourcePlayerNotFoundError),
        (429, SourceRateLimitedError),
        (500, SourceUnavailableError),
    ],
)
def test_http_failures_map_to_typed_errors(status: int, expected: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    with pytest.raises(expected):
        _client(handler).get_profile("ghost")


def test_service_down_and_unreadable_payloads_are_distinct() -> None:
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


class FakeChessComClient:
    """Stands in for the network client so route behaviour can be tested."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> FakeChessComClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_profile(self, username: str) -> object:
        from argus.importing.chesscom import ChessComProfile

        if username.lower() == "ghost":
            raise SourcePlayerNotFoundError("Chess.com has no player named 'ghost'.")
        return ChessComProfile(username="Morphy", player_id=1, status="premium")

    def get_recent_months(self, username: str, *, months: int = 3) -> list:
        from argus.importing.chesscom import ChessComArchive

        return [ChessComArchive(year=2024, month=11, url="u/2024/11", label="November 2024")]

    def get_month_games(self, username: str, year: int, month: int, **kwargs: object):
        from argus.importing.chesscom import ChessComGame, ChessComMonth

        game = ChessComGame(
            url="https://www.chess.com/game/live/123456789",
            pgn=CHESSCOM_PGN,
            time_class="blitz",
            rules="chess",
            white_username="Morphy",
            black_username="DukeKarl",
            white_rating=1900,
            black_rating=1850,
            result="1-0",
            eco_code="C41",
        )
        return ChessComMonth(
            username=username,
            year=year,
            month=month,
            games=[game],
            standard_count=1,
            variant_count=0,
        )


@pytest.fixture()
def chesscom_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import argus_api.routes.sources as sources_routes

    monkeypatch.setattr(sources_routes, "ChessComClient", FakeChessComClient)


def test_sources_endpoint_lists_what_is_real(chesscom_client, client: TestClient) -> None:
    body = client.get("/api/sources").json()
    implemented = set(body["implemented"])
    assert {"chess_com", "lichess", "pgn_text", "pgn_file"} <= implemented
    # Nothing is left in the "planned" bucket that is actually working.
    assert body["planned"] == []
    for source in body["sources"]:
        if source["supports_username_lookup"]:
            assert source["available"] is True
            assert source["id"] in implemented


def test_player_lookup_returns_profile_and_months(chesscom_client, client: TestClient) -> None:
    body = client.get("/api/sources/chesscom/morphy?months=1").json()
    assert body["profile"]["username"] == "Morphy"
    assert body["profile"]["status"] == "premium"
    assert body["months"][0]["label"] == "November 2024"
    assert body["is_closed"] is False


def test_missing_player_is_a_404_not_a_500(chesscom_client, client: TestClient) -> None:
    response = client.get("/api/sources/chesscom/ghost")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "source_player_not_found"


def test_listing_a_month_imports_nothing(chesscom_client, client: TestClient) -> None:
    body = client.get("/api/sources/chesscom/morphy/games?year=2024&month=11").json()
    assert body["count"] == 1
    assert body["imported_count"] == 0
    assert body["games"][0]["already_imported"] is False
    assert body["games"][0]["ply_count"] == 33
    # Browsing must not touch the library.
    assert client.get("/api/games").json()["count"] == 0


def test_import_is_idempotent_and_records_the_source_game(
    chesscom_client, client: TestClient
) -> None:
    url = "https://www.chess.com/game/live/123456789"
    first = client.post(
        "/api/sources/chesscom/import",
        json={"username": "morphy", "year": 2024, "month": 11, "game_urls": [url]},
    ).json()
    assert first["counts"] == {"requested": 1, "imported": 1, "skipped": 0, "failed": 0}
    game_id = first["imported"][0]["game_id"]
    assert first["imported"][0]["queued_for_analysis"] is False

    # The game is now a normal Caissa game with Chess.com provenance.
    listed = client.get("/api/games").json()["games"][0]
    assert listed["id"] == game_id
    assert listed["source"] == "chess_com"
    assert listed["source_game_id"] == url

    # Re-importing the same game skips it instead of duplicating the library.
    second = client.post(
        "/api/sources/chesscom/import",
        json={"username": "morphy", "year": 2024, "month": 11, "game_urls": [url]},
    ).json()
    assert second["counts"] == {"requested": 1, "imported": 0, "skipped": 1, "failed": 0}
    assert second["skipped"][0]["game_id"] == game_id
    assert client.get("/api/games").json()["count"] == 1

    # And the month listing now flags it as already imported.
    month = client.get("/api/sources/chesscom/morphy/games?year=2024&month=11").json()
    assert month["imported_count"] == 1
    assert month["games"][0]["game_id"] == game_id


def test_import_reports_unknown_urls_instead_of_silently_dropping_them(
    chesscom_client, client: TestClient
) -> None:
    body = client.post(
        "/api/sources/chesscom/import",
        json={
            "username": "morphy",
            "year": 2024,
            "month": 11,
            "game_urls": ["https://www.chess.com/game/live/does-not-exist"],
        },
    ).json()
    assert body["counts"]["failed"] == 1
    assert body["failed"][0]["code"] == "not_in_month"
    assert client.get("/api/games").json()["count"] == 0
