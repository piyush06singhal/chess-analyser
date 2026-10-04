"""Phase 9 opponent service: the seam between stored games and the opponent engine.

This module owns the *API* side of opponent intelligence:

* it reads the games and stored move analyses of a tracked player through the
  existing repository primitives (no second identity system, no duplicated game
  data),
* it maps those rows into the engine-free ``OpponentGameInput`` vocabulary,
* it builds the opponent profile / repertoire / responses / tendencies / phase
  statistics / preparation report through ``argus.opponent_intelligence``, and
* it caches the profile snapshot with a source fingerprint so a dashboard read
  does not re-aggregate every game every time.

It never runs the engine: a report is pure database aggregation over analysis
that was already produced. When there is not enough data, the documents say so
with their real sample sizes instead of inventing a finding.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from argus.opponent_intelligence import (
    OPPONENT_METHODOLOGY_VERSION,
    DEFAULT_POLICY,
    OpponentGameInput,
    OpponentInsightPolicy,
    OpponentIntelligenceService,
    OpponentMoveInput,
    PlayerIdentity,
)
from argus.player_intelligence.models import GameOutcome
from argus.shared.logging import get_logger

from argus_api.db.models import Player
from argus_api.db.repository import (
    delete_opponent_profiles,
    get_opponent_profile_record,
    get_player,
    opponent_game_rows,
    opponent_input_signature,
    save_opponent_profile,
)
from argus_api.services.player_profile_service import outcome_for as _outcome_for

logger = get_logger(__name__)

#: The stored snapshot's version. Bumped when the output shape changes.
PROFILE_VERSION = "9.0"


def policy_from_settings(settings: object | None = None) -> OpponentInsightPolicy:
    """Build the evidence policy, allowing settings to override the named gates.

    Every value has a documented default; settings may tighten or loosen the
    four named sample-size gates. The values actually used are stored inside
    every report (see ``policy`` on the outputs).
    """
    if settings is None:
        return DEFAULT_POLICY
    overrides: dict[str, int] = {}
    for name in (
        "min_games_for_repertoire_insight",
        "min_occurrences_for_tendency",
        "min_positions_for_structure_insight",
        "min_games_for_phase_comparison",
    ):
        value = getattr(settings, name, None)
        if isinstance(value, int) and value > 0:
            overrides[name] = value
    return DEFAULT_POLICY.model_copy(update=overrides) if overrides else DEFAULT_POLICY


def service_for(settings: object | None = None) -> OpponentIntelligenceService:
    return OpponentIntelligenceService(policy_from_settings(settings))


# ---------------------------------------------------------------------------
# Row → input mapping
# ---------------------------------------------------------------------------


def _comment_for(result: str, color: str) -> GameOutcome:
    return _outcome_for(result, color)


def game_input_from_row(row: dict) -> OpponentGameInput:
    """Map one repository row into the engine-free opponent-input model."""
    color = str(row.get("color") or "white")
    moves = [
        OpponentMoveInput(
            ply=int(move.get("ply") or 0),
            move_number=int(move.get("move_number") or 0),
            color=str(move.get("color") or color),
            san=str(move.get("san") or ""),
            uci=str(move.get("uci") or ""),
            fen_before=str(move.get("fen_before") or ""),
            phase=move.get("phase"),
            classification=move.get("classification"),
            centipawn_loss=move.get("centipawn_loss"),
            is_best_move=bool(move.get("is_best_move")),
            best_move_uci=move.get("best_move_uci"),
            best_move_san=move.get("best_move_san"),
            evaluation_before_cp=move.get("evaluation_before_cp"),
            evaluation_before_mate=move.get("evaluation_before_mate"),
            candidate_moves=list(move.get("candidate_moves") or []),
            principal_variation=list(move.get("principal_variation") or []),
        )
        for move in (row.get("moves") or [])
    ]
    return OpponentGameInput(
        game_id=str(row.get("game_id")),
        color=color,
        opponent_name=str(row.get("opponent_name") or "Unknown"),
        opponent_rating=row.get("opponent_rating"),
        other_rating=row.get("other_rating"),
        result=str(row.get("result") or "*"),
        outcome=_comment_for(str(row.get("result") or "*"), color),
        date=row.get("date"),
        event=row.get("event"),
        time_control=row.get("time_control"),
        eco_code=row.get("eco_code"),
        opening_name=row.get("opening_name"),
        move_count=int(row.get("move_count") or 0),
        source=row.get("source"),
        analysis_status=row.get("analysis_status"),
        analysis_version=row.get("analysis_version"),
        engine=row.get("engine"),
        engine_version=row.get("engine_version"),
        depth=row.get("depth"),
        moves=moves,
    )


def build_inputs(rows: list[dict]) -> list[OpponentGameInput]:
    return [game_input_from_row(row) for row in rows]


def identity_for(player: Player) -> PlayerIdentity:
    return PlayerIdentity(
        player_id=player.id,
        name=player.name,
        identity_key=player.identity_key,
        platform=player.platform,
        platform_username=player.platform_username,
        title=player.title,
    )


def _load(db: Session, player_id: int) -> tuple[Player, list[dict], list[OpponentGameInput]]:
    player = get_player(db, str(player_id))
    rows = opponent_game_rows(db, player_id)
    return player, rows, build_inputs(rows)


# ---------------------------------------------------------------------------
# Cached profile
# ---------------------------------------------------------------------------


def get_opponent_profile(
    db: Session,
    player_id: int,
    *,
    rebuild: bool = False,
    settings: object | None = None,
) -> dict:
    """The cached opponent profile, rebuilt only when the inputs changed.

    The returned dict is the stored snapshot's payload plus provenance: the
    methodology version, the source fingerprint, and whether it was rebuilt on
    this call. A stale snapshot (new games or a new analysis version) is
    rebuilt transparently — the API never serves a report it knows is out of
    date without saying so.
    """
    player, rows, inputs = _load(db, player_id)
    signature = opponent_input_signature(rows)
    existing = None if rebuild else get_opponent_profile_record(db, player_id)

    if existing is not None and existing.source_signature == signature:
        payload = dict(existing.payload or {})
        payload.setdefault("methodology_version", existing.methodology_version)
        payload["stale"] = False
        payload["rebuilt"] = False
        payload["source_signature"] = existing.source_signature
        return payload

    service = service_for(settings)
    profile = service.profile(inputs, identity_for(player))
    payload = profile.model_dump(mode="json")
    analyzed = profile.statistics.analyzed_games
    save_opponent_profile(
        db,
        player_id=player_id,
        profile_version=PROFILE_VERSION,
        methodology_version=OPPONENT_METHODOLOGY_VERSION,
        source_signature=signature,
        imported_games=profile.statistics.total_games,
        analyzed_games=analyzed,
        coverage=profile.coverage.value,
        payload=payload,
        generated_at=datetime.now(timezone.utc),
    )
    payload["stale"] = False
    payload["rebuilt"] = True
    payload["source_signature"] = signature
    return payload


# ---------------------------------------------------------------------------
# Direct documents (no cache)
# ---------------------------------------------------------------------------


def get_opponent_games(
    db: Session, player_id: int, *, limit: int = 50, offset: int = 0, color: str | None = None
) -> dict:
    """The opponent's game history, newest first, with measured statistics."""
    player, rows, _ = _load(db, player_id)
    if color:
        rows = [row for row in rows if row.get("color") == color]
    ordered = sorted(rows, key=lambda row: (row.get("date") or ""), reverse=True)
    page = ordered[offset : offset + limit]
    service = service_for(None)
    inputs = build_inputs(rows)
    stats = service.profile(inputs, identity_for(player)).statistics
    return {
        "player_id": player_id,
        "identity": identity_for(player).model_dump(mode="json"),
        "total": len(ordered),
        "offset": offset,
        "limit": limit,
        "statistics": stats.model_dump(mode="json"),
        "games": [
            {
                "game_id": row.get("game_id"),
                "color": row.get("color"),
                "opponent_name": row.get("opponent_name"),
                "opponent_rating": row.get("opponent_rating"),
                "other_rating": row.get("other_rating"),
                "result": row.get("result"),
                "outcome": _comment_for(str(row.get("result") or "*"), str(row.get("color") or "white")).value,
                "date": row.get("date"),
                "event": row.get("event"),
                "time_control": row.get("time_control"),
                "eco_code": row.get("eco_code"),
                "opening_name": row.get("opening_name"),
                "move_count": row.get("move_count"),
                "source": row.get("source"),
                "analysis_status": row.get("analysis_status"),
                "analysis_version": row.get("analysis_version"),
            }
            for row in page
        ],
    }


