"""Chess.com Published-Data API client.

Chess.com exposes a **public, key-less** read API for games a player has
published:

* ``GET /pub/player/{username}`` — profile
* ``GET /pub/player/{username}/games/archives`` — monthly archive URLs
* ``GET /pub/player/{username}/games/{YYYY}/{MM}`` — one month of games

This module is a *fetcher*, not a parser: it returns the PGN Chess.com already
provides, and ``PgnImporter`` turns that into the internal game representation.
No chess parsing is duplicated here, and nothing about the Chess.com payload
shape leaks past these models.

Rules Caissa follows, because they are the difference between a working
integration and a broken one:

* **Rated for API traffic, not browser traffic.** Chess.com asks API consumers
  to send a descriptive ``User-Agent``; the default is configurable.
* **Public data only.** No credentials are used, sent, or stored. A player who
  has not published games (or a closed account) is reported honestly instead of
  being retried into a rate limit.
* **Variants are separated, never mixed in.** Only ``rules == "chess"`` games
  are returned as standard games; everything else is counted and reported.
* **Failures are typed.** Missing player, closed account, rate limiting and
  malformed payloads are distinct errors the API maps to distinct statuses.

Nothing in this module invents a game, a rating, or an opening: fields the
source does not provide stay ``None``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from argus.importing.pgn_headers import parse_pgn_headers as _pgn_headers
from argus.shared.errors import (
    SourcePlayerNotFoundError,
    SourceRateLimitedError,
    SourceResponseError,
    SourceUnavailableError,
    ValidationError,
)

DEFAULT_BASE_URL = "https://api.chess.com/pub"

#: Chess.com asks for a descriptive User-Agent identifying the client.
DEFAULT_USER_AGENT = "Caissa-Chess/0.1 (chess analysis; +https://github.com/argus-chess)"

#: Chess.com usernames: letters, digits, underscore and hyphen.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")

#: The only rules variant Caissa analyzes today.
STANDARD_RULES = "chess"

#: Sensible defaults so a request can never walk an unbounded history.
DEFAULT_MONTHS = 3
MAX_MONTHS = 12


class ChessComProfile(BaseModel):
    """A Chess.com player as published by the API."""

    username: str
    player_id: int | None = None
    name: str | None = None
    url: str | None = None
    country: str | None = None
    avatar: str | None = None
    title: str | None = None
    status: str | None = Field(default=None, description="Chess.com account status, e.g. 'closed'")
    followers: int | None = None
    joined: datetime | None = None
    last_online: datetime | None = None

    @property
    def is_closed(self) -> bool:
        return (self.status or "").lower() == "closed"


class ChessComArchive(BaseModel):
    """One monthly archive the player has games in."""

    year: int
    month: int
    url: str
    label: str


class ChessComGame(BaseModel):
    """One published game, with the PGN Chess.com already provides."""

    url: str
    uuid: str | None = None
    pgn: str
    time_class: str | None = None
    time_control: str | None = None
    rated: bool | None = None
    rules: str | None = None
    variant: str | None = None
    white_username: str | None = None
    black_username: str | None = None
    white_rating: int | None = None
    black_rating: int | None = None
    white_result: str | None = None
    black_result: str | None = None
    result: str | None = Field(default=None, description="PGN-style result, e.g. '1-0'")
    end_time: datetime | None = None
    eco_code: str | None = None
    opening_name: str | None = None

    @property
    def is_standard(self) -> bool:
        rules = (self.rules or "").lower()
        return rules in ("", STANDARD_RULES)

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
        ]
        return len(tokens) or None


class ChessComMonth(BaseModel):
    """One month of a player's games, with what was filtered out made explicit."""

    username: str
    year: int
    month: int
    games: list[ChessComGame] = Field(default_factory=list)
    standard_count: int = 0
    variant_count: int = 0
    truncated: bool = False


