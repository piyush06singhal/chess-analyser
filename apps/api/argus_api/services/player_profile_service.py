"""Player profile service: stored games in, cached versioned profile out.

Responsibilities, in order:

1. **Map** each stored ``GameReport`` (plus the stored moves) into a
   :class:`~argus.player_intelligence.models.PlayerGameInput`. The mapping is
   the only place that knows the report's JSON shape; the aggregation core
   knows nothing about the database.
2. **Decide staleness**: a stored snapshot is reused while its input signature
   matches (same analyzed games, same analysis versions) — the dashboard never
   re-derives the profile on every request, and a new analysis invalidates it
   automatically.
3. **Build and persist** the profile when missing or stale, and expose an
   explicit ``rebuild_player_profile`` for debugging and methodology bumps.

Games that have no stored report (imported but not analyzed, or failed) are
never silently dropped: they are counted and named in ``excluded_game_ids``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from argus.player_intelligence import (
    DEFAULT_POLICY,
    METHODOLOGY_VERSION,
    PROFILE_VERSION,
    GameOutcome,
    PlayerGameInput,
    PlayerInsightPolicy,
    build_profile,
    classify_time_control,
    opening_family,
)
from argus.player_intelligence.models import (
    KingSafetyEventInput,
    MaterialInput,
    OpeningDeviationInput,
    PhasePerformanceInput,
    PlayerErrorEvent,
    PositionalEventInput,
    TacticalEventInput,
    TrajectoryInput,
)
from argus.chess_core.pgn import parse_time_control
from argus.shared.logging import get_logger

from argus_api.db.repository import (
    get_player,
    get_player_profile_record,
    player_game_rows,
    player_input_signature,
    save_player_profile,
)
from argus_api.services.report_service import ensure_report

logger = get_logger(__name__)

#: Caissa advantage bands, mirrored from the Phase 4 advantage module. Bands are
#: the *documented* scale the conversion/recovery thresholds refer to.
ADVANTAGE_BANDS: dict[str, int] = {
    "forced_mate": 4,
    "winning": 3,
    "advantage": 2,
    "slight_advantage": 1,
    "equal": 0,
    "slight_disadvantage": -1,
    "disadvantage": -2,
    "losing": -3,
}

_RESULT_BY_OUTCOME: dict[str, GameOutcome] = {
    "win": GameOutcome.WIN,
    "loss": GameOutcome.LOSS,
    "draw": GameOutcome.DRAW,
}


def _generated_at(value: str | None):  # noqa: ANN202 — datetime | None
    """Parse the profile's own timestamp so the cache row records when it was built."""
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value)
    except ValueError:
        return None


def policy_from_settings(settings: Any | None) -> PlayerInsightPolicy:
    """Build the effective policy from application settings when available."""
    if settings is None:
        return DEFAULT_POLICY
    fields = {
        "min_games_for_profile": getattr(settings, "player_min_games_for_profile", None),
        "min_games_for_tendency": getattr(settings, "player_min_games_for_tendency", None),
        "min_games_for_strong_claim": getattr(settings, "player_min_games_for_strong_claim", None),
    }
    overrides = {key: value for key, value in fields.items() if value is not None}
    return DEFAULT_POLICY.model_copy(update=overrides) if overrides else DEFAULT_POLICY


def outcome_for(result: str, color: str) -> GameOutcome:
    """The player's own result, from the game result and their colour."""
    if result == "1/2-1/2":
        return GameOutcome.DRAW
    if result == "1-0":
        return GameOutcome.WIN if color == "white" else GameOutcome.LOSS
    if result == "0-1":
        return GameOutcome.WIN if color == "black" else GameOutcome.LOSS
    return GameOutcome.UNKNOWN


def _band(state: str | None) -> int | None:
    if not state:
        return None
    return ADVANTAGE_BANDS.get(state)


