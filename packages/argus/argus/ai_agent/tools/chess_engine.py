"""Engine tools: the only place a chess evaluation can come from.

Everything an agent says about advantage, best moves or centipawn loss must trace
to one of these three tools, or to a stored analysis produced by the same engine
in an earlier phase. There is no fourth source, and the agent is given no way to
produce one.

Resource limits live here rather than in the prompt. A language model cannot be
trusted to respect "please do not search deeper than 24", so the requested depth
is clamped, the MultiPV width is capped, and both are recorded in the evidence so
a reader can see exactly what was searched. Search cost is the easiest way to make
an agent both slow and expensive, which is why the clamp is a tool-boundary
concern (spec §25).
"""

from __future__ import annotations

from typing import Any

import chess

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.analysis.engine.base import ChessEngine
from argus.shared.errors import EngineUnavailableError, InvalidMoveError

#: Hard ceilings. Requests above these are clamped, not refused — a clamped
#: answer is useful as long as the clamp is disclosed.
MAX_DEPTH = 24
MIN_DEPTH = 6
MAX_MULTIPV = 5

DEFAULT_DEPTH = 16


def clamp_depth(depth: int | None) -> tuple[int, bool]:
    """Return ``(depth, was_clamped)``."""
    if depth is None:
        return DEFAULT_DEPTH, False
    if depth > MAX_DEPTH:
        return MAX_DEPTH, True
    if depth < MIN_DEPTH:
        return MIN_DEPTH, True
    return depth, False


def clamp_multipv(multipv: int | None) -> tuple[int, bool]:
    if multipv is None:
        return 1, False
    if multipv > MAX_MULTIPV:
        return MAX_MULTIPV, True
    if multipv < 1:
        return 1, True
    return multipv, False


def normalize_move(fen: str, move: str) -> str:
    """Accept UCI or SAN and return UCI, refusing anything not legal here.

    Models emit both forms interchangeably. Parsing them here means an illegal or
    hallucinated move is rejected with a clear message instead of reaching the
    engine, where it would either error obscurely or be silently ignored.
    """
    try:
        board = chess.Board(fen)
    except ValueError as exc:  # pragma: no cover - FEN is validated upstream
        raise InvalidMoveError(f"Cannot parse FEN '{fen}': {exc}") from exc
    text = (move or "").strip()
    if not text:
        raise InvalidMoveError("A move is required")
    try:
        parsed = chess.Move.from_uci(text)
        if parsed in board.legal_moves:
            return parsed.uci()
    except ValueError:
        pass
    try:
        parsed = board.parse_san(text)
    except ValueError as exc:
        raise InvalidMoveError(f"'{text}' is not a legal move in this position") from exc
    return parsed.uci()


def _require_engine(providers: AgentProviders) -> ChessEngine:
    engine = providers.engine
    if engine is None:
        raise EngineUnavailableError(
            "No chess engine is configured, so Caissa cannot evaluate a position. "
            "No evaluation will be invented."
        )
    return engine


def _engine_status(engine: ChessEngine) -> dict[str, Any]:
    try:
        return dict(engine.info())
    except Exception:  # noqa: BLE001 — status must never be the failure
        return {"available": False}


def build_engine_tools(providers: AgentProviders) -> list[Tool]:
    """The engine tool family."""

    def analyze_position(
        _context: AgentContext, fen: str, depth: int | None = None
    ) -> dict[str, Any]:
        engine = _require_engine(providers)
        used_depth, clamped = clamp_depth(depth)
        result = engine.analyze_position(fen, depth=used_depth)
        payload = result.model_dump()
        payload["requested_depth"] = depth
        payload["depth_was_clamped"] = clamped
        payload["max_depth_policy"] = MAX_DEPTH
        payload["engine"] = payload.get("engine") or _engine_status(engine).get("engine")
        return payload

    def analyze_position_multipv(
        _context: AgentContext, fen: str, multipv: int = 3, depth: int | None = None
    ) -> dict[str, Any]:
        engine = _require_engine(providers)
        used_depth, depth_clamped = clamp_depth(depth)
        width, width_clamped = clamp_multipv(multipv)
        result = engine.analyze_position(fen, depth=used_depth, multipv=width)
        payload = result.model_dump()
        payload["requested_multipv"] = multipv
        payload["requested_depth"] = depth
        payload["multipv_was_clamped"] = width_clamped
        payload["depth_was_clamped"] = depth_clamped
        payload["max_multipv_policy"] = MAX_MULTIPV
        return payload

    def compare_moves(
        _context: AgentContext,
        fen: str,
        moves: list[str],
        depth: int | None = None,
    ) -> dict[str, Any]:
        engine = _require_engine(providers)
        used_depth, clamped = clamp_depth(depth)
        resolved = [normalize_move(fen, move) for move in moves]
        comparisons = engine.compare_moves(fen, resolved, depth=used_depth)
        return {
            "comparisons": [comparison.model_dump() for comparison in comparisons],
            "resolved_uci": resolved,
            "depth": used_depth,
            "requested_depth": depth,
            "depth_was_clamped": clamped,
        }

    return [
        Tool(
            name="analyze_position",
            description=(
                "Run the chess engine on a FEN. Returns the evaluation, best move and "
                "principal variation. This is the ONLY way to obtain an evaluation: "
                "never state one that did not come from here or from a stored analysis."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 1},
                        # The declared maximum is the *policy* maximum, so the tool
                        # spec itself teaches the limit. The handler still clamps as
                        # a backstop, and discloses when it had to.
                        "depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH},
                    },
                    "required": ["fen"],
                },
                outputs=(
                    "fen",
                    "depth",
                    "best_move_uci",
                    "best_move_san",
                    "lines",
                    "is_terminal",
                    "terminal_reason",
                ),
                uses_engine=True,
            ),
            permission=ToolPermission.ANY,
            handler=analyze_position,
            tags=("engine",),
        ),
        Tool(
            name="analyze_position_multipv",
            description=(
                "Run the engine on a FEN and return several candidate moves ranked by "
                "evaluation, for comparing alternatives."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 1},
                        "multipv": {"type": "integer", "minimum": 1, "maximum": MAX_MULTIPV},
                        "depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH},
                    },
                    "required": ["fen"],
                },
                outputs=("fen", "depth", "multipv", "lines"),
                uses_engine=True,
            ),
            permission=ToolPermission.ANY,
            handler=analyze_position_multipv,
            tags=("engine",),
        ),
        Tool(
            name="compare_moves",
            description=(
                "Compare specific moves in a position against the engine's best, to "
                "quantify what a played move cost. Accepts UCI or SAN move strings."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "fen": {"type": "string", "minLength": 1},
                        "moves": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "depth": {"type": "integer", "minimum": 1, "maximum": MAX_DEPTH},
                    },
                    "required": ["fen", "moves"],
                },
                outputs=("comparisons", "resolved_uci", "depth"),
                uses_engine=True,
            ),
            permission=ToolPermission.ANY,
            handler=compare_moves,
            tags=("engine",),
        ),
    ]


__all__ = [
    "DEFAULT_DEPTH",
    "MAX_DEPTH",
    "MAX_MULTIPV",
    "build_engine_tools",
    "clamp_depth",
    "clamp_multipv",
    "normalize_move",
]
