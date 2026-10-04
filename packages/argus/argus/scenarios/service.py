"""The decision-intelligence service: one door for every counterfactual request.

Everything the API and the agent ask for goes through here, so there is exactly
one place that decides:

* **whether the engine is available at all** — and returns ``unavailable`` when it
  is not, instead of a number nobody computed;
* **how much engine work a request may trigger** — depth, MultiPV width and
  continuation length are clamped in :mod:`argus.scenarios.policy`;
* **what gets cached** — a comparison keyed by the positions *and* the search
  configuration, because a result from a different depth is a different result;
* **whether a prediction may be attached** — only a production model may answer,
  and the refusal is carried in the response.

The service holds no database session and performs no persistence. It returns
value objects; the API layer stores them. That keeps the chess logic testable
without a database and keeps storage concerns out of the analysis.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from argus.analysis.engine.base import AnalyzedPosition, ChessEngine, MoveComparison
from argus.scenarios import metrics
from argus.scenarios.counterfactual import CounterfactualAnalyzer
from argus.scenarios.explorer import explore
from argus.scenarios.models import (
    CandidateComparison,
    ExplanationBundle,
    PositionComparison,
    ScenarioBranch,
    ScenarioOutcome,
    ScenarioType,
    TurningPointExplorer,
)
from argus.scenarios.policy import (
    DECISION_METHODOLOGY_VERSION,
    scenario_applicability,
    scenario_policy,
)
from argus.scenarios.positions import PositionComparisonService, position_facts
from argus.scenarios.whatif import (
    build_comparison,
    explanation_from_branch,
    legal_move_refusal,
    what_if_analysis,
    why_not_analysis,
)
from argus.shared.errors import (
    ArgusError,
    EngineError,
    EngineUnavailableError,
    InvalidFenError,
    InvalidMoveError,
)
from argus.shared.logging import get_logger

logger = get_logger(__name__)

#: Predictor method per task, matching :class:`argus.ml.service.PredictionService`.
_PREDICTORS = {
    "game_outcome": "predict_game_outcome",
    "position_outcome": "predict_position_outcome",
    "position_difficulty": "predict_position_difficulty",
    "move_error_risk": "predict_error_risk",
}


class ScenarioResultCache:
    """A small bounded cache with a time-to-live, for engine-derived results.

    Bounded and expiring on purpose: counterfactual results are cheap to recompute
    and expensive to keep forever, and a stale entry served after the engine has
    changed would be a wrong answer presented as a right one.
    """

    def __init__(self, max_entries: int = 256, ttl_seconds: float = 900.0) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl = max(0.0, ttl_seconds)
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if self._ttl and (time.monotonic() - stored_at) > self._ttl:
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: object) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": None if not total else round(self.hits / total, 4),
            }


class _TimedEngine(ChessEngine):
    """Records how long the engine actually spends searching.

    Wrapping at this boundary is what makes ``engine_analysis_time_ms`` a real
    measurement: it counts the searches the engine performed, not the wall time of
    the request that happened to trigger them.
    """

    def __init__(self, engine: ChessEngine, registry: metrics.MetricsRegistry) -> None:
        self._engine = engine
        self._registry = registry

    def info(self) -> dict:
        return self._engine.info()

    def _timed(self, fn, *args, **kwargs):  # noqa: ANN001, ANN202
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._registry.observe_ms(
                "engine_analysis_time_ms", (time.perf_counter() - started) * 1000
            )

    def analyze_position(
        self,
        fen: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> AnalyzedPosition:
        return self._timed(
            self._engine.analyze_position,
            fen,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
        )

    def compare_moves(
        self,
        fen: str,
        moves: list[str],
        *,
        depth: int | None = None,
        movetime_ms: int | None = None,
    ) -> list[MoveComparison]:
        return self._timed(
            self._engine.compare_moves, fen, moves, depth=depth, movetime_ms=movetime_ms
        )

    def close(self) -> None:
        self._engine.close()


class ScenarioService:
    """Counterfactual analysis, comparison and prediction attachment."""

    def __init__(
        self,
        engine: ChessEngine | None = None,
        *,
        prediction_service: object | None = None,
        cache_entries: int = 256,
        cache_ttl_seconds: float = 900.0,
        registry: metrics.MetricsRegistry | None = None,
    ) -> None:
        self.engine = engine
        self.prediction_service = prediction_service
        self.metrics = registry or metrics.REGISTRY
        # One timed wrapper per service, so the engine time reported is this
        # service's own work. The comparison service gets the wrapper too, so a
        # position comparison's search is counted here and nowhere else.
        self._timed_engine: ChessEngine | None = (
            _TimedEngine(engine, self.metrics) if engine is not None else None
        )
        self.comparisons = PositionComparisonService(self._timed_engine or engine)
        self.cache = ScenarioResultCache(cache_entries, cache_ttl_seconds)

    # --- capability ---------------------------------------------------------

    def engine_available(self) -> bool:
        if self.engine is None:
            return False
        try:
            return bool(self.engine.info().get("available", True))
        except ArgusError:  # pragma: no cover - defensive: an engine that cannot report
            return False

    def engine_version(self) -> str | None:
        if self.engine is None:
            return None
        try:
            return self.engine.info().get("version")
        except ArgusError:  # pragma: no cover
            return None

    def meta(self) -> dict:
        """What this deployment can and cannot do right now."""
        available = self.engine_available()
        return {
            "methodology_version": DECISION_METHODOLOGY_VERSION,
            "engine": {
                "available": available,
                "version": self.engine_version(),
                "authoritative": True,
                "note": "Stockfish is the only source of chess calculation in Caissa.",
            },
            "scenario_types": [item.value for item in ScenarioType],
            "capabilities": {
                "compare_positions": True,
                "compare_candidate_moves": available,
                "counterfactual_branch": available,
                "why_not_this_move": available,
                "what_if": available,
                "turning_point_explorer": True,
            },
            "metrics": {**self.metrics.snapshot(), "cache": self.cache.stats()},
            "limitations": [
                "A counterfactual is an analysis, not a prediction: it says what the "
                "engine sees, not what would have happened at the board.",
                "Scores from a separate search of a resulting position are marked as such "
                "and are not comparable to a same-search score.",
            ],
            "cache": self.cache.stats(),
        }

    # --- operations ---------------------------------------------------------

    def _cache_key(self, *parts: object) -> str:
        return "|".join(str(part) for part in parts)

    def compare_positions(
        self,
        fen_a: str,
        fen_b: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> PositionComparison:
        """Compare two positions. Structural facts need no engine; the engine axis
        is marked unavailable when there is no engine to produce it."""
        self.metrics.incr("position_comparison_requests")
        key = self._cache_key("cmp", fen_a, fen_b, depth, multipv, movetime_ms, self.engine_version())
        cached = self.cache.get(key)
        if isinstance(cached, PositionComparison):
            return cached.model_copy(deep=True)
        service = self.comparisons if self.engine_available() else PositionComparisonService(None)
        started = time.perf_counter()
        comparison = service.compare(
            fen_a, fen_b, depth=depth, multipv=multipv, movetime_ms=movetime_ms
        )
        self.metrics.observe_ms("scenario_generation_time_ms", (time.perf_counter() - started) * 1000)
        self.cache.put(key, comparison)
        return comparison.model_copy(deep=True)

    def compare_moves(
        self,
        fen: str,
        moves: list[str],
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        played_move_uci: str | None = None,
        include_top: int = 0,
    ) -> CandidateComparison:
        """Compare candidate moves in one position.

        Raises:
            EngineUnavailableError: when there is no engine — a candidate comparison
                is engine work by definition, so it cannot be approximated.
        """
        engine = self._require_engine()
        self.metrics.incr("comparison_requests")
        key = self._cache_key(
            "mv", fen, ",".join(moves), depth, multipv, movetime_ms, played_move_uci, include_top
        )
        cached = self.cache.get(key)
        if isinstance(cached, CandidateComparison):
            return cached.model_copy(deep=True)
        started = time.perf_counter()
        comparison = build_comparison(
            engine,
            fen,
            moves,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
            played_move_uci=played_move_uci,
            include_top=include_top,
        )
        self.metrics.observe_ms("scenario_generation_time_ms", (time.perf_counter() - started) * 1000)
        self.cache.put(key, comparison)
        return comparison.model_copy(deep=True)

    def counterfactual(
        self,
        fen: str,
        alternative_move: str,
        *,
        actual_move: str | None = None,
        scenario_type: ScenarioType = ScenarioType.COUNTERFACTUAL_MOVE,
        plies_ahead: int | None = None,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
        opponent_historical: dict | None = None,
    ) -> ScenarioOutcome:
        """Build one branch. Illegal moves and missing engines are honest results.

        The scenario *type* is behaviour here, not a label: it decides the search
        defaults, it can refuse the question outright when the position cannot
        support it (an opening deviation outside the opening, an
        opponent-response without the opponent's games), and anything it needs
        beyond the position travels in the branch's ``type_context``.
        """
        engine = self._require_engine()
        self.metrics.incr("counterfactual_requests")
        # Accept the enum or its value: the API layer validates against the same
        # closed set, and a string that reaches here must still key the cache the
        # same way an enum does.
        if not isinstance(scenario_type, ScenarioType):
            scenario_type = ScenarioType(scenario_type)
        policy = scenario_policy(scenario_type)
        source_phase = position_facts(fen).phase
        applicable, reason = scenario_applicability(
            scenario_type,
            source_phase=source_phase,
            has_external_context=opponent_historical is not None,
        )
        if not applicable:
            self.metrics.incr("refusals_scenario_type")
            return ScenarioOutcome(
                status="unavailable",
                message=reason,
                explanation=ExplanationBundle(
                    question=policy.label,
                    facts=[reason] if reason else [],
                    insufficient=True,
                    unavailable_reason=reason,
                ),
                methodology_version=DECISION_METHODOLOGY_VERSION,
            )
        # The type's defaults apply only where the caller left a choice. A caller
        # asking for more plies still gets them, within the clamps.
        if plies_ahead is None:
            plies_ahead = policy.plies_ahead
        if multipv is None:
            multipv = policy.multipv
        key = self._cache_key(
            "cf", fen, alternative_move, actual_move, scenario_type.value, plies_ahead, depth, multipv
        )
        cached = self.cache.get(key)
        if isinstance(cached, ScenarioBranch):
            return ScenarioOutcome(
                status="ok",
                branch=cached.model_copy(deep=True),
                explanation=explanation_from_branch(
                    cached, question="what if this move had been played?"
                ),
                methodology_version=DECISION_METHODOLOGY_VERSION,
            )
        analyzer = CounterfactualAnalyzer(engine, self.comparisons)
        started = time.perf_counter()
        branch = analyzer.branch(
            fen,
            alternative_move,
            actual_move=actual_move,
            scenario_type=scenario_type,
            plies_ahead=plies_ahead,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
        )
        self.metrics.observe_ms("scenario_generation_time_ms", (time.perf_counter() - started) * 1000)
        # Types whose question is about what the move *leads to* are checked
        # against the resulting position, once it is actually known.
        resulting_phase = branch.comparison.facts_b.phase if branch.comparison and branch.comparison.facts_b else None
        if policy.resulting_phase and resulting_phase != policy.resulting_phase:
            self.metrics.incr("refusals_scenario_type")
            message = (
                f"A {policy.label.lower()} needs the resulting position to be an "
                f"{policy.resulting_phase}; this move leads to "
                f"{resulting_phase or 'a position of unknown phase'}."
            )
            return ScenarioOutcome(
                status="unavailable",
                message=message,
                explanation=ExplanationBundle(
                    question=policy.label,
                    facts=[message],
                    insufficient=True,
                    unavailable_reason=message,
                ),
                methodology_version=DECISION_METHODOLOGY_VERSION,
            )
        branch.type_context = {
            "scenario_type": scenario_type.value,
            "label": policy.label,
            "source_phase": source_phase,
            "resulting_phase": resulting_phase,
            "search_defaults": {
                "plies_ahead": policy.plies_ahead,
                "multipv": policy.multipv,
            },
        }
        if opponent_historical is not None:
            # Historical replies are stored data, kept separate from the engine's
            # line: the product must never present one as the other.
            branch.type_context["opponent_historical"] = opponent_historical
            branch.type_context["historical_source"] = "stored games of this opponent"
        self.cache.put(key, branch)
        return ScenarioOutcome(
            status="ok",
            branch=branch,
            explanation=explanation_from_branch(
                branch, question=policy.description
            ),
            methodology_version=DECISION_METHODOLOGY_VERSION,
        )

    def explore_game(
        self,
        *,
        game_id: str,
        rows: list[dict],
        criticals: list[dict] | None = None,
        moves_total: int | None = None,
        limit: int | None = None,
        analysis_version: str | None = None,
    ) -> TurningPointExplorer:
        """List a game's branchable moments from stored analysis (no engine call)."""
        self.metrics.incr("explorer_requests")
        kwargs: dict = {"game_id": game_id, "rows": rows, "criticals": criticals}
        if moves_total is not None:
            kwargs["moves_total"] = moves_total
        if analysis_version is not None:
            kwargs["analysis_version"] = analysis_version
        if limit is not None:
            kwargs["limit"] = limit
        return explore(**kwargs)

    # --- framings -----------------------------------------------------------

    def why_not(
        self,
        fen: str,
        move: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> dict:
        """Everything measured about a move, plus the engine's better options."""
        self._require_engine()
        try:
            comparison = self.compare_moves(
                fen,
                [move],
                depth=depth,
                multipv=multipv,
                movetime_ms=movetime_ms,
                include_top=4,
            )
        except InvalidMoveError as exc:
            return legal_move_refusal(move, reason=exc.message)
        target = next(
            (
                entry
                for entry in comparison.candidates
                if entry.is_played_move is False and entry.uci
            ),
            None,
        )
        requested_uci = self._resolve_uci(fen, move, target)
        return why_not_analysis(comparison, move_uci=requested_uci, depth=depth)

    def what_if(
        self,
        fen: str,
        move: str,
        *,
        actual_move: str | None = None,
        plies_ahead: int | None = None,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> dict:
        """Analyse one alternative move against the move that was actually played."""
        outcome = self.counterfactual(
            fen,
            move,
            actual_move=actual_move,
            scenario_type=ScenarioType.USER_HYPOTHESIS,
            plies_ahead=plies_ahead,
            depth=depth,
            multipv=multipv,
            movetime_ms=movetime_ms,
        )
        if outcome.branch is None:  # pragma: no cover - counterfactual only refuses by raising
            return legal_move_refusal(move, reason=outcome.message)
        return what_if_analysis(outcome.branch)

    def counterfactual_safely(self, fen: str, move: str, **kwargs) -> ScenarioOutcome:
        """A counterfactual that converts refusals into results instead of errors.

        Used by the API and the agent, where "that move is not legal here" is an
        answer to give the user, not an exception to propagate.
        """
        try:
            return self.counterfactual(fen, move, **kwargs)
        except InvalidMoveError as exc:
            self.metrics.incr("refusals_illegal_move")
            return ScenarioOutcome(
                status="illegal_move",
                message=exc.message,
                explanation=legal_move_refusal(move, reason=exc.message)["explanation"],
            )
        except InvalidFenError as exc:
            self.metrics.incr("refusals_unavailable")
            return ScenarioOutcome(status="invalid_position", message=exc.message)
        except EngineError as exc:
            self.metrics.incr("refusals_unavailable")
            return ScenarioOutcome(status="unavailable", message=exc.message)

    # --- prediction ---------------------------------------------------------

    def attach_prediction(self, task: str, rows: list[dict]) -> dict:
        """Attach a prediction **only** when a production model can serve one.

        When no model is production-approved, this returns
        ``available: false`` with the reason — the expected answer for a phase in
        which no model has passed its gate. There is no heuristic, and no
        fallback: a probability that was not produced by a validated model is not
        reported at all.
        """
        self.metrics.incr("prediction_requests")
        if task not in _PREDICTORS:
            self.metrics.incr("prediction_rejections")
            return {
                "available": False,
                "task": task,
                "reason": f"Unknown prediction task '{task}'.",
                "detail": "The task is not declared in the prediction registry.",
            }
        if self.prediction_service is None:
            self.metrics.incr("prediction_rejections")
            return {
                "available": False,
                "task": task,
                "reason": "No prediction service is configured in this deployment.",
                "detail": "Predictions require a model registry; none is wired here.",
            }
        method = getattr(self.prediction_service, _PREDICTORS[task], None)
        if method is None:  # pragma: no cover - defensive
            return {
                "available": False,
                "task": task,
                "reason": "The prediction service does not implement this task.",
            }
        if not rows:
            self.metrics.incr("prediction_rejections")
            unavailable = self.prediction_service.unavailable(task)
            payload = unavailable.model_dump(mode="json")
            payload["detail"] = (
                "No feature rows were supplied, so there is nothing to predict. Caissa does "
                "not fill in default features."
            )
            return payload
        result = method(rows)
        payload = result.model_dump(mode="json")
        if not payload.get("available", False):
            self.metrics.incr("prediction_rejections")
        elif payload.get("model_id"):
            # Which model answered, so a served prediction is attributable.
            self.metrics.record_model(str(payload["model_id"]))
        return payload

    # --- helpers ------------------------------------------------------------

    def _require_engine(self) -> ChessEngine:
        if not self.engine_available():
            raise EngineUnavailableError(
                "No chess engine is available, so no counterfactual analysis can be produced",
                details={"engine_configured": self.engine is not None},
            )
        return self._timed_engine or self.engine  # type: ignore[return-value]

    @staticmethod
    def _resolve_uci(fen: str, move: str, fallback=None) -> str:
        """The UCI of a requested move, so it can be looked up in a comparison."""
        import chess

        try:
            board = chess.Board(fen)
        except ValueError:
            return move
        try:
            parsed = chess.Move.from_uci(move)
            if parsed in board.legal_moves:
                return parsed.uci()
        except ValueError:
            pass
        try:
            return board.parse_san(move).uci()
        except ValueError:
            return fallback.uci if fallback is not None else move


def scenario_payload(
    branch: ScenarioBranch,
    *,
    game_id: str | None = None,
    ply: int | None = None,
    owner_player_id: int | None = None,
) -> dict:
    """The storage payload for a branch: immutable, reproducible, self-describing."""
    return {
        "scenario_type": branch.scenario_type.value,
        "source_fen": branch.source_fen,
        "resulting_fen": (
            branch.alternative_continuation[-1].fen_after
            if branch.alternative_continuation
            else None
        ),
        "game_id": game_id,
        "ply": ply,
        "owner_player_id": owner_player_id,
        "methodology_version": DECISION_METHODOLOGY_VERSION,
        "engine_config": branch.engine_config.model_dump(mode="json"),
        "branch": branch.model_dump(mode="json"),
        "evidence": [ref.model_dump(mode="json") for ref in branch.evidence],
    }


__all__ = ["ScenarioResultCache", "ScenarioService", "scenario_payload"]
