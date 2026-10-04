"""Versioned feature registry with declared availability.

Three rules, each of which exists because breaking it produces a model that
looks excellent and is useless:

1. **Every feature is declared, versioned and attributed.** A feature without a
   definition is a number nobody can defend, and a feature without a version
   makes a result unreproducible the moment its definition changes.
2. **Every feature declares when it becomes knowable.** Availability is the
   leakage control, and it is enforced by
   :class:`argus.datasets.leakage.LeakageValidator`, not by convention.
3. **Existing features are reused, not re-derived.** Position features come from
   the Phase 4 extractor and player features from the Phase 5 player
   intelligence module. Re-deriving them would create two versions of the same
   quantity that could silently disagree.

``FEATURE_VERSION`` is bumped whenever a definition or a derivation changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from argus.datasets.records import Availability, IngestedGame

#: Bumped whenever a feature's meaning or derivation changes. Datasets and
#: models record it, so a result can always be traced to a feature contract.
FEATURE_VERSION = "6.0"


class FeatureGroup:
    """Named feature families (documented in ``docs/ml-and-data.md``)."""

    GAME = "game"
    POSITION = "position"
    ENGINE = "engine"
    PLAYER = "player"
    CONTEXT = "context"


class FeatureDefinition(BaseModel):
    """One declared feature: what it means, where it comes from, when it is known."""

    name: str
    group: str
    dtype: str = Field(description="int, float, bool, categorical or string")
    definition: str
    source: str = Field(description="Module and function that produces the value")
    availability: Availability
    #: True when the value can legitimately be missing; the model must handle it.
    nullable: bool = True
    feature_version: str = FEATURE_VERSION
    note: str | None = None

    @property
    def usable_for_pre_game_prediction(self) -> bool:
        """Only pre-game features may feed a pre-game prediction."""
        return self.availability is Availability.PRE_GAME


# --- registry ----------------------------------------------------------------

_REGISTRY: dict[str, FeatureDefinition] = {}


def register(definition: FeatureDefinition) -> FeatureDefinition:
    """Register one feature definition (idempotent: re-registration replaces)."""
    _REGISTRY[definition.name] = definition
    return definition


def feature(name: str, group: str, dtype: str, definition: str, source: str,
            availability: Availability, *, nullable: bool = True,
            note: str | None = None) -> FeatureDefinition:
    return register(
        FeatureDefinition(
            name=name,
            group=group,
            dtype=dtype,
            definition=definition,
            source=source,
            availability=availability,
            nullable=nullable,
            note=note,
        )
    )


# --- game features (available before the first move) --------------------------

# Ratings and their difference are the strongest legitimate pre-game signal, and
# they are the honest baseline every other model must beat.
feature(
    "white_rating", FeatureGroup.GAME, "int",
    "White player's rating as recorded in the source, or null when absent.",
    "argus.datasets.importer.game_to_record", Availability.PRE_GAME,
)
feature(
    "black_rating", FeatureGroup.GAME, "int",
    "Black player's rating as recorded in the source, or null when absent.",
    "argus.datasets.importer.game_to_record", Availability.PRE_GAME,
)
feature(
    "rating_diff", FeatureGroup.GAME, "float",
    "White rating minus black rating; null when either rating is absent. Never "
    "imputed, because an imputed rating is a fabricated strength claim.",
    "argus.datasets.features.build_game_features", Availability.PRE_GAME,
)
feature(
    "rating_mean", FeatureGroup.GAME, "float",
    "Mean of the two ratings, or null when either is absent.",
    "argus.datasets.features.build_game_features", Availability.PRE_GAME,
)
feature(
    "white_is_player", FeatureGroup.GAME, "bool",
    "Always true for the canonical white-perspective encoding; present so the "
    "encoding is explicit rather than implicit.",
    "argus.datasets.features.build_game_features", Availability.PRE_GAME,
)
feature(
    "time_control_initial_seconds", FeatureGroup.GAME, "int",
    "Initial clock in seconds as parsed from the source time control.",
    "argus.chess_core.pgn.parse_time_control", Availability.PRE_GAME,
)
feature(
    "time_control_increment_seconds", FeatureGroup.GAME, "int",
    "Increment in seconds as parsed from the source time control.",
    "argus.chess_core.pgn.parse_time_control", Availability.PRE_GAME,
)
feature(
    "time_class_index", FeatureGroup.GAME, "int",
    "Ordered index of the Caissa time class (bullet<blitz<rapid<classical); "
    "unknown is null, never a separate numeric code that would imply an order.",
    "argus.player_intelligence.classify_time_control", Availability.PRE_GAME,
)
feature(
    "eco_code", FeatureGroup.CONTEXT, "categorical",
    "ECO code from the source header. Absent headers stay null — Caissa does not "
    "guess an ECO code from the first moves here.",
    "argus.datasets.importer.game_to_record", Availability.PRE_GAME,
)
feature(
    "ply_count", FeatureGroup.CONTEXT, "int",
    "Number of plies actually played. Known only after the game, so it may not "
    "feed a pre-game prediction.",
    "argus.datasets.importer.game_to_record", Availability.POST_GAME,
    note="Declared post-game on purpose: the game length is an outcome of the game.",
)

# --- position features (Phase 4 extractor, reused) ----------------------------

_POSITION_FEATURES: dict[str, tuple[str, str, Availability]] = {
    "material_balance": ("int", "White material minus black material in centipawns.", Availability.AT_POSITION),
    "mobility_white": ("int", "Legal moves available to white.", Availability.AT_POSITION),
    "mobility_black": ("int", "Legal moves available to black.", Availability.AT_POSITION),
    "mobility_diff": ("int", "White mobility minus black mobility.", Availability.AT_POSITION),
    "king_safety_white": ("int", "Pawn-shield pawns in front of the white king.", Availability.AT_POSITION),
    "king_safety_black": ("int", "Pawn-shield pawns in front of the black king.", Availability.AT_POSITION),
    "king_safety_diff": ("int", "White minus black pawn-shield count.", Availability.AT_POSITION),
    "white_in_check": ("bool", "White is in check in this position.", Availability.AT_POSITION),
    "black_in_check": ("bool", "Black is in check in this position.", Availability.AT_POSITION),
    "white_castled": ("bool", "White has castled in this position.", Availability.AT_POSITION),
    "black_castled": ("bool", "Black has castled in this position.", Availability.AT_POSITION),
    "isolated_pawns_white": ("int", "White isolated pawns.", Availability.AT_POSITION),
    "isolated_pawns_black": ("int", "Black isolated pawns.", Availability.AT_POSITION),
    "doubled_pawns_white": ("int", "White doubled pawns.", Availability.AT_POSITION),
    "doubled_pawns_black": ("int", "Black doubled pawns.", Availability.AT_POSITION),
    "passed_pawns_white": ("int", "White passed pawns.", Availability.AT_POSITION),
    "passed_pawns_black": ("int", "Black passed pawns.", Availability.AT_POSITION),
    "backward_pawns_white": ("int", "White backward pawns.", Availability.AT_POSITION),
    "backward_pawns_black": ("int", "Black backward pawns.", Availability.AT_POSITION),
    "center_occupied_white": ("int", "Center squares occupied by white.", Availability.AT_POSITION),
    "center_occupied_black": ("int", "Center squares occupied by black.", Availability.AT_POSITION),
    "center_attacked_white": ("int", "Center squares attacked by white.", Availability.AT_POSITION),
    "center_attacked_black": ("int", "Center squares attacked by black.", Availability.AT_POSITION),
    "undeveloped_pieces_white": ("int", "White pieces still on their original squares.", Availability.AT_POSITION),
    "undeveloped_pieces_black": ("int", "Black pieces still on their original squares.", Availability.AT_POSITION),
    "hanging_pieces_white": ("int", "White pieces attacked and undefended.", Availability.AT_POSITION),
    "hanging_pieces_black": ("int", "Black pieces attacked and undefended.", Availability.AT_POSITION),
    "checkers": ("int", "Pieces currently giving check.", Availability.AT_POSITION),
    "total_pieces": ("int", "Total pieces on the board.", Availability.AT_POSITION),
}

for _name, (_dtype, _definition, _availability) in _POSITION_FEATURES.items():
    feature(
        _name, FeatureGroup.POSITION, _dtype, _definition,
        "argus.analysis.features.extractor.extract_position_features", _availability,
        note="Reused from the Phase 4 feature extractor — never re-derived.",
    )

# --- engine features ----------------------------------------------------------

feature(
    "engine_evaluation_cp", FeatureGroup.ENGINE, "int",
    "Engine evaluation at the declared depth, in centipawns from white's "
    "perspective. Available only at a position, and only for corpora that were "
    "actually evaluated at that depth.",
    "argus.analysis.engine.stockfish", Availability.AT_POSITION,
    note=(
        "A full-game engine evaluation is NOT a pre-game feature. Using the final "
        "evaluation to predict the result is the canonical leakage example."
    ),
)
feature(
    "engine_volatility", FeatureGroup.ENGINE, "float",
    "Standard deviation of the engine evaluation across the game's evaluated "
    "positions. Post-game by construction: it summarises the whole game.",
    "argus.datasets.features.build_game_features", Availability.POST_GAME,
)

# --- player features (Phase 5 player intelligence, reused) --------------------

_PLAYER_FEATURES: dict[str, tuple[str, str, Availability]] = {
    "player_average_cpl": ("float", "Historical mean centipawn loss from the Phase 5 profile.", Availability.PRE_GAME),
    "player_average_accuracy": ("float", "Historical mean accuracy from the Phase 5 profile.", Availability.PRE_GAME),
    "player_blunders_per_game": ("float", "Historical blunders per game.", Availability.PRE_GAME),
    "player_mistakes_per_game": ("float", "Historical mistakes per game.", Availability.PRE_GAME),
    "player_inaccuracies_per_game": ("float", "Historical inaccuracies per game.", Availability.PRE_GAME),
    "player_tactical_error_rate": ("float", "Historical tactical error candidates per game.", Availability.PRE_GAME),
    "player_positional_error_rate": ("float", "Historical positional error candidates per game.", Availability.PRE_GAME),
    "player_opening_diversity": ("float", "Distinct openings per game from the Chess DNA profile.", Availability.PRE_GAME),
    "player_king_safety_error_rate": ("float", "King-safety events allowed per game.", Availability.PRE_GAME),
    "player_games_analyzed": ("int", "How many analyzed games the player statistics rest on — the sample size of every feature above.", Availability.PRE_GAME),
    "player_time_control_share": ("float", "Share of the player's games in their most frequent time class.", Availability.PRE_GAME),
}

for _name, (_dtype, _definition, _availability) in _PLAYER_FEATURES.items():
    feature(
        _name, FeatureGroup.PLAYER, _dtype, _definition,
        "argus.player_intelligence.features.extract_features", _availability,
        note=(
            "Reused from the Phase 5 player feature set. Every per-player feature is "
            "derived from that player's private games and is marked "
            "user_specific/training_eligible=False upstream."
        ),
    )

# --- provenance / leakage-relevant context ------------------------------------

feature(
    "target_result", FeatureGroup.CONTEXT, "categorical",
    "The game outcome label (1-0 / 0-1 / 1/2-1/2). A label, never a feature.",
    "argus.datasets.labels.game_outcome_label", Availability.POST_GAME,
    note="Declared so the leakage validator can prove it is never used as an input.",
)


def registry() -> dict[str, FeatureDefinition]:
    """A copy of the whole registry, keyed by feature name."""
    return dict(_REGISTRY)


def definitions_for(group: str) -> list[FeatureDefinition]:
    return [item for item in _REGISTRY.values() if item.group == group]


def get_definition(name: str) -> FeatureDefinition:
    """Fetch one definition.

    Raises:
        KeyError: when the feature is not declared. An undeclared feature may not
            be used: that is the point of a registry.
    """
    return _REGISTRY[name]


def names_for_availability(*availability: Availability) -> list[str]:
    """Every declared feature with one of the given availabilities."""
    return sorted(
        item.name for item in _REGISTRY.values() if item.availability in availability
    )


# --- game feature building ----------------------------------------------------

#: Ordered index for time classes. ``unknown`` maps to ``None`` rather than a
#: number so it cannot be read as "slower than classical".
_TIME_CLASS_INDEX = {"bullet": 0, "blitz": 1, "rapid": 2, "classical": 3}


def build_game_features(record: IngestedGame) -> dict[str, Any]:
    """Pre-game features for one game.

    Only features declared ``PRE_GAME`` are produced here — a function that
    builds the input matrix cannot leak an outcome it never reads.
    """
    white = record.white_rating
    black = record.black_rating
    rating_diff = float(white - black) if white is not None and black is not None else None
    rating_mean = float((white + black) / 2) if white is not None and black is not None else None

    return {
        "white_rating": float(white) if white is not None else None,
        "black_rating": float(black) if black is not None else None,
        "rating_diff": rating_diff,
        "rating_mean": rating_mean,
        "white_is_player": True,
        "time_control_initial_seconds": (
            float(record.time_control_initial_seconds)
            if record.time_control_initial_seconds is not None
            else None
        ),
        "time_control_increment_seconds": (
            float(record.time_control_increment_seconds)
            if record.time_control_increment_seconds is not None
            else None
        ),
        "time_class_index": (
            float(_TIME_CLASS_INDEX[record.time_class])
            if record.time_class in _TIME_CLASS_INDEX
            else None
        ),
        "eco_code": record.eco,
    }


#: The input matrix for a pre-game prediction, in a fixed column order so a
#: stored model always reads its features in the order it was trained on.
GAME_OUTCOME_FEATURES: list[str] = [
    "rating_diff",
    "rating_mean",
    "time_control_initial_seconds",
    "time_control_increment_seconds",
    "time_class_index",
]


def build_position_features(fen: str) -> dict[str, Any]:
    """Position features, reusing the Phase 4 extractor."""
    from argus.analysis.features.extractor import extract_position_features_from_fen

    raw = extract_position_features_from_fen(fen)
    payload = raw.model_dump()
    payload["mobility_diff"] = raw.mobility_white - raw.mobility_black
    payload["king_safety_diff"] = raw.king_safety_white - raw.king_safety_black
    return payload


FeatureRowBuilder = Callable[[IngestedGame], dict[str, Any]]


__all__ = [
    "FEATURE_VERSION",
    "GAME_OUTCOME_FEATURES",
    "FeatureDefinition",
    "FeatureGroup",
    "build_game_features",
    "build_position_features",
    "definitions_for",
    "feature",
    "get_definition",
    "names_for_availability",
    "register",
    "registry",
]
