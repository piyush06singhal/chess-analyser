"""Decision-intelligence routes (Phase 10): counterfactuals, scenarios, what-if.

These endpoints expose the questions Phase 10 exists to answer:

* **compare positions** — two axes, kept apart (engine result vs board facts);
* **compare candidate moves** — several moves, one search, measured consequences;
* **counterfactual** — the actual line and an alternative line, ply by ply;
* **why not this move?** — the measured case against a move, and the better options;
* **what if I had played ...?** — the same branch, framed as a hypothesis;
* **turning-point explorer** — where a game could have gone differently, from the
  stored analysis alone (no engine call).

Two properties hold across the whole surface. Nothing runs the engine unless the
answer needs one, and every refusal is a *result*: an illegal move, an
unavailable engine or a missing production model comes back as a status with its
reason, never as a plausible-looking number. HTTP 200 with ``status != "ok"`` is
the intended shape for "that move is not legal in this position".
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from argus.shared.errors import ValidationError
from argus.shared.logging import get_logger

from argus_api.deps import get_db
from argus_api.schemas import (
    ScenarioBranchRequest,
    ScenarioCompareMovesRequest,
    ScenarioComparePositionsRequest,
    ScenarioMoveQueryRequest,
    ScenarioOpponentResponseRequest,
    ScenarioPositionRequest,
    ScenarioPredictRequest,
    ScenarioTrainingRequest,
)
from argus_api.services import scenario_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _service(request: Request):  # noqa: ANN202 — ScenarioService
    """The scenario service for this request: app engine + prediction registry."""
    settings = getattr(request.app.state, "settings", None)
    engine = getattr(request.app.state, "engine", None)
    return scenario_service.service_for(engine, settings)


# --- capability ---------------------------------------------------------------


@router.get("/meta")
def get_scenario_meta(request: Request) -> dict:
    """What this deployment can compute right now, and its limits.

    Reported rather than assumed: when no engine is available the capability list
    says so, and the counterfactual endpoints answer ``unavailable`` with a reason
    instead of failing or inventing a score.
    """
    service = _service(request)
    meta = service.meta()
    meta["limits"] = {
        "max_candidate_moves": 8,
        "max_continuation_plies": 12,
        "max_depth": 24,
        "default_continuation_plies": 6,
    }
    meta["refusal_semantics"] = (
        "A counterfactual can answer 'illegal_move', 'unavailable' or "
        "'insufficient_evidence'; each is a result, not an error."
    )
    return meta


@router.get("/predictions")
def get_scenario_predictions(request: Request) -> dict:
    """Prediction availability for every declared task, production-gated.

    This is the honest capability list Phase 10 requires before it may attach a
    prediction to a scenario: a task with no production model is listed as
    unavailable with its reason.
    """
    service = _service(request)
    prediction_service = getattr(service, "prediction_service", None)
    if prediction_service is None:
        return {
            "available": [],
            "unavailable": [],
            "note": "No prediction service is configured in this deployment.",
        }
    tasks = prediction_service.available_tasks()
    return {
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "available": [task.task for task in tasks if task.available],
        "unavailable": [task.task for task in tasks if not task.available],
        "note": (
            "Only models with registry status PRODUCTION may be served. A task that "
            "has no production model is not a missing feature: it is a task whose "
            "model has not been validated."
        ),
    }


@router.post("/predict")
def post_scenario_predict(
    body: ScenarioPredictRequest, request: Request
) -> dict:
    """Serve a prediction from a production model, or say why not.

    There is no fallback path: an unvalidated model answers with
    ``available: false`` and the reason.
    """
    service = _service(request)
    return service.attach_prediction(body.task, [dict(row) for row in body.rows])


# --- positions ----------------------------------------------------------------


@router.post("/position")
def post_scenario_position(body: ScenarioPositionRequest) -> dict:
    """Board facts for one position. No engine call: these are board readings."""
    from argus.scenarios import position_facts

    return position_facts(body.fen).model_dump(mode="json")


@router.post("/compare-positions")
async def post_compare_positions(
    body: ScenarioComparePositionsRequest, request: Request
) -> dict:
    """Compare two positions: engine axis and structural axis, kept separate."""
    service = _service(request)
    comparison = await asyncio.to_thread(
        service.compare_positions,
        body.fen_a,
        body.fen_b,
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
    )
    return comparison.model_dump(mode="json")


@router.post("/compare-moves")
async def post_compare_moves(body: ScenarioCompareMovesRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Compare candidate moves in one position, from a single search.

    The position comes from a FEN or a stored game ply. A game-based request is
    authorized before it is read, and its played move is marked in the result so
    the comparison always shows what was actually played.
    """
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    played = body.played_move_uci or source.get("actual_move_uci")
    service = _service(request)
    comparison = await asyncio.to_thread(
        service.compare_moves,
        source["fen"],
        list(body.moves),
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
        played_move_uci=played,
        include_top=body.include_top,
    )
    payload = comparison.model_dump(mode="json")
    payload["source"] = {
        "kind": "game" if source.get("game_id") else "fen",
        "game_id": source.get("game_id"),
        "ply": source.get("ply"),
        "played_move_uci": played,
        "played_move_san": source.get("actual_move_san"),
    }
    return payload


