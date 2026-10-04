"""Split strategies — the unit of splitting is a *game*, always.

Every split here partitions **games**, and every position of a game inherits its
game's split. That single rule removes the most common and most damaging leakage
in chess ML: splitting individual positions at random puts the position before
and the position after the same blunder on opposite sides of the wall, so the
model is scored on material it memorised.

Four strategies are supported because they answer different questions, and the
difference is not cosmetic:

``RANDOM_GAME``
    Games at random. Fine for "do the features carry signal at all"; it
    overstates performance for anything player-specific.
``GAME_GROUP``
    Keeps every game between the same pair of players in one split. Answers
    "does it work on a *pairing* it has not seen", which a per-game random split
    silently leaks.
``PLAYER_HOLDOUT``
    Whole players held out. The only split that answers "does it work on a
    player it has never seen" — the question that matters for a new user.
``TEMPORAL``
    Train on the past, test on the future. The only split that answers "would
    this have worked at the time", and therefore the only honest one for any
    prediction the product might show before a game.

The mapping from task to appropriate strategy is documented in
:func:`recommended_strategy` so a task cannot quietly be evaluated with the
flattering split.
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field

from argus.datasets.records import IngestedGame


class SplitStrategy(str, Enum):
    RANDOM_GAME = "random_game"
    GAME_GROUP = "game_group"
    PLAYER_HOLDOUT = "player_holdout"
    TEMPORAL = "temporal"


#: Task name → strategies that give an honest answer for it. The first entry is
#: the recommended one. Documented so evaluation choices are auditable.
TASK_SPLIT_GUIDANCE: dict[str, list[SplitStrategy]] = {
    "game_outcome": [SplitStrategy.TEMPORAL, SplitStrategy.PLAYER_HOLDOUT, SplitStrategy.RANDOM_GAME],
    "position_outcome": [SplitStrategy.RANDOM_GAME, SplitStrategy.GAME_GROUP, SplitStrategy.TEMPORAL],
    "position_difficulty": [SplitStrategy.RANDOM_GAME, SplitStrategy.GAME_GROUP],
    "move_error_risk": [SplitStrategy.PLAYER_HOLDOUT, SplitStrategy.GAME_GROUP, SplitStrategy.RANDOM_GAME],
    "player_performance": [SplitStrategy.TEMPORAL],
}


def recommended_strategy(task_name: str) -> SplitStrategy:
    """The strategy a task should be evaluated with by default."""
    options = TASK_SPLIT_GUIDANCE.get(task_name)
    if not options:
        raise KeyError(f"No split guidance is documented for task {task_name!r}")
    return options[0]


class SplitPlan(BaseModel):
    """A recorded, reproducible assignment of games to train/validation/test."""

    strategy: SplitStrategy
    seed: int = 42
    train_share: float = 0.7
    validation_share: float = 0.15
    test_share: float = 0.15

    train: list[str] = Field(default_factory=list, description="game_ids")
    validation: list[str] = Field(default_factory=list)
    test: list[str] = Field(default_factory=list)

    #: split → the player identity keys present in it.
    players: dict[str, list[str]] = Field(default_factory=dict)
    #: split → (earliest, latest) ISO date, when the strategy is date-aware.
    date_ranges: dict[str, tuple[str | None, str | None]] = Field(default_factory=dict)

    #: The players held out of training entirely (player-holdout splits only).
    #: Distinct from ``players['test']``: a test game also contains opponents, who
    #: are legitimately seen in training, so only this set answers "unseen player".
    holdout_players: list[str] = Field(default_factory=list)

    #: Games the strategy could not place (e.g. undated games in a temporal split).
    unassigned: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def assignments(self) -> dict[str, str]:
        """game_id → split."""
        mapping: dict[str, str] = {}
        for split, ids in (
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        ):
            for game_id in ids:
                mapping[game_id] = split
        return mapping

    def split_of(self, game_id: str) -> str | None:
        return self.assignments.get(game_id)

    def counts(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
            "unassigned": len(self.unassigned),
        }

    def ids_in(self, split: str) -> list[str]:
        return {"train": self.train, "validation": self.validation, "test": self.test}.get(
            split, []
        )


def _stable_hash(value: str, seed: int) -> int:
    """A deterministic hash — Python's ``hash`` is salted per process."""
    return int(hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()[:12], 16)


def _buckets(ordered: list[str], train_share: float, validation_share: float) -> dict[str, list[str]]:
    """Cut an ordered list into train/validation/test by share."""
    total = len(ordered)
    test_count = int(total * (1.0 - train_share - validation_share))
    validation_count = int(total * validation_share)
    train_count = total - validation_count - test_count
    return {
        "train": ordered[:train_count],
        "validation": ordered[train_count : train_count + validation_count],
        "test": ordered[train_count + validation_count :],
    }


def _players_by_game(records: list[IngestedGame]) -> dict[str, list[str]]:
    return {record.game_id: sorted(record.player_keys()) for record in records}


def _summarize_players(
    records: list[IngestedGame], buckets: dict[str, list[str]]
) -> dict[str, list[str]]:
    by_game = _players_by_game(records)
    return {
        split: sorted({key for game_id in ids for key in by_game.get(game_id, [])})
        for split, ids in buckets.items()
    }


