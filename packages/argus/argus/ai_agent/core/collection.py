"""Turning tool results into evidence.

Every tool returns a payload; the agent must reason over *evidence*. This module is
the single place that converts one into the other, which buys three things:

* **Provenance is assigned once.** A stored move analysis is an engine fact, board
  facts are Caissa-derived features, and an opening lookup is knowledge. Getting that
  classification wrong is how a derived number starts sounding like an engine
  measurement, so it is decided here rather than at each call site.
* **Summaries are stable.** The model reads the summary; the UI reads the summary;
  the validator reads the numbers in ``data``. One rendering, consistently phrased.
* **Payloads are bounded.** Only the fields that matter are carried forward, so the
  prompt does not grow with the size of the API response.
"""

from __future__ import annotations

from typing import Any

from argus.intelligence.base import Certainty, EvidenceSource

from argus.ai_agent.core.evidence import EvidenceItem, EvidenceKind

#: Default provenance per tool. Anything not listed stays a derived feature.
_TOOL_SOURCE: dict[str, EvidenceSource] = {
    "analyze_position": EvidenceSource.ENGINE_FACT,
    "analyze_position_multipv": EvidenceSource.ENGINE_FACT,
    "compare_moves": EvidenceSource.ENGINE_FACT,
    "get_move_analysis": EvidenceSource.ENGINE_FACT,
    "get_critical_moments": EvidenceSource.ENGINE_FACT,
    "inspect_position": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "get_current_position": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "get_opening_information": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "search_chess_knowledge": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "get_validated_prediction": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "get_prediction_status": EvidenceSource.ARGUS_DERIVED_FEATURE,
    "get_training_requirements": EvidenceSource.ARGUS_DERIVED_FEATURE,
}

_TOOL_KIND: dict[str, EvidenceKind] = {
    "analyze_position": EvidenceKind.ENGINE,
    "analyze_position_multipv": EvidenceKind.ENGINE,
    "compare_moves": EvidenceKind.ENGINE,
    "inspect_position": EvidenceKind.POSITION,
    "get_current_position": EvidenceKind.POSITION,
    "get_game": EvidenceKind.GAME,
    "get_game_moves": EvidenceKind.GAME,
    "get_game_summary": EvidenceKind.GAME_ANALYSIS,
    "get_game_analysis": EvidenceKind.GAME_ANALYSIS,
    "get_game_trajectory": EvidenceKind.GAME_ANALYSIS,
    "get_move_analysis": EvidenceKind.MOVE_ANALYSIS,
    "get_critical_moments": EvidenceKind.CRITICAL_MOMENT,
    "get_player_profile": EvidenceKind.PLAYER_PROFILE,
    "get_player_statistics": EvidenceKind.PLAYER_PROFILE,
    "get_player_insights": EvidenceKind.PLAYER_INSIGHT,
    "get_player_evidence": EvidenceKind.PLAYER_EVIDENCE,
    "get_opening_information": EvidenceKind.OPENING,
    "search_chess_knowledge": EvidenceKind.KNOWLEDGE,
    "get_validated_prediction": EvidenceKind.PREDICTION,
    "get_prediction_status": EvidenceKind.PREDICTION,
    "get_training_requirements": EvidenceKind.TRAINING,
    "get_training_recommendations": EvidenceKind.TRAINING,
    "generate_training_position": EvidenceKind.TRAINING,
    "get_review_queue": EvidenceKind.TRAINING,
    "get_training_progress": EvidenceKind.TRAINING,
    "get_training_from_game": EvidenceKind.TRAINING,
    "generate_training_explanation": EvidenceKind.TRAINING,
    "evaluate_training_attempt": EvidenceKind.TRAINING,
}

#: Tools whose output is a list of items, each of which becomes its own evidence.
_EXPANDING: dict[str, tuple[str, str]] = {
    "get_critical_moments": ("moments", "statement"),
    "get_player_insights": ("insights", "statement"),
    "get_player_evidence": ("insights", "id"),
    "search_chess_knowledge": ("matches", "concept"),
}


def kind_for_tool(tool: str) -> EvidenceKind:
    return _TOOL_KIND.get(tool, EvidenceKind.GAME_ANALYSIS)


def source_for_tool(tool: str) -> EvidenceSource:
    return _TOOL_SOURCE.get(tool, EvidenceSource.ARGUS_DERIVED_FEATURE)


def certainty_for(result: dict[str, Any]) -> Certainty:
    """Respect a payload's own certainty when it declares one."""
    declared = str(result.get("certainty") or "").lower()
    if declared == "candidate":
        return Certainty.CANDIDATE
    return Certainty.CONFIRMED


