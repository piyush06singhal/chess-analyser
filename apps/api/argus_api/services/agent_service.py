"""Agent service: wiring Caissa services into the Phase 7 agent.

This is the only place where the agent meets the application. It exists to do five
things, and to do them in one place so that authorization and honesty cannot drift
apart across call sites:

**Authorization, server-side.** The request's authorized game set is read from the
database and attached to the context. Every game tool then refuses an id outside
that set, and it refuses it in the backend, never by asking the model to behave
(spec §39/§40). Today Caissa is single-user, so the set is "every game this
deployment holds"; the mechanism is real and enforced, and it starts scoping the
moment accounts exist.

**Providers.** Each tool family gets a narrow callable over an existing service —
none of them a database session exposed to the agent, all of them already scoped.

**Deterministic report materialisation.** A game tool must never trigger an engine
search just because the agent asked. Reports are materialised from *stored* analysis
with the same engine-free assembly the rest of the app uses, so asking the coach a
question can never silently start a 20-second Stockfish run.

**Honest degradation.** What is missing is recorded as a reason on the tool, so the
agent can explain the gap instead of inventing around it.

**Traceability.** The turn's trace, evidence and validation travel back with the
answer, and the answer is returned as data the UI can render — including actions
that map to real routes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from argus.ai_agent.core.context import AgentContext, ResponseMode, SkillContext
from argus.ai_agent.core.evidence import EvidenceKind
from argus.ai_agent.core.loop import CoachingAgent
from argus.ai_agent.core.response import AgentAnswer
from argus.ai_agent.memory.conversation import ConversationMemory
from argus.ai_agent.safety.limits import AgentLimits
from argus.ai_agent.tools import AgentProviders, build_agent_toolbox
from argus.analysis.engine.base import ChessEngine
from argus.llm.base import LLMClient, LLMNotConfiguredError, LLMSettings
from argus.llm.factory import build_llm_client
from argus.shared.errors import ArgusError, NotFoundError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_game,
    get_moves,
    get_player,
    get_training_position,
    list_live_games,
    player_id_for_game,
)
# The authorization decision lives in one module (``services.authorization``) and
# is re-exported here, where the tools and callers have always found it.
from argus_api.services import (
    live_service,
    opponent_service,
    scenario_service,
    training_service,
)
from argus.scenarios.metrics import REGISTRY as SCENARIO_METRICS
from argus_api.services.authorization import authorized_game_ids
from argus_api.services.player_profile_service import get_player_profile
from argus_api.services.report_service import build_intelligence, ensure_report

logger = get_logger(__name__)

#: Tools whose absence is explained rather than left blank.
_ABSENT_CAPABILITIES = {
    "player_statistics": (
        "Player statistics are read from the stored profile; no separate statistics "
        "store exists in this deployment."
    ),
}


def llm_settings_from(api_settings: Any) -> LLMSettings:  # noqa: ANN001
    return LLMSettings(
        provider=api_settings.llm_provider,
        model=api_settings.llm_model,
        api_key=api_settings.llm_api_key,
        base_url=api_settings.llm_base_url,
        temperature=api_settings.llm_temperature,
        max_output_tokens=api_settings.llm_max_output_tokens,
        timeout_seconds=api_settings.llm_timeout_seconds,
    )


def _player_id_for_game(db: Session, game_id: str) -> str | None:
    """The player this deployment tracks who played in this game."""
    return player_id_for_game(db, game_id)


def _game_payload(db: Session, game_id: str) -> dict[str, Any] | None:
    game = get_game(db, game_id)
    if game is None:
        return None
    return {
        "id": game.id,
        "white_player": game.white_player_name,
        "black_player": game.black_player_name,
        "white_rating": game.white_rating,
        "black_rating": game.black_rating,
        "result": game.result,
        "date": game.date,
        "event": game.event,
        "site": game.site,
        "time_control": game.time_control,
        "eco_code": game.eco_code,
        "opening_name": game.opening_name,
        "move_count": game.move_count,
        "analysis_status": game.analysis_status,
        "source": game.source,
    }


def _moves_payload(db: Session, game_id: str) -> list[dict[str, Any]] | None:
    if get_game(db, game_id) is None:
        return None
    rows = get_moves(db, game_id)
    return [
        {
            "ply": row.ply,
            "move_number": row.move_number,
            "color": row.color,
            "san": row.san,
            "uci": row.uci,
        }
        for row in rows
    ]


def build_providers(
    db: Session,
    *,
    engine: ChessEngine | None,
    prediction_service: Any | None = None,
    live_player_id: int | None = None,
) -> AgentProviders:
    """Wire the agent's tools to Caissa services.

    ``live_player_id`` is the identity used to read a live game: a live game is
    private by default, so even in this single-user deployment the reader is named
    rather than assumed.
    """

    def game_lookup(game_id: str) -> dict[str, Any] | None:
        return _game_payload(db, game_id)

    def move_analysis(game_id: str, ply: int) -> dict[str, Any] | None:
        if get_game(db, game_id) is None:
            return None
        result = build_intelligence(db, game_id).get_move_analysis(ply)
        if result is None:
            return None
        payload = result.model_dump(mode="json")
        payload["game_id"] = game_id
        return payload

    def critical_moments(game_id: str) -> dict[str, Any] | None:
        if get_game(db, game_id) is None:
            return None
        payload = build_intelligence(db, game_id).get_critical_moments().model_dump(mode="json")
        payload["game_id"] = game_id
        return payload

    def game_summary(game_id: str) -> dict[str, Any] | None:
        if get_game(db, game_id) is None:
            return None
        payload = build_intelligence(db, game_id).get_game_summary().model_dump(mode="json")
        payload["game_id"] = game_id
        return payload

    def game_report(game_id: str) -> dict[str, Any] | None:
        if get_game(db, game_id) is None:
            return None
        # Engine-free assembly from stored analysis; ``None`` when the game has not
        # been analysed, so the tool reports the gap instead of guessing.
        payload = ensure_report(db, game_id)
        if payload is None:
            return None
        return {"game_id": game_id, "report": payload, "report_version": payload.get("report_version")}

    def player_profile(player_id: str) -> dict[str, Any] | None:
        try:
            payload = get_player_profile(db, player_id)
        except NotFoundError:
            return None
        payload["player_id"] = str(payload.get("player_id") or player_id)
        return payload

    def player_insights(player_id: str) -> dict[str, Any] | None:
        payload = player_profile(player_id)
        if payload is None:
            return None
        return {
            "player_id": payload.get("player_id"),
            "coverage": payload.get("coverage"),
            "sufficient_data": payload.get("sufficient_data"),
            "insights": payload.get("insights") or [],
        }

    def player_evidence(player_id: str, insight_id: str | None = None) -> dict[str, Any] | None:
        payload = player_profile(player_id)
        if payload is None:
            return None
        insights = payload.get("insights") or []
        if insight_id:
            insights = [item for item in insights if str(item.get("id")) == insight_id]
        return {
            "player_id": payload.get("player_id"),
            "insights": [
                {
                    "id": item.get("id"),
                    "claim_level": item.get("claim_level"),
                    "games": item.get("games"),
                    "occurrences": item.get("occurrences"),
                    "evidence": item.get("evidence") or [],
                }
                for item in insights
            ],
        }

    def _resolve_player_id(player_id: str) -> int | None:
        """Validate a client-supplied player id against the stored players."""
        try:
            resolved = int(player_id)
        except (TypeError, ValueError):
            return None
        get_player(db, str(player_id))  # raises NotFoundError when unknown
        return resolved

    def training_recommendations(player_id: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return training_service.recommendations(db, resolved)

    def training_library(
        player_id: str | None,
        *,
        game_id: str | None = None,
        category: str | None = None,
        state: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id) if player_id else None
        positions = training_service.library(
            db,
            resolved,
            include_general=True,
            category=category,
            state=state,
            source_game_id=game_id,
            limit=limit,
        )
        return {"count": len(positions), "positions": positions}

    def training_position(
        position_id: int, player_id: str | None = None, reveal: bool = False
    ) -> dict[str, Any] | None:
        row = get_training_position(db, int(position_id))
        resolved = _resolve_player_id(player_id) if player_id else None
        # Ownership is enforced here, in the backend: a foreign exercise returns
        # nothing rather than leaking another player's training data.
        if row.player_id is not None and row.player_id != resolved:
            return None
        return training_service.serialize_position(row, reveal=reveal)

    def training_progress(player_id: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return training_service.progress(db, resolved)

    def training_review_queue(player_id: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return training_service.review_queue(db, resolved)

    def training_evaluate(
        position_id: int, player_id: str, submitted_uci: str
    ) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        # Read-only: grades the move, stores nothing (the agent must not write
        # to the player's training history).
        return training_service.evaluate_only(
            db, int(position_id), submitted_uci, player_id=resolved, engine=engine
        )

    # -- opponent intelligence (Phase 9) -------------------------------------

    def opponent_profile(player_id: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return opponent_service.get_opponent_profile(db, resolved)

    def opponent_games(player_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return opponent_service.get_opponent_games(
            db, resolved, limit=max(1, min(int(limit), 200)), offset=max(0, int(offset))
        )

    def opponent_repertoire(
        player_id: str, color: str = "white", recent: bool = False
    ) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        chosen = color if color in ("white", "black") else "white"
        return opponent_service.get_repertoire(db, resolved, color=chosen, recent=bool(recent))

    def opponent_position_responses(player_id: str, fen: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return opponent_service.get_position_responses(db, resolved, fen=fen)

    def opponent_tendencies(player_id: str) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        return opponent_service.get_tendencies(db, resolved)

    def opponent_phase_statistics(player_id: str, color: str | None = None) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        chosen = color if color in ("white", "black") else None
        return opponent_service.get_phase_statistics(db, resolved, color=chosen)

    def opponent_preparation_report(player_id: str, color: str | None = None) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        chosen = color if color in ("white", "black") else None
        return opponent_service.get_preparation_report(db, resolved, color=chosen)

    def opponent_preparation(
        player_id: str,
        opponent_player_id: str,
        min_occurrences: int = 2,
        max_exercises: int = 20,
    ) -> dict[str, Any] | None:
        resolved = _resolve_player_id(player_id)
        opponent = _resolve_player_id(opponent_player_id)
        if resolved is None or opponent is None:
            return None
        return training_service.generate_opponent_preparation(
            db,
            opponent,
            preparing_player_id=resolved,
            min_occurrences=max(1, min(int(min_occurrences), 50)),
            max_exercises=max(1, min(int(max_exercises), 50)),
        )

    # --- decision intelligence (Phase 10) ---------------------------------
    #
    # One scenario service per provider set, so the tool family shares its cache
    # and its limits with the HTTP surface. Every game-scoped call goes through
    # ``source_position``, which applies the authorization decision — an agent
    # cannot branch from a game the caller may not read.
    scenario = scenario_service.service_for(engine, None)

    def _note_tool(name: str) -> None:
        """Count a scenario tool call, so agent tool usage is observable (spec §40)."""
        SCENARIO_METRICS.record_tool(name)

    def _scenario_source(
        fen: str | None, game_id: str | None, ply: int | None
    ) -> dict[str, Any]:
        return scenario_service.source_position(db, fen=fen, game_id=game_id, ply=ply)

    def scenario_compare_moves(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        moves: list[str],
        include_engine_top: int = 0,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("compare_candidate_moves")
        source = _scenario_source(fen, game_id, ply)
        comparison = scenario.compare_moves(
            source["fen"],
            list(moves),
            depth=depth,
            played_move_uci=source.get("actual_move_uci"),
            include_top=max(0, min(int(include_engine_top), 8)),
        )
        payload = comparison.model_dump(mode="json")
        payload["source"] = {
            "kind": "game" if source.get("game_id") else "fen",
            "game_id": source.get("game_id"),
            "ply": source.get("ply"),
            "played_move_san": source.get("actual_move_san"),
        }
        return payload

    def scenario_compare_positions(
        fen_a: str, fen_b: str, depth: int | None = None
    ) -> dict[str, Any] | None:
        _note_tool("compare_positions")
        return scenario.compare_positions(fen_a, fen_b, depth=depth).model_dump(mode="json")

    def scenario_counterfactual(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        alternative_move: str,
        actual_move: str | None = None,
        scenario_type: str = "counterfactual_move",
        plies_ahead: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("analyze_counterfactual")
        source = _scenario_source(fen, game_id, ply)
        outcome = scenario.counterfactual_safely(
            source["fen"],
            alternative_move,
            actual_move=actual_move or source.get("actual_move_uci"),
            scenario_type=scenario_type,
            plies_ahead=plies_ahead,
            depth=depth,
        )
        payload = outcome.model_dump(mode="json")
        payload["source"] = {
            "kind": "game" if source.get("game_id") else "fen",
            "game_id": source.get("game_id"),
            "ply": source.get("ply"),
            "actual_move_san": source.get("actual_move_san"),
        }
        return payload

    def scenario_why_not(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        move: str,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("explain_why_not_move")
        source = _scenario_source(fen, game_id, ply)
        payload = scenario.why_not(source["fen"], move, depth=depth)
        payload["source"] = {
            "kind": "game" if source.get("game_id") else "fen",
            "game_id": source.get("game_id"),
            "ply": source.get("ply"),
        }
        return payload

    def scenario_what_if(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        move: str,
        actual_move: str | None = None,
        plies_ahead: int | None = None,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("explain_what_if")
        source = _scenario_source(fen, game_id, ply)
        outcome = scenario.counterfactual_safely(
            source["fen"],
            move,
            actual_move=actual_move or source.get("actual_move_uci"),
            scenario_type="user_hypothesis",
            plies_ahead=plies_ahead,
            depth=depth,
        )
        if outcome.status != "ok" or outcome.branch is None:
            payload = outcome.model_dump(mode="json")
            payload["source"] = {
                "kind": "game" if source.get("game_id") else "fen",
                "game_id": source.get("game_id"),
                "ply": source.get("ply"),
            }
            return payload
        from argus.scenarios import what_if_analysis

        payload = what_if_analysis(outcome.branch)
        payload["source"] = {
            "kind": "game" if source.get("game_id") else "fen",
            "game_id": source.get("game_id"),
            "ply": source.get("ply"),
            "actual_move_san": source.get("actual_move_san"),
        }
        return payload

    def scenario_explorer(game_id: str, limit: int = 12) -> dict[str, Any] | None:
        _note_tool("explore_turning_points")
        explorer = scenario_service.explore_game(db, game_id, limit=max(1, min(int(limit), 40)))
        return explorer.model_dump(mode="json")

    def scenario_training(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        move: str,
        player_id: str,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("create_training_from_scenario")
        resolved = _resolve_player_id(player_id)
        if resolved is None:
            return None
        source = _scenario_source(fen, game_id, ply)
        return scenario_service.create_training_from_scenario(
            db,
            scenario,
            source=source,
            alternative_move=move,
            player_id=resolved,
            depth=depth,
        )

    def scenario_opponent_response(
        fen: str | None,
        game_id: str | None,
        ply: int | None,
        opponent_player_id: str,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        _note_tool("get_opponent_response_scenario")
        opponent = _resolve_player_id(opponent_player_id)
        if opponent is None:
            return None
        source = _scenario_source(fen, game_id, ply)
        return scenario_service.opponent_response_scenario(
            db, scenario, opponent_player_id=opponent, fen=source["fen"], depth=depth
        )

    def prediction_status(task: str) -> dict[str, Any] | None:
        if prediction_service is None:
            return None
        availability = prediction_service.task_availability(task)
        return availability.model_dump(mode="json")

    def prediction(task: str, features: dict[str, Any]) -> dict[str, Any] | None:
        if prediction_service is None:
            return None
        # The Phase 6 service takes feature rows; the agent speaks in one row per
        # question, which is exactly one prediction.
        rows = [features] if features else [{}]
        if task == "game_outcome":
            result = prediction_service.predict_game_outcome(rows)
        elif task == "position_outcome":
            result = prediction_service.predict_position_outcome(rows)
        elif task == "position_difficulty":
            result = prediction_service.predict_position_difficulty(rows)
        elif task == "move_error_risk":
            result = prediction_service.predict_error_risk(rows)
        else:
            # `player_performance` has no public operation yet: it is declared with
            # its requirements but has no baseline ladder, so it stays unavailable
            # rather than being served through a different path.
            return None
        return result.model_dump(mode="json")

    # --- live games (Phase 12) ---------------------------------------------

    def _live_payload(live_game_id: str) -> dict[str, Any] | None:
        try:
            return live_service.get_payload(
                db, live_game_id=live_game_id, player_id=live_player_id
            )
        except ArgusError:
            # Unreadable, unknown, or private to someone else: the tool reports
            # absence rather than the reason, so it cannot be used to probe.
            return None

    def live_game(live_game_id: str) -> dict[str, Any] | None:
        return _live_payload(live_game_id)

    def live_history(live_game_id: str, limit: int = 200) -> dict[str, Any] | None:
        payload = _live_payload(live_game_id)
        if payload is None:
            return None
        moves = payload.get("moves") or []
        return {
            "live_game_id": live_game_id,
            "status": payload.get("status"),
            "move_count": len(moves),
            "moves": moves[-limit:],
            "truncated": len(moves) > limit,
        }

    def live_coach_state(
        live_game_id: str, question: str | None = None
    ) -> dict[str, Any] | None:
        if live_player_id is None:
            return None
        try:
            return live_service.coach_answer(
                db, live_game_id=live_game_id, player_id=live_player_id, question=question
            )
        except ArgusError:
            return None

    # --- intelligence graph (Phase 13) -------------------------------------
    def _graph_service():
        from argus_api.services.graph_service import service_for

        return service_for(db)

    def graph_related_games(
        game_id: str | None = None,
        player_id: str | None = None,
        opening: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import EdgeType, NodeType

        try:
            if opening:
                service = _graph_service()
                games = [
                    {"game_id": edge.from_key, "shared_opening": opening}
                    for edge in service.store.in_edges(
                        NodeType.OPENING, opening, frozenset({EdgeType.BELONGS_TO_OPENING})
                    )
                ][:limit]
                if not games:
                    return {"found": False, "note": "No stored game follows that opening line."}
                return {"found": True, "relationship": "belongs_to_opening", "games": games}
            if game_id:
                from argus_api.services.graph_explorers import game_explorer

                explorer = game_explorer(db, game_id, service=_graph_service())
                if not explorer.get("found"):
                    return None
                return {
                    "found": bool(explorer["related_games"]),
                    "relationship": "shared_opening",
                    "games": explorer["related_games"][:limit],
                    "openings": explorer["openings"],
                }
            if player_id:
                from argus_api.services.graph_explorers import player_explorer

                explorer = player_explorer(db, int(player_id), service=_graph_service())
                if not explorer.get("found"):
                    return None
                return {
                    "found": True,
                    "relationship": "played",
                    "games": [{"game_id": gid} for gid in explorer["games"][:limit]],
                }
            return None
        except (ValueError, TypeError):
            return None

    def graph_related_positions(
        fen: str, minimum_similarity: str = "structurally_similar", limit: int = 10
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.similarity import SimilarityLevel, qualifies

        from argus_api.services.graph_explorers import position_explorer

        try:
            level = SimilarityLevel(minimum_similarity)
        except ValueError:
            return None
        explorer = position_explorer(db, fen, service=_graph_service())
        if not explorer.get("valid"):
            return None
        positions = list(explorer["exact_matches"])
        levels: dict[str, int] = {}
        for name, rows in explorer["similar"].items():
            try:
                found_level = SimilarityLevel(name)
            except ValueError:
                continue
            if not qualifies(found_level, level):
                continue
            levels[name] = len(rows)
            positions.extend(rows[: max(0, limit - len(positions))])
        if not positions:
            return {
                "found": False,
                "note": "No stored position is related at that similarity level.",
            }
        return {
            "found": True,
            "positions": positions[:limit],
            "levels": levels,
            "note": (
                "Only the 'exact' level is an exact match; every other level is a "
                "resemblance and is labelled as one."
            ),
        }

    def graph_player_patterns(
        player_id: str | None = None,
        pattern_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import EdgeType, NodeType

        if not player_id:
            return None
        try:
            service = _graph_service()
            patterns = [
                other.to_payload()
                for _edge, other in service.neighbors(
                    NodeType.PLAYER, str(player_id), edge_types={EdgeType.HAS_PATTERN}
                )
                if other is not None
                and (pattern_type is None or other.attributes.get("pattern_type") == pattern_type)
            ][:limit]
        except Exception:  # noqa: BLE001 — a provider must not kill the turn
            return None
        if not patterns:
            return {
                "found": False,
                "patterns": [],
                "note": "No stored pattern for this player has enough evidence yet.",
            }
        return {"found": True, "patterns": patterns}

    def graph_pattern_evidence(pattern_id: str, limit: int = 20) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import NodeType

        service = _graph_service()
        found_node = None
        for node_type in (
            NodeType.TACTICAL_PATTERN,
            NodeType.POSITIONAL_PATTERN,
            NodeType.KING_SAFETY_PATTERN,
            NodeType.MATERIAL_PATTERN,
            NodeType.PATTERN,
        ):
            found_node = service.get_node(node_type, pattern_id)
            if found_node is not None:
                break
        if found_node is None:
            return {"found": False, "note": "Caissa has no such stored pattern."}
        traced = service.trace_evidence(found_node.node_type, pattern_id)
        evidence = [ref for group in traced["evidence"].values() for ref in group]
        games = sorted({ref.get("game_id") for ref in evidence if ref.get("game_id")})
        return {
            "found": bool(evidence),
            "evidence": evidence[:limit],
            "games": games[:limit],
            "sample_size": found_node.attributes.get("occurrences"),
            "note": None if evidence else "This pattern has no stored evidence reference.",
        }

    def graph_training_history(
        player_id: str | None = None,
        pattern_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import EdgeType, NodeType

        if not player_id:
            return None
        service = _graph_service()
        attempts = [
            other.to_payload()
            for _edge, other in service.neighbors(
                NodeType.PLAYER, str(player_id), edge_types={EdgeType.ATTEMPTED}
            )
            if other is not None
        ]
        summary: dict[str, int] = {}
        for attempt in attempts:
            key = str(attempt.get("attributes", {}).get("correctness") or "unknown")
            summary[key] = summary.get(key, 0) + 1
        if not attempts:
            return {
                "found": False,
                "attempts": [],
                "summary": {},
                "note": "No training attempts are stored for this player.",
            }
        return {
            "found": True,
            "attempts": attempts[:limit],
            "summary": summary,
            "note": (
                "These are descriptive outcomes. Caissa does not infer that training "
                "caused a change unless a valid comparison exists."
            ),
        }

    def graph_opponent_connections(
        opponent_id: int, fen: str | None = None, limit: int = 20
    ) -> dict[str, Any] | None:
        try:
            repertoire = opponent_service.get_repertoire(db, opponent_id, color="white")
            responses = (
                opponent_service.get_position_responses(db, opponent_id, fen=fen)
                if fen
                else None
            )
        except ArgusError:
            return None
        except Exception:  # noqa: BLE001
            return None
        return {
            "found": True,
            "repertoire": repertoire,
            "responses": responses,
            "note": (
                "Repertoire and responses are read from that opponent's stored games "
                "through the Phase 9 layer."
            ),
        }

    def graph_opening_connections(
        opening: str | None = None, player_id: str | None = None, limit: int = 10
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import EdgeType, NodeType

        service = _graph_service()
        if not opening:
            return None
        games = [
            {"game_id": edge.from_key, "shared_opening": opening}
            for edge in service.store.in_edges(
                NodeType.OPENING, opening, frozenset({EdgeType.BELONGS_TO_OPENING})
            )
        ][:limit]
        players = [
            {"player_id": edge.from_key}
            for edge in service.store.in_edges(
                NodeType.OPENING, opening, frozenset({EdgeType.PLAYS_OPENING})
            )
        ][:limit]
        if not games and not players:
            return {"found": False, "note": "No stored game uses that opening line."}
        return {
            "found": True,
            "openings": [opening],
            "games": games,
            "players": players,
        }

    def graph_knowledge_for_position(fen: str, limit: int = 10) -> dict[str, Any] | None:
        from argus.intelligence_graph.knowledge import exhibited_concepts

        exhibitions = exhibited_concepts(fen)
        if not exhibitions:
            return {
                "found": False,
                "concepts": [],
                "note": (
                    "That position exhibits no concept Caissa has a deterministic rule "
                    "for, so Caissa will not attach one."
                ),
            }
        return {
            "found": True,
            "concepts": [exhibition.to_payload() for exhibition in exhibitions[:limit]],
        }

    def graph_trace_evidence(
        node_type: str, node_key: str, limit: int = 50
    ) -> dict[str, Any] | None:
        from argus.intelligence_graph.taxonomy import NodeType

        from argus_api.services.graph_explorers import why

        try:
            resolved = NodeType(node_type)
        except ValueError:
            return None
        traced = why(db, resolved, node_key, service=_graph_service())
        return traced

    return AgentProviders(
        graph_related_games=graph_related_games,
        graph_related_positions=graph_related_positions,
        graph_player_patterns=graph_player_patterns,
        graph_pattern_evidence=graph_pattern_evidence,
        graph_training_history=graph_training_history,
        graph_opponent_connections=graph_opponent_connections,
        graph_opening_connections=graph_opening_connections,
        graph_knowledge_for_position=graph_knowledge_for_position,
        graph_trace_evidence=graph_trace_evidence,
        live_game=live_game,
        live_history=live_history,
        live_coach_state=live_coach_state,
        game_lookup=game_lookup,
        move_analysis=move_analysis,
        game_report=game_report,
        game_summary=game_summary,
        critical_moments=critical_moments,
        game_moves=_moves_payload,
        player_profile=player_profile,
        player_insights=player_insights,
        player_evidence=player_evidence,
        training_recommendations=training_recommendations,
        training_library=training_library,
        training_position=training_position,
        training_progress=training_progress,
        training_review_queue=training_review_queue,
        training_evaluate=training_evaluate,
        opponent_profile=opponent_profile,
        opponent_games=opponent_games,
        opponent_repertoire=opponent_repertoire,
        opponent_position_responses=opponent_position_responses,
        opponent_tendencies=opponent_tendencies,
        opponent_phase_statistics=opponent_phase_statistics,
        opponent_preparation_report=opponent_preparation_report,
        opponent_preparation=opponent_preparation,
        scenario_compare_moves=scenario_compare_moves,
        scenario_compare_positions=scenario_compare_positions,
        scenario_counterfactual=scenario_counterfactual,
        scenario_why_not=scenario_why_not,
        scenario_what_if=scenario_what_if,
        scenario_explorer=scenario_explorer,
        scenario_training=scenario_training,
        scenario_opponent_response=scenario_opponent_response,
        prediction=prediction,
        prediction_status=prediction_status,
        engine=engine,
        unavailable_reasons=dict(_ABSENT_CAPABILITIES),
    )


def build_agent(
    db: Session,
    api_settings: Any,  # noqa: ANN001 — argus_api.config.Settings
    *,
    engine: ChessEngine | None = None,
    prediction_service: Any | None = None,
    limits: AgentLimits | None = None,
    live_player_id: int | None = None,
) -> CoachingAgent:
    """Build the coaching agent for one request.

    The LLM provider is optional on purpose: with none configured the agent still
    answers stored-fact questions from tools and says plainly that it cannot write
    an explanation. That is a useful state, not an error — so this does not raise the
    way the Phase 1 shell does.
    """
    providers = build_providers(
        db,
        engine=engine,
        prediction_service=prediction_service,
        live_player_id=live_player_id,
    )
    toolbox = build_agent_toolbox(providers)
    settings = llm_settings_from(api_settings)
    client: LLMClient | None = None
    if settings.is_configured:
        try:
            client = build_llm_client(settings)
        except LLMNotConfiguredError as exc:  # pragma: no cover - guarded above
            logger.warning("LLM provider configured but unusable: %s", exc.message)
            client = None
    return CoachingAgent(
        toolbox,
        llm_client=client,
        limits=limits or AgentLimits(),
        provider_name=settings.provider or None,
        model_name=settings.model or None,
    )


def build_context(
    db: Session,
    *,
    game_id: str | None = None,
    ply: int | None = None,
    move_san: str | None = None,
    fen: str | None = None,
    player_id: str | None = None,
    live_game_id: str | None = None,
    live_player_id: int | None = None,
    mode: ResponseMode | str = ResponseMode.COACH,
    skill_rating: int | None = None,
    user_id: str = "local",
) -> AgentContext:
    """Assemble the turn's context, deriving the player from the game when possible."""
    resolved_mode = ResponseMode(mode) if isinstance(mode, str) else mode
    resolved_player = str(player_id) if player_id else None
    if resolved_player is None and game_id:
        resolved_player = _player_id_for_game(db, game_id)
    skill = SkillContext(rating=skill_rating, rating_source="stored" if skill_rating else None)
    # Live games are always restricted, so the allow-list is built from the
    # caller's membership — never left empty, which would mean "all of them".
    live_ids: list[str] = []
    live_analysis_forbidden = False
    if live_player_id is not None:
        try:
            live_ids = [
                row.id
                for row in list_live_games(db, player_id=live_player_id, limit=200)
            ]
        except ArgusError:
            live_ids = []
    if live_game_id:
        if live_game_id not in live_ids:
            live_ids = [*live_ids, live_game_id]
        # The fair-play clamp is read from the game itself, so the context can
        # never promise analysis a competitive game forbids.
        try:
            permissions = live_service.get_payload(
                db, live_game_id=live_game_id, player_id=live_player_id
            ).get("permissions") or {}
            live_analysis_forbidden = not bool(permissions.get("may_give_engine_moves"))
        except ArgusError:
            live_analysis_forbidden = True
    return AgentContext(
        user_id=user_id,
        active_game_id=game_id,
        active_live_game_id=live_game_id,
        selected_ply=ply,
        selected_move_san=move_san,
        current_fen=fen,
        player_id=resolved_player,
        available_game_ids=authorized_game_ids(db),
        available_live_game_ids=live_ids,
        live_analysis_forbidden=live_analysis_forbidden,
        mode=resolved_mode,
        skill=skill,
    )