def get_repertoire(db: Session, player_id: int, *, color: str, recent: bool = False) -> dict:
    """The opponent's opening repertoire as one colour.

    ``recent`` restricts the sample to the most recent games (a windowed view
    that answers "what are they playing lately?"), reported with its own — and
    smaller — sample size.
    """
    player, rows, _ = _load(db, player_id)
    selected = rows
    window_note = ""
    if recent:
        ordered = sorted(rows, key=lambda row: (row.get("date") or ""), reverse=True)
        selected = ordered[:10]
        window_note = f"Restricted to the {len(selected)} most recent game(s)."
    service = service_for(None)
    enabled = [row for row in selected if row.get("color") == color]
    profile = service.repertoire(build_inputs(selected), color)
    _ = enabled
    payload = profile.model_dump(mode="json")
    payload["player_id"] = player_id
    payload["identity"] = identity_for(player).model_dump(mode="json")
    payload["recent"] = recent
    if window_note:
        payload["note"] = f"{payload.get('note', '')} {window_note}".strip()
    return payload


def get_position_responses(db: Session, player_id: int, *, fen: str) -> dict:
    """How the opponent answered one position, from stored games only."""
    player, _rows, inputs = _load(db, player_id)
    service = service_for(None)
    response = service.responses(inputs, fen)
    payload = response.model_dump(mode="json")
    payload["player_id"] = player_id
    payload["identity"] = identity_for(player).model_dump(mode="json")
    return payload


