"""Phase 10 API service: the seam between stored games and the scenario engine.

This module owns the *API* side of decision intelligence:

* it turns a ``(game_id, ply)`` into the FEN and the move that was actually
  played there, using the stored game moves — the same rows every other phase
  reads, so a counterfactual is always about the real game;
* it applies the authorization decision in one place (a game the caller may not
  read is a 404, exactly as in Phases 8 and 9);
* it builds the package-level :class:`~argus.scenarios.service.ScenarioService`
  with the app's engine and the app's prediction registry;
* it persists a scenario only when asked, and only as an immutable record.

The chess work itself lives in ``argus.scenarios``; nothing here evaluates a
position, and nothing here decides whether a prediction is available — the
prediction service does that.
"""

from __future__ import annotations

import chess
from sqlalchemy.orm import Session

from argus.scenarios import (
    ScenarioBranch,
    ScenarioService,
    ScenarioType,
    TurningPointExplorer,
    scenario_payload,
)
from argus.scenarios.explorer import explore as explore_rows
from argus.scenarios.metrics import REGISTRY as METRICS
from argus.shared.errors import NotFoundError, ValidationError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_critical_positions,
    get_game,
    get_moves,
    get_scenario,
    list_scenarios,
    resolve_analysis_version,
    save_scenario,
    save_training_position,
)
from argus_api.services import opponent_service
from argus_api.services.authorization import authorized_for_game
from argus_api.services.prediction_service import prediction_service_for
from argus_api.services.training_service import move_analysis_rows

logger = get_logger(__name__)

#: Stored scenarios are indexed by their type, so a filter must name a real one.
SCENARIO_TYPES = {item.value for item in ScenarioType}


#: The shared service: one per process (per engine), so the cache actually
#: persists between requests. A per-request instance would give every request an
#: empty cache, which makes the hit rate a meaningless number and re-searches the
#: same position on every repeat.
_SERVICE: ScenarioService | None = None
_SERVICE_ENGINE: object | None = None
_SERVICE_KEY: tuple[int, float] | None = None


def service_for(engine: object | None, settings: object | None = None) -> ScenarioService:
    """The process-wide scenario service for this engine and configuration.

    Rebuilt only when the engine object or the cache configuration changes, so a
    repeated question is answered from the cache and the reported hit rate means
    something. Tests build their own instance directly.
    """
    global _SERVICE, _SERVICE_ENGINE, _SERVICE_KEY
    cache_entries = int(getattr(settings, "scenario_cache_entries", 256) or 256)
    cache_ttl = float(getattr(settings, "scenario_cache_ttl_seconds", 900) or 900)
    key = (cache_entries, cache_ttl)
    if _SERVICE is not None and _SERVICE_ENGINE is engine and _SERVICE_KEY == key:
        return _SERVICE
    _SERVICE = ScenarioService(
        engine,  # type: ignore[arg-type] — the app engine satisfies ChessEngine
        prediction_service=prediction_service_for(settings),
        cache_entries=cache_entries,
        cache_ttl_seconds=cache_ttl,
    )
    _SERVICE_ENGINE = engine
    _SERVICE_KEY = key
    return _SERVICE


def reset_service() -> None:
    """Drop the shared service (used when the engine or settings change)."""
    global _SERVICE, _SERVICE_ENGINE, _SERVICE_KEY
    _SERVICE = None
    _SERVICE_ENGINE = None
    _SERVICE_KEY = None


def require_authorized_game(db: Session, game_id: str) -> None:
    """Raise 404 unless the caller may read this game.

    Authorization lives here so a new endpoint cannot forget it: every
    game-scoped operation goes through this function, and the underlying decision
    still has exactly one home (``services.authorization``).
    """
    if not authorized_for_game(db, game_id):
        # A game the caller cannot see is reported as absent, not as forbidden —
        # existence itself is information they are not entitled to.
        raise NotFoundError(f"Game '{game_id}' was not found", details={"game_id": game_id})


def game_position(db: Session, game_id: str, ply: int) -> dict:
    """The real position before a game's ply, plus the move played there.

    Raises:
        NotFoundError: when the game is unknown or the ply does not exist.
        ValidationError: when the stored move cannot be parsed as a chess move.
    """
    require_authorized_game(db, game_id)
    if ply < 1:
        raise ValidationError(
            "A ply is 1-based; ply 0 has no move to branch from", details={"ply": ply}
        )
    moves = get_moves(db, game_id)
    row = next((item for item in moves if item.ply == ply), None)
    if row is None:
        raise NotFoundError(
            f"Game '{game_id}' has no ply {ply}",
            details={"game_id": game_id, "ply": ply, "plies": len(moves)},
        )
    fen = row.fen_before
    try:
        chess.Board(fen)
    except ValueError as exc:
        raise ValidationError(f"Stored position for ply {ply} is not a valid FEN: {exc}") from exc
    return {
        "game_id": game_id,
        "ply": ply,
        "move_number": row.move_number,
        "color": row.color,
        "move_uci": row.uci,
        "move_san": row.san,
        "fen": fen,
        "fen_after": row.fen_after,
        "analysis_status": getattr(get_game(db, game_id), "analysis_status", None),
    }


