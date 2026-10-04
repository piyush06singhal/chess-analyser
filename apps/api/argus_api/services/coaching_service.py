"""Phase 11 coaching service: the API's adapter over ``argus.coaching``.

Everything the coach knows comes from a stored phase: the Game Intelligence
report, the Player Intelligence profile, the training library and attempts, the
opponent profile, the stored analysis behind the turning-point explorer, and the
prediction registry. This module's only jobs are to *load* those, hand them to the
pure coaching package, and keep the refusals honest — a section with no data
returns ``available: false`` with a reason rather than a plausible sentence.

It deliberately performs no analysis and calls no engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from argus.coaching import (
    MODE_PROFILES,
    CoachContext,
    CoachMode,
    CollectionError,
    CollectionKind,
    ItemKind,
    StudyCollection,
    StudyItem,
    add_item,
    assemble_context,
    build_debrief,
    build_evidence_packet,
    build_feed,
    build_match_preparation,
    build_training_plan,
    collections_method,
    compare_periods,
    context_brief,
    evidence_method,
    explain_method,
    feed_method,
    match_brief,
    match_prep_method,
    parse_preparation_row,
    progress_method,
    search as rank_search,
    search_method,
)
from argus.coaching.feed import answer_focus
from argus.coaching.search import SearchCandidate, SearchKind
from argus.shared.errors import ConflictError, NotFoundError, ValidationError
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    add_study_item,
    create_study_collection,
    delete_study_collection,
    find_study_collection_by_name,
    get_game,
    get_game_report,
    get_match_preparation,
    get_move_analyses,
    get_player,
    get_study_collection,
    get_training_position,
    list_games,
    list_match_preparations,
    list_players,
    list_scenarios,
    list_study_collections,
    list_training_positions,
    remove_study_item,
    save_match_preparation,
)
from argus_api.services import (
    opponent_service,
    player_profile_service,
    scenario_service,
    training_service,
)
from argus_api.services.scenario_service import require_authorized_game

logger = get_logger(__name__)

#: Bumped with the coaching package's methodology versions.
COACHING_METHODOLOGY_VERSION = "11.0"


def _player_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise NotFoundError(f"Player '{value}' not found") from None


# ---------------------------------------------------------------------------
# loading (read-only, no engine)
# ---------------------------------------------------------------------------


def _profile_payload(db: Session, player_id: int | None) -> dict | None:
    """The stored player profile, or ``None`` when it has never been computed."""
    if player_id is None:
        return None
    try:
        return player_profile_service.get_player_profile(db, str(player_id))
    except NotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a profile is optional context, never fatal
        logger.warning("Player profile unavailable for player %s", player_id, exc_info=True)
        return None


def _attempt_history_by_insight(db: Session, player_id: int | None) -> dict[str, dict]:
    """Attempts and accuracy keyed by exercise *tag*, so a pattern can see its history.

    Keyed by tag rather than by insight id because exercises carry the tags that
    were measured when they were generated; an insight and the exercises derived
    from it share those tags.
    """
    if player_id is None:
        return {}
    progress = training_service.progress(db, player_id)
    library = training_service.library(db, player_id, limit=None)
    tags_by_position = {row["id"]: list(row.get("tags") or []) for row in library}
    attempts = _attempts(db, player_id)
    history: dict[str, dict] = {}
    for attempt in attempts:
        for tag in tags_by_position.get(attempt["training_position_id"], []):
            entry = history.setdefault(tag, {"attempts": 0, "correct": 0, "accuracy": None})
            entry["attempts"] += 1
            if attempt["correctness"] == "correct":
                entry["correct"] += 1
    for entry in history.values():
        if entry["attempts"]:
            entry["accuracy"] = round(entry["correct"] / entry["attempts"], 4)
    # Also expose per-category aggregates, which is what most insights map to.
    for row in library:
        category = row.get("category")
        if not category:
            continue
        entry = history.setdefault(
            f"category:{category}", {"attempts": 0, "correct": 0, "accuracy": None}
        )
    return history | {"_progress": {"library_size": progress.get("library_size")}}


def _attempts(db: Session, player_id: int) -> list[dict]:
    from argus_api.db.repository import list_training_attempts

    return [
        {
            "training_position_id": row.training_position_id,
            "correctness": row.correctness,
        }
        for row in list_training_attempts(db, player_id=player_id)
    ]


def _report(db: Session, game_id: str | None) -> dict | None:
    """The stored Game Intelligence report payload, or ``None``.

    Read through the repository record's ``payload`` — the same stored snapshot the
    intelligence route serves — so the debrief reads one report, not a second
    interpretation of the analysis.
    """
    if not game_id:
        return None
    try:
        record = get_game_report(db, game_id)
    except Exception:  # noqa: BLE001 - an absent report is a gap, not an error
        return None
    if record is None:
        return None
    payload = getattr(record, "payload", None)
    if isinstance(payload, dict):
        return payload
    return None


def _san_by_ply(db: Session, game_id: str) -> dict[int, str]:
    """The game's own SAN by ply — used only to label a moment the report stored
    without a move name. It is the same stored move list the report was built
    from, so nothing new is claimed."""
    try:
        from argus_api.db.repository import get_moves

        return {move.ply: move.san for move in get_moves(db, game_id) if move.san}
    except Exception:  # noqa: BLE001 - labelling only
        return {}


def _game_header(db: Session, game_id: str | None) -> dict | None:
    if not game_id:
        return None
    game = get_game(db, game_id)
    moves = getattr(game, "moves", None)
    return {
        "game_id": game.id,
        "white_player": getattr(game, "white_player", None),
        "black_player": getattr(game, "black_player", None),
        "result": game.result,
        "date": str(getattr(game, "date", "") or ""),
        "event": getattr(game, "event", None),
        "eco_code": getattr(game, "eco_code", None),
        "opening_name": getattr(game, "opening_name", None),
        "analysis_status": getattr(game, "analysis_status", None),
        "move_count": len(moves) if moves is not None else getattr(game, "move_count", None),
    }


def _side_for(db: Session, game: dict | None, player_id: int | None) -> str | None:
    """Which colour the caller held in this game, from stored identity only.

    This is what lets the debrief scope "what you did well" and "mistakes you
    made" to the caller's own moves instead of describing both sides. It returns
    ``None`` when the caller is unknown or did not play the game — the debrief
    then says the side is unknown rather than guessing from the result.
    """
    if not game or player_id is None:
        return None
    try:
        player = get_player(db, str(player_id))
    except NotFoundError:
        return None
    name = (getattr(player, "name", None) or "").strip().lower()
    if not name:
        return None
    white = (game.get("white_player") or "").strip().lower()
    black = (game.get("black_player") or "").strip().lower()
    if name == white:
        return "white"
    if name == black:
        return "black"
    return None


def _has_stored_analysis(db: Session, game_id: str | None) -> bool:
    """Whether this game has any stored engine analysis to review.

    The stored ``analysis_status`` column is the *pipeline's* state; a game can
    carry completed per-move analyses and still read ``ready`` (an interrupted or
    partially resumed run). The coach cares about the second question — "is there
    evidence to discuss?" — because answering "this game has not been analysed"
    while the explorer can show its turning points would be a lie the user can
    see through.
    """
    if not game_id:
        return False
    try:
        return bool(get_move_analyses(db, game_id))
    except Exception:  # noqa: BLE001 - a read failure means "not known to be analysed"
        logger.warning("Could not read stored analysis for game %s", game_id, exc_info=True)
        return False


def _predictions_available(settings: object | None = None) -> dict | None:
    """Per-task prediction availability — the honest registry view.

    Built through the same constructor the prediction routes use, so the coaching
    layer cannot disagree with them about which models are in production.
    """
    try:
        from argus_api.services.prediction_service import prediction_service_for

        return prediction_service_for(settings).summary()
    except Exception:  # noqa: BLE001 - prediction availability is optional context
        logger.warning("Prediction availability unavailable", exc_info=True)
        return None


def _opponent_payload(db: Session, opponent_id: int | None) -> dict | None:
    if opponent_id is None:
        return None
    try:
        return opponent_service.get_opponent_profile(db, opponent_id)
    except NotFoundError:
        return None
    except Exception:  # noqa: BLE001
        logger.warning("Opponent profile unavailable for %s", opponent_id, exc_info=True)
        return None


def _training_position(db: Session, position_id: int | None) -> dict | None:
    """The exercise in front of the user — with its solution withheld."""
    if position_id is None:
        return None
    try:
        row = get_training_position(db, position_id)
    except NotFoundError:
        return None
    return training_service.serialize_position(row, reveal=False)


# ---------------------------------------------------------------------------
# public surface
# ---------------------------------------------------------------------------


def resolve_context(
    db: Session,
    *,
    user_id: int | None = None,
    mode: str | None = None,
    game_id: str | None = None,
    fen: str | None = None,
    phase: str | None = None,
    training_position_id: int | None = None,
    opponent_id: int | None = None,
    settings: object | None = None,
    now: datetime | None = None,
) -> CoachContext:
    """Assemble one user's coaching context from stored data only."""
    now = now or datetime.now(timezone.utc)
    if user_id is not None:
        get_player(db, str(user_id))  # 404 when the player is unknown
    game = _game_header(db, game_id)
    training_position = _training_position(db, training_position_id)
    context = assemble_context(
        user_id=user_id,
        requested_mode=CoachMode(mode) if mode else None,
        game_id=game_id,
        game_is_analysed=(
            None
            if game is None
            else (
                game.get("analysis_status") == "analyzed"
                or _has_stored_analysis(db, game_id)
            )
        ),
        game=game,
        fen=fen,
        phase=phase,
        training_position=training_position,
        training_session_active=training_position_id is not None,
        training_history=(
            training_service.progress(db, user_id) if user_id is not None else None
        ),
        review_queue=(
            training_service.review_queue(db, user_id, limit=5)
            if user_id is not None
            else None
        ),
        player_profile=_profile_payload(db, user_id),
        opponent_id=opponent_id,
        opponent_profile=_opponent_payload(db, opponent_id),
        available_predictions=_predictions_available(settings),
        now=now,
    )
    return context


