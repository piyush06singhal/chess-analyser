"""SQLAlchemy ORM models for the analysis data model.

Schema supports a single game having many analyzed positions/moves:
Game -> GameMove -> PositionAnalysis, plus Player/PlayerGame for longitudinal
profiling and AnalysisSession for engine run bookkeeping. Portable column
types (String, Text, JSON) keep SQLite (tests) and PostgreSQL (production)
compatible. Migrations via Alembic arrive in Phase 2 once the schema
stabilizes; until then ``create_all`` bootstraps the schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Player(Base):
    """A chess player (from imported games)."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    games: Mapped[list["PlayerGame"]] = relationship(back_populates="player")


class Game(Base):
    """An imported chess game with standardized metadata."""

    __tablename__ = "games"

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    moves: Mapped[list["GameMove"]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="GameMove.ply"
    )


class GameMove(Base):
    """One move of a game with its surrounding positions."""

    __tablename__ = "game_moves"
    __table_args__ = (Index("ix_game_moves_game_ply", "game_id", "ply"),)

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


class PositionAnalysis(Base):
    """Engine analysis of a single played move (many per game)."""

    __tablename__ = "position_analyses"
    __table_args__ = (
        UniqueConstraint("game_id", "ply", "depth", name="uq_position_analysis"),
        Index("ix_position_analyses_game", "game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    ply: Mapped[int] = mapped_column(Integer)
    move_number: Mapped[int] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(8), default="white")
    fen: Mapped[str] = mapped_column(Text)
    played_move: Mapped[str | None] = mapped_column(String(8), nullable=True)
    best_move: Mapped[str | None] = mapped_column(String(8), nullable=True)
    evaluation_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_change: Mapped[int | None] = mapped_column(Integer, nullable=True)
    depth: Mapped[int] = mapped_column(Integer)
    multipv: Mapped[int] = mapped_column(Integer, default=1)
    centipawn_loss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_sacrifice: Mapped[bool] = mapped_column(Boolean, default=False)
    is_best_move: Mapped[bool] = mapped_column(Boolean, default=False)
    principal_variation: Mapped[list] = mapped_column(JSON, default=list)
    raw_features: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerGame(Base):
    """Association of players to games for longitudinal profiling."""

    __tablename__ = "player_games"
    __table_args__ = (UniqueConstraint("player_id", "game_id", name="uq_player_game"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    color: Mapped[str] = mapped_column(String(8))
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    player: Mapped["Player"] = relationship(back_populates="games")


class AnalysisSession(Base):
    """Bookkeeping of one analysis run (engine, depth, outcome)."""

    __tablename__ = "analysis_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[str | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), nullable=True
    )
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer)
    multipv: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    positions_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
