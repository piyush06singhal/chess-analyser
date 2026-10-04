"""Dataset quality and bias reporting — describing the population we actually have.

A dataset is always a *sample of some population*, and the only honest way to
report a model is to say which population it was measured on. So this module
answers, in counts: who is in the data, how strong are they, what did they play,
and where the data is thin. It also refuses to flatter: concentration is reported
as a concentration (the top players' share of games), not hidden behind averages.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from argus.datasets.dedupe import DuplicateReport
from argus.datasets.labels import LabelledDataset
from argus.datasets.records import IngestedGame
from argus.datasets.validation import ValidationReport

#: Rating bands used for every distribution report, so two datasets can be
#: compared without re-binning. Named bands, not arbitrary quantiles.
RATING_BANDS: list[tuple[int, int, str]] = [
    (0, 1000, "under 1000"),
    (1000, 1200, "1000–1199"),
    (1200, 1400, "1200–1399"),
    (1400, 1600, "1400–1599"),
    (1600, 1800, "1600–1799"),
    (1800, 2000, "1800–1999"),
    (2000, 2200, "2000–2199"),
    (2200, 2400, "2200–2399"),
    (2400, 9999, "2400+"),
]


def rating_band(rating: int | None) -> str:
    """The named band a rating falls in (``unknown`` when absent)."""
    if rating is None:
        return "unknown"
    for low, high, label in RATING_BANDS:
        if low <= rating <= high:
            return label
    return "unknown"


class Distribution(BaseModel):
    """Counts plus shares — never shares alone."""

    counts: dict[str, int] = Field(default_factory=dict)
    shares: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def of(cls, values: list[str]) -> "Distribution":
        counts = Counter(values)
        total = sum(counts.values())
        return cls(
            counts=dict(sorted(counts.items())),
            shares={
                key: round(count / total, 4)
                for key, count in sorted(counts.items())
            }
            if total
            else {},
        )

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def entropy(self) -> float:
        """Shannon entropy in nats — a scalar summary of how spread the data is."""
        import math

        if not self.shares:
            return 0.0
        return round(
            -sum(share * math.log(share) for share in self.shares.values() if share > 0), 4
        )


class ConcentrationReport(BaseModel):
    """How much the dataset leans on a few players or openings."""

    top_players: dict[str, int] = Field(default_factory=dict)
    top_player_share: float = 0.0
    top_openings: dict[str, int] = Field(default_factory=dict)
    top_opening_share: float = 0.0
    distinct_players: int = 0
    distinct_openings: int = 0
    notes: list[str] = Field(default_factory=list)


class BiasAnalysis(BaseModel):
    """What population the dataset represents, and where it is thin."""

    rating_bands: Distribution = Field(default_factory=Distribution)
    time_controls: Distribution = Field(default_factory=Distribution)
    results: Distribution = Field(default_factory=Distribution)
    years: Distribution = Field(default_factory=Distribution)
    colours_with_rating: dict[str, int] = Field(default_factory=dict)
    concentration: ConcentrationReport = Field(default_factory=ConcentrationReport)
    limitations: list[str] = Field(default_factory=list)
    population_statement: str = ""


class DatasetQualityReport(BaseModel):
    """The developer-facing summary of a dataset's quality."""

    dataset_id: str = ""
    total_games: int = 0
    valid_games: int = 0
    invalid_games: int = 0
    rejected_games: int = 0
    duplicate_records: int = 0
    players: int = 0
    positions: int = 0
    rating_coverage: float = 0.0
    time_control_coverage: float = 0.0
    label_distribution: dict[str, int] = Field(default_factory=dict)
    label_shares: dict[str, float] = Field(default_factory=dict)
    class_balance_warning: str | None = None
    missing_metadata: dict[str, int] = Field(default_factory=dict)
    bias: BiasAnalysis = Field(default_factory=BiasAnalysis)
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "games": self.total_games,
            "valid": self.valid_games,
            "invalid": self.invalid_games,
            "duplicates": self.duplicate_records,
            "players": self.players,
            "rating_coverage": self.rating_coverage,
            "labels": self.label_shares,
            "class_balance_warning": self.class_balance_warning,
        }