def context_payload(context: CoachContext) -> dict[str, Any]:
    return {
        "context": context.model_dump(mode="json"),
        "brief": context_brief(context),
        "methodology_version": COACHING_METHODOLOGY_VERSION,
    }


def debrief(
    db: Session,
    game_id: str,
    *,
    user_id: int | None = None,
    opponent_id: int | None = None,
) -> dict[str, Any]:
    """The automatic review of one stored game.

    The game is authorized through the same single decision point the scenario and
    training layers use, so a game the caller may not read does not exist here
    either (404) rather than leaking its report.
    """
    require_authorized_game(db, game_id)
    game = _game_header(db, game_id)  # 404 when missing
    report = _report(db, game_id)
    profile = _profile_payload(db, user_id)
    recommendations: dict | None = None
    if user_id is not None:
        try:
            recommendations = training_service.recommendations(db, user_id)
        except Exception:  # noqa: BLE001
            recommendations = None
    queue = None
    if user_id is not None:
        queue = training_service.review_queue(db, user_id, limit=5)
    counterfactuals = None
    try:
        explorer = scenario_service.explore_game(db, game_id, limit=8)
        counterfactuals = explorer.model_dump(mode="json")
    except Exception:  # noqa: BLE001 - the explorer needs stored candidates only
        counterfactuals = None
    result = build_debrief(
        game_id=game_id,
        report=report,
        user_id=user_id,
        side=_side_for(db, game, user_id),
        result=game.get("result"),
        opening=game.get("opening_name") or game.get("eco_code"),
        player_profile=profile,
        san_by_ply=_san_by_ply(db, game_id),
        training_attempts_by_tag=_attempt_history_by_insight(db, user_id),
        training_recommendations=recommendations,
        training_queue=queue,
        counterfactual_summary=counterfactuals,
    )
    payload = result.to_payload()
    payload["game"] = game
    payload["report_available"] = report is not None
    return payload


