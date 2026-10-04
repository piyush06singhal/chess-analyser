"""Materialize the Caissa Intelligence Graph from the existing domain.

This is the bridge between Caissa's domain tables and the graph. It reads what is
already stored — games, players, engine analysis, player profiles, training,
opponents, scenarios, knowledge — and writes the *identity and relationships* into
the graph. It never copies authoritative data: a game node carries the result and
the ECO code for display, not the PGN; a position node carries a hash and a FEN.

Everything is **incremental** (§34). :func:`update_game` touches only the nodes
and edges a single game implies; :func:`rebuild` walks the library and calls it.
Invalidation (§35) drops the derived edges a change affects so the next update
recomputes them rather than leaving stale intelligence.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.intelligence_graph.fingerprint import fingerprint
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.service import IntelligenceGraphService
from argus.intelligence_graph.taxonomy import EdgeType, NodeType
from argus.shared.logging import get_logger

from argus_api.db.models import Game, GamePosition
from argus_api.services.graph_store import SqlGraphStore

logger = get_logger(__name__)

GRAPH_METHODOLOGY_VERSION = "13.0"


def service_for(db: Session) -> IntelligenceGraphService:
    """The graph service scoped to this request's session.

    Authorization is threaded here: the policy is the same one the rest of the API
    uses (``authorization.authorized_game_ids``). Today that is "every stored
    game"; the seam is what stops the graph becoming a backdoor later (§33).
    """
    from argus.intelligence_graph.access import GraphAccessPolicy

    from argus_api.services.authorization import authorized_game_ids

    policy = GraphAccessPolicy(allowed_game_ids=set(authorized_game_ids(db)))
    return IntelligenceGraphService(
        SqlGraphStore(db),
        policy=policy,
        methodology_version=GRAPH_METHODOLOGY_VERSION,
    )


# --- helpers ------------------------------------------------------------------


def _position_index(db: Session, game_ids: list[str]) -> dict[tuple[str, int], str]:
    """Map ``(game_id, ply)`` → position hash for the given games, in one query."""
    if not game_ids:
        return {}
    rows = db.execute(
        select(GamePosition.game_id, GamePosition.ply, GamePosition.fen).where(
            GamePosition.game_id.in_(game_ids)
        )
    ).all()
    index: dict[tuple[str, int], str] = {}
    for game_id, ply, fen in rows:
        index[(str(game_id), int(ply))] = fingerprint(fen).position_hash
    return index


def _position_node(
    service: IntelligenceGraphService,
    fen: str,
    *,
    game_id: str | None,
    ply: int | None = None,
) -> tuple[str, GraphNode]:
    """Upsert the position node for ``fen`` and return ``(hash, node)``."""
    fp = fingerprint(fen)
    attributes: dict[str, Any] = {"fen": fp.normalized_fen}
    if game_id:
        attributes["game_id"] = game_id
        attributes["game_ids"] = [game_id]
    if ply is not None:
        attributes["first_ply"] = ply
    node = GraphNode(
        node_type=NodeType.POSITION,
        node_key=fp.position_hash,
        label=f"position {fp.position_hash[:8]}",
        attributes=attributes,
        methodology_version=GRAPH_METHODOLOGY_VERSION,
    )
    service.upsert_node(node)
    return fp.position_hash, node


def _opening_node_key(game: Game) -> str | None:
    eco = (game.eco_code or "").strip().upper()
    name = (game.opening_name or "").strip()
    if not eco and not name:
        return None
    slug = name.lower().replace(" ", "-")[:80] or "unknown"
    return f"{eco or '???'}:{slug}"


def _game_node(game: Game) -> GraphNode:
    return GraphNode(
        node_type=NodeType.GAME,
        node_key=game.id,
        label=f"{game.white_player_name} vs {game.black_player_name}",
        attributes={
            "game_id": game.id,
            "result": game.result,
            "date": game.date,
            "eco_code": game.eco_code,
            "opening_name": game.opening_name,
            "time_control": game.time_control,
            "analysis_status": game.analysis_status,
            "source": game.source,
        },
        methodology_version=GRAPH_METHODOLOGY_VERSION,
    )


def _player_node(player_id: int, name: str) -> GraphNode:
    return GraphNode(
        node_type=NodeType.PLAYER,
        node_key=str(player_id),
        label=name,
        attributes={"player_id": player_id},
        methodology_version=GRAPH_METHODOLOGY_VERSION,
    )


# --- game ---------------------------------------------------------------------


def update_game(db: Session, game_id: str, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """Materialize (or refresh) one game and everything structurally implied by it."""
    from argus_api.services.authorization import authorize_game

    # Authorization before materialization: a caller may not write graph nodes for
    # a game they cannot read, and a stranger's game is absent (404), not updated.
    authorize_game(db, game_id)
    service = service or service_for(db)
    game = db.get(Game, game_id)
    if game is None:
        return {"game_id": game_id, "updated": False, "reason": "game not found"}

    service.upsert_node(_game_node(game))
    nodes = 1
    edges = 0

    for player_id, name in (
        (game.white_player_id, game.white_player_name),
        (game.black_player_id, game.black_player_name),
    ):
        if player_id is None:
            continue
        service.upsert_node(_player_node(int(player_id), name or ""))
        nodes += 1
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PLAYED,
                from_type=NodeType.PLAYER,
                from_key=str(player_id),
                to_type=NodeType.GAME,
                to_key=game.id,
            )
        )
        edges += 1

    opening_key = _opening_node_key(game)
    if opening_key:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.OPENING,
                node_key=opening_key,
                label=game.opening_name or opening_key,
                attributes={
                    "eco_code": game.eco_code,
                    "opening_name": game.opening_name,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        nodes += 1
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.BELONGS_TO_OPENING,
                from_type=NodeType.GAME,
                from_key=game.id,
                to_type=NodeType.OPENING,
                to_key=opening_key,
            )
        )
        edges += 1
        if game.white_player_id is not None:
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.PLAYS_OPENING,
                    from_type=NodeType.PLAYER,
                    from_key=str(game.white_player_id),
                    to_type=NodeType.OPENING,
                    to_key=opening_key,
                )
            )
            edges += 1
        if game.black_player_id is not None:
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.PLAYS_OPENING,
                    from_type=NodeType.PLAYER,
                    from_key=str(game.black_player_id),
                    to_type=NodeType.OPENING,
                    to_key=opening_key,
                )
            )
            edges += 1

    # Game → Position (structural containment; no evidence required).
    for position_row in game.positions:
        _hash, _node = _position_node(
            service, position_row.fen, game_id=game.id, ply=position_row.ply
        )
        nodes += 1
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.CONTAINS_POSITION,
                from_type=NodeType.GAME,
                from_key=game.id,
                to_type=NodeType.POSITION,
                to_key=_hash,
            )
        )
        edges += 1

    # Critical positions are positions too; ensure they exist even when the stored
    # position list is shorter than the analysis reports.
    from argus_api.db.repository import get_critical_positions

    for critical in get_critical_positions(db, game_id):
        _hash, _node = _position_node(service, critical.fen_before, game_id=game.id, ply=critical.ply)
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.CONTAINS_POSITION,
                from_type=NodeType.GAME,
                from_key=game.id,
                to_type=NodeType.POSITION,
                to_key=_hash,
            )
        )
        edges += 1

    return {
        "game_id": game_id,
        "updated": True,
        "nodes": nodes,
        "edges": edges,
    }


__all__ = [
    "GRAPH_METHODOLOGY_VERSION",
    "service_for",
    "update_game",
]