def _trajectory_for(payload: dict, color: str) -> TrajectoryInput:
    """Peak / worst / final advantage band in the player's own perspective."""
    points = (payload.get("trajectory") or {}).get("trajectory", {}).get("points", []) or []
    key = "white_state" if color == "white" else "black_state"
    bands: list[int] = []
    final_band: int | None = None
    evaluated = 0
    for point in points:
        if not point.get("available"):
            continue
        evaluated += 1
        band = _band(point.get(key))
        if band is not None:
            bands.append(band)
            final_band = band
    return TrajectoryInput(
        evaluated_plies=evaluated,
        peak_band=max(bands) if bands else None,
        worst_band=min(bands) if bands else None,
        final_band=final_band,
    )


def _castling_for(rows: list[dict], color: str) -> tuple[bool | None, int | None]:
    """Detect castling from the stored SAN moves for the player's colour."""
    for move in rows:
        if move.get("color") != color:
            continue
        san = (move.get("san") or "").replace("+", "").replace("#", "")
        if san in ("O-O", "0-0"):
            move_number = (move.get("ply", 0) + 1) // 2
            return True, move_number
        if san in ("O-O-O", "0-0-0"):
            move_number = (move.get("ply", 0) + 1) // 2
            return True, move_number
    # Only meaningful when the game actually reached a castling opportunity
    # (more than a handful of moves) — very short games stay unknown.
    if len(rows) < 10:
        return None, None
    return False, None


