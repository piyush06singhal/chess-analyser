"""Provider seam: how the agent core reaches Caissa data without depending on the API.

The agent package must not import the API application. If it did, the tool layer
would inherit the API's database session handling, its request objects and its
authorization decisions, and none of it could be tested without bootsrapping the
whole service. So the core declares **narrow callables** and the API supplies
them, already scoped to the authenticated request.

Every provider follows the same shape:

* it takes explicit identifiers (never a session, never a context object),
* it returns a plain ``dict`` on success,
* it returns ``None`` when the thing genuinely does not exist,
* it raises an :class:`argus.shared.errors.ArgusError` when it cannot answer.

That contract is what lets the same tools be driven by the real API in production
and by fixtures in tests, with no branching between the two.

Providers are optional. A missing provider means the tool is *declared but
unavailable*, with a reason — the honest state — rather than a silently broken
one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from argus.analysis.engine.base import ChessEngine

#: A provider returns a payload dict, or ``None`` when nothing exists.
Provider = Callable[..., dict[str, Any] | None]


@dataclass
class AgentProviders:
    """Everything the tools may read, injected by the API layer.

    Each field is optional so a partially-wired deployment degrades honestly
    instead of failing to start.
    """

    # --- game data ----------------------------------------------------------
    game_lookup: Provider | None = None
    #: (game_id, ply) → the stored move analysis for that ply.
    move_analysis: Provider | None = None
    #: (game_id) → the structured GameReport (materialised on demand upstream).
    game_report: Provider | None = None
    #: (game_id) → the deterministic game summary.
    game_summary: Provider | None = None
    #: (game_id) → engine-confirmed critical moments.
    critical_moments: Provider | None = None
    #: (game_id) → stored move rows (used to locate a move by ply or SAN).
    game_moves: Provider | None = None

    # --- player data --------------------------------------------------------
    player_profile: Provider | None = None
    player_insights: Provider | None = None
    player_statistics: Provider | None = None
    player_evidence: Provider | None = None

    # --- prediction ---------------------------------------------------------
    #: (task_name, features) → a served prediction, or ``None`` when no
    #: production model exists for the task.
    prediction: Provider | None = None
    #: (task_name) → availability facts (available, reason, requirements).
    prediction_status: Provider | None = None

    # --- training (Phase 8) -------------------------------------------------
    #: (player_id) → evidenced training recommendations (what to practise).
    training_recommendations: Provider | None = None
    #: (player_id, ...) → the player's exercise library (puzzle view, no solution).
    training_library: Provider | None = None
    #: (position_id, player_id, reveal) → one exercise; ``reveal`` includes the
    #: engine-verified solution and its line.
    training_position: Provider | None = None
    #: (player_id) → measured progress with sample sizes.
    training_progress: Provider | None = None
    #: (player_id) → exercises whose spaced-repetition review is due.
    training_review_queue: Provider | None = None
    #: (position_id, player_id, submitted_uci) → grade one attempt WITHOUT storing it.
    training_evaluate: Provider | None = None

    # --- opponent intelligence (Phase 9) ------------------------------------
    #: (player_id) → the opponent profile (identity, history, repertoire, stats).
    opponent_profile: Provider | None = None
    #: (player_id, limit, offset) → the opponent's game history.
    opponent_games: Provider | None = None
    #: (player_id, color, recent) → the opponent's repertoire as a colour.
    opponent_repertoire: Provider | None = None
    #: (player_id, fen) → how the opponent answered one position.
    opponent_position_responses: Provider | None = None
    #: (player_id) → the opponent's measured, evidence-gated tendencies.
    opponent_tendencies: Provider | None = None
    #: (player_id, color) → the opponent's performance by phase.
    opponent_phase_statistics: Provider | None = None
    #: (player_id, color) → the composed preparation report.
    opponent_preparation_report: Provider | None = None
    #: (player_id, opponent_player_id, min_occurrences, max_exercises) → generate
    #: preparation exercises owned by the player, built from the opponent's games.
    opponent_preparation: Provider | None = None

    # --- decision intelligence (Phase 10) -----------------------------------
    #: (fen, game_id, ply, moves, include_engine_top, depth) → candidate comparison.
    scenario_compare_moves: Provider | None = None
    #: (fen_a, fen_b, depth) → engine axis and structural axis, kept separate.
    scenario_compare_positions: Provider | None = None
    #: (fen, game_id, ply, alternative_move, actual_move, scenario_type, plies_ahead,
    #: depth) → one counterfactual branch, or an honest refusal.
    scenario_counterfactual: Provider | None = None
    #: (fen, game_id, ply, move, depth) → the measured case for and against a move.
    scenario_why_not: Provider | None = None
    #: (fen, game_id, ply, move, actual_move, plies_ahead, depth) → a hypothesis.
    scenario_what_if: Provider | None = None
    #: (game_id, limit) → where the game could have gone differently, engine-free.
    scenario_explorer: Provider | None = None
    #: (fen, game_id, ply, move, player_id, depth) → store a counterfactual as an
    #: exercise, or refuse when the move cannot be an answer key.
    scenario_training: Provider | None = None
    #: (fen, game_id, ply, opponent_player_id, depth) → observed opponent responses
    #: beside the engine's recommendations, kept apart.
    scenario_opponent_response: Provider | None = None

    # --- live games (Phase 12) ----------------------------------------------
    #: (live_game_id) → the live game's full payload (board, seats, clocks,
    #: status, permissions, moves), or ``None`` when the caller may not read it.
    live_game: Provider | None = None
    #: (live_game_id, limit) → the moves played so far, in order.
    live_history: Provider | None = None
    #: (live_game_id, question) → what the in-game coach may say, including the
    #: fair-play refusal for a competitive game.
    live_coach_state: Provider | None = None

    # --- intelligence graph (Phase 13) --------------------------------------
    #: (game_id, player_id, opening, limit) → games connected in the graph.
    graph_related_games: Provider | None = None
    #: (fen, minimum_similarity, limit) → stored positions, with the level each holds.
    graph_related_positions: Provider | None = None
    #: (player_id, pattern_type, limit) → the player's stored patterns with samples.
    graph_player_patterns: Provider | None = None
    #: (pattern_id, limit) → the games/positions/attempts behind one pattern.
    graph_pattern_evidence: Provider | None = None
    #: (player_id, pattern_id, limit) → training attempts and their outcomes.
    graph_training_history: Provider | None = None
    #: (opponent_id, fen, limit) → an opponent's repertoire and recorded responses.
    graph_opponent_connections: Provider | None = None
    #: (opening, player_id, limit) → games and players connected to an opening.
    graph_opening_connections: Provider | None = None
    #: (fen, limit) → sourced concepts the position exhibits.
    graph_knowledge_for_position: Provider | None = None
    #: (node_type, node_key, limit) → the evidence behind a graph node.
    graph_trace_evidence: Provider | None = None

    # --- engine -------------------------------------------------------------
    engine: ChessEngine | None = None

    # --- misc ---------------------------------------------------------------
    #: Free-form capability notes surfaced to the agent when a tool is missing.
    unavailable_reasons: dict[str, str] = field(default_factory=dict)

    def reason_for(self, capability: str, default: str) -> str:
        return self.unavailable_reasons.get(capability, default)


__all__ = ["AgentProviders", "Provider"]
