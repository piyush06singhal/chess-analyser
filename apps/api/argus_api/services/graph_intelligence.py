"""Derived intelligence layers, explorers and rebuild for the graph.

This module builds the *derived* half of the graph — patterns, training links,
opponent and preparation links, scenario provenance and knowledge links — and the
three explorers (§40–§42). It reuses :func:`graph_service.update_game` for the
structural half, so there is one definition of "a game in the graph".

Every derived edge here carries evidence (§6). A pattern with no evidence is not
written at all: an insight Caissa cannot trace is not presented as a finding.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus.intelligence_graph.evidence import EvidenceKind, EvidenceReference
from argus.intelligence_graph.fingerprint import fingerprint, normalize_fen
from argus.intelligence_graph.knowledge import (
    ARGUS_DOCUMENT,
    ARGUS_SOURCE,
    build_curated_concepts,
    exhibited_concepts,
)
from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.service import IntelligenceGraphService
from argus.intelligence_graph.taxonomy import (
    EdgeType,
    NodeType,
    PatternType,
    pattern_node_type,
)
from argus.shared.logging import get_logger

from argus_api.db.models import (
    Game,
    GamePosition,
    KnowledgeConceptRecord,
    KnowledgeDocumentRecord,
    KnowledgeSourceRecord,
    MatchPreparationRecord,
    OpponentProfileRecord,
    Player,
    PlayerProfileRecord,
    ScenarioRecordRow,
    TrainingAttempt,
    TrainingPosition,
    TrainingSession,
)
from argus_api.services.graph_service import (
    GRAPH_METHODOLOGY_VERSION,
    _position_index,
    _position_node,
    service_for,
    update_game,
)

logger = get_logger(__name__)

_CATEGORY_TO_PATTERN: dict[str, PatternType] = {
    "weakness_candidate": PatternType.POSITIONAL,
    "recurring_pattern": PatternType.POSITIONAL,
    "opening_pattern": PatternType.OPENING,
    "tactical_pattern": PatternType.TACTICAL,
    "positional_pattern": PatternType.POSITIONAL,
    "phase_pattern": PatternType.POSITIONAL,
    "conversion_pattern": PatternType.CONVERSION,
    "recovery_pattern": PatternType.RECOVERY,
}


def _latest_profile(db: Session, player_id: int) -> PlayerProfileRecord | None:
    return db.execute(
        select(PlayerProfileRecord)
        .where(PlayerProfileRecord.player_id == player_id)
        .order_by(PlayerProfileRecord.generated_at.desc(), PlayerProfileRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _game_ids_for_player(db: Session, player_id: int) -> list[str]:
    rows = db.execute(
        select(Game.id).where(
            (Game.white_player_id == player_id) | (Game.black_player_id == player_id)
        )
    ).all()
    return [str(row[0]) for row in rows]


def update_player_patterns(
    db: Session, player_id: int, *, service: IntelligenceGraphService | None = None
) -> dict[str, Any]:
    """Turn the stored player profile's insights into pattern nodes and evidence."""
    service = service or service_for(db)
    record = _latest_profile(db, player_id)
    if record is None:
        return {"player_id": player_id, "patterns": 0, "reason": "no stored profile"}
    payload = record.payload or {}
    insights = payload.get("insights") or []
    game_ids = _game_ids_for_player(db, player_id)
    index = _position_index(db, game_ids)
    player = db.get(Player, player_id)

    service.upsert_node(
        GraphNode(
            node_type=NodeType.PLAYER,
            node_key=str(player_id),
            label=player.name if player else str(player_id),
            attributes={"player_id": player_id},
            methodology_version=GRAPH_METHODOLOGY_VERSION,
        )
    )

    written = 0
    for insight in insights:
        category = str(insight.get("category") or "")
        pattern_type = _CATEGORY_TO_PATTERN.get(category)
        if pattern_type is None:
            continue
        insight_id = str(insight.get("id") or "")
        if not insight_id:
            continue
        references: list[EvidenceReference] = []
        for ref in insight.get("evidence") or []:
            game_id = str(ref.get("game_id") or "")
            ply = ref.get("ply")
            if not game_id:
                continue
            references.append(
                EvidenceReference(
                    kind=EvidenceKind.POSITION if ply is not None else EvidenceKind.GAME,
                    game_id=game_id,
                    ply=int(ply) if ply is not None else None,
                    label=str(ref.get("label") or ""),
                    statement=str(insight.get("statement") or ""),
                    value=insight.get("value") if isinstance(insight.get("value"), (int, float)) else None,
                    unit=insight.get("unit"),
                )
            )
        if not references:
            # No traceable evidence ⇒ not written. §52.
            continue
        node_type = pattern_node_type(pattern_type)
        service.upsert_node(
            GraphNode(
                node_type=node_type,
                node_key=insight_id,
                label=str(insight.get("title") or insight_id),
                attributes={
                    "player_id": player_id,
                    "pattern_type": pattern_type.value,
                    "category": category,
                    "claim_level": str(insight.get("claim_level") or ""),
                    "occurrences": int(insight.get("occurrences") or 0),
                    "games": int(insight.get("games") or 0),
                    "statement": str(insight.get("statement") or ""),
                    "severity": insight.get("severity"),
                    "methodology_version": str(insight.get("methodology_version") or ""),
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.HAS_PATTERN,
                from_type=NodeType.PLAYER,
                from_key=str(player_id),
                to_type=node_type,
                to_key=insight_id,
                evidence=references,
                sample_size=int(insight.get("occurrences") or len(references)),
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        for reference in references:
            if reference.game_id is None or reference.ply is None:
                continue
            position = index.get((reference.game_id, reference.ply))
            if not position:
                continue
            service.upsert_node(
                GraphNode(
                    node_type=NodeType.POSITION,
                    node_key=position,
                    label=f"position {position[:8]}",
                    attributes={"game_id": reference.game_id, "game_ids": [reference.game_id]},
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.PATTERN_EVIDENCE,
                    from_type=node_type,
                    from_key=insight_id,
                    to_type=NodeType.POSITION,
                    to_key=position,
                    evidence=[
                        EvidenceReference(
                            kind=EvidenceKind.POSITION,
                            game_id=reference.game_id,
                            ply=reference.ply,
                            position_hash=position,
                        )
                    ],
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
        written += 1
    return {"player_id": player_id, "patterns": written}


def update_player_training(
    db: Session, player_id: int, *, service: IntelligenceGraphService | None = None
) -> dict[str, Any]:
    """Link a player's training positions and attempts into the graph."""
    service = service or service_for(db)
    positions = db.execute(
        select(TrainingPosition).where(TrainingPosition.player_id == player_id)
    ).scalars().all()
    attempts = db.execute(
        select(TrainingAttempt).where(TrainingAttempt.player_id == player_id)
    ).scalars().all()
    game_ids = sorted({p.source_game_id for p in positions if p.source_game_id})
    index = _position_index(db, game_ids)

    # A derived edge must point at a real node or the graph health check reports a
    # dangling edge (a defect). Materialize each source game's structural graph
    # first — the single definition of "a game in the graph" — so the game and its
    # position nodes exist before the training links reference them. Idempotent.
    for game_id in game_ids:
        try:
            update_game(db, game_id, service=service)
        except Exception:  # noqa: BLE001 — a game this caller cannot read is skipped
            logger.debug("graph: training source game %s could not be materialized", game_id)

    written = 0
    for row in positions:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.TRAINING_POSITION,
                node_key=str(row.id),
                label=row.source_reason or f"training position {row.id}",
                attributes={
                    "player_id": player_id,
                    "game_id": row.source_game_id,
                    "category": row.category,
                    "difficulty": row.difficulty,
                    "position_type": row.position_type,
                    "fen": row.fen,
                    "state": row.state,
                    "attempts": row.attempts,
                    "correct_attempts": row.correct_attempts,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.TRAINED_WITH,
                from_type=NodeType.PLAYER,
                from_key=str(player_id),
                to_type=NodeType.TRAINING_POSITION,
                to_key=str(row.id),
            )
        )
        if row.source_game_id and service.get_node(NodeType.GAME, row.source_game_id) is not None:
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.DERIVED_FROM,
                    from_type=NodeType.TRAINING_POSITION,
                    from_key=str(row.id),
                    to_type=NodeType.GAME,
                    to_key=row.source_game_id,
                    evidence=[
                        EvidenceReference(
                            kind=EvidenceKind.GAME,
                            game_id=row.source_game_id,
                            ply=row.source_ply,
                        )
                    ],
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
            position = index.get((row.source_game_id, row.source_ply or -1))
            if position and service.get_node(NodeType.POSITION, position) is not None:
                service.write_edge(
                    GraphEdge(
                        edge_type=EdgeType.DERIVED_FROM,
                        from_type=NodeType.TRAINING_POSITION,
                        from_key=str(row.id),
                        to_type=NodeType.POSITION,
                        to_key=position,
                        evidence=[
                            EvidenceReference(
                                kind=EvidenceKind.POSITION,
                                game_id=row.source_game_id,
                                ply=row.source_ply,
                                position_hash=position,
                            )
                        ],
                        methodology_version=GRAPH_METHODOLOGY_VERSION,
                    )
                )
        written += 1

    session_ids: set[int] = set()
    for row in attempts:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.TRAINING_ATTEMPT,
                node_key=str(row.id),
                label=f"attempt {row.id} ({row.correctness})",
                attributes={
                    "player_id": player_id,
                    "correctness": row.correctness,
                    "training_position_id": row.training_position_id,
                    "session_id": row.session_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.ATTEMPTED,
                from_type=NodeType.PLAYER,
                from_key=str(player_id),
                to_type=NodeType.TRAINING_ATTEMPT,
                to_key=str(row.id),
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.ATTEMPT_OF,
                from_type=NodeType.TRAINING_ATTEMPT,
                from_key=str(row.id),
                to_type=NodeType.TRAINING_POSITION,
                to_key=str(row.training_position_id),
            )
        )
        if row.session_id:
            session_ids.add(int(row.session_id))

    for session_id in session_ids:
        session = db.get(TrainingSession, session_id)
        if session is None:
            continue
        service.upsert_node(
            GraphNode(
                node_type=NodeType.TRAINING_SESSION,
                node_key=str(session_id),
                label=f"{session.kind} session",
                attributes={
                    "player_id": session.player_id,
                    "kind": session.kind,
                    "status": session.status,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
    return {
        "player_id": player_id,
        "training_positions": len(positions),
        "training_attempts": len(attempts),
        "sessions": len(session_ids),
    }


def update_opponents(db: Session, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """Opponent profile nodes and preparation-report links (reuses Phase 9)."""
    service = service or service_for(db)
    profiles = db.execute(select(OpponentProfileRecord)).scalars().all()
    count = 0
    for row in profiles:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.OPPONENT_PROFILE,
                node_key=str(row.player_id),
                label=f"opponent profile {row.player_id}",
                attributes={
                    "player_id": row.player_id,
                    "coverage": row.coverage,
                    "analyzed_games": row.analyzed_games,
                    "profile_version": row.profile_version,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        count += 1
    preparations = db.execute(select(MatchPreparationRecord)).scalars().all()
    for row in preparations:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.PREPARATION_REPORT,
                node_key=str(row.id),
                label=f"preparation for {row.opponent_name}",
                attributes={
                    "player_id": row.preparing_player_id,
                    "opponent_id": row.opponent_id,
                    "coverage": row.coverage,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.PREPARED_FOR,
                from_type=NodeType.PREPARATION_REPORT,
                from_key=str(row.id),
                to_type=NodeType.OPPONENT_PROFILE,
                to_key=str(row.opponent_id),
            )
        )
    return {"opponent_profiles": count, "preparation_reports": len(preparations)}


def update_scenarios(
    db: Session,
    *,
    service: IntelligenceGraphService | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Scenario provenance: which game/position each stored counterfactual came from."""
    service = service or service_for(db)
    rows = db.execute(
        select(ScenarioRecordRow)
        .order_by(ScenarioRecordRow.created_at.desc(), ScenarioRecordRow.id.desc())
        .limit(limit)
    ).scalars().all()
    count = 0
    for row in rows:
        service.upsert_node(
            GraphNode(
                node_type=NodeType.SCENARIO,
                node_key=str(row.id),
                label=row.scenario_type,
                attributes={
                    "player_id": row.owner_player_id,
                    "game_id": row.game_id,
                    "scenario_type": row.scenario_type,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        if row.game_id:
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.DERIVED_FROM,
                    from_type=NodeType.SCENARIO,
                    from_key=str(row.id),
                    to_type=NodeType.GAME,
                    to_key=row.game_id,
                    evidence=[
                        EvidenceReference(kind=EvidenceKind.SCENARIO, scenario_id=row.id, game_id=row.game_id)
                    ],
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
        position, _node = _position_node(
            service, row.source_fen, game_id=row.game_id, ply=row.ply
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.DERIVED_FROM,
                from_type=NodeType.SCENARIO,
                from_key=str(row.id),
                to_type=NodeType.POSITION,
                to_key=position,
                evidence=[
                    EvidenceReference(
                        kind=EvidenceKind.POSITION,
                        scenario_id=row.id,
                        game_id=row.game_id,
                        ply=row.ply,
                        position_hash=position,
                    )
                ],
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        count += 1
    return {"scenarios": count}


def seed_knowledge(db: Session, *, service: IntelligenceGraphService | None = None) -> dict[str, Any]:
    """Insert the sourced concept base and its graph nodes (idempotent)."""
    service = service or service_for(db)
    if db.get(KnowledgeSourceRecord, ARGUS_SOURCE.id) is None:
        db.add(
            KnowledgeSourceRecord(
                id=ARGUS_SOURCE.id,
                name=ARGUS_SOURCE.name,
                source_type=ARGUS_SOURCE.source_type.value,
                author=ARGUS_SOURCE.author,
                licence=ARGUS_SOURCE.licence,
                publication=ARGUS_SOURCE.publication,
                url=ARGUS_SOURCE.url,
                version=ARGUS_SOURCE.version,
            )
        )
        # Flush the source before adding its document so the document's foreign
        # key resolves; the two inserts must be ordered, not batched.
        db.flush()
    if db.get(KnowledgeDocumentRecord, ARGUS_DOCUMENT.id) is None:
        db.add(
            KnowledgeDocumentRecord(
                id=ARGUS_DOCUMENT.id,
                source_id=ARGUS_DOCUMENT.source_id,
                title=ARGUS_DOCUMENT.title,
                author=ARGUS_DOCUMENT.author,
                licence=ARGUS_DOCUMENT.licence,
                version=ARGUS_DOCUMENT.version,
            )
        )
    db.commit()
    concepts = build_curated_concepts()
    for concept in concepts:
        if db.get(KnowledgeConceptRecord, concept.slug) is None:
            db.add(
                KnowledgeConceptRecord(
                    slug=concept.slug,
                    name=concept.name,
                    category=concept.category,
                    definition=concept.definition,
                    how_to_spot=concept.how_to_spot,
                    typical_mistake=concept.typical_mistake,
                    source_id=concept.source_id,
                    document_id=concept.document_id,
                    related_base_concept=concept.related_base_concept,
                    version=concept.version,
                )
            )
        service.upsert_node(
            GraphNode(
                node_type=NodeType.KNOWLEDGE_CONCEPT,
                node_key=concept.slug,
                label=concept.name,
                attributes={
                    "category": concept.category,
                    "source_id": concept.source_id,
                    "document_id": concept.document_id,
                },
                methodology_version=GRAPH_METHODOLOGY_VERSION,
            )
        )
        service.write_edge(
            GraphEdge(
                edge_type=EdgeType.DERIVED_FROM_KNOWLEDGE,
                from_type=NodeType.KNOWLEDGE_CONCEPT,
                from_key=concept.slug,
                to_type=NodeType.KNOWLEDGE_DOCUMENT,
                to_key=ARGUS_DOCUMENT.id,
            )
        )
    db.commit()
    service.upsert_node(
        GraphNode(
            node_type=NodeType.KNOWLEDGE_DOCUMENT,
            node_key=ARGUS_DOCUMENT.id,
            label=ARGUS_DOCUMENT.title,
            attributes={"source_id": ARGUS_DOCUMENT.source_id, "licence": ARGUS_DOCUMENT.licence},
            methodology_version=GRAPH_METHODOLOGY_VERSION,
        )
    )
    return {"concepts": len(concepts)}


def link_position_concepts(
    db: Session,
    *,
    service: IntelligenceGraphService | None = None,
    game_ids: list[str] | None = None,
    limit: int = 400,
) -> dict[str, Any]:
    """Attach real positions to the concepts they exhibit (§27).

    Only positions Caissa has actually analysed are linked, and only through the
    deterministic rules in :func:`argus.intelligence_graph.knowledge.exhibited_concepts`.
    Nothing is attached by inference.
    """
    service = service or service_for(db)
    query = select(GamePosition)
    if game_ids:
        query = query.where(GamePosition.game_id.in_(game_ids))
    rows = db.execute(query.limit(limit)).scalars().all()
    linked = 0
    for row in rows:
        exhibitions = exhibited_concepts(row.fen)
        if not exhibitions:
            continue
        position = fingerprint(row.fen).position_hash
        for exhibition in exhibitions:
            service.upsert_node(
                GraphNode(
                    node_type=NodeType.POSITION,
                    node_key=position,
                    label=f"position {position[:8]}",
                    attributes={"game_id": row.game_id, "game_ids": [row.game_id], "fen": normalize_fen(row.fen)},
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
            service.write_edge(
                GraphEdge(
                    edge_type=EdgeType.EXHIBITS,
                    from_type=NodeType.POSITION,
                    from_key=position,
                    to_type=NodeType.KNOWLEDGE_CONCEPT,
                    to_key=exhibition.concept_slug,
                    evidence=[
                        EvidenceReference(
                            kind=EvidenceKind.POSITION,
                            game_id=row.game_id,
                            ply=row.ply,
                            position_hash=position,
                            statement=exhibition.statement,
                        )
                    ],
                    methodology_version=GRAPH_METHODOLOGY_VERSION,
                )
            )
            linked += 1
    return {"positions_scanned": len(rows), "concept_links": linked}


def rebuild(db: Session, *, limit_games: int | None = None) -> dict[str, Any]:
    """Full materialisation: structural, then derived, then knowledge."""
    service = service_for(db)
    game_rows = db.execute(select(Game.id).order_by(Game.created_at.desc())).all()
    game_ids = [str(row[0]) for row in game_rows]
    if limit_games is not None:
        game_ids = game_ids[:limit_games]
    for game_id in game_ids:
        update_game(db, game_id, service=service)
    players = db.execute(select(Player.id).order_by(Player.id)).all()
    for (player_id,) in players:
        update_player_patterns(db, int(player_id), service=service)
        update_player_training(db, int(player_id), service=service)
    update_opponents(db, service=service)
    update_scenarios(db, service=service)
    seed_knowledge(db, service=service)
    link_position_concepts(db, service=service, game_ids=game_ids)
    snapshot = service.snapshot()
    counts = service.store.counts() if hasattr(service.store, "counts") else {}
    return {
        "games": len(game_ids),
        "players": len(players),
        "snapshot": snapshot.to_payload(),
        "counts": counts,
        "metrics": service.metrics(),
    }


__all__ = [
    "link_position_concepts",
    "rebuild",
    "seed_knowledge",
    "update_opponents",
    "update_player_patterns",
    "update_player_training",
    "update_scenarios",
]