def game_input_from_row(player_id: int, row: dict, *, policy: PlayerInsightPolicy) -> PlayerGameInput | None:
    """Map one stored game + report into a ``PlayerGameInput`` (``None`` when unanalyzed)."""
    payload = row.get("report_payload")
    if not payload:
        return None

    color = row["color"]
    accuracy = payload.get("accuracy", {}).get("analysis", {})
    side_accuracy = accuracy.get(color, {}) or {}
    moves = accuracy.get("moves", []) or []
    error_rows = payload.get("error_categories", {}).get("analysis", {}).get("errors", []) or []

    # One error row per (error, category): group by ply so multi-tag errors keep
    # every category they were given (the taxonomy is deliberately not exclusive).
    errors_by_ply: dict[int, PlayerErrorEvent] = {}
    for error in error_rows:
        if error.get("side") != color:
            continue
        ply = int(error.get("ply", 0))
        entry = errors_by_ply.get(ply)
        if entry is None:
            entry = PlayerErrorEvent(
                game_id=row["game_id"],
                ply=ply,
                move_number=error.get("move_number"),
                san=error.get("san"),
                phase=error.get("phase"),
                classification=error.get("classification"),
                categories=[],
                severity=None,
                centipawn_loss=error.get("centipawn_loss"),
            )
            errors_by_ply[ply] = entry
        category = error.get("category")
        if category and category not in entry.categories:
            entry.categories.append(category)

    # Classification counts come from the trajectory rows (they carry the engine
    # classification per ply); scored/excluded come from the accuracy rows.
    classification_by_ply: dict[int, str] = {}
    for point in (payload.get("trajectory") or {}).get("trajectory", {}).get("points", []) or []:
        if point.get("classification"):
            classification_by_ply[int(point["ply"])] = point["classification"]

    blunders = mistakes = inaccuracies = 0
    for move in moves:
        if move.get("side") != color or not move.get("scored"):
            continue
        classification = classification_by_ply.get(int(move.get("ply", 0)))
        if classification == "blunder":
            blunders += 1
        elif classification == "mistake":
            mistakes += 1
        elif classification == "inaccurate":
            inaccuracies += 1

    phase_rows: list[PhasePerformanceInput] = []
    performance = (payload.get("phases") or {}).get("performance", {}) or {}
    breakdown = (accuracy.get("breakdown") or {}).get(color, {}) or {}
    accuracy_by_phase = {
        group.get("key"): group for group in (breakdown.get("by_phase") or [])
    }
    for phase in ("opening", "middlegame", "endgame"):
        stats = (performance.get(color) or {}).get(phase)
        if not stats:
            continue
        group = accuracy_by_phase.get(phase, {})
        phase_rows.append(
            PhasePerformanceInput(
                phase=phase,
                evaluated_moves=int(stats.get("evaluated_moves", 0) or 0),
                average_centipawn_loss=stats.get("average_centipawn_loss"),
                problem_moves=int(stats.get("problem_moves", 0) or 0),
                accuracy=group.get("accuracy"),
                share_of_loss=group.get("share_of_loss"),
                small_sample=bool(stats.get("small_sample")),
            )
        )

    tactical_events: list[TacticalEventInput] = []
    for event in ((payload.get("tactical") or {}).get("analysis", {}) or {}).get("events", []) or []:
        tactical_events.append(
            TacticalEventInput(
                game_id=row["game_id"],
                ply=int(event.get("ply", 0)),
                move_number=event.get("move_number"),
                san=event.get("san"),
                event_type=event.get("type", "unknown"),
                direction="created" if event.get("side") == color else "allowed",
                severity=event.get("severity"),
                certainty=event.get("certainty", "confirmed"),
            )
        )

    positional_events: list[PositionalEventInput] = []
    for event in ((payload.get("positional") or {}).get("analysis", {}) or {}).get("events", []) or []:
        positional_events.append(
            PositionalEventInput(
                game_id=row["game_id"],
                ply=int(event.get("ply", 0)),
                move_number=event.get("move_number"),
                san=event.get("san"),
                feature=event.get("type", "unknown"),
                kind="error_candidate" if event.get("classification") == "error_candidate" else "feature",
                direction="created" if event.get("side") == color else "allowed",
                severity=event.get("severity"),
            )
        )

    king_events: list[KingSafetyEventInput] = []
    for event in ((payload.get("king_safety") or {}).get("analysis", {}) or {}).get("events", []) or []:
        king_events.append(
            KingSafetyEventInput(
                game_id=row["game_id"],
                ply=int(event.get("ply", 0)),
                move_number=event.get("move_number"),
                san=event.get("san"),
                event_type=event.get("type", "unknown"),
                direction="created" if event.get("side") == color else "allowed",
                severity=event.get("severity"),
            )
        )

    timeline = (payload.get("material") or {}).get("timeline", {}) or {}
    transitions = timeline.get("transitions", []) or []
    max_abs_balance = max(
        (abs(int(event.get("balance_after", 0) or 0)) for event in transitions),
        default=0,
    )
    material = MaterialInput(
        total_captures=int(timeline.get("total_captures", 0) or 0),
        exchanges=int(timeline.get("exchanges", 0) or 0),
        promotions=len(timeline.get("promotions", []) or []),
        final_balance=int(timeline.get("final_balance", 0) or 0),
        imbalance_reached=bool(transitions) and max_abs_balance > 0,
        max_abs_balance=max_abs_balance,
    )

    deviation = (payload.get("opening") or {}).get("deviation", {}) or {}
    opening_deviation = None
    if deviation.get("deviated") and deviation.get("ply") is not None:
        opening_deviation = OpeningDeviationInput(
            ply=int(deviation["ply"]),
            move_number=deviation.get("move_number"),
            played_san=deviation.get("played_san"),
            expected_san=list(deviation.get("expected_continuation_san") or []),
        )

    identification = (payload.get("opening") or {}).get("identification", {}) or {}

    castled, castling_ply = _castling_for(row.get("moves", []) or [], color)
    # Reuse the importer's own time-control parser so the time class is derived
    # from exactly the same reading of the PGN header that the game stored.
    parsed_control = parse_time_control(row.get("time_control") or "")
    initial = parsed_control.initial_seconds if parsed_control else None
    increment = parsed_control.increment_seconds if parsed_control else None

    return PlayerGameInput(
        game_id=row["game_id"],
        date=row.get("date"),
        opponent_name=row.get("opponent") or "Unknown",
        color=color,
        player_rating=row.get("player_rating"),
        opponent_rating=row.get("opponent_rating"),
        result_raw=row.get("result") or "*",
        outcome=outcome_for(row.get("result") or "*", color),
        time_class=classify_time_control(initial, increment),
        time_control_raw=row.get("time_control"),
        eco_code=row.get("eco_code") or identification.get("eco"),
        opening_name=row.get("opening_name") or identification.get("name"),
        opening_family=opening_family(
            row.get("eco_code") or identification.get("eco"),
            row.get("opening_name") or identification.get("name"),
        ),
        move_count=int(row.get("move_count", 0) or 0),
        accuracy=side_accuracy.get("accuracy"),
        average_centipawn_loss=side_accuracy.get("average_centipawn_loss"),
        scored_moves=int(side_accuracy.get("scored_moves", 0) or 0),
        best_moves=int(side_accuracy.get("best_moves", 0) or 0),
        problem_moves=int(side_accuracy.get("problem_moves", 0) or 0),
        blunders=blunders,
        mistakes=mistakes,
        inaccuracies=inaccuracies,
        errors=list(errors_by_ply.values()),
        tactical_events=tactical_events,
        positional_events=positional_events,
        king_safety_events=king_events,
        phase_performance=phase_rows,
        material=material,
        trajectory=_trajectory_for(payload, color),
        opening_deviation=opening_deviation,
        conversion_events=[
            event.get("type", "conversion")
            for event in ((payload.get("conversion") or {}).get("analysis", {}) or {}).get("events", []) or []
            if event.get("side") == color
        ],
        castled=castled,
        castling_ply=castling_ply,
        engine=(payload.get("provenance") or {}).get("engine"),
        engine_version=(payload.get("provenance") or {}).get("engine_version"),
        depth=(payload.get("provenance") or {}).get("depth"),
        analysis_version=(payload.get("provenance") or {}).get("analysis_version") or row.get("analysis_version"),
        report_version=payload.get("report_version") or row.get("report_version"),
    )