# --- counterfactuals ----------------------------------------------------------


@router.post("/counterfactual")
async def post_counterfactual(body: ScenarioBranchRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Build (and optionally store) one counterfactual branch."""
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    service = _service(request)
    # An opponent-response scenario is only askable with the opponent's games. The
    # history is read here, never invented: Phase 9 counts what they actually
    # played in stored games, and the branch keeps it apart from the engine line.
    opponent_historical = None
    if body.opponent_player_id is not None:
        from argus_api.services import opponent_service

        if body.scenario_type != "opponent_response":
            raise ValidationError(
                "opponent_player_id is only meaningful for an 'opponent_response' scenario",
                details={"scenario_type": body.scenario_type},
            )
        opponent_historical = await asyncio.to_thread(
            opponent_service.get_position_responses,
            db,
            body.opponent_player_id,
            fen=source["fen"],
        )
    outcome = await asyncio.to_thread(
        service.counterfactual_safely,
        source["fen"],
        body.alternative_move,
        actual_move=body.actual_move or source.get("actual_move_uci"),
        scenario_type=body.scenario_type,
        plies_ahead=body.plies_ahead,
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
        opponent_historical=opponent_historical,
    )
    payload = outcome.model_dump(mode="json")
    payload["source"] = {
        "kind": "game" if source.get("game_id") else "fen",
        "game_id": source.get("game_id"),
        "ply": source.get("ply"),
        "actual_move_uci": body.actual_move or source.get("actual_move_uci"),
        "actual_move_san": source.get("actual_move_san"),
    }
    if body.persist and outcome.branch is not None:
        scenario_id = await asyncio.to_thread(
            scenario_service.persist_scenario,
            db,
            outcome.branch,
            game_id=source.get("game_id"),
            ply=source.get("ply"),
            owner_player_id=body.player_id,
        )
        payload["scenario_id"] = scenario_id
    if body.prediction_task:
        payload["prediction"] = service.attach_prediction(
            body.prediction_task, [dict(row) for row in body.prediction_rows]
        )
    return payload


@router.post("/why-not")
async def post_why_not(body: ScenarioMoveQueryRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Why is this move inferior? Measured components, or an honest refusal."""
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    service = _service(request)
    result = await asyncio.to_thread(
        service.why_not,
        source["fen"],
        body.move,
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
    )
    result["source"] = {
        "kind": "game" if source.get("game_id") else "fen",
        "game_id": source.get("game_id"),
        "ply": source.get("ply"),
        "actual_move_uci": source.get("actual_move_uci"),
    }
    if body.persist and result.get("status") == "ok" and result.get("move"):
        branch = await asyncio.to_thread(
            service.counterfactual_safely,
            source["fen"],
            body.move,
            actual_move=source.get("actual_move_uci"),
            scenario_type="counterfactual_move",
            plies_ahead=body.plies_ahead,
            depth=body.depth,
            multipv=body.multipv,
            movetime_ms=body.movetime_ms,
        )
        if branch.branch is not None:
            result["scenario_id"] = await asyncio.to_thread(
                scenario_service.persist_scenario,
                db,
                branch.branch,
                game_id=source.get("game_id"),
                ply=source.get("ply"),
                owner_player_id=body.player_id,
            )
    return result


@router.post("/what-if")
async def post_what_if(body: ScenarioMoveQueryRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """What if this move had been played? The real line vs the alternative."""
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    service = _service(request)
    outcome = await asyncio.to_thread(
        service.counterfactual_safely,
        source["fen"],
        body.move,
        actual_move=source.get("actual_move_uci"),
        scenario_type="user_hypothesis",
        plies_ahead=body.plies_ahead,
        depth=body.depth,
        multipv=body.multipv,
        movetime_ms=body.movetime_ms,
    )
    if outcome.status != "ok" or outcome.branch is None:
        return outcome.model_dump(mode="json") | {
            "source": {"kind": "game" if source.get("game_id") else "fen", "game_id": source.get("game_id"), "ply": source.get("ply")}
        }
    from argus.scenarios import what_if_analysis

    payload = what_if_analysis(outcome.branch)
    payload["source"] = {
        "kind": "game" if source.get("game_id") else "fen",
        "game_id": source.get("game_id"),
        "ply": source.get("ply"),
        "actual_move_uci": source.get("actual_move_uci"),
    }
    if body.persist:
        payload["scenario_id"] = await asyncio.to_thread(
            scenario_service.persist_scenario,
            db,
            outcome.branch,
            game_id=source.get("game_id"),
            ply=source.get("ply"),
            owner_player_id=body.player_id,
        )
    return payload


# --- integrations: training (Phase 8) and opponents (Phase 9) -----------------


@router.post("/training")
async def post_training_from_scenario(
    body: ScenarioTrainingRequest, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Practise a counterfactual: store an exercise whose solution the engine measured.

    Refuses (with a reason, as a result rather than an error) when the requested
    move is illegal, is the move that was already played, or is one the engine
    scores as inferior — an inferior move must never become an answer key.
    """
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    service = _service(request)
    return await asyncio.to_thread(
        scenario_service.create_training_from_scenario,
        db,
        service,
        source=source,
        alternative_move=body.alternative_move,
        player_id=body.player_id,
        depth=body.depth,
        multipv=body.multipv,
    )


@router.post("/opponent-response")
async def post_opponent_response(
    body: ScenarioOpponentResponseRequest, request: Request, db: Session = Depends(get_db)
) -> dict:
    """The opponent's *observed* responses next to the engine's *recommended* ones.

    Two different questions, answered separately and never merged: what they have
    actually played (a count over stored games) and what the engine considers
    strongest (a search). Neither predicts their next move.
    """
    source = await asyncio.to_thread(
        scenario_service.source_position,
        db,
        fen=body.fen,
        game_id=body.game_id,
        ply=body.ply,
    )
    service = _service(request)
    return await asyncio.to_thread(
        scenario_service.opponent_response_scenario,
        db,
        service,
        opponent_player_id=body.opponent_player_id,
        fen=source["fen"],
        depth=body.depth,
    )


@router.get("/metrics")
def get_scenario_metrics(request: Request) -> dict:
    """Operational counters and timings for the decision-intelligence surface.

    Aggregates only: a count of requests, a duration, a cache hit rate, which
    model answered. No game content is logged or returned here.
    """
    from argus.scenarios import metrics as scenario_metrics

    service = _service(request)
    payload = scenario_metrics.snapshot()
    payload["cache"] = service.cache.stats()
    payload["refusal_semantics"] = (
        "refusals_illegal_move and refusals_unavailable count answers where Caissa "
        "declined to produce a number, which is a correct outcome, not an error."
    )
    return payload


# --- explorer and stored scenarios -------------------------------------------


@router.get("/games/{game_id}/explorer")
async def get_turning_point_explorer(
    game_id: str,
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=40),
) -> dict:
    """Where this game could have gone differently, from stored analysis only.

    No engine call is made: alternatives come from the MultiPV moves the analysis
    already recorded, and plies without them say so.
    """
    explorer = await asyncio.to_thread(scenario_service.explore_game, db, game_id, limit=limit)
    return explorer.model_dump(mode="json")


@router.get("")
def list_stored_scenarios(
    db: Session = Depends(get_db),
    game_id: str | None = Query(default=None),
    player_id: int | None = Query(default=None, ge=1),
    scenario_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Stored scenarios, scoped to what the caller may read. Newest first."""
    scenarios = scenario_service.stored_scenarios(
        db,
        game_id=game_id,
        player_id=player_id,
        scenario_type=scenario_type,
        limit=limit,
    )
    return {"count": len(scenarios), "scenarios": scenarios}


@router.get("/{scenario_id}")
def get_stored_scenario(
    scenario_id: int,
    db: Session = Depends(get_db),
    player_id: int | None = Query(default=None, ge=1),
) -> dict:
    """One stored scenario, or 404 — including when it belongs to someone else."""
    return scenario_service.get_authorized_scenario(db, scenario_id, player_id=player_id)
