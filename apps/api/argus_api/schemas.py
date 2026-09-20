"""API request/response schemas (Pydantic).

Request validation lives here; domain models from ``argus`` are reused as
response models where suitable so no business logic is duplicated.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from argus.analysis.engine.base import AnalyzedPosition
from argus.analysis.game_analyzer import GameAnalysis
from argus.analysis.reports import GameReport


class GameImportRequest(BaseModel):
    """Request body for importing (and optionally analyzing) a PGN game."""

    pgn_text: str = Field(min_length=1, description="Raw PGN text of one or more games")
    run_analysis: bool = Field(default=True, description="Run Stockfish analysis on import")
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)
    persist: bool = Field(default=True, description="Save the game and analysis to the database")


class PgnValidateRequest(BaseModel):
    pgn_text: str = Field(min_length=1)


class PositionAnalysisRequest(BaseModel):
    fen: str = Field(min_length=1)
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)


class GameAnalysisRequest(BaseModel):
    game_id: str = Field(min_length=1)
    depth: int | None = Field(default=None, ge=1, le=30)
    multipv: int | None = Field(default=None, ge=1, le=5)


class ErrorResponse(BaseModel):
    """Standard error envelope produced by the central error handler."""

    error: dict


class GameImportResponse(BaseModel):
    game_id: str
    moves: int
    analyzed: bool
    analysis: GameAnalysis | None = None
    report: GameReport | None = None
    engine: dict | None = None


class GameAnalysisResponse(BaseModel):
    game_id: str
    analysis: GameAnalysis
    report: GameReport
    engine: dict | None = None