def source_position(
    db: Session,
    *,
    fen: str | None,
    game_id: str | None,
    ply: int | None,
) -> dict:
    """Resolve a request's position from either a raw FEN or a stored game ply.

    Exactly one source must be given. A game-based request always carries the
    move that was actually played, so "what if" has something real to compare to.
    """
    if fen and game_id:
        raise ValidationError("Provide either a fen or a game_id + ply, not both")
    if fen:
        return {"fen": fen, "game_id": None, "ply": None, "actual_move_uci": None, "actual_move_san": None}
    if not game_id or ply is None:
        raise ValidationError("Provide a fen, or a game_id together with a ply")
    context = game_position(db, game_id, ply)
    return {
        "fen": context["fen"],
        "game_id": game_id,
        "ply": ply,
        "actual_move_uci": context["move_uci"],
        "actual_move_san": context["move_san"],
        "context": context,
    }


def explore_game(db: Session, game_id: str, *, limit: int | None = None) -> TurningPointExplorer:
    """The turning-point explorer for a game, from stored analysis only."""
    require_authorized_game(db, game_id)
    game = get_game(db, game_id)
    rows = move_analysis_rows(db, game_id)
    criticals = [
        {
            "ply": row.ply,
            "severity": row.severity,
            "reason": row.reason,
            "swing_cp": row.swing_cp,
        }
        for row in get_critical_positions(db, game_id)
    ]
    return explore_rows(
        game_id=game_id,
        rows=rows,
        criticals=criticals,
        moves_total=game.move_count,
        limit=limit or 12,
        # Label with the same generation the rows were read from, not merely the
        # newest written one — an interrupted re-run must not relabel old data.
        analysis_version=resolve_analysis_version(db, game_id)[0],
    )


def persist_scenario(
    db: Session,
    branch: ScenarioBranch,
    *,
    game_id: str | None = None,
    ply: int | None = None,
    owner_player_id: int | None = None,
) -> int:
    """Store a branch as an immutable scenario record and return its id."""
    row = save_scenario(
        db,
        scenario_payload(branch, game_id=game_id, ply=ply, owner_player_id=owner_player_id),
    )
    return int(row.id)


def serialize_scenario(row, *, reveal: bool = True) -> dict:  # noqa: ANN001 — ORM row
    """One stored scenario as a response payload."""
    payload = {
        "id": row.id,
        "scenario_type": row.scenario_type,
        "methodology_version": row.methodology_version,
        "source_fen": row.source_fen,
        "resulting_fen": row.resulting_fen,
        "game_id": row.game_id,
        "ply": row.ply,
        "owner_player_id": row.owner_player_id,
        "engine_config": row.engine_config or {},
        "evidence": row.evidence or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source_available": row.game_id is not None,
    }
    if reveal:
        payload["branch"] = row.branch or {}
    return payload


def get_authorized_scenario(db: Session, scenario_id: int, *, player_id: int | None = None) -> dict:
    """A stored scenario the caller may read, or 404.

    A scenario owned by a different player is reported as absent, so one player
    cannot confirm another player's analysis exists by probing ids.
    """
    row = get_scenario(db, scenario_id)
    if row.owner_player_id is not None and player_id is not None and row.owner_player_id != player_id:
        raise NotFoundError(
            f"Scenario {scenario_id} was not found", details={"scenario_id": scenario_id}
        )
    if row.game_id is not None:
        require_authorized_game(db, row.game_id)
    return serialize_scenario(row)