def _summarize_dates(
    records: list[IngestedGame], buckets: dict[str, list[str]]
) -> dict[str, tuple[str | None, str | None]]:
    dates = {record.game_id: record.date_iso for record in records}
    summary: dict[str, tuple[str | None, str | None]] = {}
    for split, ids in buckets.items():
        known = sorted(dates.get(game_id) for game_id in ids if dates.get(game_id))
        summary[split] = (known[0] if known else None, known[-1] if known else None)
    return summary


def make_split(
    records: list[IngestedGame],
    *,
    strategy: SplitStrategy,
    seed: int = 42,
    train_share: float = 0.7,
    validation_share: float = 0.15,
) -> SplitPlan:
    """Partition games according to ``strategy``.

    The result is fully determined by the inputs: the same corpus, strategy and
    seed always produce the same plan, which is what makes an experiment
    reproducible.
    """
    if not 0 < train_share < 1 or not 0 <= validation_share < 1:
        raise ValueError("train_share must be in (0, 1) and validation_share in [0, 1)")
    if train_share + validation_share >= 1:
        raise ValueError("train_share + validation_share must leave room for a test split")
    if not records:
        raise ValueError("Cannot split an empty dataset")

    plan = SplitPlan(
        strategy=strategy,
        seed=seed,
        train_share=train_share,
        validation_share=validation_share,
        test_share=round(1.0 - train_share - validation_share, 6),
    )

    if strategy is SplitStrategy.TEMPORAL:
        dated = [record for record in records if record.date_iso]
        undated = [record for record in records if not record.date_iso]
        if undated:
            # A temporal split cannot place an undated game without inventing a
            # position for it, so it is reported as unassigned instead.
            plan.unassigned = sorted(record.game_id for record in undated)
            plan.notes.append(
                f"{len(undated)} undated game(s) were left unassigned: a temporal split "
                "cannot place them without inventing a date."
            )
        ordered = [
            record.game_id
            for record in sorted(dated, key=lambda item: (item.date_iso or "", item.game_id))
        ]
        buckets = _buckets(ordered, train_share, validation_share)
        plan.notes.append(
            "Split chronologically: the earliest games train, the latest games test."
        )

    elif strategy is SplitStrategy.PLAYER_HOLDOUT:
        # Players are chosen for the holdout by a stable hash, so the holdout is
        # reproducible and independent of input order.
        players = sorted({key for record in records for key in record.player_keys()})
        if len(players) < 3:
            raise ValueError(
                "A player-holdout split needs at least 3 distinct players, "
                f"found {len(players)}"
            )
        test_players = {
            player
            for player in players
            if _stable_hash(player, seed) % 100 < int(round((1 - train_share - validation_share) * 100))
        }
        if not test_players or len(test_players) == len(players):
            # Refuse rather than fall back to a split that would silently leak:
            # a holdout with no held-out player is not a player holdout.
            raise ValueError(
                "The seed produced a degenerate player holdout (no player, or every "
                "player, held out); choose another seed"
            )
        remaining = sorted(
            (
                record.game_id
                for record in records
                if not (set(record.player_keys()) & test_players)
            ),
            key=lambda game_id: (_stable_hash(game_id, seed), game_id),
        )
        # The held-out players take the whole test split, so the remaining games
        # are cut into train and validation only.
        validation_count = int(len(remaining) * validation_share)
        buckets = {
            "train": remaining[: len(remaining) - validation_count],
            "validation": remaining[len(remaining) - validation_count :],
            "test": sorted(
                record.game_id
                for record in records
                if set(record.player_keys()) & test_players
            ),
        }
        plan.holdout_players = sorted(test_players)
        plan.notes.append(
            f"{len(test_players)} of {len(players)} players were held out entirely; "
            "no game of a held-out player appears in train or validation. Note that "
            "test games also contain opponents who ARE seen in training, so "
            "'unseen player' means holdout_players, not players['test']."
        )

    elif strategy is SplitStrategy.GAME_GROUP:
        # The group key is the unordered player pair, so every game between the
        # same two players lands in the same split.
        groups: dict[str, list[str]] = {}
        for record in records:
            key = "|".join(sorted(record.player_keys()))
            groups.setdefault(key, []).append(record.game_id)
        ordered_groups = sorted(groups, key=lambda key: (_stable_hash(key, seed), key))
        buckets_groups = _buckets(ordered_groups, train_share, validation_share)
        buckets = {
            split: sorted(game_id for key in keys for game_id in groups[key])
            for split, keys in buckets_groups.items()
        }
        plan.notes.append(
            f"Grouped by player pairing ({len(groups)} groups): all games between the "
            "same two players stay in one split."
        )

    else:  # RANDOM_GAME
        ordered = sorted(
            (record.game_id for record in records),
            key=lambda game_id: (_stable_hash(game_id, seed), game_id),
        )
        buckets = _buckets(ordered, train_share, validation_share)
        plan.notes.append(
            "Games split at random by a stable hash of the game id; every position of a "
            "game inherits its game's split."
        )

    plan.train = sorted(buckets["train"])
    plan.validation = sorted(buckets["validation"])
    plan.test = sorted(buckets["test"])
    plan.players = _summarize_players(records, buckets)
    plan.date_ranges = _summarize_dates(records, buckets)
    return plan


__all__ = [
    "TASK_SPLIT_GUIDANCE",
    "SplitPlan",
    "SplitStrategy",
    "make_split",
    "recommended_strategy",
]
