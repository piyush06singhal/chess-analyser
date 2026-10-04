"""Leakage validation — the check that decides whether a result means anything.

Every number a model reports is conditional on leakage not having happened, so
leakage is *verified*, not assumed. The checks below are the ones that actually
bite in chess ML, each with the specific failure it catches:

``GAME``
    A game in two splits. Catches the classic off-by-one where positions (not
    games) were split, so the same game is both memorised and tested.
``POSITION``
    The same *position* in two splits. Catches duplicated imports and a split
    made on rows rather than games.
``TEMPORAL``
    Test data older than training data. Catches a shuffle that silently destroys
    chronology, which would make a "prediction" one made with hindsight.
``PLAYER``
    A held-out player present in training. Catches a holdout that did not hold
    anything out — the model then looks excellent and generalises to nobody.
``FEATURE_AVAILABILITY``
    A post-game feature feeding a pre-game prediction. Catches the canonical
    leak of predicting the result from the final evaluation.
``LABEL``
    The label column listed among the inputs.
``DUPLICATE``
    Near-identical records across splits (same players, same date, same moves).

Findings are graded: an ERROR fails the report, a WARNING is recorded as a known
limitation of the dataset that must travel with its results.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from argus.datasets.records import Availability, IngestedGame, PositionRecord
from argus.datasets.splits import SplitPlan, SplitStrategy


class LeakageCheck(str, Enum):
    GAME = "game_leakage"
    POSITION = "position_leakage"
    TEMPORAL = "temporal_leakage"
    PLAYER = "player_leakage"
    FEATURE_AVAILABILITY = "feature_availability_leakage"
    LABEL = "label_leakage"
    DUPLICATE = "duplicate_leakage"


class LeakageFinding(BaseModel):
    """One check and its outcome, with the evidence it was judged on."""

    check: LeakageCheck
    passed: bool
    message: str
    severity: str = "error"
    details: dict[str, object] = Field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return not self.passed and self.severity == "error"

    @property
    def is_warning(self) -> bool:
        return not self.passed and self.severity == "warning"


class LeakageReport(BaseModel):
    """The full leakage verdict for a split (and optionally its features)."""

    strategy: str = ""
    findings: list[LeakageFinding] = Field(default_factory=list)

    @property
    def errors(self) -> list[LeakageFinding]:
        return [finding for finding in self.findings if finding.is_error]

    @property
    def warnings(self) -> list[LeakageFinding]:
        return [finding for finding in self.findings if finding.is_warning]

    @property
    def passed(self) -> bool:
        """True only when every check passed without an error."""
        return not self.errors

    def summary(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "passed": self.passed,
            "checks": {finding.check.value: finding.passed for finding in self.findings},
            "errors": [finding.message for finding in self.errors],
            "warnings": [finding.message for finding in self.warnings],
        }


class LeakageValidator:
    """Runs the leakage suite against a split plan and the data behind it."""

    def __init__(
        self,
        plan: SplitPlan,
        *,
        records: list[IngestedGame] | None = None,
        positions: list[PositionRecord] | None = None,
    ) -> None:
        self.plan = plan
        self.records = records or []
        self.positions = positions or []
        self._by_id = {record.game_id: record for record in self.records}
        self._findings: list[LeakageFinding] = []

    # --- individual checks --------------------------------------------------

    def check_game_disjointness(self) -> LeakageFinding:
        """No game may appear in more than one split."""
        seen: dict[str, str] = {}
        clashes: dict[str, list[str]] = {}
        for split in ("train", "validation", "test"):
            for game_id in self.plan.ids_in(split):
                if game_id in seen:
                    clashes.setdefault(game_id, [seen[game_id]]).append(split)
                seen[game_id] = split
        return LeakageFinding(
            check=LeakageCheck.GAME,
            passed=not clashes,
            message=(
                "every game belongs to exactly one split"
                if not clashes
                else f"{len(clashes)} game(s) appear in more than one split"
            ),
            details={"clashes": clashes, "games": len(seen)},
        )

    def check_position_disjointness(self) -> LeakageFinding:
        """Positions inherit their game's split; the same position cannot cross."""
        if not self.positions:
            return LeakageFinding(
                check=LeakageCheck.POSITION,
                passed=True,
                message="no position-level rows supplied; only game-level disjointness was checked",
                severity="warning",
                details={"positions": 0},
            )

        assignments = self.plan.assignments
        crossing: dict[str, list[str]] = {}
        unknown: set[str] = set()
        for position in self.positions:
            split = assignments.get(position.game_id)
            if split is None:
                unknown.add(position.game_id)
                continue
            crossing.setdefault(position.position_id, [])
            crossing[position.position_id].append(split)

        repeats = {key: splits for key, splits in crossing.items() if len(set(splits)) > 1}

        # Identical positions reached by *different* games are legitimate (every
        # game opens in a known position), so this is reported, not failed.
        by_hash: dict[str, set[str]] = {}
        for position in self.positions:
            by_hash.setdefault(position.position_hash, set()).add(position.game_id)
        shared = sum(1 for games in by_hash.values() if len(games) > 1)

        finding = LeakageFinding(
            check=LeakageCheck.POSITION,
            passed=not repeats,
            message=(
                f"all {len(self.positions)} position(s) stay inside their game's split"
                if not repeats
                else f"{len(repeats)} position(s) appear in more than one split"
            ),
            details={
                "positions": len(self.positions),
                "crossing": len(repeats),
                "games_without_a_split": sorted(unknown),
                "positions_reached_by_multiple_games": shared,
            },
        )
        if unknown:
            finding.passed = False
            finding.message = (
                f"{len(unknown)} game(s) with positions were not assigned to any split"
            )
        return finding

    def check_temporal_ordering(self) -> LeakageFinding:
        """For a temporal split, train must precede validation precede test."""
        if self.plan.strategy is not SplitStrategy.TEMPORAL:
            return LeakageFinding(
                check=LeakageCheck.TEMPORAL,
                passed=True,
                message=(
                    f"split strategy is '{self.plan.strategy.value}', which makes no "
                    "chronological claim"
                ),
                severity="warning",
            )

        def bounds(split: str) -> tuple[str | None, str | None]:
            dates = sorted(
                self._by_id[game_id].date_iso
                for game_id in self.plan.ids_in(split)
                if game_id in self._by_id and self._by_id[game_id].date_iso
            )
            return (dates[0] if dates else None, dates[-1] if dates else None)

        train = bounds("train")
        validation = bounds("validation")
        test = bounds("test")
        problems: list[str] = []
        if train[1] and validation[0] and validation[0] < train[1]:
            problems.append(
                f"validation starts {validation[0]} before train ends {train[1]}"
            )
        if validation[1] and test[0] and test[0] < validation[1]:
            problems.append(f"test starts {test[0]} before validation ends {validation[1]}")
        if train[0] and test[0] and test[0] < train[0]:
            problems.append(f"test starts {test[0]} before train starts {train[0]}")

        return LeakageFinding(
            check=LeakageCheck.TEMPORAL,
            passed=not problems,
            message=(
                "chronology holds: train ≤ validation ≤ test"
                if not problems
                else "; ".join(problems)
            ),
            details={"train": train, "validation": validation, "test": test},
        )

    def _players_in(self, split: str) -> set[str]:
        """The players appearing in a split.

        Derived from the plan's actual game ids and the records supplied, not from
        the plan's cached ``players`` summary. A validator that trusts a summary
        cannot detect a plan that was modified after it was built — and a plan is
        exactly the kind of thing that gets modified.
        """
        ids = self.plan.ids_in(split)
        if self.records and self._by_id:
            return {
                key
                for game_id in ids
                if game_id in self._by_id
                for key in self._by_id[game_id].player_keys()
            }
        return set(self.plan.players.get(split, []))

    def check_player_holdout(self) -> LeakageFinding:
        """A held-out player must not appear in train or validation.

        The comparison uses ``holdout_players`` — the players the split *set out*
        to hold out — not every player appearing in a test game. A held-out
        player's opponents legitimately appear in training (they played the
        training games too), and treating them as leakage would make every
        holdout look broken.
        """
        train_players = self._players_in("train")
        validation_players = self._players_in("validation")
        test_players = self._players_in("test")
        held_out = set(self.plan.holdout_players)

        if self.plan.strategy is not SplitStrategy.PLAYER_HOLDOUT:
            overlap = test_players & (train_players | validation_players)
            return LeakageFinding(
                check=LeakageCheck.PLAYER,
                passed=True,
                message=(
                    f"strategy '{self.plan.strategy.value}' does not hold players out; "
                    f"{len(overlap)} player(s) appear in both train and test, so "
                    "player-generalisation must not be claimed from this split"
                ),
                severity="warning",
                details={"overlapping_players": sorted(overlap)[:20]},
            )

        leaked = held_out & (train_players | validation_players)
        missing = held_out - test_players
        problems: list[str] = []
        if leaked:
            problems.append(f"{len(leaked)} held-out player(s) also appear in train/validation")
        if missing:
            problems.append(f"{len(missing)} held-out player(s) have no game in the test split")
        if not held_out:
            problems.append("the plan declares no held-out players at all")

        return LeakageFinding(
            check=LeakageCheck.PLAYER,
            passed=not problems,
            message=(
                f"{len(held_out)} held-out player(s) appear in no training game, and all "
                "of them appear in the test split"
                if not problems
                else "; ".join(problems)
            ),
            details={
                "train_players": len(train_players),
                "validation_players": len(validation_players),
                "test_players": len(test_players),
                "holdout_players": len(held_out),
                "leaked": sorted(leaked)[:20],
                "absent_from_test": sorted(missing)[:20],
            },
        )

    def check_feature_availability(
        self,
        feature_names: list[str],
        *,
        task_availability: Availability = Availability.PRE_GAME,
    ) -> LeakageFinding:
        """No feature may be used before the moment it becomes knowable."""
        from argus.datasets.features import get_definition

        order = {Availability.PRE_GAME: 0, Availability.AT_POSITION: 1, Availability.POST_GAME: 2}
        violations: dict[str, str] = {}
        undeclared: list[str] = []
        for name in feature_names:
            try:
                definition = get_definition(name)
            except KeyError:
                undeclared.append(name)
                continue
            if order[definition.availability] > order[task_availability]:
                violations[name] = definition.availability.value

        passed = not violations and not undeclared
        message = "every input is available at prediction time"
        if violations:
            message = (
                f"{len(violations)} feature(s) are not knowable at prediction time "
                f"for a {task_availability.value} task"
            )
        if undeclared:
            message = (
                f"{len(undeclared)} input(s) are not declared in the feature registry, "
                "so their availability cannot be verified"
            )
        return LeakageFinding(
            check=LeakageCheck.FEATURE_AVAILABILITY,
            passed=passed,
            message=message,
            details={
                "task_availability": task_availability.value,
                "unavailable_features": violations,
                "undeclared_features": sorted(undeclared),
            },
        )

    def check_label_not_in_features(
        self, feature_names: list[str], label_column: str
    ) -> LeakageFinding:
        """The label may never be one of the inputs."""
        present = label_column in feature_names
        return LeakageFinding(
            check=LeakageCheck.LABEL,
            passed=not present,
            message=(
                f"the label column '{label_column}' is not among the inputs"
                if not present
                else f"the label column '{label_column}' is also an input feature"
            ),
            details={"label_column": label_column, "inputs": len(feature_names)},
        )

    def check_duplicate_records(self) -> LeakageFinding:
        """Near-identical records should not straddle the split boundary."""
        if not self.records:
            return LeakageFinding(
                check=LeakageCheck.DUPLICATE,
                passed=True,
                message="no records supplied; duplicate records were not checked",
                severity="warning",
            )
        assignments = self.plan.assignments
        by_signature: dict[str, set[str]] = {}
        for record in self.records:
            signature = f"{record.white.identity_key}|{record.black.identity_key}|{record.date_iso}"
            by_signature.setdefault(signature, set())
            split = assignments.get(record.game_id)
            if split:
                by_signature[signature].add(split)

        straddling = {key: sorted(value) for key, value in by_signature.items() if len(value) > 1}
        # Different games between the same players on the same day is normal (a
        # match), so this is a warning the dataset must carry, not a failure.
        return LeakageFinding(
            check=LeakageCheck.DUPLICATE,
            passed=True,
            message=(
                f"{len(straddling)} player-pair/date signature(s) span more than one "
                "split — expected for matches, but it limits independence"
            )
            if straddling
            else "no player-pair/date signature spans more than one split",
            severity="warning" if straddling else "error",
            details={"straddling": len(straddling), "examples": list(straddling)[:10]},
        )

    # --- aggregate ----------------------------------------------------------

    def validate(
        self,
        *,
        feature_names: list[str] | None = None,
        label_column: str | None = None,
        task_availability: Availability = Availability.PRE_GAME,
    ) -> LeakageReport:
        """Run the whole suite and return a graded report."""
        findings = [
            self.check_game_disjointness(),
            self.check_position_disjointness(),
            self.check_temporal_ordering(),
            self.check_player_holdout(),
            self.check_duplicate_records(),
        ]
        if feature_names is not None:
            findings.append(
                self.check_feature_availability(feature_names, task_availability=task_availability)
            )
        if feature_names is not None and label_column is not None:
            findings.append(self.check_label_not_in_features(feature_names, label_column))
        return LeakageReport(strategy=self.plan.strategy.value, findings=findings)


def assert_no_leakage(report: LeakageReport) -> None:
    """Raise when a leakage report contains an error.

    Raises:
        ValueError: with every failing check named, so a caller cannot proceed on
            a leaked split without acknowledging it.
    """
    if report.passed:
        return
    details = "; ".join(finding.message for finding in report.errors)
    raise ValueError(f"Leakage checks failed: {details}")


__all__ = [
    "LeakageCheck",
    "LeakageFinding",
    "LeakageReport",
    "LeakageValidator",
    "assert_no_leakage",
]
