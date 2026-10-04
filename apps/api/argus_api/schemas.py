"""API request/response schemas (Pydantic).

Request validation lives here; domain models from ``argus`` are reused as
response models where suitable so no business logic is duplicated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from argus.analysis.game_analyzer import GameAnalysis
from argus.analysis.reports import GameReport
from argus.importing.base import ImportSource, ValidationIssue


class GameImportRequest(BaseModel):
    """Request body for importing (and optionally analyzing) a PGN game."""

    pgn_text: str = Field(min_length=1, description="Raw PGN text of one or more games")
    run_analysis: bool = Field(default=True, description="Run Stockfish analysis on import")
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)
    persist: bool = Field(default=True, description="Save the game and analysis to the database")
    # Deliberately a plain string: an unknown source must reach the importer
    # registry, which answers with the real list of supported sources instead of
    # a schema-level enum error that tells the caller nothing.
    source: str = Field(default=ImportSource.PGN_TEXT.value, description="Origin of the payload")


class PgnValidateRequest(BaseModel):
    pgn_text: str = Field(min_length=1)
    source: str = Field(default=ImportSource.PGN_TEXT.value)


class PgnValidationResponse(BaseModel):
    """Structured validation outcome (never exposes internal exceptions)."""

    is_valid: bool
    game_count: int
    ply_count: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list, description="Human-readable messages")
    available_sources: list[str] = Field(default_factory=list)
    planned_sources: list[str] = Field(default_factory=list)


class GameStatusResponse(BaseModel):
    """Canonical analysis lifecycle state for one game."""

    game_id: str
    analysis_status: str
    analysis_depth: int | None = None
    positions_analyzed: int = 0
    analysis_error: str | None = None
    updated_at: datetime | None = None


class PositionAnalysisRequest(BaseModel):
    fen: str = Field(min_length=1)
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)
    movetime_ms: int | None = Field(default=None, ge=50, le=60_000)


class PositionMultiPvRequest(BaseModel):
    """MultiPV request: the top ``multipv`` lines for one position."""

    fen: str = Field(min_length=1)
    multipv: int = Field(default=3, ge=1, le=10)
    depth: int | None = Field(default=None, ge=1, le=30)
    movetime_ms: int | None = Field(default=None, ge=50, le=60_000)


class GameAnalysisStartRequest(BaseModel):
    """Start (or resume) a background game analysis."""

    profile: str = Field(default="standard", description="fast | standard | deep")
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=10)
    movetime_ms: int | None = Field(default=None, ge=50, le=120_000)
    resume: bool = Field(default=True, description="Skip plies already analyzed")


class ErrorResponse(BaseModel):
    """Standard error envelope produced by the central error handler."""

    error: dict


class GameImportResponse(BaseModel):
    game_id: str
    moves: int
    analyzed: bool
    analysis_status: str = "imported"
    source: str | None = None
    warnings: list[str] = Field(default_factory=list)
    analysis: GameAnalysis | None = None
    report: GameReport | None = None
    engine: dict | None = None


class CoachChatRequest(BaseModel):
    """One coaching turn: a user message plus optional prior history."""

    message: str = Field(min_length=1, max_length=8000)
    history: list[dict] | None = Field(
        default=None,
        description="Prior conversation turns (role/content), used as context",
    )


class CoachChatResponse(BaseModel):
    """Coaching reply with a trace of the tools the agent actually used."""

    message: str
    tool_calls_used: int = 0
    tool_trace: list[dict] = Field(default_factory=list)


# --- Phase 7 agent -------------------------------------------------------------------


class AgentHistoryTurn(BaseModel):
    """One prior turn supplied by the client, in the response's own message shape."""

    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=8000)


class AgentTurnRequest(BaseModel):
    """One agent turn, with the context the user is standing in.

    The context fields are how board awareness reaches the agent: the client tells
    the backend which game and which ply are open, and the agent resolves "this
    move" against them. Without a game id the agent still answers general,
    opening and concept questions — it simply cannot make claims about a position,
    and it says so rather than guessing.
    """

    question: str = Field(min_length=1, max_length=4000)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=0, le=1000)
    move_san: str | None = Field(default=None, max_length=12)
    fen: str | None = Field(default=None, max_length=120)
    player_id: str | None = Field(default=None, max_length=64)
    #: A live game in progress. When set, the agent may read the live board and
    #: the coach's fair-play permissions, and engine tools are disabled for a
    #: competitive game (Phase 12, §53/§54).
    live_game_id: str | None = Field(default=None, max_length=64)
    #: The seated player asking — needed to authorize a private live game.
    live_player_id: int | None = Field(default=None, ge=1)
    mode: str = Field(default="coach", max_length=24)
    #: Prior turns, so a follow-up works without repeating the context. Bounded by
    #: the agent's own memory window rather than by the request size. Typed (not a
    #: bare dict list) because an untyped history is exactly how a follow-up silently
    #: breaks: the client sent `content`, the memory model wanted `text`, and the
    #: mismatch only surfaced at runtime.
    history: list["AgentHistoryTurn"] | None = Field(default=None, max_length=40)
    #: Whether to return the evidence packet alongside the answer.
    include_evidence: bool = True