def _context_ids(result: dict[str, Any]) -> tuple[str | None, int | None]:
    game_id = result.get("game_id") or result.get("id")
    ply = result.get("ply")
    if ply is None:
        evidence = result.get("evidence")
        if isinstance(evidence, dict):
            ply = evidence.get("ply")
    resolved_game = str(game_id) if game_id else None
    resolved_ply = int(ply) if isinstance(ply, int) else None
    return resolved_game, resolved_ply


def items_from_outcome(
    tool: str, result: dict[str, Any], *, summary_limit: int = 240
) -> list[EvidenceItem]:
    """Convert one tool payload into evidence items.

    Expanding tools (moments, insights, concept matches) yield one item each, so a
    single moment can be cited on its own — otherwise "the critical moment" would be
    an unsplittable blob and the validator could not check a claim about one ply.
    """
    kind = kind_for_tool(tool)
    source = source_for_tool(tool)
    game_id, ply = _context_ids(result)
    items: list[EvidenceItem] = []

    expand = _EXPANDING.get(tool)
    if expand and isinstance(result.get(expand[0]), list):
        collection_key, text_key = expand
        for entry in result[collection_key]:
            if not isinstance(entry, dict):
                continue
            entry_game, entry_ply = _context_ids(entry)
            text = str(entry.get(text_key) or entry.get("title") or "").strip()
            # An expanded item keeps its own fields. The tool-level keep list
            # describes the *wrapper* payload, so applying it to each entry
            # would drop exactly what the entry has to say (its id, metric,
            # value and sample size) and leave every insight looking empty.
            data = {key: value for key, value in entry.items() if not key.startswith("_")}
            # Critical moments mix perspectives: the swing is the *mover's*, the
            # before/after evaluations are *White's*. Left unlabelled, a model reads
            # "+1.80" as the mover's advantage and reports the game backwards. The
            # statement already names the mover; this names the fields, so the raw
            # numbers cannot be misattributed.
            if tool == "get_critical_moments":
                # The evaluation fields live at the top level of some payloads and
                # nested under ``evidence`` in others, so read both.
                nested = entry.get("evidence")
                nested = nested if isinstance(nested, dict) else {}

                def pick(key: str) -> Any:
                    value = entry.get(key)
                    return nested.get(key) if value is None else value

                before_white = pick("evaluation_before_cp")
                if before_white is None:
                    before_white = nested.get("evaluation_before_white")
                after_white = pick("evaluation_after_cp")
                if after_white is None:
                    after_white = nested.get("evaluation_after_white")
                swing_mover = pick("swing_cp")

                data["perspective"] = (
                    "swing_cp is from the mover's (side's) perspective; the "
                    "before/after evaluations are from White's perspective. Do not mix "
                    "them."
                )
                # Pre-convert the centipawn fields to pawns. A model asked to divide
                # by 100 by hand occasionally divides by 1000 instead, quoting a
                # 1.80-pawn advantage as 0.18 — a wrong number the validator then has
                # to catch. Giving the pawn figure removes the arithmetic entirely.
                data["evaluation_pawns"] = {
                    "before_white": _pawns(before_white),
                    "after_white": _pawns(after_white),
                    "swing_mover": _pawns(swing_mover),
                }
            items.append(
                EvidenceItem(
                    kind=kind,
                    tool=tool,
                    source=source,
                    certainty=certainty_for(entry),
                    summary=(text[:summary_limit] or f"{tool} entry"),
                    data=data,
                    game_id=entry_game or game_id,
                    ply=entry_ply if entry_ply is not None else ply,
                )
            )
        return items

    items.append(
        EvidenceItem(
            kind=kind,
            tool=tool,
            source=source,
            certainty=certainty_for(result),
            summary=summarize(tool, result, limit=summary_limit),
            data=_trim(result, tool),
            game_id=game_id,
            ply=ply,
        )
    )
    return items


