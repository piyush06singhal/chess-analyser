"""SQLAlchemy ORM models for the analysis data model.

Schema supports a single game having many analysed moves: Game -> GameMove ->
MoveAnalysis, plus Player/PlayerGame for longitudinal profiling and
AnalysisSession for engine run bookkeeping. Portable column types (String, Text,
JSON) keep SQLite (tests) and PostgreSQL (production) compatible. ``create_all``
bootstraps a fresh schema and ``ensure_schema_upgrades`` applies additive column
changes; the drop of the retired ``position_analyses`` table is an explicit
operator migration (see ``docs/production/migrations.md``), not a startup side
effect.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from argus.shared.time import utcnow


class Base(DeclarativeBase):
    pass


class Player(Base):
    """A chess player.

    Identity is *not* just the display name. ``identity_key`` is the matching
    key used to attach games to a player (normalized name today), while
    ``platform`` / ``platform_username`` carry the upstream identity when the
    game came from an account read (Chess.com, Lichess). That split is what
    lets a future platform-linking step (or a merge of two spellings of the
    same player) happen without changing the games or the profiles: only the
    ``identity_key`` moves.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    #: Normalized identity key (case/whitespace-insensitive name today; the
    #: seat a platform link would refine later). Indexed and never unique:
    #: legacy rows predate it, and duplicate cleanup is an explicit operation
    #: rather than a crash at startup.
    identity_key: Mapped[str | None] = mapped_column(String(220), index=True, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    games: Mapped[list["PlayerGame"]] = relationship(back_populates="player")
    profiles: Mapped[list["PlayerProfileRecord"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


VALID_RESULTS_SQL = "'1-0','0-1','1/2-1/2','*'"
VALID_COLORS_SQL = "'white','black'"
# Game analysis lifecycle. 'analyzed' is the completed state; 'paused' and
# 'cancelled' cover interrupted runs (see AnalysisSession.status).
VALID_STATUS_SQL = (
    "'imported','validating','ready','analyzing','analyzed','failed','paused','cancelled'"
)
VALID_SESSION_STATUS_SQL = "'running','completed','failed','cancelled','paused'"
# Phase 8 training: lifecycle states for an exercise and a training session.
# The position states encode the SRS lifecycle; 'mastered' requires repeated
# evidence, never one correct answer (see argus.training.scheduler).
VALID_TRAINING_STATE_SQL = (
    "'new','learning','review','mastered','needs_review','failed'"
)
VALID_TRAINING_SESSION_STATUS_SQL = "'active','completed','cancelled'"
VALID_TRAINING_TYPES_SQL = (
    "'find_best_move','find_tactical_move','find_defense','continue_line',"
    "'what_went_wrong','choose_between_moves','reconstruction'"
)
VALID_TRAINING_CATEGORIES_SQL = (
    "'tactical','positional','calculation','opening','endgame','conversion','recovery'"
)
VALID_TRAINING_DIFFICULTY_SQL = "'beginner','easy','intermediate','advanced','expert'"
# 'opponent_preparation' (Phase 9): a reply to a move an opponent demonstrably
# plays, built from that opponent's stored games — never labelled personalized.
VALID_TRAINING_SOURCES_SQL = (
    "'personalized','general','opponent_preparation','counterfactual'"
)
VALID_ATTEMPT_CORRECTNESS_SQL = "'correct','near_best','incorrect'"
VALID_SESSION_KINDS_SQL = (
    "'quick','daily','weakness','game_review','endgame','tactical','custom'"
)


class Game(Base):
    """An imported chess game with standardized metadata."""

    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint(f"result IN ({VALID_RESULTS_SQL})", name="ck_games_result"),
        CheckConstraint(
            f"analysis_status IN ({VALID_STATUS_SQL})", name="ck_games_analysis_status"
        ),
        Index("ix_games_created_at", "created_at"),
        # Upstream identity for platform imports (e.g. the Chess.com game URL), so
        # fetching a player's games twice can never create a duplicate. NULLs are
        # distinct in SQL, so pasted/uploaded PGNs are unaffected.
        Index("uq_games_source_game", "source", "source_game_id", unique=True),
        # Ownership is what makes cross-account isolation real. NULL means the game
        # is part of the shared library (a single-user/open deployment, or data
        # imported before ownership existed); a non-NULL value names the caller
        # who imported it. The authorization policy reads this one column.
        Index("ix_games_owner", "owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    white_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    black_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    white_player_name: Mapped[str] = mapped_column(String(200))
    black_player_name: Mapped[str] = mapped_column(String(200))
    white_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    black_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(8), default="*")
    date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event: Mapped[str | None] = mapped_column(String(300), nullable=True)
    site: Mapped[str | None] = mapped_column(String(300), nullable=True)
    time_control: Mapped[str | None] = mapped_column(String(64), nullable=True)
    eco_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    opening_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    initial_position: Mapped[str] = mapped_column(Text)
    final_position: Mapped[str] = mapped_column(Text)
    move_count: Mapped[int] = mapped_column(Integer, default=0)
    pgn_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="pgn_text")
    source_game_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: The caller who imported this game. NULL = shared library (visible to every
    #: caller); a value = private to that caller. Set only when authentication is
    #: on, so an open/single-user deployment behaves exactly as before.
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Canonical analysis lifecycle. The backend owns this value; the frontend
    # never infers whether analysis exists (see AnalysisStatus).
    analysis_status: Mapped[str] = mapped_column(String(16), default="imported")
    analysis_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    moves: Mapped[list["GameMove"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="GameMove.ply"
    )
    positions: Mapped[list["GamePosition"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="GamePosition.ply"
    )


class GameMove(Base):
    """One move of a game with its surrounding positions."""

    __tablename__ = "game_moves"
    __table_args__ = (
        Index("ix_game_moves_game_ply", "game_id", "ply"),
        UniqueConstraint("game_id", "ply", name="uq_game_move_ply"),
        CheckConstraint("ply >= 1", name="ck_game_moves_ply"),
        CheckConstraint("move_number >= 1", name="ck_game_moves_move_number"),
        CheckConstraint(f"color IN ({VALID_COLORS_SQL})", name="ck_game_moves_color"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(8))
    san: Mapped[str] = mapped_column(String(16))
    uci: Mapped[str] = mapped_column(String(8))
    fen_before: Mapped[str] = mapped_column(Text)
    fen_after: Mapped[str] = mapped_column(Text)

    game: Mapped["Game"] = relationship(back_populates="moves")


class GamePosition(Base):
    """One position in the linear sequence of a game (ply 0 = initial).

    This is the canonical, engine-consumable representation of every position
    a game passes through. The Stockfish phase reads ``fen`` directly, so no
    FEN parsing or board-state logic is duplicated anywhere else.
    """

    __tablename__ = "game_positions"
    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_game_position_ply"),
        Index("ix_game_positions_game_ply", "game_id", "ply"),
        CheckConstraint("ply >= 0", name="ck_game_positions_ply"),
        CheckConstraint("move_number >= 1", name="ck_game_positions_move_number"),
        CheckConstraint(
            f"side_to_move IN ({VALID_COLORS_SQL})", name="ck_game_positions_side"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    side_to_move: Mapped[str] = mapped_column(String(8))
    fen: Mapped[str] = mapped_column(Text)
    san: Mapped[str | None] = mapped_column(String(16), nullable=True)
    uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    previous_fen: Mapped[str | None] = mapped_column(Text, nullable=True)
    resulting_fen: Mapped[str] = mapped_column(Text)
    is_check: Mapped[bool] = mapped_column(Boolean, default=False)
    is_checkmate: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stalemate: Mapped[bool] = mapped_column(Boolean, default=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    terminal_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    game: Mapped["Game"] = relationship(back_populates="positions")


# The retired Phase 1/2 ``position_analyses`` table and its ``PositionAnalysis``
# ORM class were removed in Phase 15. The Phase 3 pipeline persists
# ``MoveAnalysis`` — the record every reader resolves against — and the endpoints
# that wrote the old store were retired so no code path could persist an analysis
# nothing else could see. Dropping the table on a deployment that already has it
# is an explicit operator step (``scripts/drop_legacy_tables.py``); it is not a
# startup side effect, because destroying data must never be automatic.


class PlayerGame(Base):
    """Association of players to games for longitudinal profiling.

    Uniqueness includes ``color`` so a game whose two sides share a name (both
    "Unknown", for instance) links the single player row under both colors
    instead of violating a (player, game) constraint.
    """

    __tablename__ = "player_games"
    __table_args__ = (
        UniqueConstraint("player_id", "game_id", "color", name="uq_player_game_color"),
        CheckConstraint(f"color IN ({VALID_COLORS_SQL})", name="ck_player_games_color"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    color: Mapped[str] = mapped_column(String(8))
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    player: Mapped["Player"] = relationship(back_populates="games")


class PlayerProfileRecord(Base):
    """A stored, versioned Player Intelligence profile snapshot.

    The profile is a *derived* document, so it is cached rather than recomputed
    on every dashboard request. ``source_signature`` is the fingerprint of the
    inputs it was built from (analyzed game count, latest analysis timestamps,
    versions); when the fingerprint changes the snapshot is stale and the
    service rebuilds it — which is also what makes ``rebuild`` meaningful.

    One row per (player, profile_version): rebuilding the same version replaces
    the row instead of growing the table, while a methodology bump keeps the
    old snapshot readable.
    """

    __tablename__ = "player_profiles"
    __table_args__ = (
        UniqueConstraint("player_id", "profile_version", name="uq_player_profile_version"),
        Index("ix_player_profiles_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    profile_version: Mapped[str] = mapped_column(String(16))
    methodology_version: Mapped[str] = mapped_column(String(16))
    feature_version: Mapped[str] = mapped_column(String(16))
    #: Fingerprint of the inputs (see repository.player_input_signature).
    source_signature: Mapped[str] = mapped_column(String(300))
    imported_games: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_games: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[str] = mapped_column(String(24), default="insufficient")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    player: Mapped["Player"] = relationship(back_populates="profiles")


class AnalysisSession(Base):
    """One analysis run: engine configuration, progress, and outcome.

    Every field needed to reproduce and audit a run is stored: the engine and
    its version, the exact search parameters, the analysis version, and the
    classification policy. ``current_position``/``total_positions`` drive
    progress reporting and make the run resumable after interruption.
    """

    __tablename__ = "analysis_sessions"
    __table_args__ = (
        CheckConstraint(f"status IN ({VALID_SESSION_STATUS_SQL})", name="ck_session_status"),
        Index("ix_analysis_sessions_game", "game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str | None] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=True
    )
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    multipv: Mapped[int] = mapped_column(Integer, default=1)
    movetime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile: Mapped[str | None] = mapped_column(String(16), nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(16), default="3.1")
    engine_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    positions_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    total_positions: Mapped[int] = mapped_column(Integer, default=0)
    current_position: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MoveAnalysis(Base):
    """Structured, engine-derived analysis of one played move.

    This is the canonical per-move record: before/after evaluations (mover
    perspective), the evaluation change, centipawn loss, the engine's best
    move, classification, and the principal variation. It is written
    incrementally so an interrupted run keeps the moves it finished.
    """

    __tablename__ = "move_analyses"
    __table_args__ = (
        UniqueConstraint("game_id", "ply", "analysis_version", name="uq_move_analysis"),
        Index("ix_move_analyses_game", "game_id"),
        CheckConstraint("ply >= 1", name="ck_move_analyses_ply"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    mover: Mapped[str] = mapped_column(String(8))
    played_move_uci: Mapped[str] = mapped_column(String(8))
    played_move_san: Mapped[str] = mapped_column(String(16))
    fen_before: Mapped[str] = mapped_column(Text)
    fen_after: Mapped[str] = mapped_column(Text)
    best_move_uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    best_move_san: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evaluation_before_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_before_mate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_after_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_after_mate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_change_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    centipawn_loss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: MultiPV root moves and their scores from the same search that produced
    #: the best line. This is what lets a consumer treat a practically
    #: equivalent alternative as acceptable instead of wrong.
    candidate_moves: Mapped[list] = mapped_column(JSON, default=list)
    # The played move's own score, from the same search that produced the best
    # line whenever it was inside the MultiPV window. Accuracy is computed from
    # this pair; evaluation_after_* mixes two searches and is kept as separate,
    # clearly-labelled evidence.
    played_eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_eval_mate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_eval_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_best_move: Mapped[bool] = mapped_column(Boolean, default=False)
    phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    principal_variation: Mapped[list] = mapped_column(JSON, default=list)
    analysis_version: Mapped[str] = mapped_column(String(16), default="3.1")
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CriticalPosition(Base):
    """A candidate critical position detected from engine measurements."""

    __tablename__ = "critical_positions"
    __table_args__ = (
        Index("ix_critical_positions_game", "game_id"),
        UniqueConstraint(
            "game_id", "ply", "reason", "analysis_version", name="uq_critical_position"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(8))
    san: Mapped[str] = mapped_column(String(16))
    fen_before: Mapped[str] = mapped_column(Text)
    evaluation_before_white: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_after_white: Mapped[int | None] = mapped_column(Integer, nullable=True)
    swing_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    severity_score: Mapped[int] = mapped_column(Integer, default=0)
    is_mate_related: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(16), default="3.1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GameReportRecord(Base):
    """A generated Phase 4 intelligence report, stored as structured JSON.

    The report is derived data (the engine analysis stays authoritative), so it
    is versioned by ``report_version`` and the ``analysis_version`` it was built
    from: a report generated over an older analysis is never silently reused for
    a newer one.
    """

    __tablename__ = "game_reports"
    __table_args__ = (
        UniqueConstraint(
            "game_id", "report_version", "analysis_version", name="uq_game_report"
        ),
        Index("ix_game_reports_game", "game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    report_version: Mapped[str] = mapped_column(String(16))
    analysis_version: Mapped[str] = mapped_column(String(16))
    engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moves_considered: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_moves: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EngineConfiguration(Base):
    """A recorded engine configuration, for auditability of analyses."""

    __tablename__ = "engine_configurations"
    __table_args__ = (UniqueConstraint("config_hash", name="uq_engine_config_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_version: Mapped[str] = mapped_column(String(16), default="3.1")
    profile: Mapped[str | None] = mapped_column(String(16), nullable=True)
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    movetime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    multipv: Mapped[int] = mapped_column(Integer, default=1)
    threads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# =============================================================================
# Phase 8: personalized chess training engine
# =============================================================================
#
# Three tables carry the product loop (play → analyze → understand → train →
# retest → measure):
#
#   TrainingPosition  one exercise, with its traceable origin and its
#                     engine-verified solution (created once, never re-searched
#                     at read time — see spec §44)
#   TrainingAttempt   every attempt ever made, including hints and timing
#                     (spec §24: never only final scores)
#   TrainingSession   one sitting, resumable, with its planned positions
#
# Ownership follows the existing model: a TrainingPosition points at the
# player it was derived from (or is a GENERAL/global exercise with a NULL
# player), and every attempt/session carries its own player_id so endpoints
# can authorize without trusting the frontend (spec §36).


class TrainingPosition(Base):
    """One training exercise derived from a verified mistake.

    The origin chain is stored, not implied: source_game_id + source_ply +
    source_position_id trace back to the exact game position, and
    source_reason carries the human-readable reason this qualified. The
    solution is engine-verified at creation time (engine version + depth
    recorded), so serving a puzzle never runs Stockfish.
    """

    __tablename__ = "training_positions"
    __table_args__ = (
        # Duplicate detection starts exact (spec §23): the same FEN may not
        # repeat for the same player *in the same exercise format*. The
        # single-move formats share a position between themselves; the whole-game
        # formats added in methodology 8.2 (WHAT_WENT_WRONG, RECONSTRUCTION) are
        # separate exercises over the same position because they carry different
        # evidence (the game's real continuation, or its stored best-move line).
        # Genuinely different positions that share an opening are untouched.
        UniqueConstraint(
            "player_id",
            "source_fen_normalized",
            "position_type",
            name="uq_training_position_fen_type",
        ),
        Index("ix_training_positions_player", "player_id"),
        Index("ix_training_positions_game", "source_game_id"),
        Index("ix_training_positions_review", "player_id", "next_review_at"),
        CheckConstraint(
            f"category IN ({VALID_TRAINING_CATEGORIES_SQL})", name="ck_training_category"
        ),
        CheckConstraint(
            f"difficulty IN ({VALID_TRAINING_DIFFICULTY_SQL})", name="ck_training_difficulty"
        ),
        CheckConstraint(
            f"position_type IN ({VALID_TRAINING_TYPES_SQL})", name="ck_training_position_type"
        ),
        CheckConstraint(
            f"data_source IN ({VALID_TRAINING_SOURCES_SQL})", name="ck_training_source"
        ),
        CheckConstraint(
            f"state IN ({VALID_TRAINING_STATE_SQL})", name="ck_training_state"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Owning player. NULL only for GENERAL (global) exercises.
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: The traceable origin (spec §1). Kept even if the game is later deleted:
    #: the attempt history survives and the source is marked unavailable
    #: (spec §43).
    source_game_id: Mapped[str | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), nullable=True
    )
    source_ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The side the exercise is played from — the side that made the mistake.
    side_to_move: Mapped[str] = mapped_column(String(8))
    fen: Mapped[str] = mapped_column(Text)
    #: Normalized FEN (halfmove/fullmove counters stripped) — the dedupe key.
    source_fen_normalized: Mapped[str] = mapped_column(String(120))
    position_type: Mapped[str] = mapped_column(String(32), default="find_best_move")
    category: Mapped[str] = mapped_column(String(24))
    difficulty: Mapped[str] = mapped_column(String(16), default="easy")
    #: Measurable difficulty factors, stored beside the label (spec §15).
    difficulty_factors: Mapped[dict] = mapped_column(JSON, default=dict)
    #: PERSONALIZED vs GENERAL (spec §33) — rendered verbatim in the UI.
    #: Width must fit the longest valid source (``opponent_preparation``, 20
    #: chars); a shorter column makes that source un-insertable (a 500 at the
    #: prepare endpoint), which is why this is 32 and not 16.
    data_source: Mapped[str] = mapped_column(String(32), default="personalized")
    #: Human-readable origin: "Recurring tactical pattern: hanging pieces".
    source_reason: Mapped[str] = mapped_column(Text, default="")
    #: Tactical/positional tags used for similarity retrieval (spec §38).
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # --- the engine-verified solution (spec §7) ------------------------------
    solution_uci: Mapped[str] = mapped_column(String(8))
    solution_san: Mapped[str] = mapped_column(String(16))
    #: Other moves within the acceptance tolerance — all accepted as correct
    #: (spec §8). UCI keys, SAN values.
    acceptable_moves: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Candidate moves (played move included) for CHOOSE_BETWEEN_MOVES puzzles.
    candidate_moves: Mapped[list] = mapped_column(JSON, default=list)
    principal_variation: Mapped[list] = mapped_column(JSON, default=list)
    #: The stored engine line *after* the solution (UCI), for CONTINUE_LINE
    #: exercises. Empty for every other position type.
    continuation_line: Mapped[list] = mapped_column(JSON, default=list)
    #: Whole-game provenance for the replay formats (WHAT_WENT_WRONG, added in
    #: methodology 8.2): the game's real continuation and its evaluation
    #: trajectory, read from storage. Empty for the single-move types.
    replay_context: Mapped[dict] = mapped_column(JSON, default=dict)
    solution_eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solution_eval_mate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_move_uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    played_move_san: Mapped[str | None] = mapped_column(String(16), nullable=True)
    played_eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_loss_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- provenance of the solution ------------------------------------------
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    analysis_version: Mapped[str] = mapped_column(String(16), default="3.1")
    methodology_version: Mapped[str] = mapped_column(String(16), default="8.0")

    # --- spaced repetition state (spec §17) -----------------------------------
    state: Mapped[str] = mapped_column(String(16), default="new")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct_attempts: Mapped[int] = mapped_column(Integer, default=0)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    review_interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingAttempt(Base):
    """One attempt at one exercise — the full history, kept forever (spec §24)."""

    __tablename__ = "training_attempts"
    __table_args__ = (
        Index("ix_training_attempts_player", "player_id"),
        Index("ix_training_attempts_position", "training_position_id"),
        Index("ix_training_attempts_session", "session_id"),
        CheckConstraint(
            f"correctness IN ({VALID_ATTEMPT_CORRECTNESS_SQL})", name="ck_training_attempt_correctness"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Denormalized from the position so history survives a re-scoped exercise.
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    training_position_id: Mapped[int] = mapped_column(
        ForeignKey("training_positions.id", ondelete="CASCADE")
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="SET NULL"), nullable=True
    )
    submitted_uci: Mapped[str] = mapped_column(String(8))
    submitted_san: Mapped[str | None] = mapped_column(String(16), nullable=True)
    correctness: Mapped[str] = mapped_column(String(16))
    #: The engine evaluation of the submitted move, and the gap to the solution
    #: (centipawns, mover perspective) — what the feedback renders (spec §10).
    submitted_eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_delta_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingSession(Base):
    """One training sitting: planned positions, resumable, with a score.

    ``planned_position_ids`` is an ordered JSON list, so the session can be
    resumed exactly where it stopped (spec §19) and cancelling it destroys
    nothing but the session row itself.
    """

    __tablename__ = "training_sessions"
    __table_args__ = (
        Index("ix_training_sessions_player", "player_id"),
        CheckConstraint(
            f"status IN ({VALID_TRAINING_SESSION_STATUS_SQL})", name="ck_training_session_status"
        ),
        CheckConstraint(f"kind IN ({VALID_SESSION_KINDS_SQL})", name="ck_training_session_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="quick")
    target_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    planned_position_ids: Mapped[list] = mapped_column(JSON, default=list)
    completed_position_ids: Mapped[list] = mapped_column(JSON, default=list)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    near_best_count: Mapped[int] = mapped_column(Integer, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, default=0)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def score(self) -> float | None:
        """Accuracy over decided attempts; None when nothing was attempted."""
        correct = self.correct_count or 0
        near = self.near_best_count or 0
        incorrect = self.incorrect_count or 0
        decided = correct + near + incorrect
        if not decided:
            return None
        return round((correct + 0.5 * near) / decided, 4)


# =============================================================================
# Phase 9: opponent intelligence
# =============================================================================
#
# Opponent intelligence is *derived* from the same Game/MoveAnalysis rows the
# rest of the platform already stores: there is deliberately no second player
# identity system and no duplicated game data. One cached snapshot table holds
# the generated report so a dashboard request does not re-aggregate every game
# on every read (the fingerprint in ``source_signature`` marks it stale).


class ScenarioRecordRow(Base):
    """A stored, immutable counterfactual scenario (Phase 10).

    A scenario is a *record of an analysis*, not a modification of a game:
    nothing here can change a stored game. The row keeps the source FEN and the
    branch it produced, and the game foreign key is ``ON DELETE SET NULL`` so a
    deleted game leaves the record readable (with its origin marked unavailable)
    rather than cascading away the evidence that an analysis was once run.

    Immutable by intent: a scenario is reproducible from its stored engine
    configuration and FENs, so there is nothing to update. Re-running the same
    question writes a new row, and both remain auditable.
    """

    __tablename__ = "scenarios"
    __table_args__ = (
        Index("ix_scenarios_game", "game_id"),
        Index("ix_scenarios_owner", "owner_player_id"),
        Index("ix_scenarios_type", "scenario_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_type: Mapped[str] = mapped_column(String(32))
    methodology_version: Mapped[str] = mapped_column(String(16))
    source_fen: Mapped[str] = mapped_column(Text)
    resulting_fen: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The game this scenario branched from, kept for provenance. ``SET NULL``
    #: because the scenario outlives the game it was derived from.
    game_id: Mapped[str | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), nullable=True
    )
    ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The player who asked, so a stored analysis is private to them.
    owner_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    #: The search limits, so the result is reproducible or provably not.
    engine_config: Mapped[dict] = mapped_column(JSON, default=dict)
    branch: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudyCollectionRecord(Base):
    """A user-curated collection of pointers (Phase 11 §29).

    The collection holds *typed pointers* (game, position, training exercise,
    scenario, insight, opening, endgame) plus an optional note; it never copies a
    game or an analysis. Ownership is a real column so the API can authorize
    without trusting the frontend.
    """

    __tablename__ = "study_collections"
    __table_args__ = (
        Index("ix_study_collections_owner", "player_id"),
        UniqueConstraint("player_id", "name", name="uq_study_collection_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(24), default="mixed")
    methodology_version: Mapped[str] = mapped_column(String(16), default="11.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["StudyItemRecord"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="StudyItemRecord.id",
    )


class StudyItemRecord(Base):
    """One typed pointer inside a study collection (Phase 11 §29)."""

    __tablename__ = "study_items"
    __table_args__ = (
        Index("ix_study_items_collection", "collection_id"),
        UniqueConstraint("collection_id", "item_kind", "item_ref", name="uq_study_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("study_collections.id", ondelete="CASCADE")
    )
    item_kind: Mapped[str] = mapped_column(String(24))
    item_ref: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    game_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fen: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped["StudyCollectionRecord"] = relationship(back_populates="items")


class MatchPreparationRecord(Base):
    """A stored match preparation snapshot (Phase 11 §14–§17).

    A snapshot, not a live view: it records what Caissa knew about an opponent at
    the moment the user prepared, keyed to the opponent profile version it was
    built from so a later read can tell that it is stale rather than silently
    serving old evidence.
    """

    __tablename__ = "match_preparations"
    __table_args__ = (
        Index("ix_match_preparations_owner", "preparing_player_id"),
        Index("ix_match_preparations_opponent", "opponent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preparing_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE")
    )
    opponent_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    opponent_name: Mapped[str] = mapped_column(String(200), default="")
    as_white: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    opponent_profile_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    coverage: Mapped[str] = mapped_column(String(24), default="insufficient")
    opponent_games: Mapped[int] = mapped_column(Integer, default=0)
    analysed_games: Mapped[int] = mapped_column(Integer, default=0)
    methodology_version: Mapped[str] = mapped_column(String(16), default="11.0")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    scenarios: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OpponentProfileRecord(Base):
    """A stored, versioned Opponent Intelligence snapshot.

    Mirrors ``PlayerProfileRecord``: one row per (player, profile_version),
    replaced in place when rebuilt, with ``source_signature`` recording the
    fingerprint of the games + analysis versions it was built from so a stale
    snapshot is detectable without re-deriving it.
    """

    __tablename__ = "opponent_profiles"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "profile_version", name="uq_opponent_profile_version"
        ),
        Index("ix_opponent_profiles_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    profile_version: Mapped[str] = mapped_column(String(16))
    methodology_version: Mapped[str] = mapped_column(String(16))
    #: Fingerprint of the inputs (analyzed game count, latest analysis, versions).
    source_signature: Mapped[str] = mapped_column(String(300))
    imported_games: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_games: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[str] = mapped_column(String(24), default="insufficient")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# =============================================================================
# Phase 12: live chess
# =============================================================================
#
# A live game is a first-class row, not a session in memory. Its canonical state
# (position, clocks, status, version) lives in `live_games`, its moves in
# `live_game_moves`, and every broadcast event in `live_game_events`. Persisting
# the event log is what makes reconnection exact: a client reports the last
# sequence it saw and the server replays the difference.
#
# When a game finishes it is *also* written into the existing `games` table (via
# `live_games.library_game_id`) so the whole post-game pipeline — analysis,
# report, debrief, training — runs unchanged on real library data.

VALID_LIVE_STATUS_SQL = (
    "'waiting','ready','active','paused','finished','resigned','timeout',"
    "'draw_agreed','aborted','disconnected'"
)
VALID_LIVE_MODES_SQL = "'local','private_match','training','sandbox'"
VALID_LIVE_VISIBILITY_SQL = "'private','unlisted','public'"


class LiveGameRecord(Base):
    """The server-authoritative state of one live game."""

    __tablename__ = "live_games"
    __table_args__ = (
        Index("ix_live_games_status", "status"),
        Index("ix_live_games_owner", "owner_player_id"),
        Index("uq_live_games_invite", "invite_token", unique=True),
        CheckConstraint(f"status IN ({VALID_LIVE_STATUS_SQL})", name="ck_live_game_status"),
        CheckConstraint(f"mode IN ({VALID_LIVE_MODES_SQL})", name="ck_live_game_mode"),
        CheckConstraint(
            f"visibility IN ({VALID_LIVE_VISIBILITY_SQL})", name="ck_live_game_visibility"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="waiting")
    mode: Mapped[str] = mapped_column(String(16), default="private_match")
    analysis_mode: Mapped[str] = mapped_column(String(24), default="no_analysis")
    coach_level: Mapped[str] = mapped_column(String(16), default="hints")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    rated: Mapped[bool] = mapped_column(Boolean, default=False)
    variant: Mapped[str] = mapped_column(String(16), default="standard")

    owner_player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    white_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    black_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    white_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    black_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Single-use-ish invite token; unpredictable, expires, and is the only way to
    #: join a private game by link.
    invite_token: Mapped[str] = mapped_column(String(64))
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    initial_fen: Mapped[str] = mapped_column(Text)
    current_fen: Mapped[str] = mapped_column(Text)
    side_to_move: Mapped[str] = mapped_column(String(8), default="white")
    move_number: Mapped[int] = mapped_column(Integer, default=1)

    clock_config: Mapped[dict] = mapped_column(JSON, default=dict)
    clock: Mapped[dict] = mapped_column(JSON, default=dict)

    #: Who fills each seat: ``{"white": "human"|"engine"|"open", ...}``. Stored so
    #: the server (not the client) decides whose move it is, including the
    #: engine's.
    seats: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Engine-opponent settings (depth / skill / elo). Never sent to a
    #: competitive opponent.
    engine: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Training-game preset (§55); empty for a normal game.
    training_mode: Mapped[str] = mapped_column(String(24), default="")

    version: Mapped[int] = mapped_column(Integer, default=0)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(8), default="*")
    result_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    draw_offer: Mapped[str | None] = mapped_column(String(8), nullable=True)

    #: The library `games.id` created when this live game finished. NULL until then.
    library_game_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    methodology_version: Mapped[str] = mapped_column(String(16), default="12.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    moves: Mapped[list["LiveGameMoveRecord"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="LiveGameMoveRecord.ply"
    )
    events: Mapped[list["LiveGameEventRecord"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        order_by="LiveGameEventRecord.sequence_number",
    )


class LiveGameMoveRecord(Base):
    """One move of a live game, with the clock after it."""

    __tablename__ = "live_game_moves"
    __table_args__ = (
        UniqueConstraint("live_game_id", "ply", name="uq_live_move_ply"),
        Index("ix_live_moves_game", "live_game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    live_game_id: Mapped[str] = mapped_column(
        ForeignKey("live_games.id", ondelete="CASCADE")
    )
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))
    san: Mapped[str] = mapped_column(String(16))
    uci: Mapped[str] = mapped_column(String(8))
    fen_before: Mapped[str] = mapped_column(Text)
    fen_after: Mapped[str] = mapped_column(Text)
    clock_white_ms: Mapped[int] = mapped_column(Integer, default=0)
    clock_black_ms: Mapped[int] = mapped_column(Integer, default=0)
    sequence_number: Mapped[int] = mapped_column(Integer, default=0)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped["LiveGameRecord"] = relationship(back_populates="moves")


class LiveGameEventRecord(Base):
    """One broadcast event, stored so reconnection can replay the difference."""

    __tablename__ = "live_game_events"
    __table_args__ = (
        UniqueConstraint("live_game_id", "sequence_number", name="uq_live_event_sequence"),
        Index("ix_live_events_game", "live_game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    live_game_id: Mapped[str] = mapped_column(
        ForeignKey("live_games.id", ondelete="CASCADE")
    )
    event_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(32))
    sequence_number: Mapped[int] = mapped_column(Integer)
    game_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped["LiveGameRecord"] = relationship(back_populates="events")


# =============================================================================
# Phase 13: Caissa Intelligence Graph + chess knowledge system
# =============================================================================
#
# The graph is a *view* over the domain, not a second copy of it. Nodes reference
# existing objects by ``(node_type, node_key)`` — a game id, a player id, a
# position hash, a pattern id — and carry only small, non-authoritative display
# attributes. The authoritative data stays in its own table.
#
# Edges are directional and typed, and every derived edge carries its evidence in
# a JSON column so a claim can always be traced to the games/positions/attempts
# that produced it. A unique constraint on the full edge identity makes writes
# idempotent, which is what lets the incremental updater re-run safely.


class GraphNodeRecord(Base):
    """One graph node: a pointer to an existing domain object."""

    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("node_type", "node_key", name="uq_graph_node"),
        Index("ix_graph_nodes_type", "node_type"),
        Index("ix_graph_nodes_key", "node_key"),
        Index("ix_graph_nodes_type_methodology", "node_type", "methodology_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32))
    node_key: Mapped[str] = mapped_column(String(160))
    label: Mapped[str] = mapped_column(String(300), default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    methodology_version: Mapped[str] = mapped_column(String(16), default="13.0")
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphEdgeRecord(Base):
    """One directed, typed relationship, with the evidence behind it."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "edge_type",
            "from_type",
            "from_key",
            "to_type",
            "to_key",
            name="uq_graph_edge",
        ),
        Index("ix_graph_edges_from", "from_type", "from_key"),
        Index("ix_graph_edges_to", "to_type", "to_key"),
        Index("ix_graph_edges_type", "edge_type"),
        Index("ix_graph_edges_methodology", "methodology_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_type: Mapped[str] = mapped_column(String(32))
    from_type: Mapped[str] = mapped_column(String(32))
    from_key: Mapped[str] = mapped_column(String(160))
    to_type: Mapped[str] = mapped_column(String(32))
    to_key: Mapped[str] = mapped_column(String(160))
    #: The evidence references that justify this edge (see
    #: ``argus.intelligence_graph.evidence.EvidenceReference``). Empty for
    #: structural edges (a game contains its positions).
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    methodology_version: Mapped[str] = mapped_column(String(16), default="13.0")
    data_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphSnapshotRecord(Base):
    """A reproducible description of the graph at a point in time (§8/§37)."""

    __tablename__ = "graph_snapshots"
    __table_args__ = (Index("ix_graph_snapshots_cutoff", "data_cutoff"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(16))
    methodology_version: Mapped[str] = mapped_column(String(16))
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    node_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    edge_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    total_nodes: Mapped[int] = mapped_column(Integer, default=0)
    total_edges: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- knowledge system ---------------------------------------------------------
#
# Source-backed chess knowledge. Only material the application is authorized to
# use is ingested, so every source row records its licence. The concept text for
# Caissa's own glossary is derived from the curated agent base rather than copied,
# so the two cannot drift.


class KnowledgeSourceRecord(Base):
    """Where a body of knowledge came from, and the terms it is held under."""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(24), default="argus")
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    licence: Mapped[str] = mapped_column(String(300), default="")
    publication: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(16), default="13.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeDocumentRecord(Base):
    """One titled work from a knowledge source."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_source", "source_id"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    licence: Mapped[str] = mapped_column(String(300), default="")
    publication: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(16), default="13.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunkRecord(Base):
    """A retrievable passage of a knowledge document."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_document", "document_id"),)

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    concepts: Mapped[list] = mapped_column(JSON, default=list)


class KnowledgeConceptRecord(Base):
    """A sourced, versioned chess concept, linkable from positions (§27)."""

    __tablename__ = "knowledge_concepts"
    __table_args__ = (
        Index("ix_knowledge_concepts_source", "source_id"),
        Index("ix_knowledge_concepts_category", "category"),
    )

    slug: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(48))
    definition: Mapped[str] = mapped_column(Text)
    how_to_spot: Mapped[str] = mapped_column(Text, default="")
    typical_mistake: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    document_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    related_base_concept: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str] = mapped_column(String(16), default="13.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