def render_answer(answer: AgentAnswer, *, include_evidence: bool = True) -> dict[str, Any]:
    """The API representation of an answer, with its evidence and trace."""
    payload: dict[str, Any] = {
        "message": answer.message,
        "mode": answer.mode.value,
        "deterministic": answer.deterministic,
        "provider": answer.provider,
        "model": answer.model,
        "prompt_version": answer.prompt_version,
        "claims": [
            {
                "kind": claim.kind.value,
                "label": claim.kind.label,
                "text": claim.text,
                "verified": claim.verified,
                "note": claim.note,
            }
            for claim in answer.claims
        ],
        "actions": [
            {
                "action": action.action.value,
                "label": action.label,
                "href": action.href,
                "params": action.params,
                "available": action.is_available,
                "unavailable_reason": action.unavailable_reason,
            }
            for action in answer.actions
        ],
        "validation": {
            "passed": answer.validation.passed,
            "summary": answer.validation.summary(),
            "checked": answer.validation.checked,
            "failures": [
                {"claim": finding.claim, "kind": finding.kind, "detail": finding.detail}
                for finding in answer.validation.failures
            ],
        },
        "limitations": list(answer.limitations),
        "trace": answer.trace,
    }
    if include_evidence and answer.evidence is not None:
        payload["evidence"] = {
            "items": [
                {
                    "kind": item.kind.value,
                    "source": item.source.value,
                    "certainty": item.certainty.value,
                    "tool": item.tool,
                    "summary": item.summary,
                    "ref": item.ref(),
                    "game_id": item.game_id,
                    "ply": item.ply,
                }
                for item in answer.evidence.items
            ],
            "missing": [
                {"tool": entry.tool, "reason": entry.reason} for entry in answer.evidence.missing
            ],
            "limitations": answer.evidence.limitations,
            "kinds": sorted({item.kind.value for item in answer.evidence.items}),
        }
    return payload


