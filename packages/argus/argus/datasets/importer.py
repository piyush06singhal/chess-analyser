"""Dataset ingestion — one normalized format out, whatever the source.

The importer is deliberately source-agnostic. A PGN file, a directory tree of
PGN exports, a future database cursor and a future API page all funnel through
:meth:`DatasetImporter._accept`, which is the only place that builds an
:class:`~argus.datasets.records.IngestedGame`.

Three things make the result trustworthy at scale:

``Streaming``
    Games are read one at a time and released, so a 10M-game corpus does not
    have to fit in memory. Callers receive records in batches through
    ``on_batch`` and the importer keeps only a hash set for deduplication.
``Stable ids``
    ``game_id`` is derived from the UCI move sequence, so re-ingesting the same
    corpus is idempotent and duplicates are detectable instead of invisible.
``Attribution``
    Every rejected game is recorded with a reason and a source position. The
    importer never silently drops input.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path

from argus.chess_core.models import Game
from argus.chess_core.pgn import parse_games
from argus.datasets.identity import build_identity
from argus.datasets.records import (
    PROCESSING_VERSION,
    IngestedGame,
    IngestionStats,
    RejectedGame,
)
from argus.player_intelligence import TimeClass, classify_time_control
from argus.shared.errors import ArgusError
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Default batch size for streaming ingestion. Bounded so peak memory stays
#: flat regardless of corpus size.
DEFAULT_BATCH_SIZE = 2_000

#: Safety cap handed to the PGN reader. Deliberately far above the policy limit
#: (``DatasetImporter.max_plies``) so the *policy* decides what is too long and
#: reports it as ``game_too_long`` — rather than the reader rejecting first and
#: the record being mislabelled as malformed input.
PARSER_MAX_PLIES = 1_200

BatchCallback = Callable[[list[IngestedGame]], None]


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _short(payload: str, length: int = 16) -> str:
    return _sha256(payload)[:length]


def stable_game_id(moves_hash: str, metadata_hash: str, occurrence: int = 0) -> str:
    """The canonical game id formula, shared by every layer.

    Exposed so the position layer can derive ids with *exactly* the same rule: a
    position row that cannot be joined back to its game record is a silent
    data-integrity failure.
    """
    return _short(f"{moves_hash}|{metadata_hash}|{occurrence}")


def _split_games(lines: Iterator[str]) -> Iterator[tuple[int, str]]:
    """Split a stream of PGN lines into one game's text at a time.

    A game ends where tag pairs resume after movetext, which is how every
    exporter separates games. Splitting *here* rather than handing the whole
    payload to the parser is what keeps memory flat on a large corpus, and it
    also guarantees each game is parsed with its own headers: python-chess
    attaches a following tag section to the previous game when two games are
    concatenated, which would silently strip the second game's players.
    """
    buffer: list[str] = []
    index = -1
    for line in lines:
        starts_tag = line.startswith("[")
        if starts_tag and buffer and any(
            not candidate.startswith("[") for candidate in buffer
        ):
            index += 1
            yield index, "".join(buffer)
            buffer = []
        buffer.append(line)
    if buffer:
        index += 1
        yield index, "".join(buffer)


def iter_game_texts(path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield ``(index, pgn_text)`` one game at a time from a PGN file."""
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        yield from _split_games(iter(handle))


