"""Study Collections: named, user-curated sets of things worth returning to.

Phase 11 §29 asks for collections a learner can build ("Study Collections"), and
it inherits the platform's discipline: a collection is a set of *pointers to
stored things*, never a place new claims are invented. An item names what it
points at (a game, a stored position, an exercise, a scenario, an insight, an
opening study or an endgame study) and the identifier to resolve it.

This module is pure: it defines the rules for what a collection is, which item
kinds are legal, how duplicates are treated, and what a collection may and may
not claim. Persistence lives in the API layer's repository, which stores these
shapes rather than re-inventing them.

Two rules matter:

* **A collection references, it does not copy.** Nothing here duplicates a game,
  a position or an analysis; the item is a typed pointer plus a note.
* **An empty collection is legal and honest.** It says so, rather than being
  padded with suggested items the user never chose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

COLLECTIONS_METHODOLOGY_VERSION = "11.0"

#: A collection longer than this is almost certainly a mistake, not curation.
MAX_ITEMS_PER_COLLECTION = 500

#: A collection name longer than this is refused rather than silently truncated.
MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 500


class CollectionKind(str, Enum):
    """What a collection is *for* — which decides its default presentation."""

    MIXED = "mixed"
    GAME_SET = "game_set"
    OPENING_STUDY = "opening_study"
    ENDGAME_STUDY = "endgame_study"
    TACTICS = "tactics"
    POSITION_SET = "position_set"


class ItemKind(str, Enum):
    """The kinds of stored thing a collection may point at."""

    GAME = "game"
    POSITION = "position"
    TRAINING = "training"
    SCENARIO = "scenario"
    INSIGHT = "insight"
    OPENING = "opening"
    ENDGAME = "endgame"


#: Which item kinds each collection kind permits. A game set that contains an
#: opening study would be a taxonomy error, so it is refused at the boundary.
ALLOWED_ITEMS: dict[CollectionKind, frozenset[ItemKind]] = {
    CollectionKind.MIXED: frozenset(ItemKind),
    CollectionKind.GAME_SET: frozenset({ItemKind.GAME, ItemKind.POSITION}),
    CollectionKind.OPENING_STUDY: frozenset({ItemKind.OPENING, ItemKind.GAME, ItemKind.POSITION}),
    CollectionKind.ENDGAME_STUDY: frozenset({ItemKind.ENDGAME, ItemKind.TRAINING, ItemKind.POSITION}),
    CollectionKind.TACTICS: frozenset({ItemKind.TRAINING, ItemKind.POSITION}),
    CollectionKind.POSITION_SET: frozenset({ItemKind.POSITION}),
}


class StudyItem(BaseModel):
    """One typed pointer inside a collection."""

    kind: ItemKind
    ref: str = Field(description="The stored identifier the item resolves to")
    label: str = ""
    note: str = ""
    #: Optional denormalised context so a list renders without a second read.
    game_id: str | None = None
    ply: int | None = None
    fen: str | None = None
    added_at: datetime | None = None

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("note")
    @classmethod
    def _trim_note(cls, value: str) -> str:
        if len(value) > MAX_NOTE_LENGTH:
            raise ValueError(f"a note may be at most {MAX_NOTE_LENGTH} characters")
        return value


class StudyCollection(BaseModel):
    """A stored collection: identity, intent, and the items it points at."""

    id: int | None = None
    player_id: int
    name: str
    description: str = ""
    kind: CollectionKind = CollectionKind.MIXED
    items: list[StudyItem] = Field(default_factory=list)
    methodology_version: str = COLLECTIONS_METHODOLOGY_VERSION
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("a collection needs a name")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"a collection name may be at most {MAX_NAME_LENGTH} characters")
        return name

    @property
    def size(self) -> int:
        return len(self.items)

    def item_keys(self) -> list[str]:
        """The identity of each item for duplicate detection: kind + ref."""
        return [f"{item.kind.value}:{item.ref}" for item in self.items]

    def kinds_present(self) -> list[str]:
        return sorted({item.kind.value for item in self.items})

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "player_id": self.player_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
            "size": self.size,
            "kinds_present": self.kinds_present(),
            "items": [item.model_dump(mode="json") for item in self.items],
            "methodology_version": self.methodology_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CollectionError(ValueError):
    """A refused collection operation, carrying the real reason."""


def validate_item(collection_kind: CollectionKind, item: StudyItem) -> None:
    """Refuse an item the collection kind does not permit."""
    allowed = ALLOWED_ITEMS.get(collection_kind, ALLOWED_ITEMS[CollectionKind.MIXED])
    if item.kind not in allowed:
        permitted = ", ".join(sorted(kind.value for kind in allowed))
        raise CollectionError(
            f"a {collection_kind.value} collection cannot hold a {item.kind.value} item; "
            f"it permits: {permitted}"
        )
    if not item.ref.strip():
        raise CollectionError("an item needs a stored identifier to point at")


def add_item(collection: StudyCollection, item: StudyItem) -> StudyCollection:
    """Return a copy of the collection with the item added, or refuse it.

    Duplicate (kind, ref) pairs are refused rather than silently overwritten: a
    user adding the same game twice has made a mistake, and telling them is more
    useful than quietly ignoring it.
    """
    validate_item(collection.kind, item)
    if len(collection.items) >= MAX_ITEMS_PER_COLLECTION:
        raise CollectionError(
            f"a collection may hold at most {MAX_ITEMS_PER_COLLECTION} items"
        )
    if item.kind.value + ":" + item.ref in collection.item_keys():
        raise CollectionError("this item is already in the collection")
    if item.added_at is None:
        item = item.model_copy(update={"added_at": datetime.now(timezone.utc)})
    return collection.model_copy(update={"items": [*collection.items, item]})


def remove_item(collection: StudyCollection, *, kind: ItemKind, ref: str) -> StudyCollection:
    """Return a copy without the matching item, or refuse when it is absent."""
    target = f"{kind.value}:{ref}"
    if target not in collection.item_keys():
        raise CollectionError("that item is not in the collection")
    remaining = [
        item for item in collection.items if f"{item.kind.value}:{item.ref}" != target
    ]
    return collection.model_copy(update={"items": remaining})


def build_collection(
    *,
    player_id: int,
    name: str,
    kind: CollectionKind = CollectionKind.MIXED,
    description: str = "",
    items: list[StudyItem] | None = None,
    now: datetime | None = None,
) -> StudyCollection:
    """Create a collection, validating every item against the collection kind."""
    now = now or datetime.now(timezone.utc)
    collection = StudyCollection(
        player_id=player_id,
        name=name,
        description=description,
        kind=kind,
        items=[],
        created_at=now,
        updated_at=now,
    )
    for item in items or []:
        collection = add_item(collection, item)
    return collection


def collections_method() -> dict:
    """Publish the collection rules, so the surface can be argued with."""
    return {
        "methodology_version": COLLECTIONS_METHODOLOGY_VERSION,
        "collection_kinds": [kind.value for kind in CollectionKind],
        "item_kinds": [kind.value for kind in ItemKind],
        "allowed_items": {
            kind.value: sorted(item.value for item in allowed)
            for kind, allowed in ALLOWED_ITEMS.items()
        },
        "max_items_per_collection": MAX_ITEMS_PER_COLLECTION,
        "rules": [
            "A collection holds pointers to stored things; it never copies or invents data.",
            "An item must be a kind its collection permits and must name a stored identifier.",
            "Adding the same item twice is refused rather than silently ignored.",
            "An empty collection is valid and says so.",
        ],
    }


__all__ = [
    "ALLOWED_ITEMS",
    "COLLECTIONS_METHODOLOGY_VERSION",
    "MAX_ITEMS_PER_COLLECTION",
    "CollectionError",
    "CollectionKind",
    "ItemKind",
    "StudyCollection",
    "StudyItem",
    "add_item",
    "build_collection",
    "collections_method",
    "remove_item",
    "validate_item",
]