def get_tendencies(db: Session, player_id: int) -> dict:
    """The opponent's measured, evidence-gated tendencies."""
    player, _rows, inputs = _load(db, player_id)
    service = service_for(None)
    measured = service.tendencies(inputs)
    return {
        "player_id": player_id,
        "identity": identity_for(player).model_dump(mode="json"),
        "methodology_version": OPPONENT_METHODOLOGY_VERSION,
        "policy": service.policy.to_dict(),
        "tendencies": [tendency.model_dump(mode="json") for tendency in measured],
        "note": (
            "Every tendency states what was counted and over how many observations; a tendency "
            "below its sample-size gate is returned but marked insufficient."
        ),
    }


def get_phase_statistics(db: Session, player_id: int, *, color: str | None = None) -> dict:
    """The opponent's measured performance by phase."""
    player, _rows, inputs = _load(db, player_id)
    service = service_for(None)
    stats = service.phase_statistics(inputs, color=color)
    payload = stats.model_dump(mode="json")
    payload["player_id"] = player_id
    payload["identity"] = identity_for(player).model_dump(mode="json")
    return payload


def get_preparation_report(
    db: Session,
    player_id: int,
    *,
    color: str | None = None,
    settings: object | None = None,
) -> dict:
    """The full preparation document for one opponent (and optional colour)."""
    player, _rows, inputs = _load(db, player_id)
    service = service_for(settings)
    report = service.preparation_report(inputs, identity_for(player), color_to_prepare=color)
    payload = report.model_dump(mode="json")
    payload["player_id"] = player_id
    return payload


def rebuild_player_opponent_profile(db: Session, player_id: int, *, settings: object | None = None) -> dict:
    """Discard cached snapshots and rebuild the opponent profile from scratch."""
    player = get_player(db, str(player_id))
    delete_opponent_profiles(db, player.id)
    return get_opponent_profile(db, player_id, rebuild=True, settings=settings)


__all__ = [
    "PROFILE_VERSION",
    "build_inputs",
    "game_input_from_row",
    "get_opponent_games",
    "get_opponent_profile",
    "get_phase_statistics",
    "get_position_responses",
    "get_preparation_report",
    "get_repertoire",
    "get_tendencies",
    "identity_for",
    "policy_from_settings",
    "rebuild_player_opponent_profile",
    "service_for",
]