def stored_scenarios(
    db: Session,
    *,
    game_id: str | None = None,
    player_id: int | None = None,
    scenario_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Stored scenarios, scoped to what the caller may read."""
    if game_id is not None:
        require_authorized_game(db, game_id)
    if scenario_type is not None and scenario_type not in SCENARIO_TYPES:
        raise ValidationError(
            f"Unknown scenario type '{scenario_type}'; known types: "
            + ", ".join(sorted(SCENARIO_TYPES)),
            details={"scenario_type": scenario_type},
        )
    rows = list_scenarios(
        db,
        game_id=game_id,
        owner_player_id=player_id,
        scenario_type=scenario_type,
        limit=limit,
    )
    return [serialize_scenario(row) for row in rows]


def create_training_from_scenario(
    db: Session,
    service: ScenarioService,
    *,
    source: dict,
    alternative_move: str,
    player_id: int,
    depth: int | None = None,
    multipv: int | None = None,
) -> dict:
    """Turn a counterfactual into a stored exercise, when the evidence supports it.

    The bridge Phase 10 → Phase 8 asks for, with the same honesty rules as both
    phases: the solution is a move the engine measured as best (or within the
    near-best tolerance the grader already uses), the category is assigned only
    from board evidence, and a move the engine scores as inferior never becomes an
    answer key. When the position already has an exercise, that one is returned
    instead of a duplicate.
    """
    from argus.training.from_scenario import (
        build_training_position,
        solution_is_defensible,
    )

    from argus_api.services.training_service import _position_payload, serialize_position

    comparison = service.compare_moves(
        source["fen"],
        [alternative_move],
        depth=depth,
        multipv=multipv,
        played_move_uci=source.get("actual_move_uci"),
        include_top=3,
    )
    requested = next(
        (item for item in comparison.candidates if item.uci == _uci_of(source["fen"], alternative_move)),
        None,
    )
    if requested is None:
        return {
            "status": "not_found",
            "message": "The move was not among the compared moves for this position.",
        }
    if not requested.legal:
        return {
            "status": "illegal_move",
            "message": requested.legality_note or "That move is not legal in this position.",
        }
    played_uci = source.get("actual_move_uci")
    if played_uci and requested.uci == played_uci:
        return {
            "status": "already_played",
            "message": (
                "That is the move that was played in this position, so it is not a "
                "counterfactual to practise."
            ),
        }
    ok, reason = solution_is_defensible(
        solution_uci=requested.uci,
        best_move_uci=comparison.best_move_uci,
        centipawn_loss=requested.centipawn_loss,
    )
    if not ok:
        return {"status": "not_defensible", "message": reason}

    played = next(
        (item for item in comparison.candidates if item.uci == played_uci), None
    )
    try:
        position = build_training_position(
            fen=source["fen"],
            player_id=player_id,
            solution_uci=requested.uci,
            solution_san=requested.san,
            solution_eval_cp=requested.cp,
            solution_eval_mate=requested.mate,
            alternatives=[item.model_dump(mode="json") for item in comparison.candidates],
            played_move_uci=played_uci,
            played_move_san=source.get("actual_move_san"),
            played_eval_cp=played.cp if played is not None else None,
            tactical_notes=list(requested.tactical_consequence),
            engine=comparison.engine_config.engine,
            engine_version=comparison.engine_config.engine_version,
            depth=comparison.engine_config.depth,
            analysis_version=comparison.methodology_version,
            source_game_id=source.get("game_id"),
            source_ply=source.get("ply"),
            evaluation_change_cp=(
                None
                if requested.cp is None or played is None or played.cp is None
                else requested.cp - played.cp
            ),
        )
    except ValueError as exc:
        return {"status": "insufficient_evidence", "message": str(exc)}

    row = save_training_position(db, _position_payload(position))
    METRICS.incr("training_from_scenario_count")
    return {
        "status": "ok",
        "position_id": int(row.id),
        # Revealed here because the caller just supplied this move: telling them
        # what was stored leaks nothing. A later read of the exercise withholds the
        # solution the way every other training read does.
        "position": serialize_position(row, reveal=True),
        "source": {
            "kind": "game" if source.get("game_id") else "fen",
            "game_id": source.get("game_id"),
            "ply": source.get("ply"),
            "played_move_uci": played_uci,
        },
        "engine_config": comparison.engine_config.model_dump(mode="json"),
        "note": (
            "The exercise's solution is the move the engine measured here, with the "
            "engine's own score. Nothing about it is predicted."
        ),
    }


def _uci_of(fen: str, move: str) -> str:
    """Normalise a move to UCI so it can be matched against a comparison."""
    try:
        board = chess.Board(fen)
    except ValueError:
        return move
    try:
        parsed = chess.Move.from_uci(move)
        if parsed in board.legal_moves:
            return parsed.uci()
    except ValueError:
        pass
    try:
        return board.parse_san(move).uci()
    except ValueError:
        return move


def opponent_response_scenario(
    db: Session,
    service: ScenarioService,
    *,
    opponent_player_id: int,
    fen: str,
    depth: int | None = None,
) -> dict:
    """What the opponent *has* played here versus what the engine recommends.

    Phase 9 integration, and the distinction the spec insists on: the historical
    distribution is a count over stored games, the engine list is a search result.
    They are reported side by side and never merged, and the response never claims
    the opponent will choose any particular move.
    """
    historical = opponent_service.get_position_responses(db, opponent_player_id, fen=fen)
    engine = service.compare_moves(fen, [], depth=depth, include_top=3)
    return {
        "fen": fen,
        "opponent_player_id": opponent_player_id,
        "historically_observed": historical,
        "engine_recommended": {
            "candidates": [item.model_dump(mode="json") for item in engine.candidates],
            "engine_config": engine.engine_config.model_dump(mode="json"),
            "phase": engine.phase,
        },
        "distinction": (
            "'historically_observed' counts what the opponent actually played in their "
            "stored games. 'engine_recommended' is what Stockfish considers strongest in "
            "this position. They answer different questions, and neither predicts the "
            "opponent's next move."
        ),
    }


__all__ = [
    "SCENARIO_TYPES",
    "create_training_from_scenario",
    "reset_service",
    "explore_game",
    "opponent_response_scenario",
    "game_position",
    "get_authorized_scenario",
    "persist_scenario",
    "require_authorized_game",
    "serialize_scenario",
    "service_for",
    "source_position",
    "stored_scenarios",
]
