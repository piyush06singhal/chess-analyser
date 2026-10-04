"""Duplicate detection — three distinct kinds, three distinct decisions.

Corpora in the wild contain the same game more than once (monthly exports that
overlap, a curated file that also came from a dump, a tournament PGN reissued
with corrections). Treating all of those identically loses information, and
being too eager deletes real games. So three kinds are separated:

``EXACT``
    Same move sequence *and* identical metadata. This is the same record twice.
``METADATA_VARIANT``
    Same move sequence, different metadata (a corrected date, a fuller player
    name, a rating added later). One of the two is a better record of one game —
    this is a *merge* candidate, not a repeat.
``AMBIGUOUS_SHORT``
    Same move sequence, but the game is so short that two genuinely different
    games can share it (a 1-move "game"). Never dropped.

The default policy drops **exact** duplicates only. Metadata variants and short
games are reported and kept, because deciding between two records of the same
game is a judgement the dataset owner should make — and because keeping a
duplicate is visible, while silently deleting a real game is not.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from argus.datasets.records import IngestedGame

#: Games at or below this length are treated as ambiguous when their moves repeat.
SHORT_GAME_PLIES = 10


class DuplicateKind(str, Enum):
    EXACT = "exact"
    METADATA_VARIANT = "metadata_variant"
    AMBIGUOUS_SHORT = "ambiguous_short"


class DedupeGroup(BaseModel):
    """One set of records that share a move sequence, and what was done with it."""

    moves_hash: str
    kind: DuplicateKind
    kept: str = Field(description="game_id that represents the group after deduplication")
    duplicates: list[str] = Field(default_factory=list)
    #: metadata_hash → game_ids, so a variant difference is inspectable.
    metadata_hashes: dict[str, list[str]] = Field(default_factory=dict)
    ply_count: int = 0
    dropped: bool = False
    reason: str = ""

    @property
    def size(self) -> int:
        return 1 + len(self.duplicates)


class DedupePolicy(BaseModel):
    """The documented deduplication policy.

    Every field is explicit because the policy is part of the dataset's meaning:
    two datasets built with different policies are not comparable.
    """

    #: Drop groups where every record has the same metadata (a true repeat).
    drop_exact: bool = True
    #: Drop groups that differ only in metadata. Off: one of the two records may
    #: carry information the other lacks, and choosing is the owner's call.
    drop_metadata_variants: bool = False
    #: Games at or below this many plies are never dropped, whatever matches.
    never_drop_at_or_below_plies: int = SHORT_GAME_PLIES
    rationale: str = (
        "Exact repeats are the same record twice and are dropped. Metadata variants "
        "of one game are kept and reported, because selecting the better record is a "
        "judgement, and because a kept duplicate is visible while a wrongly deleted "
        "game is not. Short games are never dropped: identical openings are not "
        "identical games."
    )


DEFAULT_DEDUPE_POLICY = DedupePolicy()


class DuplicateReport(BaseModel):
    """What duplication the source contained and what was done about it."""

    policy: DedupePolicy = Field(default_factory=lambda: DEFAULT_DEDUPE_POLICY)
    groups: list[DedupeGroup] = Field(default_factory=list)
    records_in: int = 0
    records_out: int = 0

    @property
    def exact_count(self) -> int:
        return sum(len(g.duplicates) for g in self.groups if g.kind is DuplicateKind.EXACT)

    @property
    def metadata_variant_count(self) -> int:
        return sum(
            len(g.duplicates) for g in self.groups if g.kind is DuplicateKind.METADATA_VARIANT
        )

    @property
    def ambiguous_count(self) -> int:
        return sum(
            len(g.duplicates) for g in self.groups if g.kind is DuplicateKind.AMBIGUOUS_SHORT
        )

    @property
    def dropped_ids(self) -> list[str]:
        return sorted({game_id for group in self.groups if group.dropped for game_id in group.duplicates})

    @property
    def kept_ids(self) -> list[str]:
        return sorted({group.kept for group in self.groups})

    @property
    def duplicate_records(self) -> int:
        """How many input records were duplicates of another record."""
        return self.exact_count + self.metadata_variant_count + self.ambiguous_count

    def summary(self) -> dict[str, int | str]:
        return {
            "records_in": self.records_in,
            "records_out": self.records_out,
            "exact_duplicates": self.exact_count,
            "metadata_variants": self.metadata_variant_count,
            "ambiguous_short": self.ambiguous_count,
            "dropped": len(self.dropped_ids),
            "policy": self.policy.rationale,
        }


def find_duplicates(
    records: list[IngestedGame], *, policy: DedupePolicy | None = None
) -> DuplicateReport:
    """Classify every group of records that share a move sequence."""
    effective = policy or DEFAULT_DEDUPE_POLICY
    grouped: dict[str, list[IngestedGame]] = {}
    order: list[str] = []
    for record in records:
        if record.moves_hash not in grouped:
            grouped[record.moves_hash] = []
            order.append(record.moves_hash)
        grouped[record.moves_hash].append(record)

    report = DuplicateReport(policy=effective, records_in=len(records))
    dropped = 0
    for moves_hash in order:
        group = grouped[moves_hash]
        if len(group) == 1:
            continue

        by_metadata: dict[str, list[str]] = {}
        for record in group:
            by_metadata.setdefault(record.metadata_hash, []).append(record.game_id)
        ply_count = group[0].ply_count

        if ply_count <= effective.never_drop_at_or_below_plies:
            kind = DuplicateKind.AMBIGUOUS_SHORT
            drop = False
            reason = (
                f"{ply_count} plies is at or below the {effective.never_drop_at_or_below_plies}-ply "
                "floor where two different games can share a move sequence"
            )
        elif len(by_metadata) == 1:
            kind = DuplicateKind.EXACT
            drop = effective.drop_exact
            reason = (
                "every record in the group has identical metadata"
                if drop
                else "exact repeats were kept because the policy disables drop_exact"
            )
        else:
            kind = DuplicateKind.METADATA_VARIANT
            drop = effective.drop_metadata_variants
            reason = (
                f"one move sequence recorded with {len(by_metadata)} different metadata sets"
            )

        # The first record in source order represents the group: deterministic and
        # explainable, and it never reorders the corpus.
        group_record = DedupeGroup(
            moves_hash=moves_hash,
            kind=kind,
            kept=group[0].game_id,
            duplicates=[record.game_id for record in group[1:]],
            metadata_hashes={key: sorted(value) for key, value in sorted(by_metadata.items())},
            ply_count=ply_count,
            dropped=drop,
            reason=reason,
        )
        report.groups.append(group_record)
        if drop:
            dropped += len(group_record.duplicates)

    report.records_out = report.records_in - dropped
    return report


def apply_dedupe(
    records: list[IngestedGame], report: DuplicateReport
) -> list[IngestedGame]:
    """Return the records to keep, per the report's decisions."""
    drop = set(report.dropped_ids)
    return [record for record in records if record.game_id not in drop]


def deduplicate(
    records: list[IngestedGame], *, policy: DedupePolicy | None = None
) -> tuple[list[IngestedGame], DuplicateReport]:
    """Classify duplicates and apply the policy in one call."""
    report = find_duplicates(records, policy=policy)
    return apply_dedupe(records, report), report


__all__ = [
    "DEFAULT_DEDUPE_POLICY",
    "SHORT_GAME_PLIES",
    "DedupeGroup",
    "DedupePolicy",
    "DuplicateKind",
    "DuplicateReport",
    "apply_dedupe",
    "deduplicate",
    "find_duplicates",
]
