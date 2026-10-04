"""Unified search: one query across everything Caissa stores about you.

Phase 11 §30 asks for a single search box that reaches games, positions, players,
opponents, training exercises, scenarios, insights and collections. The naive
implementation is a database ``LIKE`` per table stitched together; that produces
two problems the spec explicitly forbids: results that are not comparable, and
"no results" that hides *why*.

This module is the ranking core, kept pure and testable. The service layer
gathers candidates from storage (one candidate per real row) and this module
scores them with documented, deterministic rules:

* matching is case-folded token matching on the candidate's searchable text;
* fields are weighted (a title match outweighs a body match);
* each result carries the terms that matched and a human sentence explaining
  its rank, so the UI never has to guess;
* a query with no candidates at all is distinguished from a query that matched
  nothing, because those are different products ("your library is empty" vs
  "nothing here matches 'Sicilian'").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

SEARCH_METHODOLOGY_VERSION = "11.0"

#: Field weights. A title match is a stronger signal than a body match, and a
#: tag match sits between them. Documented rather than tuned invisibly.
FIELD_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "tag": 2.0,
    "subtitle": 1.5,
    "body": 1.0,
}

#: A small, fixed bonus per kind so a tie between, say, a player and a note
#: resolves the same way every time. Not a quality judgement — a stable order.
KIND_ORDER: tuple[str, ...] = (
    "game",
    "player",
    "opponent",
    "opening",
    "training",
    "scenario",
    "insight",
    "collection",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SearchKind(str, Enum):
    GAME = "game"
    PLAYER = "player"
    OPPONENT = "opponent"
    OPENING = "opening"
    TRAINING = "training"
    SCENARIO = "scenario"
    INSIGHT = "insight"
    COLLECTION = "collection"


@dataclass(frozen=True)
class SearchCandidate:
    """One real stored row, prepared for ranking. ``id`` is authoritative."""

    kind: SearchKind
    id: str
    title: str = ""
    subtitle: str = ""
    body: str = ""
    tags: tuple[str, ...] = ()
    href: str | None = None
    updated_at: datetime | None = None
    #: Real measurements that travel with the row (sample size, dates) so the
    #: result can show them without a second read.
    metadata: dict | None = None


class SearchHit(BaseModel):
    kind: SearchKind
    id: str
    title: str
    subtitle: str = ""
    href: str | None = None
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    explanation: str = ""
    metadata: dict = Field(default_factory=dict)


class SearchResult(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    total_candidates: int = 0
    counts_by_kind: dict[str, int] = Field(default_factory=dict)
    searched_kinds: list[str] = Field(default_factory=list)
    #: ``ok`` | ``no_candidates`` | ``no_matches`` — different products.
    status: str = "ok"
    reason: str | None = None
    methodology_version: str = SEARCH_METHODOLOGY_VERSION

    def to_payload(self) -> dict:
        return {
            "query": self.query,
            "hits": [hit.model_dump(mode="json") for hit in self.hits],
            "total_candidates": self.total_candidates,
            "counts_by_kind": dict(self.counts_by_kind),
            "searched_kinds": list(self.searched_kinds),
            "status": self.status,
            "reason": self.reason,
            "methodology_version": self.methodology_version,
        }


def tokenize(text: str) -> list[str]:
    """Case-folded alphanumeric tokens; the one tokenizer all matching uses."""
    return _TOKEN_RE.findall((text or "").lower())


def _field_score(query_tokens: list[str], field_text: str, weight: float) -> tuple[float, list[str]]:
    field_tokens = tokenize(field_text)
    if not field_tokens:
        return 0.0, []
    field_set = set(field_tokens)
    matched = [term for term in query_tokens if term in field_set]
    if not matched:
        return 0.0, []
    # Coverage of the query in this field, times the field's weight; a field
    # containing every query term scores its full weight.
    coverage = len(set(matched)) / len(set(query_tokens))
    return weight * coverage, sorted(set(matched))


def score_candidate(candidate: SearchCandidate, query_tokens: list[str]) -> SearchHit | None:
    """Score one candidate, or return ``None`` when it does not match at all."""
    tag_text = " ".join(candidate.tags)
    fields = (
        ("title", candidate.title, FIELD_WEIGHTS["title"]),
        ("tag", tag_text, FIELD_WEIGHTS["tag"]),
        ("subtitle", candidate.subtitle, FIELD_WEIGHTS["subtitle"]),
        ("body", candidate.body, FIELD_WEIGHTS["body"]),
    )
    total = 0.0
    matched_terms: set[str] = set()
    matched_fields: list[str] = []
    for name, text, weight in fields:
        field_score, terms = _field_score(query_tokens, text, weight)
        if field_score > 0:
            total += field_score
            matched_fields.append(name)
            matched_terms.update(terms)
    if total <= 0:
        return None
    kind_bonus = (len(KIND_ORDER) - KIND_ORDER.index(candidate.kind.value)) * 0.01
    score = round(total + kind_bonus, 4)
    explanation = _explain(candidate, matched_fields, sorted(matched_terms))
    return SearchHit(
        kind=candidate.kind,
        id=candidate.id,
        title=candidate.title or candidate.id,
        subtitle=candidate.subtitle,
        href=candidate.href,
        score=score,
        matched_terms=sorted(matched_terms),
        matched_fields=matched_fields,
        explanation=explanation,
        metadata=dict(candidate.metadata or {}),
    )


def _explain(candidate: SearchCandidate, matched_fields: list[str], terms: list[str]) -> str:
    where = {
        "title": "its title",
        "tag": "its tags",
        "subtitle": "its summary",
        "body": "its stored text",
    }
    places = ", ".join(where[field] for field in matched_fields) or "its stored text"
    return f"Matched {', '.join(terms)} in {places} ({candidate.kind.value})."


def search(
    candidates: list[SearchCandidate],
    query: str,
    *,
    kinds: set[SearchKind] | None = None,
    limit: int = 30,
) -> SearchResult:
    """Rank candidates for a query, deterministically.

    Order: score descending, then title, then kind order, then id — so identical
    inputs always produce the identical list.
    """
    query_tokens = tokenize(query)
    selected = [c for c in candidates if kinds is None or c.kind in kinds]
    counts: dict[str, int] = {}
    for candidate in selected:
        counts[candidate.kind.value] = counts.get(candidate.kind.value, 0) + 1
    searched = sorted({c.kind.value for c in selected})
    if not query_tokens:
        return SearchResult(
            query=query,
            total_candidates=len(selected),
            counts_by_kind=counts,
            searched_kinds=searched,
            status="empty_query",
            reason="Enter a search term — Caissa does not show an unranked dump of your library.",
        )
    hits = [hit for candidate in selected if (hit := score_candidate(candidate, query_tokens))]
    hits.sort(
        key=lambda hit: (
            -hit.score,
            hit.title.lower(),
            KIND_ORDER.index(hit.kind.value) if hit.kind.value in KIND_ORDER else 99,
            hit.id,
        )
    )
    hits = hits[:limit]
    if not hits:
        if not selected:
            status, reason = "no_candidates", (
                "There is nothing stored in the searched categories yet, so there is "
                "nothing to match."
            )
        else:
            status, reason = "no_matches", (
                f"{len(selected)} stored item(s) were searched and none matched "
                f"'{query}'."
            )
    else:
        status, reason = "ok", None
    return SearchResult(
        query=query,
        hits=hits,
        total_candidates=len(selected),
        counts_by_kind=counts,
        searched_kinds=searched,
        status=status,
        reason=reason,
    )


def search_method() -> dict:
    """Publish the ranking rules, so a result order can be argued with."""
    return {
        "methodology_version": SEARCH_METHODOLOGY_VERSION,
        "field_weights": dict(FIELD_WEIGHTS),
        "kind_order": list(KIND_ORDER),
        "rules": [
            "Search ranks only rows that already exist in storage; nothing is synthesised.",
            "Matching is case-folded token matching; every hit names the terms that matched.",
            "Ties are broken by title, then kind, then id, so ordering is deterministic.",
            "An empty query and an empty result are reported differently, with reasons.",
        ],
    }


__all__ = [
    "FIELD_WEIGHTS",
    "KIND_ORDER",
    "SEARCH_METHODOLOGY_VERSION",
    "SearchCandidate",
    "SearchHit",
    "SearchKind",
    "SearchResult",
    "score_candidate",
    "search",
    "search_method",
    "tokenize",
]