class AgentActionOut(BaseModel):
    """A button the UI may render. Only ever backed by real data."""

    action: str
    label: str
    href: str | None = None
    params: dict = Field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None


class AgentClaimOut(BaseModel):
    """One typed claim from the answer (fact / observation / interpretation / coaching)."""

    kind: str
    label: str
    text: str
    verified: bool | None = None
    note: str | None = None


class AgentEvidenceItemOut(BaseModel):
    kind: str
    source: str
    certainty: str
    tool: str
    summary: str
    ref: str
    game_id: str | None = None
    ply: int | None = None


class AgentEvidenceOut(BaseModel):
    items: list[AgentEvidenceItemOut] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)


class AgentValidationOut(BaseModel):
    passed: bool = True
    summary: str = ""
    checked: int = 0
    failures: list[dict] = Field(default_factory=list)


class AgentTurnResponse(BaseModel):
    """A complete agent turn: the answer plus how it was produced."""

    message: str
    mode: str
    deterministic: bool = False
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    claims: list[AgentClaimOut] = Field(default_factory=list)
    actions: list[AgentActionOut] = Field(default_factory=list)
    validation: AgentValidationOut = Field(default_factory=AgentValidationOut)
    limitations: list[str] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)
    evidence: AgentEvidenceOut | None = None


# --- external sources (Chess.com) ---------------------------------------------


class LichessImportRequest(BaseModel):
    """Import specific games a Lichess player has played.

    Lichess is addressed the same way as Chess.com — month plus game URL — so the
    month is fetched once and only the games the user picked are stored. Lichess
    has no archive index, so the month is whatever month the client is showing.
    """

    username: str = Field(min_length=2, max_length=64)
    year: int = Field(ge=2007, le=2100, description="Month year")
    month: int = Field(ge=1, le=12, description="Month")
    game_urls: list[str] = Field(
        min_length=1,
        max_length=25,
        description="Lichess game URLs to import (already-imported games are skipped)",
    )
    analyze: bool = Field(
        default=False,
        description="Queue a Stockfish analysis for each newly imported game",
    )
    persist: bool = Field(default=True)
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)


class ChessComImportRequest(BaseModel):
    """Import specific games a Chess.com player has published.

    Games are addressed by month plus URL, because the public API serves whole
    months: Caissa fetches the month once and imports only what was asked for.
    """

    username: str = Field(min_length=2, max_length=64)
    year: int = Field(ge=2007, le=2100, description="Archive year")
    month: int = Field(ge=1, le=12, description="Archive month")
    game_urls: list[str] = Field(
        min_length=1,
        max_length=25,
        description="Chess.com game URLs to import (already-imported games are skipped)",
    )
    analyze: bool = Field(
        default=False,
        description="Queue a Stockfish analysis for each newly imported game",
    )
    persist: bool = Field(default=True)
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)


# --- Phase 8 training -----------------------------------------------------------------


class TrainingGenerateRequest(BaseModel):
    """Generate training exercises from one game's stored analysis.

    ``player_id`` scopes the exercises to the side that player held. Without it
    the run is a GENERAL (global) set derived from both sides, generated only
    when the caller explicitly asks for a shared exercise set.
    """

    player_id: str | None = Field(default=None, max_length=64)
    data_source: Literal["personalized", "general"] = "personalized"
    regenerate: bool = Field(
        default=False,
        description="Delete this game's existing exercises before regenerating",
    )
    include_replay: bool = Field(
        default=True,
        description=(
            "Also build the whole-game formats (what_went_wrong, reconstruction) "
            "from this game's stored analyses. They read the full game, not one side."
        ),
    )


class TrainingAttemptRequest(BaseModel):
    """Grade one attempt at one training position.

    The solution is never accepted from the client; only the submitted move is,
    and the server grades it against the stored, engine-verified solution.
    """

    player_id: str = Field(min_length=1, max_length=64)
    submitted_uci: str = Field(min_length=2, max_length=8)
    session_id: int | None = Field(default=None, ge=1)
    hints_used: int = Field(default=0, ge=0, le=20)
    response_time_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    reveal: bool = Field(
        default=False,
        description="Include the principal variation in the feedback (after the attempt)",
    )


class TrainingContinueRequest(BaseModel):
    """Grade the continuation of a CONTINUE_LINE exercise.

    Only the moves the solver was to *find* are sent; the opponent's replies come
    from the exercise's stored principal variation. Grading stores nothing.
    """

    player_id: str = Field(min_length=1, max_length=64)
    moves: list[str] = Field(default_factory=list, max_length=8)


