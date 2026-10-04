"""The planner: what is this question asking for, and what would answer it?

Chess questions fall into a small number of shapes, and recognising the shape is
deterministic work — no model required. "How many games have I analysed?" is a
count, and paying a language model to look it up is slower, dearer and *less*
reliable than reading the number (spec §37).

So the planner does three jobs, all testable without an LLM:

**Intent detection.** Classify the question into one or more :class:`Intent` values.
A question may be several at once ("I keep losing this opening — is that my biggest
weakness?" is both an opening question and a player question).

**Tool shortlisting.** Emit the tools that could plausibly answer it. The tool
budget is not cosmetic: an agent offered 22 tools calls more of them, more often,
and more irrelevantly than one offered the four that matter. Shortlisting is how
the loop stays fast and focused.

**Deterministic fast paths.** Some questions need no model at all. When the planner
recognises one, the loop answers it from the tools directly and marks the answer
``deterministic`` — exact, instant, and free of hallucination risk because no
generation happens.

The planner is allowed to be wrong about intent, because it is a *hint*: the model
may still call any permitted tool. It is not allowed to be wrong about *facts*,
which is why it never computes anything — it only routes.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """What a question is asking for."""

    MOVE_WHY = "move_why"
    MOVE_ALTERNATIVE = "move_alternative"
    GAME_REVIEW = "game_review"
    GAME_META = "game_meta"
    PLAYER_WEAKNESS = "player_weakness"
    PLAYER_HISTORY = "player_history"
    OPENING = "opening"
    POSITION_EVAL = "position_eval"
    PREDICTION = "prediction"
    TRAINING = "training"
    CAPABILITY = "capability"
    GENERAL = "general"


#: Which tools answer each intent. Order is a preference order: earlier tools are
#: the ones a well-behaved agent reaches for first.
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.MOVE_WHY: (
        "get_move_analysis",
        "get_current_position",
        "inspect_position",
        "get_critical_moments",
    ),
    Intent.MOVE_ALTERNATIVE: (
        "get_move_analysis",
        "analyze_position_multipv",
        "compare_moves",
        "get_current_position",
    ),
    Intent.GAME_REVIEW: (
        "get_game_summary",
        "get_critical_moments",
        "get_game",
        "get_game_trajectory",
        "get_game_analysis",
    ),
    Intent.GAME_META: ("get_game", "get_game_summary", "get_opening_information"),
    Intent.PLAYER_WEAKNESS: (
        "get_player_insights",
        "get_player_profile",
        "get_player_evidence",
    ),
    Intent.PLAYER_HISTORY: (
        "get_player_statistics",
        "get_player_profile",
        "get_player_insights",
    ),
    Intent.OPENING: ("get_opening_information", "get_player_profile", "get_game"),
    Intent.POSITION_EVAL: (
        "get_current_position",
        "inspect_position",
        "analyze_position",
        "analyze_position_multipv",
    ),
    Intent.PREDICTION: ("get_prediction_status", "get_validated_prediction"),
    Intent.TRAINING: (
        "get_training_recommendations",
        "generate_training_position",
        "get_review_queue",
        "get_training_progress",
        "get_training_from_game",
        "get_training_requirements",
        "get_player_insights",
    ),
    Intent.CAPABILITY: ("get_prediction_status", "get_training_requirements"),
    # A general question gets a bounded, high-value default set — NOT the whole
    # catalogue. Offering every tool is both a latency cost and a correctness bug:
    # a real provider rejects a request carrying sixty tool schemas (Groq's
    # on-demand tier caps a request at 8000 tokens and returns 413), so the model
    # never gets to answer. These are the tools that answer the common questions
    # about a game and its position; a question that needs something else is
    # classified into a specific intent above and gets that intent's tools.
    Intent.GENERAL: (
        "get_current_position",
        "get_move_analysis",
        "get_game_summary",
        "get_game",
        "get_critical_moments",
        "get_opening_information",
        "search_chess_knowledge",
        "get_player_profile",
        "get_prediction_status",
    ),
}

_PATTERNS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (
        Intent.MOVE_ALTERNATIVE,
        (
            r"\bwhat should i (have )?play",
            r"\bwhat (was|is) the best move",
            r"\bbetter move\b",
            r"\bwhat did i miss\b",
            r"\bwhat about\b",
            r"\binstead\b",
            r"\balternative",
            # "compare this move with the engine's choice" is an alternative-move
            # question, and routing it here (rather than to GENERAL) is what lets the
            # Compare action in the UI reach the stored analysis instead of nothing.
            r"\bcompare\b",
        ),
    ),
    (
        Intent.MOVE_WHY,
        (
            r"\bwhy (was|is|did)\b",
            r"\bwhy\b.*\b(bad|wrong|blunder|mistake|error)",
            r"\bwhat went wrong\b",
            r"\bexplain (this|the) move\b",
            r"\bis this (bad|wrong)",
        ),
    ),
    (
        Intent.GAME_REVIEW,
        (
            r"\bwhere did i (lose|go wrong|blunder)\b",
            r"\bhow did i lose\b",
            r"\b(biggest|worst|main) (mistake|blunder|moment|error)\b",
            r"\b(when|where) did the game turn\b",
            r"\bcritical moment\b",
            r"\breview (the|my) game\b",
            r"\bturning point\b",
        ),
    ),
    (
        Intent.PLAYER_WEAKNESS,
        (
            r"\b(my|a) (biggest|worst|main) (weakness|problem|issue)",
            r"\brecurring\b",
            r"\bwhy do i keep\b",
            r"\bwhat am i (bad|worst) at\b",
            r"\bwhat should i (practise|practice|work on|improve)\b",
            r"\bweakness(es)?\b",
            r"\bpattern\b.*\bmy games\b",
        ),
    ),
    (
        Intent.PLAYER_HISTORY,
        (
            r"\bhow many games\b",
            r"\bmy (stats|statistics|record|accuracy|rating)\b",
            r"\bhave i (played|analysed|analyzed)\b",
            r"\bmy (win|loss|draw) (rate|record)\b",
            # "have I made this mistake before?" is a question about the player's
            # history, so it must reach the player tools rather than being classified
            # as general and answering from nothing.
            r"\bhave i (made|missed|seen|hit) (this|that|it)\b",
        ),
    ),
    (
        Intent.OPENING,
        (
            r"\bopening", 
            r"\bsicilian\b",
            r"\bcaro[- ]kann\b",
            r"\brepertoire\b",
            r"\beco\b",
        ),
    ),
    (
        Intent.PREDICTION,
        (
            r"\b(win|draw|loss) (probability|chance|chances)\b",
            r"\bwhat are my chances\b",
            r"\bwho (will|would) win\b",
            r"\bpredict\b",
            r"\bprobability\b",
        ),
    ),
    (
        Intent.TRAINING,
        (
            r"\bpuzzle\b",
            r"\bpracti[cs]e\b",
            r"\bdrill\b",
            r"\btrain(ing)?\b",
            r"\bexercise",
            r"\bteach me\b",
        ),
    ),
    (
        Intent.CAPABILITY,
        (
            r"\bwhat can you do\b",
            r"\bare you able\b",
            r"\bdo you have\b",
            r"\bis (it|that) available\b",
            r"\bcan you predict\b",
        ),
    ),
    (
        Intent.POSITION_EVAL,
        (
            r"\b(evaluation|eval)\b",
            r"\bwho is (better|winning|ahead)\b",
            r"\bhow (is|isn't) my position\b",
            r"\bhow am i doing\b",
            r"\bwhat.s the position\b",
            r"\badvantage\b",
        ),
    ),
)

_GAME_META_PATTERN = re.compile(
    r"\b(what|which) (opening|game|result|colour|color)\b|\bwho (did|was) i play\b",
    re.IGNORECASE,
)


class Plan(BaseModel):
    """The planner's routing decision for one turn."""

    intents: list[Intent] = Field(default_factory=list)
    #: Preferred tools, in the order a well-behaved agent should try them.
    suggested_tools: list[str] = Field(default_factory=list)
    #: Tools that must not be offered for this turn (e.g. prediction when the user
    #: did not ask for a probability).
    discouraged_tools: list[str] = Field(default_factory=list)
    #: True when this question can be answered from tools with no model at all.
    deterministic_candidate: bool = False
    #: The deterministic handler key, when there is one.
    fast_path: str | None = None
    #: Notes that should shape the answer, e.g. "the sample is too small".
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        names = ", ".join(intent.value for intent in self.intents) or "general"
        return f"intents=[{names}] deterministic={self.deterministic_candidate}"