def normalize_username(username: str) -> str:
    """Validate and normalize a Chess.com username.

    Raises:
        ValidationError: when the value cannot be a Chess.com username. The value
            is validated before it is ever placed in a request path.
    """
    cleaned = (username or "").strip().lstrip("@").lower()
    if not cleaned:
        raise ValidationError("Enter a Chess.com username.")
    if not _USERNAME_RE.match(cleaned):
        raise ValidationError(
            "That is not a valid Chess.com username.",
            details={"username": username, "expected": "letters, digits, '_' or '-'"},
        )
    return cleaned


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        candidate = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


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


class ChessComClient:
    """Read-only client for the public Chess.com Published-Data API."""

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
                headers={"User-Agent": self._user_agent, "Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> ChessComClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get_json(self, path: str, *, username: str) -> Any:
        """GET ``path`` and decode JSON, mapping every failure to a typed error."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        try:
            response = self._http().get(url)
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(
                "Chess.com did not respond in time. Try again in a moment.",
                details={"username": username},
            ) from exc
        except httpx.HTTPError as exc:  # connection refused, DNS, TLS…
            raise SourceUnavailableError(
                "Could not reach Chess.com.",
                details={"username": username, "reason": str(exc)},
            ) from exc

        if response.status_code == 404:
            raise SourcePlayerNotFoundError(
                f"Chess.com has no player named '{username}'.",
                details={"username": username, "status": 404},
            )
        if response.status_code == 403:
            raise SourcePlayerNotFoundError(
                f"Chess.com does not publish games for '{username}' (account closed or private).",
                details={"username": username, "status": 403},
            )
        if response.status_code == 429:
            raise SourceRateLimitedError(
                "Chess.com is rate limiting Caissa. Wait a few seconds and try again.",
                details={"username": username, "status": 429},
            )
        if response.status_code >= 400:
            raise SourceUnavailableError(
                f"Chess.com returned HTTP {response.status_code}.",
                details={"username": username, "status": response.status_code},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceResponseError(
                "Chess.com returned a response Caissa could not read.",
                details={"username": username, "url": url},
            ) from exc

    # --- endpoints ----------------------------------------------------------

    def get_profile(self, username: str) -> ChessComProfile:
        """Fetch a player's public profile."""
        handle = normalize_username(username)
        payload = self._get_json(f"/player/{handle}", username=handle)
        if not isinstance(payload, dict):
            raise SourceResponseError("Chess.com returned an unexpected profile payload.")
        return ChessComProfile(
            username=payload.get("username") or handle,
            player_id=payload.get("player_id"),
            name=payload.get("name"),
            url=payload.get("url"),
            country=payload.get("country"),
            avatar=payload.get("avatar"),
            title=payload.get("title"),
            status=payload.get("status"),
            followers=payload.get("followers"),
            joined=_parse_datetime(payload.get("joined")),
            last_online=_parse_datetime(payload.get("last_online")),
        )

    def list_archives(self, username: str) -> list[ChessComArchive]:
        """List the months the player has published games in (oldest first)."""
        handle = normalize_username(username)
        payload = self._get_json(f"/player/{handle}/games/archives", username=handle)
        archives = payload.get("archives") if isinstance(payload, dict) else None
        if not isinstance(archives, list):
            return []
        months: list[ChessComArchive] = []
        for url in archives:
            if not isinstance(url, str):
                continue
            parts = url.rstrip("/").split("/")
            if len(parts) < 2:
                continue
            try:
                year, month = int(parts[-2]), int(parts[-1])
            except ValueError:
                continue
            if not 1 <= month <= 12:
                continue
            months.append(
                ChessComArchive(
                    year=year,
                    month=month,
                    url=url,
                    label=f"{_MONTH_NAMES[month]} {year}",
                )
            )
        return months

    def get_month_games(
        self,
        username: str,
        year: int,
        month: int,
        *,
        standard_only: bool = True,
        time_class: str | None = None,
        limit: int | None = None,
    ) -> ChessComMonth:
        """Fetch one month of games for a player."""
        handle = normalize_username(username)
        if not 1 <= month <= 12:
            raise ValidationError("Month must be between 1 and 12.", details={"month": month})
        payload = self._get_json(
            f"/player/{handle}/games/{year:04d}/{month:02d}", username=handle
        )
        raw_games = payload.get("games") if isinstance(payload, dict) else None
        if not isinstance(raw_games, list):
            raise SourceResponseError(
                "Chess.com returned an unexpected games payload.",
                details={"username": handle, "year": year, "month": month},
            )

        games: list[ChessComGame] = []
        variants = 0
        for raw in raw_games:
            if not isinstance(raw, dict):
                continue
            game = _game_from_payload(raw)
            if game is None:
                continue
            if not game.is_standard:
                variants += 1
                if standard_only:
                    continue
            if time_class and (game.time_class or "") != time_class:
                continue
            games.append(game)

        if limit is not None and len(games) > limit:
            games = games[-limit:]  # newest, since Chess.com returns oldest first
            truncated = True
        else:
            truncated = False

        return ChessComMonth(
            username=handle,
            year=year,
            month=month,
            games=games,
            standard_count=len(games),
            variant_count=variants,
            truncated=truncated,
        )

    def get_recent_months(
        self, username: str, *, months: int = DEFAULT_MONTHS
    ) -> list[ChessComArchive]:
        """The most recent ``months`` archives that contain games."""
        handle = normalize_username(username)
        bounded = max(1, min(int(months), MAX_MONTHS))
        archives = self.list_archives(handle)
        return archives[-bounded:]

    def get_games_by_url(
        self, username: str, game_url: str, *, years_back: int = MAX_MONTHS
    ) -> ChessComGame | None:
        """Resolve one game by its Chess.com URL.

        The Published-Data API only serves whole months, so the archive that
        owns the game is located from the URL and that month is fetched once.
        Returns ``None`` when the game is not published in the searched months.
        """
        handle = normalize_username(username)
        match = re.search(r"/game/([a-z]+)/(\d+)", game_url, flags=re.IGNORECASE)
        if match is None:
            return None
        game_id = match.group(2)
        for archive in reversed(self.get_recent_months(handle, months=years_back)):
            month = self.get_month_games(
                handle, archive.year, archive.month, standard_only=False
            )
            for game in month.games:
                if game.url.endswith(game_id):
                    return game
        return None


def _game_from_payload(raw: dict[str, Any]) -> ChessComGame | None:
    """Build a :class:`ChessComGame` from one Chess.com game object."""
    pgn = raw.get("pgn")
    url = raw.get("url")
    if not isinstance(pgn, str) or not pgn.strip() or not isinstance(url, str):
        return None

    white = raw.get("white") if isinstance(raw.get("white"), dict) else {}
    black = raw.get("black") if isinstance(raw.get("black"), dict) else {}
    headers = _pgn_headers(pgn)

    return ChessComGame(
        url=url,
        uuid=raw.get("uuid") if isinstance(raw.get("uuid"), str) else None,
        pgn=pgn,
        time_class=raw.get("time_class"),
        time_control=raw.get("time_control"),
        rated=raw.get("rated") if isinstance(raw.get("rated"), bool) else None,
        rules=raw.get("rules"),
        variant=headers.get("Variant"),
        white_username=white.get("username"),
        black_username=black.get("username"),
        white_rating=white.get("rating") if isinstance(white.get("rating"), int) else None,
        black_rating=black.get("rating") if isinstance(black.get("rating"), int) else None,
        white_result=white.get("result"),
        black_result=black.get("result"),
        result=headers.get("Result"),
        end_time=_parse_datetime(raw.get("end_time")),
        eco_code=headers.get("ECO"),
        opening_name=None,  # Chess.com does not publish the opening name; Caissa detects it
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MONTHS",
    "DEFAULT_USER_AGENT",
    "MAX_MONTHS",
    "STANDARD_RULES",
    "ChessComArchive",
    "ChessComClient",
    "ChessComGame",
    "ChessComMonth",
    "ChessComProfile",
    "normalize_username",
]