def ensure_report_inputs(db: Session, rows: list[dict]) -> int:
    """Materialise stored reports for analyzed games that do not have one yet.

    The profile is built from the structured report, so a game that was analyzed
    but whose report page was never opened would otherwise be invisible to the
    profile. Report assembly is deterministic and engine-free, so repairing that
    is cheap; games with no analysis are left out rather than guessed at. Returns
    how many reports were generated.
    """
    generated = 0
    for row in rows:
        if row.get("report_payload"):
            continue
        payload = ensure_report(db, row["game_id"])
        if payload is None:
            continue
        row["report_payload"] = payload
        row["report_version"] = payload.get("report_version")
        provenance = payload.get("provenance") or {}
        row["analysis_version"] = provenance.get("analysis_version") or row.get("analysis_version")
        row["engine"] = provenance.get("engine")
        row["engine_version"] = provenance.get("engine_version")
        generated += 1
    if generated:
        logger.info("Materialised %d stored report(s) for the player profile", generated)
    return generated


def build_inputs(rows: list[dict], *, policy: PlayerInsightPolicy) -> tuple[list[PlayerGameInput], list[str]]:
    """Map every analyzed row; collect the ids of games that are not analyzed."""
    inputs: list[PlayerGameInput] = []
    excluded: list[str] = []
    for row in rows:
        mapped = game_input_from_row(0, row, policy=policy)
        if mapped is None:
            excluded.append(row["game_id"])
        else:
            inputs.append(mapped)
    return inputs, excluded