def ask(
    db: Session,
    api_settings: Any,  # noqa: ANN001
    *,
    question: str,
    context: AgentContext,
    engine: ChessEngine | None = None,
    prediction_service: Any | None = None,
    memory: ConversationMemory | None = None,
    live_player_id: int | None = None,
) -> dict[str, Any]:
    """Answer one question and return the API representation."""
    agent = build_agent(
        db,
        api_settings,
        engine=engine,
        prediction_service=prediction_service,
        live_player_id=live_player_id,
    )
    answer = agent.ask(question, context=context, memory=memory)
    logger.info(
        "Agent turn [mode=%s deterministic=%s tools=%d validation=%s]",
        answer.mode.value,
        answer.deterministic,
        int(answer.trace.get("tool_count") or 0),
        answer.validation.passed,
    )
    return render_answer(answer)


def tool_catalogue(
    db: Session,
    *,
    engine: ChessEngine | None = None,
    prediction_service: Any | None = None,
    context: AgentContext | None = None,
) -> list[dict[str, Any]]:
    """The machine-readable tool catalogue, for the UI and for review."""
    providers = build_providers(db, engine=engine, prediction_service=prediction_service)
    toolbox = build_agent_toolbox(providers)
    return toolbox.catalogue(context)


def evidence_kinds() -> list[str]:
    return [kind.value for kind in EvidenceKind]


__all__ = [
    "ask",
    "authorized_game_ids",
    "build_agent",
    "build_context",
    "build_providers",
    "llm_settings_from",
    "render_answer",
    "tool_catalogue",
]
