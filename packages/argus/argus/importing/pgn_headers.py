"""PGN tag-section reader shared by the platform source clients.

Both ``chesscom`` and ``lichess`` receive PGN text created by another service
and need to read its seven-tag section without parsing chess. Keeping that in
one place means the two platform clients cannot drift apart, and there is
exactly one implementation to test.
"""

from __future__ import annotations

__all__ = ["parse_pgn_headers"]


def parse_pgn_headers(pgn: str) -> dict[str, str]:
    """Read the seven-tag-style headers out of a PGN string.

    Only the tag section (the leading ``[Key "Value"]`` block) is read; the first
    non-tag, non-blank line ends it, so a ``[`` inside a comment in the movetext
    can never be mistaken for a header. Values are returned verbatim except for
    the surrounding quotes.

    Returns an empty mapping when the input has no tag section — never a guess.
    """
    headers: dict[str, str] = {}
    for line in pgn.splitlines():
        stripped = line.strip()
        if not stripped.startswith("["):
            if stripped:
                break
            continue
        try:
            key, _, value = stripped[1:-1].partition(" ")
            headers[key] = value.strip().strip('"')
        except (IndexError, ValueError):
            continue
    return headers
