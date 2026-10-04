"""Dataset validation — a structured report, never a silent filter.

A dataset pipeline that silently drops bad rows is worse than one that keeps
them, because the row count then lies about the source. So validation *describes*
what is wrong with each record, and the decision about what to do with it is
explicit and recorded (see :mod:`argus.datasets.dedupe` for the duplicate case).

Errors mean "this record cannot be used for prediction"; warnings mean "this
record is usable but its limitations must travel with it" (a missing rating, an
unknown result). Both are counted, and both name the records they apply to.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from argus.datasets.identity import is_placeholder_name
from argus.datasets.records import IngestedGame

#: Chess has no recorded game before this year; a source claiming one is corrupt.
EARLIEST_PLAUSIBLE_YEAR = 1475
#: 300 moves is already extreme; beyond it a "game" is a concatenation failure.
MAX_PLAUSIBLE_PLIES = 600
#: Ratings outside this range do not exist on any platform Caissa reads.
MIN_PLAUSIBLE_RATING = 100
MAX_PLAUSIBLE_RATING = 4000

DRAW = "1/2-1/2"
UNKNOWN_RESULT = "*"
PLAYED_RESULTS = {"1-0", "0-1", DRAW}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class DatasetIssue(BaseModel):
    """One concrete problem with one concrete record."""

    severity: IssueSeverity
    code: str
    message: str
    game_id: str | None = None
    field: str | None = None
    source_file: str | None = None
    source_index: int | None = None


class ValidationReport(BaseModel):
    """The outcome of validating a batch of records."""

    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    duplicate_records: int = Field(
        default=0, description="Records whose move sequence repeats an earlier record"
    )
    issues: list[DatasetIssue] = Field(default_factory=list)
    #: Every record id seen, in order, so "valid" can be answered without keeping
    #: the records themselves. Private: it is bookkeeping, not part of the report.
    _all_ids: list[str] = []

    @property
    def errors(self) -> list[DatasetIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.ERROR]

    @property
    def warnings(self) -> list[DatasetIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.WARNING]

    @property
    def is_valid(self) -> bool:
        """A dataset is valid when nothing in it is an error."""
        return not self.errors

    @property
    def valid_ids(self) -> list[str]:
        invalid = set(self.invalid_ids)
        return [game_id for game_id in self._all_ids if game_id not in invalid]

    @property
    def invalid_ids(self) -> list[str]:
        return sorted({issue.game_id for issue in self.errors if issue.game_id})

    def codes(self, severity: IssueSeverity) -> dict[str, int]:
        """Issue code → count, for a readable summary."""
        counts: dict[str, int] = {}
        for issue in self.issues:
            if issue.severity is severity:
                counts[issue.code] = counts.get(issue.code, 0) + 1
        return dict(sorted(counts.items()))


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    game: IngestedGame,
    *,
    field: str | None = None,
) -> DatasetIssue:
    return DatasetIssue(
        severity=severity,
        code=code,
        message=message,
        game_id=game.game_id,
        field=field,
        source_file=game.source_file,
        source_index=game.source_index,
    )


def validate_record(game: IngestedGame) -> tuple[list[DatasetIssue], list[DatasetIssue]]:
    """Return ``(errors, warnings)`` for one record."""
    errors: list[DatasetIssue] = []
    warnings: list[DatasetIssue] = []

    # --- structural ---------------------------------------------------------
    if game.ply_count <= 0:
        errors.append(_issue(IssueSeverity.ERROR, "no_moves", "Game has no moves", game))
    elif game.ply_count > MAX_PLAUSIBLE_PLIES:
        errors.append(
            _issue(
                IssueSeverity.ERROR,
                "impossible_game_length",
                f"{game.ply_count} plies exceeds the plausible maximum of {MAX_PLAUSIBLE_PLIES}",
                game,
                field="ply_count",
            )
        )
    elif game.ply_count < 4:
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "very_short_game",
                f"Only {game.ply_count} plies — too short for most game-level tasks",
                game,
                field="ply_count",
            )
        )

    if not game.moves_hash or not game.movetext_hash:
        errors.append(
            _issue(
                IssueSeverity.ERROR,
                "corrupted_record",
                "Move hashes are missing, so this record cannot be deduplicated or split safely",
                game,
            )
        )

    # --- players ------------------------------------------------------------
    for label, identity in (("white", game.white), ("black", game.black)):
        if is_placeholder_name(identity.original_name):
            errors.append(
                _issue(
                    IssueSeverity.ERROR,
                    "missing_player",
                    f"{label.capitalize()} player name is a placeholder "
                    f"({identity.original_name!r})",
                    game,
                    field=label,
                )
            )
    if game.white.identity_key == game.black.identity_key:
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "same_player_both_colours",
                "Both colours resolve to the same player identity",
                game,
            )
        )

    # --- ratings ------------------------------------------------------------
    for label, rating in (("white_rating", game.white_rating), ("black_rating", game.black_rating)):
        if rating is None:
            continue
        if rating < MIN_PLAUSIBLE_RATING or rating > MAX_PLAUSIBLE_RATING:
            errors.append(
                _issue(
                    IssueSeverity.ERROR,
                    "impossible_rating",
                    f"{label.replace('_', ' ')} {rating} is outside the plausible range "
                    f"{MIN_PLAUSIBLE_RATING}–{MAX_PLAUSIBLE_RATING}",
                    game,
                    field=label,
                )
            )
    if game.white_rating is None or game.black_rating is None:
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "missing_rating",
                "One or both ratings are absent — rating-based features are unavailable here",
                game,
                field="rating",
            )
        )

    # --- result and date ----------------------------------------------------
    if game.result == UNKNOWN_RESULT:
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "missing_result",
                "Game has no recorded result, so it carries no outcome label",
                game,
                field="result",
            )
        )
    elif game.result not in PLAYED_RESULTS:
        errors.append(
            _issue(
                IssueSeverity.ERROR,
                "invalid_result",
                f"Unsupported result value {game.result!r}",
                game,
                field="result",
            )
        )

    if game.date_iso:
        if not _ISO_DATE.match(game.date_iso):
            errors.append(
                _issue(
                    IssueSeverity.ERROR,
                    "invalid_timestamp",
                    f"Date {game.date_iso!r} is not an ISO date",
                    game,
                    field="date_iso",
                )
            )
        else:
            year = int(game.date_iso[:4])
            parsed = date.fromisoformat(game.date_iso)
            if year < EARLIEST_PLAUSIBLE_YEAR:
                errors.append(
                    _issue(
                        IssueSeverity.ERROR,
                        "impossible_metadata",
                        f"Date {game.date_iso} predates recorded chess",
                        game,
                        field="date_iso",
                    )
                )
            elif parsed > date.today():
                warnings.append(
                    _issue(
                        IssueSeverity.WARNING,
                        "future_date",
                        f"Date {game.date_iso} is in the future",
                        game,
                        field="date_iso",
                    )
                )
    else:
        # A missing date is not an error for random/game splits, but it makes the
        # game unusable for a temporal split — which is exactly why it is flagged.
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "missing_date",
                "No usable date — excluded from temporal splits",
                game,
                field="date_iso",
            )
        )

    # --- time control -------------------------------------------------------
    if not game.time_control_raw or game.time_control_raw in {"-", "?", "*"}:
        warnings.append(
            _issue(
                IssueSeverity.WARNING,
                "missing_time_control",
                "No time control recorded — time-class features are unavailable here",
                game,
                field="time_control_raw",
            )
        )

    return errors, warnings


class ValidatedDataset(BaseModel):
    """Records plus the report that describes them."""

    records: list[IngestedGame] = Field(default_factory=list)
    report: ValidationReport = Field(default_factory=ValidationReport)

    @property
    def valid_records(self) -> list[IngestedGame]:
        invalid = set(self.report.invalid_ids)
        return [record for record in self.records if record.game_id not in invalid]


def validate_records(records: list[IngestedGame]) -> ValidatedDataset:
    """Validate a batch of records.

    Duplicate counting is done here (via move hashes) because "how many duplicates
    does this source contain" is a data-quality fact that must be visible even
    when the caller then chooses to keep them.
    """
    report = ValidationReport(total_records=len(records))
    seen: dict[str, int] = {}
    for record in records:
        seen[record.moves_hash] = seen.get(record.moves_hash, 0) + 1

    invalid: set[str] = set()
    for record in records:
        errors, warnings = validate_record(record)
        report.issues.extend(errors)
        report.issues.extend(warnings)
        if errors:
            invalid.add(record.game_id)

    report.duplicate_records = sum(count - 1 for count in seen.values() if count > 1)
    report.invalid_records = len(invalid)
    report.valid_records = len(records) - len(invalid)
    report._all_ids = [record.game_id for record in records]
    return ValidatedDataset(records=list(records), report=report)


__all__ = [
    "EARLIEST_PLAUSIBLE_YEAR",
    "MAX_PLAUSIBLE_PLIES",
    "DatasetIssue",
    "IssueSeverity",
    "ValidatedDataset",
    "ValidationReport",
    "validate_record",
    "validate_records",
]
