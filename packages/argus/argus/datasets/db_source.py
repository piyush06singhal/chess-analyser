"""Database game source — ingest Caissa's own stored games as a dataset.

The PGN importer covers files. This module covers the other half of the spec's
ingestion interface: rows already in the Caissa database, normalised into the same
``IngestedGame`` records by the same importer, so validation, deduplication,
labelling and manifests behave identically no matter where a game came from.

Two rules are enforced here rather than documented and hoped for:

**Scope separation.**
    A dataset carries a :class:`DatasetScope`. ``GLOBAL`` is a public or neutral
    corpus; ``USER`` is an account's own games. The database is a user source, so
    building one requires an explicit ``allow_user_scope=True``. Private games are
    therefore never folded into a training set by accident.

**One extraction path.**
    Games are re-serialised from their *normalised* columns (headers plus the
    stored SAN move list) rather than replayed from the original import payload.
    Stored ``pgn_text`` may hold many games in one blob and would ingest as
    duplicates; the normalised columns are exactly one game each.

The module is read-only: it opens the database, reads, and closes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from argus.datasets.importer import DatasetImporter, IngestionResult
from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Rows fetched per round trip. A dataset must be able to grow past memory, so the
#: cursor streams rather than materialising every game at once.
DEFAULT_BATCH_SIZE = 500


class DatasetScope(str, Enum):
    """Who the rows belong to, in the sense that governs training rights."""

    GLOBAL = "global"
    USER = "user"


@dataclass(slots=True)
class SourcedGame:
    """One game read from a source, with its provenance."""

    source_game_id: str
    pgn_text: str
    scope: DatasetScope
    owner_key: str | None = None
    origin: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


def _clean_header(value: Any) -> str:
    """Make a value safe for a PGN header line.

    PGN headers cannot contain a quote or a newline, and an unclean value would
    silently truncate the header — losing exactly the metadata that predictions
    depend on.
    """
    if value is None:
        return ""
    return str(value).replace('"', "'").replace("\n", " ").replace("\r", " ").strip()


def _header(name: str, value: Any) -> str | None:
    cleaned = _clean_header(value)
    return f'[{name} "{cleaned}"]' if cleaned else None


def render_pgn(
    *,
    headers: list[str | None],
    moves: Sequence[tuple[str, int, str]],
    starting_fen: str | None = None,
) -> str:
    """Render one game's headers and SAN moves as a single-game PGN payload.

    ``moves`` is ``(color, move_number, san)`` in ply order. The colour is carried
    explicitly rather than inferred from position, so a game that starts from a
    custom FEN with Black to move numbers correctly (``1... d5``) instead of
    silently shifting every move.
    """
    lines = [header for header in headers if header]
    if starting_fen:
        lines.append(f'[FEN "{_clean_header(starting_fen)}"]')
        lines.append('[SetUp "1"]')
    result = ""
    for header in lines:
        if header.startswith("[Result "):
            result = header.split('"')[1] if '"' in header else ""
            break

    body: list[str] = []
    for index, (color, move_number, san) in enumerate(moves):
        if color == "white":
            body.append(f"{move_number}. {san}")
        elif index == 0:
            # Black opens from a custom position: the move number needs a marker.
            body.append(f"{move_number}... {san}")
        else:
            body.append(san)
    rendered = " ".join(body)
    if result:
        rendered = f"{rendered} {result}".strip()
    return "\n".join(lines) + "\n\n" + rendered + "\n"


#: Normalised columns read from ``games``. Only what the pipeline uses: a dataset
#: should not carry a user's whole row just because it was available.
_GAME_COLUMNS = (
    "id",
    "white_player_name",
    "black_player_name",
    "white_rating",
    "black_rating",
    "result",
    "date",
    "event",
    "site",
    "time_control",
    "eco_code",
    "opening_name",
    "initial_position",
    "move_count",
    "source",
    "source_game_id",
    "created_at",
)


def _game_query(where: str) -> str:
    columns = ", ".join(f"g.{name}" for name in _GAME_COLUMNS)
    return (
        f"SELECT {columns}, m.ply AS ply, m.move_number AS move_number, "
        "m.color AS color, m.san AS san "
        "FROM games g LEFT JOIN game_moves m ON m.game_id = g.id "
        f"{where} ORDER BY g.id, m.ply"
    )


class DatabaseGameSource:
    """Read games out of a Caissa database, one game at a time.

    Example::

        source = DatabaseGameSource(url)                     # user-scoped by default
        result = source.ingest(importer, allow_user_scope=True)
    """

    def __init__(
        self,
        url: str,
        *,
        scope: DatasetScope = DatasetScope.USER,
        owner_key: str | None = None,
        label: str = "argus-db",
        where: str | None = None,
        engine: Engine | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.url = url
        self.scope = scope
        self.owner_key = owner_key
        self.label = label
        self.where = where or ""
        self.batch_size = batch_size
        self._engine = engine
        self._owns_engine = engine is None

    # --- plumbing -----------------------------------------------------------

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self.url, future=True)
        return self._engine

    def close(self) -> None:
        if self._engine is not None and self._owns_engine:
            self._engine.dispose()
            self._engine = None

    def __enter__(self) -> "DatabaseGameSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        """Provenance for the manifest: what was read and from where."""
        described = {
            "kind": "database",
            "label": self.label,
            "scope": self.scope.value,
            "filter": self.where.strip() or None,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.owner_key:
            described["owner_key"] = self.owner_key
        # The driver/dialect is useful for reproducibility; the URL carries
        # credentials and is deliberately not recorded.
        if self._engine is not None:
            described["dialect"] = self._engine.dialect.name
        return described

    # --- reading ------------------------------------------------------------

    def iter_games(self, *, limit: int | None = None) -> Iterator[SourcedGame]:
        """Yield one :class:`SourcedGame` per game, streaming from the cursor.

        Rows arrive ordered by game id then ply, so a game is complete as soon as
        the id changes. Nothing beyond the current game is held in memory.
        """
        statement = text(_game_query(self.where))
        current_id: str | None = None
        columns: dict[str, Any] = {}
        moves: list[tuple[str, int, str]] = []
        emitted = 0

        def build() -> SourcedGame:
            return self._to_sourced(columns, moves)

        with self.engine.connect().execution_options(stream_results=True) as connection:
            result = connection.execute(statement)
            for row in result.mappings():
                game_id = str(row["id"])
                if current_id is not None and game_id != current_id:
                    yield build()
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return
                    moves = []
                current_id = game_id
                columns = {name: row[name] for name in _GAME_COLUMNS}
                move_number = row["move_number"]
                san = row["san"]
                if san is not None and move_number is not None:
                    moves.append((str(row["color"]), int(move_number), str(san)))
            if current_id is not None:
                yield build()

    def _to_sourced(
        self, columns: dict[str, Any], moves: list[tuple[str, int, str]]
    ) -> SourcedGame:
        date = columns.get("date")
        headers = [
            _header("Event", columns.get("event") or "Caissa"),
            _header("Site", columns.get("site") or "Caissa"),
            _header("Date", (date or "").replace("-", ".")),
            _header("Round", "-"),
            _header("White", columns.get("white_player_name") or "White"),
            _header("Black", columns.get("black_player_name") or "Black"),
            _header("Result", columns.get("result") or "*"),
            _header("WhiteElo", columns.get("white_rating")),
            _header("BlackElo", columns.get("black_rating")),
            _header("ECO", columns.get("eco_code")),
            _header("Opening", columns.get("opening_name")),
            _header("TimeControl", columns.get("time_control")),
        ]
        initial = columns.get("initial_position")
        starting_fen = initial if initial and not str(initial).startswith(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
        ) else None
        pgn = render_pgn(headers=headers, moves=moves, starting_fen=starting_fen)
        return SourcedGame(
            source_game_id=str(columns.get("source_game_id") or columns.get("id")),
            pgn_text=pgn,
            scope=self.scope,
            owner_key=self.owner_key,
            origin=self.label,
            metadata={
                "argus_game_id": str(columns.get("id")),
                "ply_count": len(moves),
                "ratings_present": bool(
                    columns.get("white_rating") is not None
                    and columns.get("black_rating") is not None
                ),
            },
        )

    # --- ingestion ----------------------------------------------------------

    def ingest(
        self,
        importer: DatasetImporter,
        *,
        allow_user_scope: bool = False,
        limit: int | None = None,
    ) -> IngestionResult:
        """Feed every game through ``importer`` and return its ingestion result.

        Fails closed: a user-scoped source cannot be ingested without the caller
        stating, in code, that private games are intended.
        """
        assert_scope_allowed(self.scope, allow_user_scope=allow_user_scope)
        count = 0
        try:
            for game in self.iter_games(limit=limit):
                importer.ingest_text(game.pgn_text, source_file=f"db:{game.source_game_id}")
                count += 1
        finally:
            self.close()
        logger.info(
            "Ingested %d game(s) from database source '%s' [scope=%s]",
            count,
            self.label,
            self.scope.value,
        )
        return importer.result()


def assert_scope_allowed(scope: DatasetScope, *, allow_user_scope: bool) -> None:
    """Refuse to turn private games into a dataset without an explicit opt-in."""
    if scope is DatasetScope.USER and not allow_user_scope:
        raise ValidationError(
            "This source is user-scoped: it holds an account's own games. Building a "
            "dataset from it requires allow_user_scope=True, because private games must "
            "never enter a global training set by accident."
        )


def database_source_from_settings(
    *, scope: DatasetScope | None = None, where: str | None = None
) -> "DatabaseGameSource":
    """A source pointed at the configured Caissa database.

    Kept behind a default-argument-free factory so importing this module never
    requires application settings to exist (tests and scripts build their own).
    """
    from argus_api.config import get_settings  # local import: avoid a hard dependency

    settings = get_settings()
    return DatabaseGameSource(
        settings.database_url,
        scope=scope or DatasetScope.USER,
        where=where,
        label="argus-db",
    )


def local_sqlite_source(path: str | Path, **kwargs: Any) -> DatabaseGameSource:
    """A source over a local SQLite file — convenient for scripts and tests."""
    return DatabaseGameSource(f"sqlite+pysqlite:///{Path(path)}", **kwargs)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DatabaseGameSource",
    "DatasetScope",
    "SourcedGame",
    "assert_scope_allowed",
    "database_source_from_settings",
    "local_sqlite_source",
    "render_pgn",
]