def summarize(tool: str, result: dict[str, Any], *, limit: int = 240) -> str:
    """A short factual line describing a tool payload."""
    text = _summarize(tool, result)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summarize(tool: str, result: dict[str, Any]) -> str:  # noqa: C901 — a switchboard
    if tool == "get_move_analysis":
        mover = str(result.get("mover") or "").lower()
        mover_label = "White" if mover == "white" else "Black" if mover == "black" else "the mover"
        # The stored evaluations are from the *mover's* perspective, so the summary
        # names the mover explicitly. Without it a model can read "+1.80 for the
        # mover" and attribute the advantage to the wrong side.
        return (
            f"Move {result.get('move_number')}{'.' if mover == 'white' else '...'}"
            f"{result.get('san')} ({mover_label}) was classified '{result.get('classification')}': "
            f"evaluation {_cp(result.get('eval_before_cp'))} → {_cp(result.get('eval_after_cp'))} "
            f"from {mover_label}'s perspective (centipawn loss {result.get('centipawn_loss')}); "
            f"engine best {result.get('best_move_san')}."
        )
    if tool == "analyze_position":
        lines = result.get("lines") or []
        best = lines[0] if lines else {}
        return (
            f"Stockfish depth {result.get('depth')}: best {best.get('move_san') or result.get('best_move_san')}, "
            f"score {_cp(best.get('score_cp') if best else None)}"
            + (f", mate {best.get('score_mate')}" if best and best.get("score_mate") else "")
            + "."
        )
    if tool == "analyze_position_multipv":
        lines = result.get("lines") or []
        parts = [
            f"{line.get('move_san')} {_cp(line.get('score_cp'))}"
            for line in lines[:4]
            if isinstance(line, dict)
        ]
        return (
            f"MultiPV {result.get('multipv')} at depth {result.get('depth')} for "
            f"{result.get('fen', '')[:24]}…: " + ", ".join(parts)
        )
    if tool == "compare_moves":
        comparisons = result.get("comparisons") or []
        parts = [
            f"{item.get('played_move_san') or item.get('played_move_uci')} costs "
            f"{item.get('centipawn_loss')}cp vs {item.get('best_move_san')}"
            for item in comparisons[:4]
            if isinstance(item, dict)
        ]
        return "Move comparison: " + "; ".join(parts)
    if tool == "get_game":
        return (
            f"{result.get('white_player')} ({result.get('white_rating')}) vs "
            f"{result.get('black_player')} ({result.get('black_rating')}), result "
            f"{result.get('result')}, {result.get('date')}, status "
            f"{result.get('analysis_status')}, {result.get('move_count')} plies."
        )
    if tool == "get_game_summary":
        return (
            f"{result.get('white_player')} vs {result.get('black_player')}: "
            f"{result.get('result_label') or result.get('result')}, opening "
            f"{result.get('opening_name') or 'unknown'} ({result.get('eco_code') or 'no ECO'}), "
            f"{result.get('moves')} moves."
        )
    if tool == "get_game_moves":
        return f"{result.get('move_count')} stored ply rows."
    if tool == "get_game_analysis":
        report = result.get("report") or {}
        return (
            f"GameReport v{result.get('report_version')} with sections: "
            + ", ".join(sorted(report)[:12])
            + "."
        )
    if tool == "get_game_trajectory":
        return f"{len(result.get('trajectory') or [])} trajectory point(s)."
    if tool == "get_critical_moments":
        return (
            f"{result.get('total_moments')} critical moment(s); showing "
            f"{result.get('returned')}, ordered by absolute evaluation swing."
        )
    if tool == "get_player_profile":
        return (
            f"Player {result.get('display_name')} (id {result.get('player_id')}): "
            f"{result.get('analyzed_games')} analysed game(s), coverage "
            f"{result.get('coverage')}, sufficient_data={result.get('sufficient_data')}."
        )
    if tool == "get_player_statistics":
        games = result.get("games") or {}
        return (
            f"{result.get('analyzed_games')} analysed game(s); accuracy "
            f"{games.get('accuracy')}, average centipawn loss {games.get('centipawn_loss')}."
        )
    if tool == "get_player_insights":
        return (
            f"{result.get('total_insights')} insight(s) at coverage {result.get('coverage')}"
            f" (sufficient_data={result.get('sufficient_data')})."
        )
    if tool == "get_player_evidence":
        return f"Evidence for {len(result.get('insights') or [])} insight(s)."
    if tool in ("inspect_position", "get_current_position"):
        return str(result.get("summary") or "board facts")
    if tool == "get_opening_information":
        if result.get("matched"):
            return (
                f"Opening: {result.get('name')} ({result.get('eco')}), matched "
                f"{result.get('matched_plies')} plies from base v{result.get('base_version')}."
            )
        return (
            f"No entry in the Caissa opening base matches this sequence (base "
            f"v{result.get('base_version')}): the opening is unknown."
        )
    if tool == "search_chess_knowledge":
        if result.get("found"):
            if "concept" in result:
                return f"{result.get('concept')} ({result.get('category')}): {result.get('definition')}"
            return f"{len(result.get('matches') or [])} matching concept(s)."
        return "No stored explanation in the Caissa knowledge base for that query."
    if tool == "get_prediction_status":
        if "tasks" in result:
            available = [entry["task"] for entry in result["tasks"] if entry.get("available")]
            return (
                f"Prediction availability: {len(available)} task(s) available "
                f"({', '.join(available) if available else 'none'})."
            )
        return f"Task {result.get('task')}: available={result.get('available')} ({result.get('reason')})."
    if tool == "get_validated_prediction":
        probabilities = result.get("probabilities") or {}
        rendered = ", ".join(f"{name} {round(float(value) * 100, 1)}%" for name, value in probabilities.items())
        return (
            f"Validated model {result.get('model_id')} predicts {result.get('prediction')} "
            f"({rendered}); coverage {result.get('data_coverage', {}).get('label')}."
        )
    if tool == "get_training_requirements":
        return f"Training positions arrive in {result.get('available_from')}."
    if tool == "list_chess_concepts":
        return f"{len(result.get('concepts') or [])} stored concept(s), v{result.get('version')}."
    return f"{tool} returned {len(result)} field(s)."