def _concentration(records: list[IngestedGame]) -> ConcentrationReport:
    players = Counter(
        key for record in records for key in record.player_keys()
    )
    openings = Counter(
        record.opening_name for record in records if record.opening_name
    )
    games = len(records)
    top_players = dict(players.most_common(10))
    top_openings = dict(openings.most_common(10))
    return ConcentrationReport(
        top_players=top_players,
        top_player_share=round(sum(top_players.values()) / (2 * games), 4) if games else 0.0,
        top_openings=top_openings,
        top_opening_share=round(
            sum(top_openings.values()) / sum(openings.values()), 4
        )
        if openings
        else 0.0,
        distinct_players=len(players),
        distinct_openings=len(openings),
        notes=[
            "top_player_share is the share of all player-slots held by the ten most "
            "frequent players: high values mean a few accounts dominate the corpus.",
        ],
    )


def analyse_bias(
    records: list[IngestedGame], *, label_distribution: dict[str, int] | None = None
) -> BiasAnalysis:
    """Describe the population the dataset represents."""
    rating_values: list[str] = []
    time_values: list[str] = []
    years: list[str] = []
    for record in records:
        rating_values.append(rating_band(record.white_rating))
        rating_values.append(rating_band(record.black_rating))
        time_values.append(record.time_class or "unknown")
        if record.date_iso:
            years.append(record.date_iso[:4])

    concentration = _concentration(records)
    limitations = [
        "This dataset is a sample, not a census of chess.",
        "Ratings are as recorded by the source; there is no cross-platform rating "
        "normalisation, so rating bands are only comparable within one source.",
    ]
    if concentration.top_player_share > 0.5:
        limitations.append(
            f"The ten most frequent players hold {concentration.top_player_share:.0%} of "
            "player-slots: results are dominated by a few accounts."
        )
    if concentration.top_opening_share > 0.5:
        limitations.append(
            f"The ten most common openings cover {concentration.top_opening_share:.0%} of "
            "games: opening-specific behaviour is over-represented."
        )

    bands = Distribution.of(rating_values)
    if "unknown" in bands.shares and bands.shares.get("unknown", 0) > 0.5:
        limitations.append("Most games carry no rating, so rating-based features are mostly missing.")

    years_distribution = Distribution.of(years)
    rated_share = 1 - bands.shares.get("unknown", 0.0)
    if records and years_distribution.counts:
        span = f"{min(years_distribution.counts)}–{max(years_distribution.counts)}"
        statement = (
            f"{len(records)} games, {concentration.distinct_players} distinct players, "
            f"rated in {100 * rated_share:.0f}% of player-slots, spanning {span}."
        )
    else:
        statement = (
            f"{len(records)} games, {concentration.distinct_players} distinct players"
            + (f", rated in {100 * rated_share:.0f}% of player-slots" if records else "")
            + "."
        )

    return BiasAnalysis(
        rating_bands=bands,
        time_controls=Distribution.of(time_values),
        results=Distribution.of([record.result for record in records]),
        years=years_distribution,
        colours_with_rating={
            "white": sum(1 for record in records if record.white_rating is not None),
            "black": sum(1 for record in records if record.black_rating is not None),
        },
        concentration=concentration,
        limitations=limitations,
        population_statement=statement,
    )


