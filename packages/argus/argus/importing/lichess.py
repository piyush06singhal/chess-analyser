"""Lichess public API client.

Lichess exposes a **public, key-less** read API for every finished game a player
has played — not just the ones they published:

* ``GET /api/user/{username}`` — profile (including total game count)
* ``GET /api/games/user/{username}?since=…&until=…`` — finished games in a time
  window, as NDJSON

This module is a *fetcher*, not a parser: it returns the PGN Lichess already
provides, and ``PgnImporter`` turns that into the internal game representation.

Two differences from Chess.com shape this client, and both are handled
honestly rather than papered over:

* **No archive index.** Chess.com publishes a list of months that contain games;
  Lichess does not. Caissa therefore offers calendar months between the account's
  creation month and the current month, and a month with no games simply comes
  back empty when it is opened. Caissa never invents a game count for a month it
  has not fetched.
* **Variants are mixed into the same feed.** Lichess returns standard chess and
  every variant together, so variants are counted and reported, never silently
  imported as if they were standard chess.

Everything else matches the Chess.com rules: public data only, typed failures,
and fields the source does not provide stay ``None``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Iterator

import httpx
from pydantic import BaseModel, Field

from argus.importing.pgn_headers import parse_pgn_headers
from argus.shared.errors import (
    SourcePlayerNotFoundError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceUnavailableError,
    ValidationError,
)

DEFAULT_BASE_URL = "https://lichess.org"

#: Lichess asks API consumers to identify themselves.
DEFAULT_USER_AGENT = "Caissa-Chess/0.1 (chess analysis; +https://github.com/argus-chess)"

#: Lichess account ids: letters, digits, underscore and hyphen (case-insensitive).
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")

#: The only variant Caissa analyzes today. Lichess reports standard chess as
#: ``variant == "standard"``.
STANDARD_VARIANT = "standard"

#: Sensible defaults so a request can never walk an unbounded history.
DEFAULT_MONTHS = 3
MAX_MONTHS = 12
MAX_GAMES_PER_REQUEST = 100

#: Lichess perftype values that are variants rather than standard chess speeds.
_VARIANT_PERF_TYPES = frozenset(
    {
        "chess960",
        "crazyhouse",
        "antichess",
        "atomic",
        "horde",
        "kingofthehill",
        "racingkings",
        "threecheck",
        "ultraBullet".lower(),
    }
)

#: Time controls Caissa lets a user filter by, in Lichess's own vocabulary.
TIME_CLASSES = ("bullet", "blitz", "rapid", "classical", "correspondence")


class LichessProfile(BaseModel):
    """A Lichess player as published by the API."""

    username: str
    player_id: str | None = None
    url: str | None = None
    title: str | None = None
    created_at: datetime | None = None
    seen_at: datetime | None = None
    total_games: int | None = None
    play_time_seconds: int | None = None
    ratings: dict[str, int] = Field(
        default_factory=dict,
        description="Per-speed rating, e.g. {'blitz': 1450}. Only speeds with games.",
    )


class LichessMonth(BaseModel):
    """One calendar month Caissa can ask Lichess about."""

    year: int
    month: int
    label: str
    since_ms: int
    until_ms: int


class LichessGame(BaseModel):
    """One finished game, with the PGN Lichess already provides."""

    url: str
    game_id: str
    pgn: str
    variant: str | None = None
    time_class: str | None = None
    time_control: str | None = None
    rated: bool | None = None
    white_username: str | None = None
    black_username: str | None = None
    white_rating: int | None = None
    black_rating: int | None = None
    result: str | None = Field(default=None, description="PGN-style result, e.g. '1-0'")
    status: str | None = Field(default=None, description="Lichess game status, e.g. 'mate'")
    played_at: datetime | None = None
    eco_code: str | None = None
    opening_name: str | None = None

    @property
    def is_standard(self) -> bool:
        variant = (self.variant or STANDARD_VARIANT).lower()
        return variant in ("", STANDARD_VARIANT)

    @property
    def move_count_estimate(self) -> int | None:
        """Plies as written in the PGN movetext (``None`` when it cannot be read)."""
        movetext = self.pgn.split("\n\n", 1)
        if len(movetext) < 2:
            return None
        tokens = [
            token
            for token in movetext[1].split()
            if not token.endswith(".") and token not in {"1-0", "0-1", "1/2-1/2", "*"}
            and not token.startswith("{")
        ]
        return len(tokens) or None


class LichessMonthGames(BaseModel):
    """One month of a player's games, with what was filtered out made explicit."""

    username: str
    year: int
    month: int
    games: list[LichessGame] = Field(default_factory=list)
    standard_count: int = 0
    variant_count: int = 0
    truncated: bool = False


def normalize_username(username: str) -> str:
    """Validate and normalize a Lichess username.

    Raises:
        ValidationError: when the value cannot be a Lichess username. The value
            is validated before it is ever placed in a request path.
    """
    cleaned = (username or "").strip().lstrip("@").lower()
    if not cleaned:
        raise ValidationError("Enter a Lichess username.")
    if not _USERNAME_RE.match(cleaned):
        raise ValidationError(
            "That is not a valid Lichess username.",
            details={"username": username, "expected": "letters, digits, '_' or '-'"},
        )
    return cleaned


