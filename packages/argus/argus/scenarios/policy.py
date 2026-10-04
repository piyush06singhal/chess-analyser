"""Decision-intelligence policy: versions, scenario types, and resource limits.

Phase 10 answers counterfactual questions ("what if I had played this?") with the
same discipline the earlier phases use for facts: **Stockfish remains the only
source of chess calculation**, ML is used only where a validated production model
exists, and the agent only ever explains numbers that were actually computed.

This module holds the parts that must not drift between the engine layer, the
API layer and the agent: the methodology version stamped on every scenario, the
closed list of scenario types, and the hard limits on how much engine work a
single request may trigger. A limit that lives in a prompt is not a limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Bumped whenever the counterfactual method changes in a way that would make an
#: earlier scenario record mean something different. Stored on every record so a
#: reader can tell which method produced it.
DECISION_METHODOLOGY_VERSION = "10.0"

#: MultiPV width used when nothing narrower is requested. Enough for a real
#: comparison, small enough that a single request stays fast.
DEFAULT_MULTIPV = 4

#: How many alternative moves a single comparison may carry. Requests above this
#: are truncated (and told so) rather than refused - and never silently widened.
MAX_CANDIDATE_MOVES = 8

#: How many plies of continuation a branch follows. Each ply is an engine call,
#: so this is the dominant cost of a counterfactual and is deliberately small.
DEFAULT_CONTINUATION_PLIES = 6
MAX_CONTINUATION_PLIES = 12

#: How many plies a stored analysis may be from a requested ply and still count as
#: the same position (see ``leaf_freshness``). Zero means exact ply only.
EXPLORER_MOMENT_LIMIT = 12

#: A branch is only reproducible if it records its search limit. These are the
#: limits used when a caller does not pass one.
DEFAULT_DEPTH = 14
MIN_DEPTH = 6
MAX_DEPTH = 24


class ScenarioType(str, Enum):
    """The closed set of counterfactual scenarios Caissa can build.

    Each type is a different *question*, not a different method: all of them run
    the same engine-grounded branching and differ only in what they set up and
    what they emphasise in the evidence.
    """

    COUNTERFACTUAL_MOVE = "counterfactual_move"
    ALTERNATIVE_LINE = "alternative_line"
    OPENING_DEVIATION = "opening_deviation"
    TACTICAL_VARIATION = "tactical_variation"
    ENDGAME_TRANSITION = "endgame_transition"
    OPPONENT_RESPONSE = "opponent_response"
    USER_HYPOTHESIS = "user_hypothesis"


@dataclass(frozen=True)
class ScenarioPolicy:
    """What one scenario type actually *does*, beyond carrying a label.

    Every type runs the same engine-grounded branching, so without this a type
    would be a name and nothing else. A policy makes a type behavioural:

    * it chooses that type's search defaults (a line study looks further ahead
      than a single-move comparison),
    * it states which positions the question can even be asked in, so an
      inapplicable request is refused with a reason instead of being answered
      with a scenario that does not mean what it says,
    * and it states whether the branch needs data from outside the position
      (an opponent's stored games), which the caller must supply.

    ``phase_required`` is checked against the *source* position's measured phase;
    ``resulting_phase`` against the position after the alternative move, because
    an endgame transition is defined by what the move leads to.
    """

    label: str
    description: str
    plies_ahead: int
    multipv: int
    phase_required: str | None = None
    resulting_phase: str | None = None
    requires_external_context: str | None = None


#: One policy per scenario type. The values are the type's defaults, not limits:
#: a caller may still request a deeper or longer search, within the clamps above.
SCENARIO_POLICIES: dict[ScenarioType, ScenarioPolicy] = {
    ScenarioType.COUNTERFACTUAL_MOVE: ScenarioPolicy(
        label="Counterfactual move",
        description="One alternative to the move that was actually played.",
        plies_ahead=4,
        multipv=3,
    ),
    ScenarioType.ALTERNATIVE_LINE: ScenarioPolicy(
        label="Alternative line",
        description="A longer play-through of an alternative move's main line.",
        plies_ahead=8,
        multipv=2,
    ),
    ScenarioType.OPENING_DEVIATION: ScenarioPolicy(
        label="Opening deviation",
        description=(
            "Leaving the stored opening: only askable while the position is still "
            "in the opening phase, where a deviation has a meaning."
        ),
        plies_ahead=8,
        multipv=3,
        phase_required="opening",
    ),
    ScenarioType.TACTICAL_VARIATION: ScenarioPolicy(
        label="Tactical variation",
        description=(
            "A variation that must be justified by a measurable tactical "
            "consequence of the alternative move."
        ),
        plies_ahead=6,
        multipv=4,
    ),
    ScenarioType.ENDGAME_TRANSITION: ScenarioPolicy(
        label="Endgame transition",
        description=(
            "A variation whose resulting position is an endgame: the question is "
            "what the move leads to, so the resulting phase is what is checked."
        ),
        plies_ahead=8,
        multipv=3,
        resulting_phase="endgame",
    ),
    ScenarioType.OPPONENT_RESPONSE: ScenarioPolicy(
        label="Opponent response",
        description=(
            "An alternative viewed against an opponent's stored replies; it needs "
            "that opponent's history, which the position alone cannot supply."
        ),
        plies_ahead=6,
        multipv=4,
        requires_external_context="opponent",
    ),
    ScenarioType.USER_HYPOTHESIS: ScenarioPolicy(
        label="Your hypothesis",
        description="A move the user proposed, analysed like any other alternative.",
        plies_ahead=6,
        multipv=4,
    ),
}


def scenario_policy(scenario_type: ScenarioType) -> ScenarioPolicy:
    """The policy for a type; unknown types are a programming error, not input."""
    try:
        return SCENARIO_POLICIES[scenario_type]
    except KeyError as exc:  # pragma: no cover - the enum is closed
        raise ValueError(f"no policy for scenario type {scenario_type!r}") from exc


def scenario_applicability(
    scenario_type: ScenarioType,
    *,
    source_phase: str | None,
    has_external_context: bool = False,
) -> tuple[bool, str | None]:
    """Whether a scenario type's question can be asked of this position.

    Returns ``(applicable, reason)``; the reason is written for the user, because
    it is what the API returns when it refuses.
    """
    policy = scenario_policy(scenario_type)
    if policy.phase_required and source_phase != policy.phase_required:
        return False, (
            f"A {policy.label.lower()} needs a position in the "
            f"{policy.phase_required} phase; this one is "
            f"{source_phase or 'of unknown phase'}."
        )
    if policy.requires_external_context == "opponent" and not has_external_context:
        return False, (
            "An opponent-response scenario needs the opponent's stored games; "
            "none were supplied, so Caissa will not describe a response it has "
            "no evidence for."
        )
    return True, None


class ComparisonDomain(str, Enum):
    """Where a difference between two positions comes from.

    The whole point of the position comparison is to keep these separate: an
    engine difference is a search result, a structural difference is a board
    fact, and a product that mixes them is how "+0.8 and a better pawn structure"
    becomes a single invented claim.
    """

    ENGINE = "engine"
    MATERIAL = "material"
    STRUCTURE = "structure"
    ACTIVITY = "activity"
    KING_SAFETY = "king_safety"
    TACTICS = "tactics"
    PHASE = "phase"


class MoveQuality(str, Enum):
    """A coarse, engine-derived label for a candidate move."""

    BEST = "best"
    NEAR_BEST = "near_best"
    PLAYABLE = "playable"
    INACCURATE = "inaccurate"
    MISTAKE = "mistake"
    BLUNDER = "blunder"
    ILLEGAL = "illegal"
    UNKNOWN = "unknown"


#: Centipawn bands used by :func:`classify_move_quality`. They describe how far a
#: move is from the engine's first choice *in this search*, nothing more.
NEAR_BEST_CP = 30
PLAYABLE_CP = 80
INACCURATE_CP = 150
MISTAKE_CP = 300


def classify_move_quality(centipawn_loss: int | None, *, is_best: bool = False) -> MoveQuality:
    """Label a move by its centipawn loss, or ``UNKNOWN`` when it has none.

    ``None`` is never treated as ``0``: an unavailable loss is reported as
    unknown rather than silently promoted to a perfect move.
    """
    if is_best:
        return MoveQuality.BEST
    if centipawn_loss is None:
        return MoveQuality.UNKNOWN
    if centipawn_loss <= NEAR_BEST_CP:
        return MoveQuality.NEAR_BEST
    if centipawn_loss <= PLAYABLE_CP:
        return MoveQuality.PLAYABLE
    if centipawn_loss <= INACCURATE_CP:
        return MoveQuality.INACCURATE
    if centipawn_loss <= MISTAKE_CP:
        return MoveQuality.MISTAKE
    return MoveQuality.BLUNDER


def clamp_depth(depth: int | None) -> tuple[int, bool]:
    """Return ``(depth, was_clamped)`` within the decision-intelligence limits."""
    if depth is None:
        return DEFAULT_DEPTH, False
    if depth > MAX_DEPTH:
        return MAX_DEPTH, True
    if depth < MIN_DEPTH:
        return MIN_DEPTH, True
    return depth, False


def clamp_multipv(multipv: int | None) -> tuple[int, bool]:
    if multipv is None:
        return DEFAULT_MULTIPV, False
    if multipv > MAX_CANDIDATE_MOVES:
        return MAX_CANDIDATE_MOVES, True
    if multipv < 1:
        return 1, True
    return multipv, False


def clamp_plies_ahead(plies: int | None) -> tuple[int, bool]:
    """Return ``(plies, was_clamped)`` for a continuation length."""
    if plies is None:
        return DEFAULT_CONTINUATION_PLIES, False
    if plies > MAX_CONTINUATION_PLIES:
        return MAX_CONTINUATION_PLIES, True
    if plies < 1:
        return 1, True
    return plies, False


__all__ = [
    "ComparisonDomain",
    "SCENARIO_POLICIES",
    "ScenarioPolicy",
    "DECISION_METHODOLOGY_VERSION",
    "DEFAULT_CONTINUATION_PLIES",
    "DEFAULT_DEPTH",
    "DEFAULT_MULTIPV",
    "EXPLORER_MOMENT_LIMIT",
    "MAX_CANDIDATE_MOVES",
    "MAX_CONTINUATION_PLIES",
    "MAX_DEPTH",
    "MIN_DEPTH",
    "MoveQuality",
    "ScenarioType",
    "scenario_applicability",
    "scenario_policy",
    "clamp_depth",
    "clamp_multipv",
    "clamp_plies_ahead",
    "classify_move_quality",
]
