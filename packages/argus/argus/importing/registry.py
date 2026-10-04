"""Importer registry.

Resolves an :class:`ImportSource` to a concrete :class:`GameImporter`. Future
platform importers register here; nothing downstream changes because every
importer returns the same internal representation.
"""

from __future__ import annotations

from argus.importing.base import GameImporter, ImportSource
from argus.importing.pgn_importer import PgnImporter
from argus.shared.errors import UnsupportedSourceError

_IMPORTERS: dict[ImportSource, GameImporter] = {
    ImportSource.PGN_TEXT: PgnImporter(ImportSource.PGN_TEXT),
    ImportSource.PGN_FILE: PgnImporter(ImportSource.PGN_FILE),
    # Both platform sources serve PGN, so their importer is the PGN importer with
    # a different provenance: fetching is the client's job
    # (argus.importing.chesscom / argus.importing.lichess), parsing stays in
    # exactly one place.
    ImportSource.CHESS_COM: PgnImporter(ImportSource.CHESS_COM),
    ImportSource.LICHESS: PgnImporter(ImportSource.LICHESS),
}

# Declared for forward compatibility but intentionally unimplemented. Empty
# today: Chess.com, Lichess, pasted PGN and uploaded PGN are all implemented.
_PLANNED_SOURCES: set[str] = set()


def get_importer(source: ImportSource | str) -> GameImporter:
    """Return the importer for ``source``.

    Raises:
        UnsupportedSourceError: when the source is unknown, or is a planned
            platform source that is not implemented yet (honest 422, never a
            silent fallback).

    Note:
        A platform source registered here means Caissa can *parse* that source's
        PGN. Fetching games from a platform is a separate concern handled by
        ``argus.importing.chesscom`` and the ``/api/sources`` routes.
    """
    try:
        key = ImportSource(source)
    except ValueError as exc:
        raise UnsupportedSourceError(
            f"Unknown import source {source!r}",
            details={
                "supported": available_sources(),
                "hint": "Games from a site Caissa does not read can still be imported as PGN.",
            },
        ) from exc
    importer = _IMPORTERS.get(key)
    if importer is None:
        raise UnsupportedSourceError(
            f"Import source {key.value!r} is not implemented yet",
            details={"planned": sorted(_PLANNED_SOURCES), "supported": available_sources()},
        )
    return importer


def available_sources() -> list[str]:
    """Sources currently implemented."""
    return sorted(source.value for source in _IMPORTERS)


def planned_sources() -> list[str]:
    """Sources declared but not yet implemented."""
    return sorted(_PLANNED_SOURCES)