_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    """Epoch-millisecond bounds ``[since, until)`` for a calendar month (UTC)."""
    if not 1 <= month <= 12:
        raise ValidationError("Month must be between 1 and 12.", details={"month": month})
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def months_for_profile(
    username: str,
    created_at: datetime | None,
    *,
    months: int = DEFAULT_MONTHS,
    now: datetime | None = None,
) -> list[LichessMonth]:
    """Calendar months Caissa can offer for a player, most recent first.

    Lichess publishes no archive index, so the window runs from the account's
    creation month to the current month. When the creation date is unknown the
    current month is the only month offered — Caissa does not guess how far back
    an account goes.
    """
    reference = now or datetime.now(UTC)
    bounded = max(1, min(int(months), MAX_MONTHS))
    latest = (reference.year, reference.month)

    if created_at is None:
        years: list[tuple[int, int]] = [latest]
    else:
        earliest = (created_at.year, created_at.month)
        years = []
        year, month = latest
        while (year, month) >= earliest and len(years) < bounded:
            years.append((year, month))
            month -= 1
            if month == 0:
                year, month = year - 1, 12

    result: list[LichessMonth] = []
    for year, month in years:
        since, until = month_bounds_ms(year, month)
        result.append(
            LichessMonth(
                year=year,
                month=month,
                label=f"{_MONTH_NAMES[month]} {year}",
                since_ms=since,
                until_ms=until,
            )
        )
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            # Lichess timestamps are epoch milliseconds.
            seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class LichessClient:
    """Read-only client for the public Lichess API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_USER_AGENT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._user_agent = user_agent

    # --- transport ----------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> LichessClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, path: str, *, username: str, accept: str) -> httpx.Response:
        """GET ``path``, mapping every transport failure to a typed error."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        try:
            response = self._http().get(url, headers={"Accept": accept})
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(
                "Lichess did not respond in time. Try again in a moment.",
                details={"username": username},
            ) from exc
        except httpx.HTTPError as exc:  # connection refused, DNS, TLS…
            raise SourceUnavailableError(
                "Could not reach Lichess.",
                details={"username": username, "reason": str(exc)},
            ) from exc

        if response.status_code == 404:
            raise SourcePlayerNotFoundError(
                f"Lichess has no player named '{username}'.",
                details={"username": username, "status": 404},
            )
        if response.status_code == 429:
            raise SourceRateLimitedError(
                "Lichess is rate limiting Caissa. Wait a few seconds and try again.",
                details={"username": username, "status": 429},
            )
        if response.status_code >= 400:
            raise SourceUnavailableError(
                f"Lichess returned HTTP {response.status_code}.",
                details={"username": username, "status": response.status_code},
            )
        return response

    # --- endpoints ----------------------------------------------------------

    def get_profile(self, username: str) -> LichessProfile:
        """Fetch a player's public profile."""
        handle = normalize_username(username)
        response = self._request(f"/api/user/{handle}", username=handle, accept="application/json")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceResponseError(
                "Lichess returned a response Caissa could not read.",
                details={"username": handle},
            ) from exc
        if not isinstance(payload, dict):
            raise SourceResponseError("Lichess returned an unexpected profile payload.")

        ratings: dict[str, int] = {}
        perfs = payload.get("perfs")
        if isinstance(perfs, dict):
            for speed, entry in perfs.items():
                if isinstance(entry, dict) and isinstance(entry.get("rating"), int):
                    if int(entry.get("games") or 0) > 0:
                        ratings[speed] = entry["rating"]

        count = payload.get("count") if isinstance(payload.get("count"), dict) else {}
        play_time = payload.get("playTime") if isinstance(payload.get("playTime"), dict) else {}

        return LichessProfile(
            username=payload.get("username") or handle,
            player_id=payload.get("id"),
            url=payload.get("url"),
            title=payload.get("title"),
            created_at=_parse_datetime(payload.get("createdAt")),
            seen_at=_parse_datetime(payload.get("seenAt")),
            total_games=count.get("all") if isinstance(count.get("all"), int) else None,
            play_time_seconds=(
                play_time.get("total") if isinstance(play_time.get("total"), int) else None
            ),
            ratings=ratings,
        )

    def iter_month_games(
        self,
        username: str,
        year: int,
        month: int,
        *,
        time_class: str | None = None,
        limit: int = MAX_GAMES_PER_REQUEST,
    ) -> Iterator[LichessGame]:
        """Stream the finished games a player played inside one calendar month."""
        handle = normalize_username(username)
        since_ms, until_ms = month_bounds_ms(year, month)
        bounded = max(1, min(int(limit), MAX_GAMES_PER_REQUEST))
        params = {
            "since": since_ms,
            "until": until_ms,
            "max": bounded,
            "pgnInJson": "true",
            "clocks": "false",
            "evals": "false",
            "opening": "true",
            "sort": "dateDesc",
        }
        query = "&".join(f"{key}={value}" for key, value in params.items())
        path = f"/api/games/user/{handle}?{query}"
        response = self._request(
            path, username=handle, accept="application/x-ndjson"
        )

        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = _json_line(line)
            except ValueError:
                continue  # one unreadable line must not discard the whole month
            if not isinstance(payload, dict):
                continue
            game = _game_from_payload(payload)
            if game is None:
                continue
            if time_class and (game.time_class or "") != time_class:
                continue
            yield game

    def get_month_games(
        self,
        username: str,
        year: int,
        month: int,
        *,
        standard_only: bool = True,
        time_class: str | None = None,
        limit: int | None = None,
    ) -> LichessMonthGames:
        """Fetch one month of games for a player, newest first."""
        handle = normalize_username(username)
        bounded = MAX_GAMES_PER_REQUEST if limit is None else max(1, min(int(limit), MAX_GAMES_PER_REQUEST))
        games: list[LichessGame] = []
        variants = 0
        for game in self.iter_month_games(handle, year, month, time_class=time_class, limit=bounded):
            if not game.is_standard:
                variants += 1
                if standard_only:
                    continue
            games.append(game)

        return LichessMonthGames(
            username=handle,
            year=year,
            month=month,
            games=games,
            standard_count=len(games),
            variant_count=variants,
            truncated=len(games) >= bounded,
        )

    def get_game(self, username: str, game_id: str) -> LichessGame | None:
        """Fetch one game by its Lichess id (used to resolve an exact URL)."""
        handle = normalize_username(username)
        cleaned = (game_id or "").strip()
        if not cleaned or not re.fullmatch(r"[A-Za-z0-9]{8,12}", cleaned):
            return None
        response = self._request(
            f"/api/games/user/{handle}?max=1&pgnInJson=true&opening=true",
            username=handle,
            accept="application/x-ndjson",
        )
        # The public feed cannot be addressed by id, so this is only used when
        # the game is the player's most recent one; anything else returns None
        # rather than a wrong game.
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = _json_line(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                game = _game_from_payload(payload)
                if game is not None and game.game_id == cleaned:
                    return game
        return None


def _json_line(line: str) -> Any:
    import json

    return json.loads(line)


def _game_from_payload(raw: dict[str, Any]) -> LichessGame | None:
    """Build a :class:`LichessGame` from one Lichess game object."""
    game_id = raw.get("id")
    pgn = raw.get("pgn")
    if not isinstance(game_id, str) or not isinstance(pgn, str) or not pgn.strip():
        return None

    players = raw.get("players") if isinstance(raw.get("players"), dict) else {}
    white = players.get("white") if isinstance(players.get("white"), dict) else {}
    black = players.get("black") if isinstance(players.get("black"), dict) else {}
    white_user = white.get("user") if isinstance(white.get("user"), dict) else {}
    black_user = black.get("user") if isinstance(black.get("user"), dict) else {}
    opening = raw.get("opening") if isinstance(raw.get("opening"), dict) else {}
    clock = raw.get("clock") if isinstance(raw.get("clock"), dict) else {}
    headers = parse_pgn_headers(pgn)

    initial = clock.get("initial")
    increment = clock.get("increment")
    time_control = (
        f"{initial}+{increment}"
        if isinstance(initial, int) and isinstance(increment, int)
        else None
    )

    return LichessGame(
        url=f"https://lichess.org/{game_id}",
        game_id=game_id,
        pgn=pgn,
        variant=raw.get("variant") if isinstance(raw.get("variant"), str) else None,
        time_class=raw.get("speed") if isinstance(raw.get("speed"), str) else None,
        time_control=time_control,
        rated=raw.get("rated") if isinstance(raw.get("rated"), bool) else None,
        white_username=white_user.get("name") or white_user.get("id"),
        black_username=black_user.get("name") or black_user.get("id"),
        white_rating=white.get("rating") if isinstance(white.get("rating"), int) else None,
        black_rating=black.get("rating") if isinstance(black.get("rating"), int) else None,
        result=headers.get("Result") or _result_from_status(raw),
        status=raw.get("status") if isinstance(raw.get("status"), str) else None,
        played_at=_parse_datetime(raw.get("createdAt")),
        eco_code=opening.get("eco") if isinstance(opening.get("eco"), str) else headers.get("ECO"),
        opening_name=opening.get("name") if isinstance(opening.get("name"), str) else None,
    )


def _result_from_status(raw: dict[str, Any]) -> str | None:
    """Derive the PGN result from the game status when the PGN has no Result tag."""
    winner = raw.get("winner")
    if winner == "white":
        return "1-0"
    if winner == "black":
        return "0-1"
    if (raw.get("status") or "") in {"draw", "stalemate"}:
        return "1/2-1/2"
    return None


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MONTHS",
    "DEFAULT_USER_AGENT",
    "MAX_GAMES_PER_REQUEST",
    "MAX_MONTHS",
    "STANDARD_VARIANT",
    "TIME_CLASSES",
    "LichessClient",
    "LichessGame",
    "LichessMonth",
    "LichessMonthGames",
    "LichessProfile",
    "month_bounds_ms",
    "months_for_profile",
    "normalize_username",
]