class TrainingOpponentPrepRequest(BaseModel):
    """Generate preparation exercises against an opponent, for a player.

    The exercises are positions after a move the *opponent* demonstrably plays
    (>= ``min_occurrences`` of their games), with the engine's stored best reply
    as the solution. Nothing is predicted; a move that is not yet characteristic
    yields no exercise.
    """

    player_id: str = Field(min_length=1, max_length=64)
    min_occurrences: int = Field(default=2, ge=1, le=50)
    max_exercises: int = Field(default=20, ge=1, le=50)


class TrainingSessionStartRequest(BaseModel):
    """Start a resumable training session of one of the seven kinds."""

    player_id: str = Field(min_length=1, max_length=64)
    kind: Literal[
        "quick", "daily", "weakness", "game_review", "endgame", "tactical", "custom"
    ] = "quick"
    target_category: str | None = Field(default=None, max_length=24)
    game_id: str | None = Field(default=None, max_length=64)
    custom_position_ids: list[int] | None = Field(default=None, max_length=50)
    max_positions: int | None = Field(default=None, ge=1, le=50)


# --- Phase 10: decision intelligence (counterfactuals) -------------------------


class ScenarioPositionRequest(BaseModel):
    """Board facts for one position (no engine call)."""

    fen: str = Field(min_length=1, max_length=120)


class ScenarioComparePositionsRequest(BaseModel):
    """Compare two positions on the engine axis and the board axis separately."""

    fen_a: str = Field(min_length=1, max_length=120)
    fen_b: str = Field(min_length=1, max_length=120)
    depth: int | None = Field(default=None, ge=1, le=60)
    multipv: int | None = Field(default=None, ge=1, le=10)
    movetime_ms: int | None = Field(default=None, ge=50, le=30_000)


class ScenarioCompareMovesRequest(BaseModel):
    """Compare candidate moves in one position, all from one search.

    The position may be given directly (``fen``) or as a stored game ply
    (``game_id`` + ``ply``); exactly one of the two must be provided.
    """

    fen: str | None = Field(default=None, max_length=120)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=1, le=500)
    moves: list[str] = Field(default_factory=list, max_length=12)
    include_top: int = Field(
        default=0,
        ge=0,
        le=8,
        description="Also compare the engine's own top-N moves, from the same search",
    )
    played_move_uci: str | None = Field(default=None, max_length=8)
    depth: int | None = Field(default=None, ge=1, le=60)
    multipv: int | None = Field(default=None, ge=1, le=10)
    movetime_ms: int | None = Field(default=None, ge=50, le=30_000)


class ScenarioBranchRequest(BaseModel):
    """Build a counterfactual branch, and optionally store it.

    ``persist`` records the branch as an immutable scenario. ``prediction_task``
    attaches a prediction **only** when a production model can serve one; the
    response carries the refusal otherwise.
    """

    fen: str | None = Field(default=None, max_length=120)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=1, le=500)
    alternative_move: str = Field(min_length=1, max_length=12)
    actual_move: str | None = Field(default=None, max_length=12)
    scenario_type: Literal[
        "counterfactual_move",
        "alternative_line",
        "opening_deviation",
        "tactical_variation",
        "endgame_transition",
        "opponent_response",
        "user_hypothesis",
    ] = "counterfactual_move"
    plies_ahead: int | None = Field(default=None, ge=1, le=20)
    depth: int | None = Field(default=None, ge=1, le=60)
    multipv: int | None = Field(default=None, ge=1, le=10)
    movetime_ms: int | None = Field(default=None, ge=50, le=30_000)
    persist: bool = False
    player_id: int | None = Field(default=None, ge=1)
    opponent_player_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Required for an 'opponent_response' scenario: that opponent's stored "
            "replies are read and attached, kept separate from the engine line."
        ),
    )
    prediction_task: str | None = Field(default=None, max_length=32)
    prediction_rows: list[dict] = Field(default_factory=list, max_length=8)


class ScenarioMoveQueryRequest(BaseModel):
    """A question about one move: "why not this?" or "what if I had played this?"."""

    fen: str | None = Field(default=None, max_length=120)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=1, le=500)
    move: str = Field(min_length=1, max_length=12)
    depth: int | None = Field(default=None, ge=1, le=60)
    multipv: int | None = Field(default=None, ge=1, le=10)
    movetime_ms: int | None = Field(default=None, ge=50, le=30_000)
    plies_ahead: int | None = Field(default=None, ge=1, le=20)
    persist: bool = False
    player_id: int | None = Field(default=None, ge=1)


