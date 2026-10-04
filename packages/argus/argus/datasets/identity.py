"""Player identity for datasets — conservative by construction.

Player identity is the join key of the whole ML track, and the failure mode is
asymmetric: merging two different people is unrecoverable and silently corrupts
every player-level statistic, while keeping one person under two spellings is
visible and fixable. So the rules here only ever merge on *exact* agreement
after normalization, and near-miss spellings are **reported for review, never
merged**.

``normalize_player_name`` deliberately does very little: Unicode NFKC, a
trailing title suffix, whitespace collapse, and case folding. It does not strip
digits, drop punctuation, or apply fuzzy matching, because each of those turns
distinct accounts (``hans`` vs ``hans1``, ``Nakamura`` vs ``Nakamur``) into one
person.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from argus.datasets.records import PlayerIdentity

_WHITESPACE = re.compile(r"\s+")
#: Trailing parenthesised title/label some exporters append to a name, e.g.
#: ``Carlsen, M (GM)``. Removing it does not change *who* the player is.
_TRAILING_TITLE = re.compile(r"\s*\((?:[wgifc]?m|nm|w?[gifc]m)\)\s*$", re.IGNORECASE)
#: Names that carry no identity at all.
_PLACEHOLDERS = {"", "?", "??", "unknown", "n/a", "none", "-"}


def normalize_player_name(name: str) -> str:
    """Case-, width- and whitespace-insensitive form of a player name."""
    text = unicodedata.normalize("NFKC", name or "")
    text = _TRAILING_TITLE.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text.casefold()


def is_placeholder_name(name: str) -> bool:
    """Whether a name carries no usable identity (``?``, empty, ``Unknown``)."""
    return normalize_player_name(name) in _PLACEHOLDERS


def identity_key_for(
    name: str, *, platform: str | None = None, platform_username: str | None = None
) -> str:
    """The merge key for a player.

    A platform account is stronger evidence than a spelling, so when one is
    known it wins: two different display names on the same account are one
    player, and the same display name on two platforms is two players.
    """
    if platform and platform_username:
        return f"{platform.strip().casefold()}:{platform_username.strip().casefold()}"
    return normalize_player_name(name)


def build_identity(
    name: str, *, platform: str | None = None, platform_username: str | None = None
) -> "PlayerIdentity":
    """Build the normalized identity for one spelling of a player."""
    from argus.datasets.records import PlayerIdentity

    return PlayerIdentity(
        original_name=(name or "").strip(),
        normalized=normalize_player_name(name),
        identity_key=identity_key_for(
            name, platform=platform, platform_username=platform_username
        ),
        platform=platform,
        platform_username=platform_username,
    )


class SimilarNamePair(BaseModel):
    """Two *distinct* identities that look alike — a review candidate.

    Reported so a human can decide. Never merged automatically: a 0.9 string
    similarity between ``Ivan`` and ``Iva`` is not evidence of anything.
    """

    left: str
    right: str
    similarity: float
    left_identity_key: str
    right_identity_key: str


class IdentityResolution(BaseModel):
    """The outcome of resolving a set of names into identities."""

    #: identity_key → every original spelling that resolved to it.
    clusters: dict[str, list[str]] = Field(default_factory=dict)
    #: k players, n spellings: a difference is a real (visible) data fact.
    spellings: int = 0
    players: int = 0
    #: Near-miss pairs deliberately left separate.
    review_candidates: list[SimilarNamePair] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def merged_spellings(self) -> int:
        """How many spellings were folded into an existing player."""
        return max(0, self.spellings - self.players)


def cluster_identities(names: list[str]) -> IdentityResolution:
    """Group names by identity key; identical normalized names are one player."""
    clusters: dict[str, list[str]] = {}
    spellings = 0
    for name in names:
        key = identity_key_for(name)
        clusters.setdefault(key, [])
        stripped = (name or "").strip()
        if stripped not in clusters[key]:
            clusters[key].append(stripped)
        spellings += 1
    return IdentityResolution(
        clusters=dict(sorted(clusters.items())),
        spellings=spellings,
        players=len(clusters),
        notes=[
            "Identity merges only on exact agreement after normalization "
            "(case, width, whitespace and a trailing title bracket).",
        ],
    )


def find_similar_names(
    names: list[str], *, threshold: float = 0.9, limit: int = 50
) -> list[SimilarNamePair]:
    """Near-miss spellings that remain separate identities.

    Similarity is reported as evidence for review, never as a merge decision.
    Identical normalized names are already one identity and are not reported.
    """
    unique: dict[str, str] = {}
    for name in names:
        normalized = normalize_player_name(name)
        if normalized and normalized not in unique:
            unique[normalized] = (name or "").strip()

    candidates: list[SimilarNamePair] = []
    ordered = sorted(unique)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            similarity = SequenceMatcher(None, left, right).ratio()
            if similarity >= threshold:
                candidates.append(
                    SimilarNamePair(
                        left=unique[left],
                        right=unique[right],
                        similarity=round(similarity, 4),
                        left_identity_key=left,
                        right_identity_key=right,
                    )
                )
                if len(candidates) >= limit:
                    return candidates
    return candidates


def resolve_identities(names: list[str], *, threshold: float = 0.9) -> IdentityResolution:
    """Cluster names and attach the near-miss review list in one call."""
    resolution = cluster_identities(names)
    resolution.review_candidates = find_similar_names(names, threshold=threshold)
    if resolution.review_candidates:
        resolution.notes.append(
            f"{len(resolution.review_candidates)} near-miss spelling pair(s) were left as "
            "separate players pending review; see docs/ml-and-data.md."
        )
    return resolution


__all__ = [
    "IdentityResolution",
    "SimilarNamePair",
    "build_identity",
    "cluster_identities",
    "find_similar_names",
    "identity_key_for",
    "is_placeholder_name",
    "normalize_player_name",
    "resolve_identities",
]