def _cp(value: Any) -> str:
    if value is None:
        return "unavailable"
    try:
        centipawns = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{centipawns / 100:+.2f}"


def _pawns(value: Any) -> str | None:
    """A centipawn value rendered in pawns (``180`` → ``"+1.80"``).

    ``None`` stays ``None``: an unevaluated ply has no pawn figure, and inventing
    ``"+0.00"`` would report a real evaluation that does not exist.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return f"{float(value) / 100:+.2f}"
    except (TypeError, ValueError):
        return None


#: Fields kept in the evidence payload, per tool. Everything else is dropped:
#: the prompt and the validator only need these, and a full GameReport would
#: dominate the budget.
_KEEP: dict[str, tuple[str, ...]] = {
    "get_move_analysis": (
        "ply",
        "move_number",
        "mover",
        "san",
        "uci",
        "fen_before",
        "fen_after",
        "eval_before_cp",
        "eval_after_cp",
        "eval_change_cp",
        "centipawn_loss",
        "played_eval_cp",
        "classification",
        "best_move_uci",
        "best_move_san",
        "is_best_move",
        "phase",
        "principal_variation",
        "depth",
    ),
    "analyze_position": (
        "fen",
        "depth",
        "multipv",
        "best_move_uci",
        "best_move_san",
        "lines",
        "is_terminal",
        "terminal_reason",
        "engine",
        "depth_was_clamped",
    ),
    "analyze_position_multipv": (
        "fen",
        "depth",
        "multipv",
        "lines",
        "multipv_was_clamped",
        "depth_was_clamped",
    ),
    "compare_moves": ("comparisons", "resolved_uci", "depth", "depth_was_clamped"),
    "get_critical_moments": (
        "game_id",
        "total_moments",
        "returned",
        "selection",
        "moments",
    ),
    "get_player_profile": (
        "player_id",
        "display_name",
        "coverage",
        "sufficient_data",
        "analyzed_games",
        "imported_games",
        "games",
        "by_color",
        "openings",
        "phases",
        "tactical",
        "trends",
        "sample_note",
        "profile_version",
        "methodology_version",
    ),
    "get_player_insights": (
        "player_id",
        "coverage",
        "sufficient_data",
        "total_insights",
        "insights",
    ),
    "get_opening_information": (
        "matched",
        "name",
        "eco",
        "line_uci",
        "matched_plies",
        "base_version",
        "stored_game_record",
        "note",
    ),
    "get_validated_prediction": (
        "available",
        "task",
        "prediction",
        "probabilities",
        "data_coverage",
        "model_id",
        "model_status",
        "disclaimer",
        "reading_note",
    ),
    "get_prediction_status": ("task", "available", "reason", "tasks", "any_available", "statement"),
    "search_chess_knowledge": (
        "found",
        "concept",
        "category",
        "definition",
        "how_to_spot",
        "typical_mistake",
        "matches",
        "available_concepts",
        "version",
        "note",
    ),
    "inspect_position": ("facts", "summary"),
}


def _trim(result: dict[str, Any], tool: str) -> dict[str, Any]:
    """Keep the fields the agent and validator need, and nothing else."""
    keep = _KEEP.get(tool)
    if keep is None:
        return {key: value for key, value in result.items() if not key.startswith("_")}
    return {key: result[key] for key in keep if key in result}


__all__ = [
    "certainty_for",
    "items_from_outcome",
    "kind_for_tool",
    "source_for_tool",
    "summarize",
]