#: Questions whose answer is a stored fact, not a judgement. These are answered
#: without the LLM: exact, instant, and impossible to hallucinate.
_FAST_PATH_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "player_counts",
        (
            r"how many games (have i|did i|are)",
            r"how many (games )?(have i )?analys",
            r"how many games do you have",
        ),
    ),
    # A probability question is a stored fact when no model exists: "Caissa has no
    # model" is exact, needs no generation, and must not depend on an LLM being
    # reachable. When a model *is* available the fast path steps aside (see the
    # loop) and the model's own output is presented through the gated tool.
    (
        "prediction_status",
        (
            r"\bcan you predict\b",
            r"\bis prediction available\b",
            r"\bprobabilit(?:y|ies)\b",
            r"\bchance(?:s)?\b",
            r"\bwho (?:will|would) win\b",
        ),
    ),
)


def plan(question: str, *, has_game: bool = False, has_player: bool = False) -> Plan:
    """Classify a question and shortlist the tools that could answer it.

    ``has_game``/``has_player`` matter: suggesting a player tool in a conversation
    with no active player produces a tool error the model then has to explain, which
    is a worse experience than not offering the tool.
    """
    text = (question or "").strip().lower()
    findings: list[Intent] = []
    for intent, patterns in _PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            findings.append(intent)

    if not findings and _GAME_META_PATTERN.search(text):
        findings.append(Intent.GAME_META)
    if not findings:
        findings.append(Intent.GENERAL)

    suggestions: list[str] = []
    for intent in findings:
        for tool in INTENT_TOOLS[intent]:
            if tool not in suggestions:
                suggestions.append(tool)

    # Availability tools are cheaper than the answer and gate it: "can Caissa do this
    # at all?" must be settled before a capability question is answered, or the turn
    # spends its tool budget describing a weakness and never says that the training
    # engine does not exist yet. They are front-loaded only when the question actually
    # asks about them, so nothing else changes position.
    availability_first = ("get_prediction_status", "get_training_requirements")
    suggestions.sort(key=lambda name: name not in availability_first)

    # Filter to tools this context can actually use, without hiding the rest: they
    # stay in `discouraged_tools` so the evaluator can see the reasoning.
    discouraged: list[str] = []
    if not has_game:
        game_only = {
            "get_game",
            "get_game_moves",
            "get_game_summary",
            "get_game_analysis",
            "get_game_trajectory",
            "get_move_analysis",
            "get_critical_moments",
            "get_current_position",
        }
        discouraged = [tool for tool in suggestions if tool in game_only]
        suggestions = [tool for tool in suggestions if tool not in game_only]
    if not has_player:
        player_only = {
            "get_player_profile",
            "get_player_insights",
            "get_player_evidence",
            "get_player_statistics",
        }
        discouraged.extend(tool for tool in suggestions if tool in player_only)
        suggestions = [tool for tool in suggestions if tool not in player_only]

    fast_path = _fast_path(text, has_game=has_game, has_player=has_player)
    notes: list[str] = []
    if any(intent in findings for intent in (Intent.PLAYER_WEAKNESS, Intent.PLAYER_HISTORY)):
        if not has_player:
            notes.append(
                "No player profile is active, so no historical claim can be made. Ask "
                "which player, rather than estimating."
            )
    if Intent.PREDICTION in findings:
        notes.append(
            "Discuss a probability only if get_prediction_status reports a validated "
            "model; otherwise say predictive functionality is unavailable."
        )
    return Plan(
        intents=findings,
        suggested_tools=suggestions,
        discouraged_tools=discouraged,
        deterministic_candidate=fast_path is not None,
        fast_path=fast_path,
        notes=notes,
    )


def _fast_path(text: str, *, has_game: bool, has_player: bool) -> str | None:
    for name, patterns in _FAST_PATH_PATTERNS:
        if not any(re.search(pattern, text) for pattern in patterns):
            continue
        if name == "player_counts" and not has_player:
            continue
        return name
    return None


def is_counting_question(question: str) -> bool:
    """True when the question asks for a stored count (used by the tester/evaluator)."""
    return _fast_path((question or "").lower(), has_game=True, has_player=True) is not None


__all__ = ["INTENT_TOOLS", "Intent", "Plan", "is_counting_question", "plan"]