def feed(
    db: Session,
    *,
    user_id: int | None = None,
    game_id: str | None = None,
    opponent_id: int | None = None,
    dismissed: list[str] | None = None,
) -> dict[str, Any]:
    """Today's Caissa for one user."""
    if user_id is not None:
        get_player(db, str(user_id))
    if game_id is not None:
        require_authorized_game(db, game_id)
    profile = _profile_payload(db, user_id)
    report = _report(db, game_id)
    recommendations = None
    progress = None
    queue = None
    if user_id is not None:
        try:
            recommendations = training_service.recommendations(db, user_id)
        except Exception:  # noqa: BLE001
            recommendations = None
        progress = training_service.progress(db, user_id)
        queue = training_service.review_queue(db, user_id, limit=5)
    result = build_feed(
        user_id=user_id,
        game_id=game_id,
        game_report=report,
        player_profile=profile,
        training_by_tag=_attempt_history_by_insight(db, user_id),
        training_recommendations=recommendations,
        training_progress=progress,
        training_queue=queue,
        opponent_id=opponent_id,
        opponent_profile=_opponent_payload(db, opponent_id),
        dismissed=set(dismissed or []),
    )
    return result.to_payload()


def focus(
    db: Session,
    *,
    user_id: int,
    dismissed: list[str] | None = None,
) -> dict[str, Any]:
    """"What should I work on?" — from measured patterns only."""
    get_player(db, str(user_id))
    profile = _profile_payload(db, user_id)
    if profile is None:
        payload = answer_focus(player_profile=None, dismissed=set(dismissed or []))
        payload = payload.to_payload()
        payload["player_id"] = user_id
        return payload
    recommendations = None
    try:
        recommendations = training_service.recommendations(db, user_id)
    except Exception:  # noqa: BLE001
        recommendations = None
    answer = answer_focus(
        player_profile=profile,
        training_by_tag=_attempt_history_by_insight(db, user_id),
        training_recommendations=recommendations,
        dismissed=set(dismissed or []),
    )
    payload = answer.to_payload()
    payload["player_id"] = user_id
    return payload