def compute_profile_payload(
    db: Session,
    player_id: str,
    *,
    policy: PlayerInsightPolicy | None = None,
    max_games: int | None = None,
) -> tuple[dict, str, list[dict]]:
    """Build (but do not store) the profile payload for a player.

    Returns ``(payload, signature, rows)`` so callers can store or compare.
    """
    effective = policy or DEFAULT_POLICY
    player = get_player(db, player_id)
    rows = player_game_rows(db, player.id)
    if max_games:
        rows = rows[-max_games:]
    ensure_report_inputs(db, rows)
    signature = player_input_signature(rows)
    inputs, excluded = build_inputs(rows, policy=effective)

    profile = build_profile(
        inputs,
        player_id=str(player.id),
        display_name=player.name,
        platform=player.platform,
        platform_username=player.platform_username,
        imported_games=len(rows),
        excluded_game_ids=excluded,
        policy=effective,
    )
    profile.analyzed_games = len(inputs)
    payload = profile.model_dump(mode="json")
    # A profile built from a capped window says so, rather than pretending it saw
    # the whole history.
    if max_games and len(rows) >= max_games:
        payload.setdefault("notes", []).append(
            f"The profile was built from the {max_games} most recent games (configured cap)."
        )
    return payload, signature, rows


def get_player_profile(
    db: Session,
    player_id: str,
    *,
    policy: PlayerInsightPolicy | None = None,
    max_games: int | None = None,
    force_rebuild: bool = False,
) -> dict:
    """Return the cached profile snapshot, rebuilding it when stale.

    Staleness = the input signature changed (new analysis, changed versions) or
    no snapshot exists for the current profile version.
    """
    effective = policy or DEFAULT_POLICY
    player = get_player(db, player_id)
    rows = player_game_rows(db, player.id)
    if max_games:
        rows = rows[-max_games:]
    ensure_report_inputs(db, rows)
    current_signature = player_input_signature(rows)

    if not force_rebuild:
        stored = get_player_profile_record(db, player.id, profile_version=PROFILE_VERSION)
        # Two independent things make a snapshot stale: its *inputs* (new analysis,
        # new versions) and its *methodology* (the same inputs now derive numbers
        # differently). Only checking the signature would serve a reading produced
        # by the previous methodology beside code that no longer agrees with it.
        current_methodology = stored is not None and stored.methodology_version == METHODOLOGY_VERSION
        if stored is not None and current_methodology and stored.source_signature == current_signature:
            payload = dict(stored.payload or {})
            payload["cache"] = {
                "hit": True,
                "generated_at": stored.generated_at.isoformat() if stored.generated_at else None,
                "updated_at": stored.updated_at.isoformat() if stored.updated_at else None,
                "signature": stored.source_signature,
            }
            return payload

    payload, signature, _ = compute_profile_payload(
        db, player_id, policy=effective, max_games=max_games
    )
    try:
        save_player_profile(
            db,
            player_id=player.id,
            profile_version=PROFILE_VERSION,
            methodology_version=METHODOLOGY_VERSION,
            feature_version=payload.get("feature_version", ""),
            source_signature=signature,
            imported_games=payload.get("imported_games", 0),
            analyzed_games=payload.get("analyzed_games", 0),
            coverage=payload.get("coverage", "insufficient"),
            payload=payload,
            generated_at=_generated_at(payload.get("generated_at")),
        )
    except Exception as exc:  # noqa: BLE001 — a cache write must not break a read
        logger.warning("Could not persist player profile for %s: %s", player_id, exc)
    payload["cache"] = {"hit": False, "signature": signature}
    return payload


def rebuild_player_profile(
    db: Session,
    player_id: str,
    *,
    policy: PlayerInsightPolicy | None = None,
    max_games: int | None = None,
) -> dict:
    """Force a full rebuild (debugging and methodology changes)."""
    return get_player_profile(
        db, player_id, policy=policy, max_games=max_games, force_rebuild=True
    )


__all__ = [
    "ADVANTAGE_BANDS",
    "build_inputs",
    "ensure_report_inputs",
    "compute_profile_payload",
    "game_input_from_row",
    "get_player_profile",
    "outcome_for",
    "policy_from_settings",
    "rebuild_player_profile",
]