class ScenarioPredictRequest(BaseModel):
    """Ask for a prediction in a task's own feature space.

    Mirrors ``/api/predictions/{task}``; kept here so the decision-intelligence
    surface can say "prediction unavailable" in the same breath as its
    counterfactual answer, rather than silently omitting a section.
    """

    task: str = Field(min_length=1, max_length=32)
    rows: list[dict] = Field(default_factory=list, max_length=8)


class ScenarioTrainingRequest(BaseModel):
    """Turn a counterfactual into a stored exercise for a player.

    The solution is the engine-measured alternative move; the athlete practises a
    position Caissa actually holds. A move the engine scores as inferior is refused
    rather than stored as an answer key.
    """

    fen: str | None = Field(default=None, max_length=120)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=1, le=500)
    alternative_move: str = Field(min_length=1, max_length=12)
    player_id: int = Field(ge=1)
    depth: int | None = Field(default=None, ge=1, le=60)
    multipv: int | None = Field(default=None, ge=1, le=10)


class ScenarioOpponentResponseRequest(BaseModel):
    """How an opponent answered a position, next to what the engine recommends."""

    opponent_player_id: int = Field(ge=1)
    fen: str | None = Field(default=None, max_length=120)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=1, le=500)
    depth: int | None = Field(default=None, ge=1, le=60)


# --- Phase 11: coaching workspace extras (collections, search, prep, progress) ---


class CoachingCollectionCreateRequest(BaseModel):
    """Create a study collection owned by one player."""

    player_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "mixed", "game_set", "opening_study", "endgame_study", "tactics", "position_set"
    ] = "mixed"
    description: str = Field(default="", max_length=2000)


class CoachingCollectionItemRequest(BaseModel):
    """Add one typed pointer to a study collection."""

    player_id: str = Field(min_length=1, max_length=64)
    kind: Literal[
        "game", "position", "training", "scenario", "insight", "opening", "endgame"
    ]
    ref: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)
    game_id: str | None = Field(default=None, max_length=64)
    ply: int | None = Field(default=None, ge=0)
    fen: str | None = Field(default=None, max_length=120)


class CoachingMatchPrepRequest(BaseModel):
    """Build (and store) a match preparation object for one opponent."""

    preparing_player_id: str = Field(min_length=1, max_length=64)
    opponent_id: int = Field(ge=1)
    as_white: bool | None = None
    persist: bool = True


# --- Phase 12: live chess -------------------------------------------------------


class LiveGameCreateRequest(BaseModel):
    """Create a live game. Fair-play permission is chosen here, at creation."""

    player_id: int = Field(ge=1)
    mode: Literal["local", "private_match", "training", "sandbox"] = "private_match"
    colour: Literal["white", "black"] = "white"
    time_control: str = Field(default="10+5", max_length=12)
    visibility: Literal["private", "unlisted", "public"] = "private"
    rated: bool = False
    opponent_player_id: int | None = Field(default=None, ge=1)
    analysis_mode: Literal[
        "no_analysis", "post_move_analysis", "training_analysis", "sandbox_analysis"
    ] | None = None
    coach_level: Literal["off", "hints", "conceptual", "full_analysis"] | None = None
    training_mode: Literal[
        "", "coach_game", "puzzle_game", "practice_game",
        "opening_practice", "endgame_practice", "free_analysis",
    ] = ""
    #: "engine" puts Stockfish in the far seat (training/sandbox only).
    opponents: Literal["engine", "human"] | None = None
    engine_depth: int | None = Field(default=None, ge=1, le=30)
    start_fen: str | None = Field(default=None, max_length=120)


class LiveMoveRequest(BaseModel):
    """One move intent. A position, clock or result is never accepted from here."""

    player_id: int = Field(ge=1)
    uci: str | None = Field(default=None, max_length=12)
    san: str | None = Field(default=None, max_length=12)
    #: The version the client believes it is looking at (optimistic concurrency).
    expected_version: int | None = Field(default=None, ge=0)


class LivePlayerRequest(BaseModel):
    """An action taken by one player."""

    player_id: int = Field(ge=1)


class LiveJoinRequest(BaseModel):
    """Join a game, with an invitation when the caller is not already a member."""

    player_id: int = Field(ge=1)
    invite_token: str | None = Field(default=None, max_length=128)


class LiveClaimDrawRequest(BaseModel):
    """Claim a threefold-repetition or fifty-move draw."""

    player_id: int = Field(ge=1)
    rule: Literal["threefold_repetition", "fifty_move_rule"]


class LiveCoachRequest(BaseModel):
    """Ask the in-game coach. A competitive game never receives an engine move."""

    player_id: int = Field(ge=1)
    question: str | None = Field(default=None, max_length=500)


class LiveVisibilityRequest(BaseModel):
    """Change who may watch a game."""

    player_id: int = Field(ge=1)
    visibility: Literal["private", "unlisted", "public"]