def quality_report(
    records: list[IngestedGame],
    *,
    dataset_id: str = "",
    validation: ValidationReport | None = None,
    duplicates: DuplicateReport | None = None,
    labelled: LabelledDataset | None = None,
    positions: int = 0,
    rejected_games: int = 0,
    min_class_share: float = 0.05,
) -> DatasetQualityReport:
    """Assemble the quality report from the artifacts of a build.

    Class balance is *described* before anything is done about it: the natural
    distribution is the finding, and rebalancing is a later, explicit choice.
    """
    label_distribution = labelled.distribution() if labelled else {}
    label_shares = labelled.shares() if labelled else {}

    balance_warning = None
    if label_shares:
        thin = {value: share for value, share in label_shares.items() if 0 < share < min_class_share}
        missing = [value for value, count in label_distribution.items() if count == 0]
        if thin:
            balance_warning = (
                "Classes below the "
                f"{min_class_share:.0%} share floor: "
                + ", ".join(f"{value} ({share:.1%})" for value, share in sorted(thin.items()))
            )
        if missing:
            balance_warning = (
                (balance_warning + "; " if balance_warning else "")
                + f"Classes with no examples at all: {', '.join(sorted(missing))}"
            )

    rated = sum(
        1
        for record in records
        if record.white_rating is not None and record.black_rating is not None
    )
    timed = sum(1 for record in records if record.time_class)
    players = {key for record in records for key in record.player_keys()}

    missing_metadata = {
        "rating": sum(
            1 for record in records if record.white_rating is None or record.black_rating is None
        ),
        "date": sum(1 for record in records if not record.date_iso),
        "time_control": sum(1 for record in records if not record.time_class),
        "opening": sum(1 for record in records if not record.opening_name),
    }

    warnings: list[str] = []
    if validation is not None:
        warnings.extend(f"{issue.code}: {issue.message}" for issue in validation.warnings[:20])
    if duplicates is not None and duplicates.duplicate_records:
        warnings.append(
            f"{duplicates.duplicate_records} duplicate record(s): "
            f"{duplicates.exact_count} exact, {duplicates.metadata_variant_count} metadata variant(s), "
            f"{duplicates.ambiguous_count} ambiguous short game(s)"
        )
    if labelled is not None and labelled.unlabelled:
        warnings.append(f"{len(labelled.unlabelled)} game(s) carry no usable label")

    return DatasetQualityReport(
        dataset_id=dataset_id,
        total_games=len(records),
        valid_games=validation.valid_records if validation else len(records),
        invalid_games=validation.invalid_records if validation else 0,
        rejected_games=rejected_games,
        duplicate_records=duplicates.duplicate_records if duplicates else 0,
        players=len(players),
        positions=positions,
        rating_coverage=round(rated / len(records), 4) if records else 0.0,
        time_control_coverage=round(timed / len(records), 4) if records else 0.0,
        label_distribution=label_distribution,
        label_shares=label_shares,
        class_balance_warning=balance_warning,
        missing_metadata=missing_metadata,
        bias=analyse_bias(records, label_distribution=label_distribution),
        warnings=warnings,
    )


def render_quality_report(report: DatasetQualityReport) -> str:
    """A plain-text rendering for a CLI or a log."""
    lines = [
        f"Dataset quality — {report.dataset_id or '(unnamed)'}",
        "=" * 60,
        f"games            {report.total_games}",
        f"  valid          {report.valid_games}",
        f"  invalid        {report.invalid_games}",
        f"  rejected       {report.rejected_games} (refused at ingestion)",
        f"  duplicates     {report.duplicate_records}",
        f"players          {report.players}",
        f"positions        {report.positions}",
        f"rating coverage  {report.rating_coverage:.1%}",
        f"time coverage    {report.time_control_coverage:.1%}",
    ]
    if report.label_shares:
        lines.append("labels")
        for value, share in sorted(report.label_shares.items()):
            lines.append(f"  {value:14s} {report.label_distribution.get(value, 0):>7}  {share:>7.1%}")
    if report.class_balance_warning:
        lines.append(f"class balance    !! {report.class_balance_warning}")
    lines.append("missing metadata")
    for field, count in sorted(report.missing_metadata.items()):
        lines.append(f"  {field:14s} {count:>7}")
    lines.append("population")
    lines.append(f"  {report.bias.population_statement}")
    for limitation in report.bias.limitations:
        lines.append(f"  - {limitation}")
    return "\n".join(lines)


__all__ = [
    "RATING_BANDS",
    "BiasAnalysis",
    "ConcentrationReport",
    "DatasetQualityReport",
    "Distribution",
    "analyse_bias",
    "quality_report",
    "rating_band",
    "render_quality_report",
]