def plan(
    db: Session,
    *,
    user_id: int,
    weeks: int = 1,
    dismissed: list[str] | None = None,
) -> dict[str, Any]:
    """A short training plan, built only from focus areas that carry evidence."""
    get_player(db, str(user_id))
    profile = _profile_payload(db, user_id)
    recommendations = None
    try:
        recommendations = training_service.recommendations(db, user_id)
    except Exception:  # noqa: BLE001
        recommendations = None
    answer = answer_focus(
        player_profile=profile,
        training_by_tag=_attempt_history_by_insight(db, user_id),
        training_recommendations=recommendations,
        dismissed=set(dismissed or []),
    )
    focus_items = ([answer.primary_focus] if answer.primary_focus else []) + list(
        answer.supporting_focus
    )
    result = build_training_plan(
        player_id=user_id,
        focus=[item for item in focus_items if item is not None],
        training_recommendations=recommendations,
        weeks=weeks,
    )
    payload = result.to_payload()
    if answer.reason:
        payload["notes"] = [answer.reason]
    return payload


def show_me_why(
    db: Session,
    *,
    claim: str,
    game_id: str | None = None,
    ply: int | None = None,
    player_id: int | None = None,
    insight_key: str | None = None,
    references: list[dict] | None = None,
) -> dict[str, Any]:
    """Resolve a claim to its stored evidence — §24/§25's *Show me why*.

    Sources, in the order they are attached: the stored move analysis (engine
    facts), the stored report's error/timeline entries (interpretations), the
    player profile insight (a pattern), and any caller-supplied references. A
    reference Caissa cannot follow becomes a stated gap, never a dropped one.
    """
    collected: list[dict] = []
    gaps: list[str] = []

    if game_id and ply is not None:
        analyses = get_move_analyses(db, game_id)
        row = next((item for item in analyses if item.ply == ply), None)
        if row is not None:
            collected.append(
                {
                    "kind": "engine",
                    "label": f"Engine evaluation at ply {ply}",
                    "statement": (
                        f"You played {row.played_move_san}; the engine's choice was "
                        f"{row.best_move_san or 'not stored'}"
                        + (
                            f", a loss of {abs(int(row.centipawn_loss))} centipawns"
                            if row.centipawn_loss is not None
                            else ""
                        )
                        + "."
                    ),
                    "certainty": "confirmed",
                    "source_system": "analysis",
                    "game_id": game_id,
                    "ply": ply,
                    "value": float(abs(int(row.centipawn_loss))) if row.centipawn_loss is not None else None,
                    "unit": "cp",
                }
            )
            if row.classification:
                collected.append(
                    {
                        "kind": "interpretation",
                        "label": "Classification",
                        "statement": (
                            f"Caissa classified {row.played_move_san} as a {row.classification} "
                            f"from the engine's own measurements."
                        ),
                        "certainty": "confirmed",
                        "source_system": "classification",
                        "game_id": game_id,
                        "ply": ply,
                    }
                )
        else:
            gaps.append(
                f"There is no stored engine analysis for ply {ply} of this game, so no "
                f"engine fact can be shown for it."
            )

    report = _report(db, game_id)
    if report and ply is not None:
        for moment in report.get("critical_moments") or []:
            if isinstance(moment, dict) and moment.get("ply") == ply:
                collected.append(
                    {
                        "kind": "interpretation",
                        "label": "Critical moment",
                        "statement": str(moment.get("reason") or moment.get("detail") or "This ply is a stored critical moment."),
                        "certainty": moment.get("certainty") or "candidate",
                        "source_system": "game_report",
                        "game_id": game_id,
                        "ply": ply,
                    }
                )

    if insight_key:
        profile = _profile_payload(db, player_id)
        found = None
        for insight in (profile or {}).get("insights") or []:
            if isinstance(insight, dict) and str(insight.get("id")) == str(insight_key):
                found = insight
                break
        if found is None:
            gaps.append(
                f"No stored player-profile insight has the key '{insight_key}', so nothing "
                f"is claimed for it."
            )
        else:
            for reference in found.get("evidence") or []:
                if isinstance(reference, dict):
                    collected.append(
                        {
                            "kind": "interpretation",
                            "label": str(found.get("title") or insight_key),
                            "statement": str(reference.get("detail") or reference.get("statement") or ""),
                            "certainty": found.get("certainty") or "candidate",
                            "source_system": "player_profile",
                            "game_id": reference.get("game_id"),
                            "ply": reference.get("ply"),
                        }
                    )

    collected.extend(references or [])
    packet = build_evidence_packet(
        claim=claim,
        references=collected,
        claim_level="confirmed" if any(r.get("certainty") == "confirmed" for r in collected) else None,
        extra_gaps=gaps,
    )
    payload = packet.to_payload()
    payload["method"] = evidence_method()
    return payload


