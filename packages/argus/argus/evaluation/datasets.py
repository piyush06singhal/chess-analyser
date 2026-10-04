"""Evaluation dataset descriptors (§4).

Every benchmark dataset declares what it is before it is used: an id, a version,
its source, its licence, when it was created, a description and its sample
count. That is what lets a report say *which* data produced a result — and lets a
licence problem be caught before data reaches a benchmark, not after.

These datasets are Caissa-authored fixtures of real, public-domain chess records
and standard positions. No user data is used in a benchmark, and no dataset is
built by scraping without a recorded licence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from argus.evaluation.fixtures import (
    FEN_CASES,
    FIXTURE_VERSION,
    POSITION_CASES,
)

#: The licence Caissa's own evaluation fixtures are held under.
ARGUS_FIXTURE_LICENCE = "Caissa-authored fixtures of public-domain chess records — internal use."


@dataclass(frozen=True)
class EvaluationDataset:
    """A benchmark dataset's identity and provenance."""

    dataset_id: str
    version: str
    source: str
    license: str
    created_at: str
    description: str
    sample_count: int

    def to_payload(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "source": self.source,
            "license": self.license,
            "created_at": self.created_at,
            "description": self.description,
            "sample_count": self.sample_count,
        }


def _created() -> str:
    # A fixture dataset's creation date is stable per release, not per process.
    return datetime(2026, 10, 2, tzinfo=timezone.utc).date().isoformat()


def dataset_registry() -> dict[str, EvaluationDataset]:
    """Every dataset the benchmark suite draws on, keyed by id."""
    created = _created()
    datasets = [
        EvaluationDataset(
            dataset_id="chess-positions",
            version=FIXTURE_VERSION,
            source="argus.evaluation.fixtures.POSITION_CASES",
            license=ARGUS_FIXTURE_LICENCE,
            created_at=created,
            description=(
                "Standard positions with checkable properties: start, mate, "
                "stalemate, pins, castling, en passant, promotion, fifty-move, "
                "insufficient material."
            ),
            sample_count=len(POSITION_CASES),
        ),
        EvaluationDataset(
            dataset_id="fen-cases",
            version=FIXTURE_VERSION,
            source="argus.evaluation.fixtures.FEN_CASES",
            license=ARGUS_FIXTURE_LICENCE,
            created_at=created,
            description="Valid, malformed and impossible FEN strings with expected outcomes.",
            sample_count=len(FEN_CASES),
        ),
        EvaluationDataset(
            dataset_id="pgn-corpus",
            version=FIXTURE_VERSION,
            source="argus.evaluation.fixtures (PGN_* constants)",
            license=ARGUS_FIXTURE_LICENCE,
            created_at=created,
            description=(
                "Real-game PGNs across every parse category: standard, complex, "
                "castling, promotion, en passant, draw, checkmate, annotated, "
                "multi-game, invalid, partial and corrupted."
            ),
            sample_count=12,
        ),
        EvaluationDataset(
            dataset_id="engine-positions",
            version=FIXTURE_VERSION,
            source="argus.evaluation.fixtures.POSITION_CASES",
            license=ARGUS_FIXTURE_LICENCE,
            created_at=created,
            description="Positions used to verify engine correctness and perspective.",
            sample_count=len(POSITION_CASES),
        ),
        EvaluationDataset(
            dataset_id="agent-questions",
            version=FIXTURE_VERSION,
            source="argus.evaluation.suites.agent",
            license=ARGUS_FIXTURE_LICENCE,
            created_at=created,
            description=(
                "Grounded and adversarial questions with expected tool selection and "
                "expected refusal."
            ),
            sample_count=12,
        ),
    ]
    return {dataset.dataset_id: dataset for dataset in datasets}


def dataset_version(dataset_id: str) -> str:
    """The version of one dataset, or ``unknown`` when it is not registered."""
    dataset = dataset_registry().get(dataset_id)
    return dataset.version if dataset else "unknown"


__all__ = [
    "ARGUS_FIXTURE_LICENCE",
    "EvaluationDataset",
    "dataset_registry",
    "dataset_version",
]