def iter_game_texts_from_text(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(index, pgn_text)`` one game at a time from an in-memory payload.

    The same splitter the file path uses, so an ingested string and an ingested
    file produce identical records.
    """
    yield from _split_games(iter(text.splitlines(keepends=True)))


def identity_hashes(game: Game) -> tuple[str, str]:
    """``(moves_hash, metadata_hash)`` for a parsed game.

    ``moves_hash`` answers "is this the same game as played"; ``metadata_hash``
    answers "was it recorded the same way". Keeping them separate is what lets
    deduplication distinguish a re-import from a corrected record.
    """
    uci = " ".join(move.uci for move in game.moves)
    white = build_identity(game.white_player.name)
    black = build_identity(game.black_player.name)
    metadata_hash = _sha256(
        f"{white.identity_key}|{black.identity_key}|{game.date_iso or ''}|{game.result.value}"
    )
    return _sha256(uci), metadata_hash


def game_to_record(
    game: Game,
    *,
    source: str,
    source_version: str | None = None,
    source_file: str | None = None,
    source_index: int | None = None,
    occurrence: int = 0,
) -> IngestedGame:
    """Map one parsed game onto the canonical record.

    Nothing is inferred: a header the source did not provide stays ``None``.

    ``occurrence`` disambiguates a record from an identical one already seen in
    the same ingestion run. Without it, two identical copies of a game would share
    an id, and any bookkeeping keyed by id (deduplication, splits, leakage
    checks) would then be unable to tell them apart.
    """
    san = " ".join(move.san for move in game.moves)
    moves_hash, metadata_hash = identity_hashes(game)
    movetext_hash = _sha256(san)

    white = build_identity(game.white_player.name)
    black = build_identity(game.black_player.name)
    date_iso = game.date_iso

    control = game.time_control
    time_class = classify_time_control(control.initial_seconds, control.increment_seconds)

    return IngestedGame(
        # Content-derived, so the same game re-ingested from the same source keeps
        # its id; the occurrence suffix keeps two copies of it distinguishable.
        game_id=(
            stable_game_id(moves_hash, metadata_hash, occurrence)
            if game.moves
            else _short(f"{metadata_hash}|{movetext_hash}|{occurrence}")
        ),
        source=source,
        source_version=source_version,
        source_file=source_file,
        source_index=source_index,
        white=white,
        black=black,
        white_rating=game.white_rating,
        black_rating=game.black_rating,
        result=game.result.value,
        date_raw=game.date,
        date_iso=date_iso,
        event=game.event,
        site=game.site,
        time_control_raw=control.raw,
        time_control_initial_seconds=control.initial_seconds,
        time_control_increment_seconds=control.increment_seconds,
        time_class=None if time_class is TimeClass.UNKNOWN else time_class.value,
        eco=game.opening.eco_code,
        opening_name=game.opening.name,
        ply_count=game.move_count,
        moves_hash=moves_hash,
        movetext_hash=movetext_hash,
        metadata_hash=metadata_hash,
    )



class IngestionResult:
    """Records plus the accounting of how they were produced."""

    def __init__(
        self,
        records: list[IngestedGame],
        stats: IngestionStats,
        rejected: list[RejectedGame],
    ) -> None:
        self.records = records
        self.stats = stats
        self.rejected = rejected

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"IngestionResult(records={len(self.records)}, "
            f"rejected={len(self.rejected)}, seen={self.stats.games_seen})"
        )


class DatasetImporter:
    """Source-agnostic ingestion into normalized game records."""

    def __init__(
        self,
        *,
        source: str = "pgn",
        source_version: str | None = None,
        processing_version: str = PROCESSING_VERSION,
        max_plies: int | None = 600,
        deduplicate: bool = False,
    ) -> None:
        self.source = source
        self.source_version = source_version
        self.processing_version = processing_version
        #: Games longer than this are refused: a 1000-ply "game" is almost always
        #: a concatenation failure, and it would poison length statistics.
        self.max_plies = max_plies
        #: Off by default. Duplicates are *kept* and classified by
        #: :mod:`argus.datasets.dedupe`, because dropping them here would destroy
        #: the difference between an exact re-import and the same moves with
        #: different metadata — and that difference is what the report explains.
        self.deduplicate = deduplicate
        #: moves_hash → the metadata hashes seen for it (small: one entry per game).
        self._seen: dict[str, set[str]] = {}
        #: "moves|metadata" → how many times it has been seen, for id disambiguation.
        self._occurrences: dict[str, int] = {}
        self._stats = IngestionStats()
        self._rejected: list[RejectedGame] = []
        self._accepted: list[IngestedGame] = []

    # --- accounting ---------------------------------------------------------

    @property
    def stats(self) -> IngestionStats:
        return self._stats

    def _reject(
        self, reason: str, message: str, *, source_file: str | None, index: int | None
    ) -> None:
        self._stats.reject(reason)
        self._rejected.append(
            RejectedGame(
                source_file=source_file, source_index=index, reason=reason, message=message
            )
        )

    def _accept(self, record: IngestedGame) -> IngestedGame | None:
        """Validate, deduplicate and keep one record.

        Returns the record when it was kept, ``None`` when it was a duplicate or
        was rejected (either way the accounting has already recorded why).
        """
        if record.ply_count == 0:
            self._reject(
                "no_moves",
                "Game has headers but no moves",
                source_file=record.source_file,
                index=record.source_index,
            )
            return None
        if self.max_plies is not None and record.ply_count > self.max_plies:
            self._reject(
                "game_too_long",
                f"{record.ply_count} plies exceeds the {self.max_plies}-ply limit",
                source_file=record.source_file,
                index=record.source_index,
            )
            return None

        # Always account for duplication, whether or not this run drops it: the
        # count is a property of the source, not of the flag.
        seen_metadata = self._seen.setdefault(record.moves_hash, set())
        if seen_metadata:
            if record.metadata_hash in seen_metadata:
                self._stats.duplicates_exact += 1
            else:
                self._stats.duplicates_metadata_variant += 1
            if self.deduplicate:
                return None
        seen_metadata.add(record.metadata_hash)

        self._stats.games_accepted += 1
        self._accepted.append(record)
        return record

    # --- sources ------------------------------------------------------------

    def ingest_text(
        self, text: str, *, source_file: str | None = None
    ) -> IngestionResult:
        """Ingest one PGN payload (already in memory).

        Split with the same rule as a file, so concatenated games keep their own
        headers.
        """
        self._ingest_pieces(iter_game_texts_from_text(text), source_file=source_file)
        return self.result()

    def ingest_file(self, path: str | Path) -> IngestionResult:
        """Ingest a single PGN file, streaming game by game."""
        file_path = Path(path)
        self._stats.files_read += 1
        self._stats.bytes_read += file_path.stat().st_size if file_path.is_file() else 0
        self._ingest_pieces(iter_game_texts(file_path), source_file=str(file_path))
        return self.result()

    def ingest_directory(
        self, root: str | Path, *, pattern: str = "*.pgn"
    ) -> IngestionResult:
        """Ingest every matching file under a directory (sorted, deterministic)."""
        files = sorted(Path(root).glob(pattern))
        logger.info("Ingesting %d file(s) from %s", len(files), root)
        for path in files:
            self.ingest_file(path)
        return self.result()

    def ingest_paths(
        self,
        paths: list[str | Path],
        *,
        on_batch: BatchCallback | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> IngestionResult:
        """Ingest many files, streaming batches to ``on_batch`` as they fill.

        This is the entry point for a large corpus: records never have to be held
        in memory all at once, because ``on_batch`` is called every
        ``batch_size`` accepted games and the batch is then released.
        """
        pending: list[IngestedGame] = []
        for path in paths:
            file_path = Path(path)
            self._stats.files_read += 1
            self._stats.bytes_read += file_path.stat().st_size if file_path.is_file() else 0
            for record in self._iter_records(iter_game_texts(file_path), source_file=str(file_path)):
                pending.append(record)
                if on_batch is not None and len(pending) >= batch_size:
                    on_batch(pending)
                    pending = []
        if pending and on_batch is not None:
            on_batch(pending)
        return self.result()

    def _ingest_pieces(
        self, pieces, *, source_file: str | None  # noqa: ANN001 — iterator of (index, text)
    ) -> None:
        for _ in self._iter_records(pieces, source_file=source_file):
            pass

    def _occurrence_for(self, game: Game) -> int:
        """How many identical records this run has already seen for this game."""
        moves_hash, metadata_hash = identity_hashes(game)
        key = f"{moves_hash}|{metadata_hash}"
        occurrence = self._occurrences.get(key, 0)
        self._occurrences[key] = occurrence + 1
        return occurrence

    def _iter_records(
        self, pieces, *, source_file: str | None  # noqa: ANN001 — iterator of (index, text)
    ) -> Iterator[IngestedGame]:
        """Turn ``(index, pgn_text)`` pieces into accepted records."""
        for index, text in pieces:
            self._stats.games_seen += 1
            try:
                games = parse_games(text, max_plies=PARSER_MAX_PLIES)
            except ArgusError as exc:
                self._reject("malformed_pgn", exc.message, source_file=source_file, index=index)
                continue
            except Exception as exc:  # noqa: BLE001 — the parser raises arbitrary errors
                self._reject(
                    "malformed_pgn", f"unreadable PGN: {exc}", source_file=source_file, index=index
                )
                continue

            if not games:
                self._reject(
                    "no_games", "no game found in payload", source_file=source_file, index=index
                )
                continue

            for game in games:
                record = game_to_record(
                    game,
                    source=self.source,
                    source_version=self.source_version,
                    source_file=source_file,
                    source_index=index,
                    occurrence=self._occurrence_for(game),
                )
                accepted = self._accept(record)
                if accepted is not None:
                    yield accepted

    def result(self) -> IngestionResult:
        """The current result (records so far, stats, rejections)."""
        from datetime import datetime, timezone

        self._stats.finished_at = datetime.now(timezone.utc)
        return IngestionResult(list(self._accepted), self._stats, list(self._rejected))

    def reset(self) -> None:
        """Forget everything ingested so far (used between corpus runs)."""
        self._seen.clear()
        self._occurrences.clear()
        self._stats = IngestionStats()
        self._rejected = []
        self._accepted = []


def read_pgn_source(path: str | Path) -> IngestionResult:
    """Convenience: ingest one PGN file with a default importer."""
    return DatasetImporter(source=str(path)).ingest_file(path)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "PARSER_MAX_PLIES",
    "DatasetImporter",
    "IngestionResult",
    "game_to_record",
    "identity_hashes",
    "stable_game_id",
    "iter_game_texts",
    "iter_game_texts_from_text",
    "read_pgn_source",
]
