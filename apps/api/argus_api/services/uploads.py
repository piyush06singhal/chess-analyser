"""Upload handling for PGN files.

Uploaded content is treated strictly as data: it is never executed, never
written to a user-controlled path, and never trusted. These helpers enforce
extension, MIME type, size and encoding rules and sanitize the displayed
filename, raising :class:`UploadError` (mapped to HTTP 422) on violation.
"""

from __future__ import annotations

import re
from pathlib import Path

from argus.shared.errors import UploadError

MAX_UPLOAD_BYTES = 2_000_000  # 2 MB — far larger than any real PGN, bounded on purpose
ALLOWED_EXTENSIONS = {".pgn", ".txt"}
ALLOWED_MIME_TYPES = {
    "application/x-chess-pgn",
    "application/pgn",
    "text/plain",
    "application/octet-stream",  # browsers often send this for unknown types
    "application/vnd.chess-pgn",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str | None) -> str:
    """Return a safe, display-only filename (no path components)."""
    base = Path(name or "upload.pgn").name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "upload.pgn"
    return cleaned[:120]


def validate_upload(*, filename: str | None, content_type: str | None, data: bytes) -> str:
    """Validate an uploaded PGN file and return its decoded text.

    Raises:
        UploadError: for an empty file, unsupported extension/MIME type, or an
            oversized or non-UTF-8 payload.
    """
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UploadError(
            f"Unsupported file type {suffix or '(none)'!s}. Upload a .pgn or .txt file.",
            details={"allowed_extensions": sorted(ALLOWED_EXTENSIONS)},
        )
    if content_type and content_type.split(";")[0].strip() not in ALLOWED_MIME_TYPES:
        raise UploadError(
            f"Unsupported content type {content_type!r}. Upload a PGN text file.",
            details={"allowed_mime_types": sorted(ALLOWED_MIME_TYPES)},
        )
    if not data:
        raise UploadError("Uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"Uploaded file is too large ({len(data)} bytes); the limit is "
            f"{MAX_UPLOAD_BYTES} bytes.",
            details={"max_bytes": MAX_UPLOAD_BYTES},
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadError(
            "Uploaded file is not valid UTF-8 text", details={"reason": str(exc)}
        ) from exc
