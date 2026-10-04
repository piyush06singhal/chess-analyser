"""Coach routes: LLM status and the coaching chat endpoint.

The coach is probabilistic AI and is kept strictly separate from the
deterministic analysis routes: it explains tool outputs (engine analyses,
stored reports) but never calculates chess facts itself. When no LLM provider
is configured, the endpoints respond with an honest 501 instead of a fake
conversation.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from argus.shared.logging import get_logger

from argus_api.deps import get_db, rate_limited
from argus_api.schemas import (
    AgentTurnRequest,
    AgentTurnResponse,
    CoachChatRequest,
    CoachChatResponse,
)
from argus_api.services import agent_service, coach_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.get("/status")
def get_coach_status(request: Request) -> dict:
    """LLM coach configuration status (no key material)."""
    return coach_service.coach_status(request.app.state.settings)


# --- Phase 7 agent -------------------------------------------------------------
#
# `/chat` above is the Phase 1 shell: an engine-only tool registry with no board
# awareness. `/ask` is the Phase 7 agent — context-aware, evidence-based, budgeted
# and validated. Both are kept working; the UI uses `/ask`.


@router.get("/tools")
def get_agent_tools(request: Request, db: Session = Depends(get_db)) -> dict:
    """The agent's tool catalogue: permissions, availability and output fields.

    Exposed deliberately. A reader can see exactly what the agent is able to do and
    which capabilities are declared-but-unavailable, rather than inferring it from
    the answers.
    """
    from argus.ai_agent.prompts import PROMPT_VERSION

    context = agent_service.build_context(db)
    catalogue = agent_service.tool_catalogue(
        db, engine=request.app.state.engine, context=context
    )
    return {
        "tools": catalogue,
        "count": len(catalogue),
        "available": sorted(entry["name"] for entry in catalogue if entry["available"]),
        "unavailable": {
            entry["name"]: entry["reason"] for entry in catalogue if not entry["available"]
        },
        "prompt_version": PROMPT_VERSION,
        "note": (
            "The agent calls these tools only. It never calculates a chess fact "
            "itself, and every answer carries the evidence it used."
        ),
    }


@router.post(
    "/ask", response_model=AgentTurnResponse, dependencies=[Depends(rate_limited("coach"))]
)
async def agent_ask(
    body: AgentTurnRequest, request: Request, db: Session = Depends(get_db)
) -> dict:
    """One agent turn: plan, call tools, answer from evidence, then validate.

    Returns an honest answer in every case — including when no LLM provider is
    configured, when a tool fails, and when the user asks for a prediction Caissa
    cannot make.
    """
    from argus.ai_agent.memory.conversation import ConversationMemory
    from argus.ai_agent.prompts import PROMPT_VERSION
    from argus_api.services.prediction_service import prediction_service_for

    context = agent_service.build_context(
        db,
        game_id=body.game_id,
        ply=body.ply,
        move_san=body.move_san,
        fen=body.fen,
        player_id=body.player_id,
        live_game_id=body.live_game_id,
        live_player_id=body.live_player_id,
        mode=body.mode,
    )
    # History arrives in the same `{role, content}` shape the response uses, and is
    # translated by the memory module rather than here — the shape is its contract.
    memory = ConversationMemory.from_messages(
        [turn.model_dump() for turn in body.history] if body.history else None
    )
    prediction_service = prediction_service_for(request.app.state.settings)
    logger.info("Agent turn requested [%s]", context.describe())
    payload = await asyncio.to_thread(
        agent_service.ask,
        db,
        request.app.state.settings,
        question=body.question,
        context=context,
        engine=request.app.state.engine,
        prediction_service=prediction_service,
        memory=memory,
        live_player_id=body.live_player_id,
    )
    payload["prompt_version"] = payload.get("prompt_version") or PROMPT_VERSION
    if not body.include_evidence:
        payload.pop("evidence", None)
    return payload


@router.post(
    "/chat", response_model=CoachChatResponse, dependencies=[Depends(rate_limited("coach"))]
)
async def coach_chat(body: CoachChatRequest, request: Request) -> CoachChatResponse:
    """One coaching turn: LLM reasoning over tool outputs.

    Runs the blocking LLM/tool loop in a worker thread. Raises
    ``LLMNotConfiguredError`` (→ 501) when no provider is configured.
    """
    settings = request.app.state.settings
    engine = request.app.state.engine
    agent = coach_service.build_coach(settings, engine)
    logger.info("Coach turn started [provider=%s]", settings.llm_provider or "none")
    result = await asyncio.to_thread(agent.run, body.message, history=body.history)
    return CoachChatResponse(
        message=result.get("message", ""),
        tool_calls_used=result.get("tool_calls_used", 0),
        tool_trace=result.get("tool_trace", []),
    )