# ---------------------------------------------------------------------------
# §29 study collections
# ---------------------------------------------------------------------------


def _collection_payload(row: object) -> dict[str, Any]:
    items = []
    for item in getattr(row, "items", None) or []:
        items.append(
            {
                "kind": item.item_kind,
                "ref": item.item_ref,
                "label": item.label,
                "note": item.note,
                "game_id": item.game_id,
                "ply": item.ply,
                "fen": item.fen,
                "added_at": item.created_at.isoformat() if item.created_at else None,
            }
        )
    return {
        "id": row.id,
        "player_id": row.player_id,
        "name": row.name,
        "description": row.description,
        "kind": row.kind,
        "size": len(items),
        "items": items,
        "methodology_version": row.methodology_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _authorized_collection(db: Session, collection_id: int, player_id: int | None) -> object:
    row = get_study_collection(db, collection_id)
    if player_id is not None and row.player_id != player_id:
        # A collection the caller does not own does not exist for them.
        raise NotFoundError(
            f"Study collection {collection_id} was not found",
            details={"collection_id": collection_id},
        )
    return row


def collections(db: Session, *, player_id: int) -> dict[str, Any]:
    """One player's collections, with their item counts."""
    get_player(db, str(player_id))
    rows = list_study_collections(db, player_id=player_id)
    return {
        "player_id": player_id,
        "collections": [_collection_payload(row) for row in rows],
        "count": len(rows),
        "method": collections_method(),
    }


def create_collection(
    db: Session,
    *,
    player_id: int,
    name: str,
    kind: str = "mixed",
    description: str = "",
) -> dict[str, Any]:
    """Create an empty collection, validating the kind up front."""
    get_player(db, str(player_id))
    try:
        collection_kind = CollectionKind(kind)
    except ValueError:
        raise NotFoundError(
            f"'{kind}' is not a known collection kind",
            details={"kind": kind},
        ) from None
    if not name.strip():
        raise ValidationError("a collection needs a name")
    if find_study_collection_by_name(db, player_id=player_id, name=name) is not None:
        raise ConflictError(f"You already have a collection named '{name.strip()}'")
    row = create_study_collection(
        db,
        name=name,
        player_id=player_id,
        kind=collection_kind.value,
        description=description,
        methodology_version=COACHING_METHODOLOGY_VERSION,
    )
    return _collection_payload(row)


def get_collection(db: Session, collection_id: int, *, player_id: int | None = None) -> dict[str, Any]:
    return _collection_payload(_authorized_collection(db, collection_id, player_id))


def delete_collection(db: Session, collection_id: int, *, player_id: int | None = None) -> dict[str, Any]:
    row = _authorized_collection(db, collection_id, player_id)
    delete_study_collection(db, row.id)
    return {"deleted": True, "collection_id": collection_id}


def add_collection_item(
    db: Session,
    collection_id: int,
    *,
    player_id: int | None = None,
    kind: str,
    ref: str,
    label: str = "",
    note: str = "",
    game_id: str | None = None,
    ply: int | None = None,
    fen: str | None = None,
) -> dict[str, Any]:
    """Add a typed pointer, refusing a kind the collection does not permit."""
    row = _authorized_collection(db, collection_id, player_id)
    try:
        item_kind = ItemKind(kind)
    except ValueError:
        raise NotFoundError(f"'{kind}' is not a known collection item kind", details={"kind": kind}) from None
    current = _collection_payload(row)
    try:
        collection = StudyCollection.model_validate(
            {
                "player_id": row.player_id,
                "name": row.name,
                "kind": row.kind,
                "items": current["items"],
            }
        )
        add_item(
            collection,
            StudyItem(kind=item_kind, ref=ref, label=label, note=note, game_id=game_id, ply=ply, fen=fen),
        )
    except CollectionError as exc:
        raise ValidationError(str(exc)) from None
    add_study_item(
        db,
        collection=row,
        item_kind=item_kind.value,
        item_ref=ref,
        label=label,
        note=note,
        game_id=game_id,
        ply=ply,
        fen=fen,
    )
    _reload_collection_items(db, row)
    return _collection_payload(row)


def _reload_collection_items(db: Session, row: object) -> None:
    """Force the collection's items to be re-read after a commit."""
    try:
        db.expire(row, ["items"])
    except Exception:  # noqa: BLE001 - a stale read is worse than a slow one
        db.expire_all()


def remove_collection_item(
    db: Session,
    collection_id: int,
    *,
    player_id: int | None = None,
    kind: str,
    ref: str,
) -> dict[str, Any]:
    row = _authorized_collection(db, collection_id, player_id)
    remove_study_item(db, collection=row, item_kind=kind, item_ref=ref)
    _reload_collection_items(db, row)
    return _collection_payload(row)


# ---------------------------------------------------------------------------
# §30 unified search
# ---------------------------------------------------------------------------


def _search_candidates(db: Session, *, player_id: int | None) -> list[SearchCandidate]:
    """Gather one candidate per real stored row — nothing synthesised."""
    candidates: list[SearchCandidate] = []

    for game in list_games(db, limit=500):
        candidates.append(
            SearchCandidate(
                kind=SearchKind.GAME,
                id=game.id,
                title=f"{game.white_player_name} vs {game.black_player_name}",
                subtitle=f"{game.result} · {game.date or 'date unknown'}",
                body=f"{game.opening_name or ''} {game.eco_code or ''} {game.event or ''} {game.site or ''}",
                tags=(game.eco_code or "", game.opening_name or ""),
                href=f"/game/{game.id}",
                updated_at=game.created_at,
                metadata={"result": game.result, "analysis_status": game.analysis_status},
            )
        )

    for player in list_players(db):
        name = player.get("name") or ""
        candidates.append(
            SearchCandidate(
                kind=SearchKind.PLAYER,
                id=str(player.get("id")),
                title=name,
                subtitle=f"{player.get('games', 0)} game(s), {player.get('analyzed', 0)} analysed",
                body=name,
                href=f"/players/{player.get('id')}",
                metadata={"games": player.get("games", 0), "analyzed": player.get("analyzed", 0)},
            )
        )

    if player_id is not None:
        for position in list_training_positions(db, player_id=player_id, limit=500):
            candidates.append(
                SearchCandidate(
                    kind=SearchKind.TRAINING,
                    id=str(position.id),
                    title=position.source_reason or position.category,
                    subtitle=f"{position.category} · {position.position_type} · {position.difficulty}",
                    body=" ".join([position.fen or "", " ".join(position.tags or [])]),
                    tags=tuple(position.tags or []),
                    href=f"/training/solve/{position.id}",
                    updated_at=position.updated_at,
                    metadata={"category": position.category, "state": position.state},
                )
            )

    for scenario in list_scenarios(db, owner_player_id=player_id, limit=500) if player_id is not None else list_scenarios(db, limit=500):
        candidates.append(
            SearchCandidate(
                kind=SearchKind.SCENARIO,
                id=str(scenario.id),
                title=scenario.scenario_type.replace("_", " "),
                subtitle=f"ply {scenario.ply}" if scenario.ply else "stored scenario",
                body=scenario.source_fen or "",
                href="/scenarios",
                updated_at=scenario.created_at,
                metadata={"scenario_type": scenario.scenario_type, "game_id": scenario.game_id},
            )
        )

    for collection in list_study_collections(db, player_id=player_id):
        candidates.append(
            SearchCandidate(
                kind=SearchKind.COLLECTION,
                id=str(collection.id),
                title=collection.name,
                subtitle=f"{collection.kind} · {len(collection.items or [])} item(s)",
                body=collection.description or "",
                href=f"/collections/{collection.id}",
                updated_at=collection.updated_at,
            )
        )

    profile = _profile_payload(db, player_id)
    for insight in (profile or {}).get("insights") or []:
        if not isinstance(insight, dict):
            continue
        candidates.append(
            SearchCandidate(
                kind=SearchKind.INSIGHT,
                id=str(insight.get("id")),
                title=str(insight.get("title") or insight.get("id")),
                subtitle=str(insight.get("claim_level") or "observation"),
                body=str(insight.get("statement") or ""),
                tags=(str(insight.get("category") or ""),),
                href=f"/players/{player_id}" if player_id else "/players",
            )
        )
    for opening in ((profile or {}).get("openings") or (profile or {}).get("opening_repertoire") or []):
        if not isinstance(opening, dict):
            continue
        name = str(opening.get("opening_name") or opening.get("name") or opening.get("eco") or "")
        if not name:
            continue
        candidates.append(
            SearchCandidate(
                kind=SearchKind.OPENING,
                id=str(opening.get("eco") or name),
                title=name,
                subtitle=f"{opening.get('games', 0)} game(s)",
                body=f"{opening.get('eco') or ''}",
                href=f"/players/{player_id}" if player_id else "/players",
            )
        )
    return candidates


def search(
    db: Session,
    query: str,
    *,
    player_id: int | None = None,
    kinds: list[str] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Rank stored rows for a query; empty and unmatched results differ."""
    selected: set[SearchKind] | None = None
    if kinds:
        selected = set()
        for kind in kinds:
            try:
                selected.add(SearchKind(kind))
            except ValueError:
                raise NotFoundError(f"'{kind}' is not a searchable kind", details={"kind": kind}) from None
    candidates = _search_candidates(db, player_id=player_id)
    result = rank_search(candidates, query, kinds=selected, limit=limit)
    payload = result.to_payload()
    payload["method"] = search_method()
    return payload


# ---------------------------------------------------------------------------
# §14–§17 match preparation
# ---------------------------------------------------------------------------


def prepare(
    db: Session,
    *,
    preparing_player_id: int,
    opponent_id: int,
    as_white: bool | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Build (and optionally store) a match preparation object + brief."""
    get_player(db, str(preparing_player_id))
    # Validate the opponent exists before building or persisting: an opponent the
    # library does not know is a 404, not an empty brief written against a
    # dangling foreign key.
    get_player(db, str(opponent_id))
    opponent = _opponent_payload(db, opponent_id)
    preparation = build_match_preparation(
        preparing_player_id=preparing_player_id,
        opponent_id=opponent_id,
        opponent_profile=opponent,
        as_white=as_white,
    )
    if persist:
        row = save_match_preparation(
            db,
            preparing_player_id=preparing_player_id,
            opponent_id=opponent_id,
            opponent_name=preparation.opponent_name,
            as_white=as_white,
            coverage=preparation.coverage,
            opponent_games=preparation.opponent_games,
            analysed_games=preparation.analysed_games,
            sections=[section.model_dump(mode="json") for section in preparation.sections],
            scenarios=[scenario.model_dump(mode="json") for scenario in preparation.scenarios],
            opponent_profile_version=preparation.opponent_profile_version,
            methodology_version=COACHING_METHODOLOGY_VERSION,
        )
        preparation = parse_preparation_row(row)
    return {
        "preparation": preparation.to_payload(),
        "brief": match_brief(preparation),
        "method": match_prep_method(),
    }


def get_preparation(db: Session, preparation_id: int, *, preparing_player_id: int | None = None) -> dict[str, Any]:
    row = get_match_preparation(db, preparation_id)
    if preparing_player_id is not None and row.preparing_player_id != preparing_player_id:
        raise NotFoundError(
            f"Match preparation {preparation_id} was not found",
            details={"preparation_id": preparation_id},
        )
    preparation = parse_preparation_row(row)
    return {
        "preparation": preparation.to_payload(),
        "brief": match_brief(preparation),
        "method": match_prep_method(),
    }


def preparations(
    db: Session, *, preparing_player_id: int, opponent_id: int | None = None
) -> dict[str, Any]:
    get_player(db, str(preparing_player_id))
    rows = list_match_preparations(
        db, preparing_player_id=preparing_player_id, opponent_id=opponent_id
    )
    return {
        "preparing_player_id": preparing_player_id,
        "preparations": [
            {"id": row.id, "brief": match_brief(parse_preparation_row(row)), "preparation": parse_preparation_row(row).to_payload()}
            for row in rows
        ],
        "count": len(rows),
    }


# ---------------------------------------------------------------------------
# §26–§28 progress and improvement comparison
# ---------------------------------------------------------------------------


def _game_metrics(payload: dict | None, side: str | None) -> dict[str, Any]:
    """Extract the comparable measures from a stored report payload."""
    if not payload or not side:
        return {}
    accuracy = (payload.get("accuracy") or {}).get("analysis") or {}
    side_accuracy = accuracy.get(side) or {}
    moves = accuracy.get("moves") or []
    blunders = sum(1 for m in moves if isinstance(m, dict) and m.get("side") == side and m.get("classification") == "blunder")
    mistakes = sum(1 for m in moves if isinstance(m, dict) and m.get("side") == side and m.get("classification") == "mistake")
    return {
        "accuracy": side_accuracy.get("accuracy"),
        "mean_centipawn_loss": side_accuracy.get("average_centipawn_loss"),
        "blunder_count": blunders,
        "mistake_count": mistakes,
    }


def _progress_games(db: Session, player_id: int) -> list[dict]:
    """Every game of one player with its stored report metrics, oldest first."""
    from argus_api.db.models import Game as GameModel

    get_player(db, str(player_id))
    games = (
        db.query(GameModel)
        .filter(
            (GameModel.white_player_id == player_id) | (GameModel.black_player_id == player_id)
        )
        .order_by(GameModel.created_at)
        .all()
    )
    rows: list[dict] = []
    for game in games:
        header = _game_header(db, game.id)
        side = _side_for(db, header, player_id)
        report = _report(db, game.id)
        rows.append(
            {
                "game_id": game.id,
                "created_at": game.created_at,
                "has_report": report is not None,
                "metrics": _game_metrics(report, side),
            }
        )
    return rows


def compare(
    db: Session,
    *,
    player_id: int,
    split: float = 0.5,
    before_label: str = "Earlier games",
    after_label: str = "Recent games",
) -> dict[str, Any]:
    """Compare the player's earlier and recent analysed games.

    The split is chronological (by stored creation order), because Caissa dates in
    imported PGNs are often absent or unreliable — a measured split must be on a
    field Caissa actually controls.
    """
    rows = _progress_games(db, player_id)
    if not rows:
        result = compare_periods(
            before_games=[],
            after_games=[],
            before_label=before_label,
            after_label=after_label,
        )
        payload = result.to_payload()
        payload["player_id"] = player_id
        payload["method"] = progress_method()
        return payload
    pivot = max(1, int(len(rows) * split))
    before = rows[:pivot]
    after = rows[pivot:]
    result = compare_periods(
        before_games=before,
        after_games=after,
        before_label=before_label,
        after_label=after_label,
        before_end=before[-1]["created_at"] if before else None,
        after_start=after[0]["created_at"] if after else None,
    )
    payload = result.to_payload()
    payload["player_id"] = player_id
    payload["method"] = progress_method()
    return payload


def method() -> dict[str, Any]:
    """The coaching methodology, published so it can be argued with."""
    return {
        "methodology_version": COACHING_METHODOLOGY_VERSION,
        "priority": explain_method(),
        "feed": feed_method(),
        "evidence": evidence_method(),
        "collections": collections_method(),
        "search": search_method(),
        "match_preparation": match_prep_method(),
        "progress": progress_method(),
        "modes": {
            mode.value: {
                "label": profile.label,
                "summary": profile.summary,
                "explanation_depth": profile.explanation_depth,
                "expose_evaluation": profile.expose_evaluation,
                "expose_principal_variation": profile.expose_principal_variation,
                "allowed_tool_families": list(profile.allowed_tool_families),
            }
            for mode, profile in MODE_PROFILES.items()
        },
    }


__all__ = [
    "COACHING_METHODOLOGY_VERSION",
    "add_collection_item",
    "collections",
    "compare",
    "context_payload",
    "create_collection",
    "debrief",
    "delete_collection",
    "feed",
    "focus",
    "get_collection",
    "get_preparation",
    "method",
    "plan",
    "prepare",
    "preparations",
    "remove_collection_item",
    "resolve_context",
    "search",
    "show_me_why",
]
