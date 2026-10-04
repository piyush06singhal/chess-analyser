"""The game-intelligence service.

``GameIntelligence`` is the single entry point of Phase 4. It consumes the data
the Phase 3 engine pipeline already stored (per-move evaluations, centipawn
loss, classifications, principal variations, critical positions) and produces
the structured report — **without calling Stockfish once**. If a section needs
engine data that is not stored, the section is reported as unavailable instead
of being computed from a second, hidden engine run.

The class is also the tool surface the future AI agent will call:

``get_game_summary``, ``get_game_trajectory``, ``get_critical_moments``,
``get_move_analysis``, ``get_tactical_events``, ``get_positional_events``,
``get_phase_analysis``, ``get_material_timeline``, ``get_accuracy``,
``get_player_game_statistics``

Every accessor returns the same typed, evidence-carrying models the report uses,
so an explanation layer can never see a number it cannot trace back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel

from argus.analysis.phase import GamePhase
from argus.chess_core.models import Color
from argus.intelligence.accuracy import AccuracyAnalysis, analyse_accuracy
from argus.intelligence.advantage import describe_policy
from argus.intelligence.base import (
    REPORT_VERSION,
    Certainty,
    CriticalFact,
    EvidenceSource,
    Finding,
    GameContext,
    Insight,
    IntelligencePolicy,
    MoveFact,
    Recommendation,
    Unavailable,
    color_label,
)
from argus.intelligence.categories import CategoryAnalysis, categorise_errors
from argus.intelligence.conversion import ConversionAnalysis, analyse_conversion
from argus.intelligence.forecast import ResultForecast, build_forecast
from argus.intelligence.king_safety import (
    KingSafetyAnalysis,
    build_king_safety,
    events_by_ply as king_events_by_ply,
)
from argus.intelligence.material import MaterialTimeline, build_material_timeline
from argus.intelligence.openings import OpeningAnalysis, detect_opening
from argus.intelligence.performance import PhasePerformance, build_phase_performance
from argus.intelligence.phases import GamePhaseDetector, PhaseDetection
from argus.intelligence.positional import (
    PositionalAnalysis,
    build_positional_analysis,
    events_by_ply as positional_events_by_ply,
)
from argus.intelligence.report import (
    EVIDENCE_POLICY,
    AccuracyReportSection,
    AnalysisProvenance,
    ConversionReportSection,
    CriticalMomentsSection,
    ErrorCategorySection,
    ForecastReportSection,
    GameReport,
    GameSummarySection,
    KingSafetyReportSection,
    MaterialReportSection,
    PhaseReportSection,
    PhaseTransition,
    PositionalReportSection,
    TacticalReportSection,
    TimelineEntry,
    TrajectoryReportSection,
    build_key_lessons,
    build_summary,
    build_training_recommendations,
)
from argus.intelligence.structure import PawnStructureAnalysis, build_pawn_structure
from argus.intelligence.tactics import TacticalAnalysis, detect_tactical_events, tactics_by_ply
from argus.intelligence.trajectory import GameTrajectory, build_trajectory
from argus.intelligence.turning_points import (
    TurningPointAnalysis,
    TurningPointType,
    detect_turning_points,
)

#: Severity ordering used to rank timeline entries.
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


class AnalysisMeta(BaseModel):
    """Provenance of the stored engine analysis the report is built on."""

    analysis_version: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    depth: int | None = None
    multipv: int | None = None
    movetime_ms: int | None = None
    profile: str | None = None
    positions_analyzed: int = 0


class GameIntelligence:
    """Builds structured chess intelligence from stored engine analysis."""

    def __init__(
        self,
        context: GameContext,
        moves: list[MoveFact],
        *,
        criticals: list[CriticalFact] | None = None,
        analysis: AnalysisMeta | None = None,
        policy: IntelligencePolicy | None = None,
    ) -> None:
        self.context = context
        self.moves = list(moves)
        self.criticals = list(criticals or [])
        self.analysis = analysis or AnalysisMeta()
        self.policy = policy or IntelligencePolicy()

        self._opening: OpeningAnalysis | None = None
        self._material: MaterialTimeline | None = None
        self._structure: PawnStructureAnalysis | None = None
        self._activity = None
        self._phases: dict[int, GamePhase] | None = None
        self._detections: dict[int, PhaseDetection] | None = None
        self._tactical: TacticalAnalysis | None = None
        self._positional: PositionalAnalysis | None = None
        self._king_safety: KingSafetyAnalysis | None = None
        self._trajectory: GameTrajectory | None = None
        self._accuracy: AccuracyAnalysis | None = None
        self._performance: PhasePerformance | None = None
        self._categories: CategoryAnalysis | None = None
        self._conversion: ConversionAnalysis | None = None
        self._turning_points: TurningPointAnalysis | None = None
        self._forecast: ResultForecast | None = None

    # --- lazily computed sections (each one computed at most once) -------------

    @property
    def opening(self) -> OpeningAnalysis:
        if self._opening is None:
            self._opening = detect_opening(self.moves, self.context)
        return self._opening

    @property
    def material(self) -> MaterialTimeline:
        if self._material is None:
            self._material = build_material_timeline(
                self.moves,
                initial_position=self.context.initial_position,
                policy=self.policy.material,
            )
        return self._material

    @property
    def structure(self) -> PawnStructureAnalysis:
        if self._structure is None:
            self._structure = build_pawn_structure(
                self.moves, initial_position=self.context.initial_position
            )
        return self._structure

    def _detect_phases(self) -> tuple[dict[int, GamePhase], dict[int, PhaseDetection]]:
        if self._phases is None or self._detections is None:
            detector = GamePhaseDetector(self.policy.game_phase)
            phase_by_ply: dict[int, GamePhase] = {}
            detections: dict[int, PhaseDetection] = {}
            for fact in self.moves:
                detection = detector.detect_fen(fact.fen_after)
                phase_by_ply[fact.ply] = detection.phase
                detections[fact.ply] = detection
            self._phases, self._detections = phase_by_ply, detections
        return self._phases, self._detections

    @property
    def phase_by_ply(self) -> dict[int, GamePhase]:
        return self._detect_phases()[0]

    @property
    def tactical(self) -> TacticalAnalysis:
        if self._tactical is None:
            self._tactical = detect_tactical_events(self.moves, policy=self.policy.tactics)
        return self._tactical

    @property
    def positional(self) -> PositionalAnalysis:
        if self._positional is None:
            self._positional = build_positional_analysis(
                self.moves,
                initial_position=self.context.initial_position,
                structure=self.structure,
                policy=self.policy.positional,
            )
        return self._positional

    @property
    def king_safety(self) -> KingSafetyAnalysis:
        if self._king_safety is None:
            self._king_safety = build_king_safety(
                self.moves,
                initial_position=self.context.initial_position,
                policy=self.policy.king_safety,
            )
        return self._king_safety

    @property
    def trajectory(self) -> GameTrajectory:
        if self._trajectory is None:
            balances = {snapshot.ply: snapshot.balance for snapshot in self.material.snapshots}
            self._trajectory = build_trajectory(
                self.moves,
                initial_position=self.context.initial_position,
                material_balances=balances,
                policy=self.policy.advantage,
                result=self.context.result,
            )
        return self._trajectory

    @property
    def accuracy(self) -> AccuracyAnalysis:
        if self._accuracy is None:
            self._accuracy = analyse_accuracy(self.moves, policy=self.policy.accuracy)
        return self._accuracy

    @property
    def performance(self) -> PhasePerformance:
        if self._performance is None:
            self._performance = build_phase_performance(
                self.moves,
                phase_by_ply=self.phase_by_ply,
                tactical_events=self.tactical.events,
                policy=self.policy.phase_performance,
            )
        return self._performance

    @property
    def categories(self) -> CategoryAnalysis:
        if self._categories is None:
            self._categories = categorise_errors(
                self.moves,
                tactical_by_ply=tactics_by_ply(self.tactical),
                positional_by_ply=positional_events_by_ply(self.positional),
                king_safety_by_ply=king_events_by_ply(self.king_safety),
                phase_by_ply=self.phase_by_ply,
                opening_plies=self.opening.identification.matched_plies,
            )
        return self._categories

    @property
    def conversion(self) -> ConversionAnalysis:
        if self._conversion is None:
            self._conversion = analyse_conversion(
                self.trajectory,
                result=self.context.result,
                policy=self.policy.conversion,
                move_number_by_ply={fact.ply: fact.move_number for fact in self.moves},
            )
        return self._conversion

    @property
    def turning_points(self) -> TurningPointAnalysis:
        if self._turning_points is None:
            self._turning_points = detect_turning_points(
                self.moves, policy=self.policy.turning_points, conversion=self.conversion
            )
        return self._turning_points

    @property
    def forecast(self) -> ResultForecast:
        """Engine-derived three-way outcome forecast (documented curve, no ML)."""
        if self._forecast is None:
            self._forecast = build_forecast(self.moves)
        return self._forecast

    # --- AI-ready accessors ----------------------------------------------------

    def get_game_summary(self) -> GameSummarySection:
        """Factual summary statements for the game."""
        return build_summary(
            self.context,
            self.moves,
            opening=self.opening,
            performance=self.performance,
            accuracy=self.accuracy,
            material=self.material,
            tactical=self.tactical,
            turning_points=self.turning_points,
            conversion=self.conversion,
        )

    def get_game_trajectory(self) -> TrajectoryReportSection:
        """Evaluation trajectory, advantage bands and analytical states."""
        return self.get_trajectory_section()

    def get_move_analysis(self, ply: int | None = None) -> list[MoveFact] | MoveFact | None:
        """Stored per-move engine analysis (optionally for a single ply)."""
        if ply is None:
            return list(self.moves)
        return next((fact for fact in self.moves if fact.ply == ply), None)

    def get_tactical_events(self) -> TacticalAnalysis:
        """Tactical events, confirmed facts and candidates clearly separated."""
        return self.tactical

    def get_positional_events(self) -> PositionalAnalysis:
        """Positional features and error candidates."""
        return self.positional

    def get_phase_analysis(self) -> PhaseReportSection:
        """Detected phases, transitions and phase-specific performance."""
        return self.get_phase_section()

    def get_material_timeline(self) -> MaterialTimeline:
        """Material snapshots and events, measured from the board."""
        return self.material

    def get_accuracy(self) -> AccuracyAnalysis:
        """Caissa accuracy with its methodology attached."""
        return self.accuracy

    def get_king_safety(self) -> KingSafetyAnalysis:
        """King-safety measurements and structured events."""
        return self.king_safety

    def get_opening_analysis(self) -> OpeningAnalysis:
        """Opening identification and deviation."""
        return self.opening

    def get_turning_points(self) -> TurningPointAnalysis:
        """Turning points with their evidence."""
        return self.turning_points

    def get_conversion_analysis(self) -> ConversionAnalysis:
        """Conversion, slide and comeback observations."""
        return self.conversion

    def get_result_forecast(self) -> ResultForecast:
        """Outcome forecast derived from the engine evaluation, with its curve."""
        return self.forecast

    def get_error_categories(self) -> CategoryAnalysis:
        """Engine-flagged moves grouped by evidence-derived category."""
        return self.categories

    def get_player_game_statistics(self) -> dict[str, Any]:
        """Single-game statistics per side.

        This is deliberately **single-game only**: Phase 5's player intelligence
        must not infer a player profile from one game, so nothing here is
        presented as a tendency, a trend or a rating estimate.
        """
        stats: dict[str, Any] = {"scope": "single_game", "games_considered": 1}
        for color in (Color.WHITE, Color.BLACK):
            facts = [fact for fact in self.moves if fact.mover == color]
            losses = [fact.centipawn_loss for fact in facts if fact.centipawn_loss is not None]
            side_accuracy = self.accuracy.white if color == Color.WHITE else self.accuracy.black
            stats[color.value] = {
                "moves": len(facts),
                "evaluated_moves": len([fact for fact in facts if fact.evaluated]),
                "average_centipawn_loss": round(sum(losses) / len(losses), 2)
                if losses
                else None,
                "accuracy": side_accuracy.accuracy,
                "excluded_decided_moves": side_accuracy.excluded_decided_moves,
                "error_categories": self.categories.by_side_category.get(color.value, {}),
                "phase_statistics": {
                    phase: self.performance.white.get(phase).model_dump()
                    if color == Color.WHITE and self.performance.white.get(phase)
                    else (
                        self.performance.black.get(phase).model_dump()
                        if color == Color.BLACK and self.performance.black.get(phase)
                        else None
                    )
                    for phase in ("opening", "middlegame", "endgame")
                },
                "tactical_events_created": len(
                    [event for event in self.tactical.events if event.side == color]
                ),
                "turning_points_against": len(
                    [point for point in self.turning_points.turning_points if point.side == color]
                ),
                "conversion_events": len(
                    [event for event in self.conversion.events if event.side == color]
                ),
            }
        stats["note"] = (
            "Single-game observations only. A player profile requires multiple games "
            "(Phase 5) and is never inferred from one game."
        )
        return stats

    def available_tools(self) -> dict[str, Callable[..., Any]]:
        """The named tool surface a future AI agent will call (no agent here)."""
        return {
            "get_game_summary": self.get_game_summary,
            "get_game_trajectory": self.get_game_trajectory,
            "get_critical_moments": self.get_critical_moments,
            "get_move_analysis": self.get_move_analysis,
            "get_tactical_events": self.get_tactical_events,
            "get_positional_events": self.get_positional_events,
            "get_phase_analysis": self.get_phase_analysis,
            "get_material_timeline": self.get_material_timeline,
            "get_accuracy": self.get_accuracy,
            "get_result_forecast": self.get_result_forecast,
            "get_player_game_statistics": self.get_player_game_statistics,
        }

    # --- sections --------------------------------------------------------------

    def get_phase_section(self) -> PhaseReportSection:
        detections = self._detect_phases()[1]
        transitions: list[PhaseTransition] = []
        facts: list[Finding] = []
        ordered = sorted(self.phase_by_ply)
        final_phase = self.phase_by_ply[ordered[-1]] if ordered else GamePhase.OPENING

        previous: GamePhase | None = None
        for ply in ordered:
            phase = self.phase_by_ply[ply]
            if previous is not None and phase != previous:
                detection = detections[ply]
                move_number = next(
                    (fact.move_number for fact in self.moves if fact.ply == ply), ply
                )
                transitions.append(
                    PhaseTransition(
                        ply=ply,
                        move_number=move_number,
                        from_phase=previous,
                        to_phase=phase,
                        reasons=detection.reasons,
                        statement=(
                            f"Caissa classified the position after ply {ply} (move "
                            f"{move_number}) as the {phase.value}; previously {previous.value}."
                        ),
                    )
                )
            previous = phase

        for transition in transitions:
            facts.append(
                Finding(
                    key=f"phase_transition_{transition.ply}",
                    statement=transition.statement,
                    ply=transition.ply,
                    move_number=transition.move_number,
                    evidence={
                        "from": transition.from_phase.value,
                        "to": transition.to_phase.value,
                        "reasons": transition.reasons,
                    },
                )
            )
        for color in (Color.WHITE, Color.BLACK):
            bucket = self.performance.white if color == Color.WHITE else self.performance.black
            for name, stats in bucket.items():
                facts.append(
                    Finding(
                        key=f"phase_stats_{color.value}_{name}",
                        statement=(
                            f"{color_label(color)} in the {name}: {stats.moves} move(s), mean "
                            f"centipawn loss {stats.average_centipawn_loss}, "
                            f"{stats.problem_moves} flagged move(s), {stats.tactical_events} "
                            f"tactical event(s)"
                            + (" (small sample)." if stats.small_sample else ".")
                        ),
                        side=color,
                        evidence={
                            "phase": name,
                            "moves": stats.moves,
                            "evaluated_moves": stats.evaluated_moves,
                            "average_centipawn_loss": stats.average_centipawn_loss,
                            "counts": stats.counts,
                            "small_sample": stats.small_sample,
                            "plies": stats.plies[:20],
                        },
                    )
                )

        final_detection = detections[ordered[-1]] if ordered else None
        return PhaseReportSection(
            final_phase=final_phase,
            phase_by_ply={str(ply): phase.value for ply, phase in sorted(self.phase_by_ply.items())},
            transitions=transitions,
            phase_indicators_final=final_detection.reasons if final_detection else [],
            detector_confidence_final=final_detection.confidence if final_detection else None,
            performance=self.performance,
            facts=facts,
        )

    def get_trajectory_section(self) -> TrajectoryReportSection:
        facts: list[Finding] = []
        if self.trajectory.missing_plies:
            facts.append(
                Finding(
                    key="trajectory_coverage",
                    statement=(
                        f"{self.trajectory.evaluated_plies} plies have an engine evaluation and "
                        f"{self.trajectory.missing_plies} do not; missing evaluations are reported "
                        "as unavailable and are never interpolated."
                    ),
                    evidence={
                        "evaluated_plies": self.trajectory.evaluated_plies,
                        "missing_plies": self.trajectory.missing_plies,
                    },
                )
            )
        if self.trajectory.min_evaluation_white is not None:
            mate_note = (
                f" {self.trajectory.mate_plies} ply evaluations are mate scores and are "
                "reported as #n, not as centipawns."
                if self.trajectory.mate_plies
                else ""
            )
            facts.append(
                Finding(
                    key="trajectory_range",
                    statement=(
                        f"The centipawn evaluation ranged from "
                        f"{self.trajectory.min_evaluation_white} to "
                        f"{self.trajectory.max_evaluation_white} (White perspective, mate "
                        f"plies excluded) and finished at "
                        f"{self.trajectory.final_evaluation_display or self.trajectory.final_evaluation_white}."
                        f"{mate_note}"
                    ),
                    evidence={
                        "states_seen": self.trajectory.states_seen,
                        "segments": len(self.trajectory.segments),
                        "mate_plies": self.trajectory.mate_plies,
                        "final_evaluation_display": self.trajectory.final_evaluation_display,
                    },
                )
            )
        return TrajectoryReportSection(
            trajectory=self.trajectory,
            advantage_thresholds=describe_policy(self.policy.advantage),
            states_seen=self.trajectory.states_seen,
            facts=facts,
        )

    def get_critical_moments(self) -> CriticalMomentsSection:
        engine_moments = [
            Insight(
                insight_type=f"critical_position:{fact.reason}",
                category="critical_position",
                source=EvidenceSource.ENGINE_FACT,
                certainty=Certainty.CONFIRMED,
                ply=fact.ply,
                move_number=fact.move_number,
                side=fact.color,
                severity=fact.severity,
                statement=fact.detail
                or f"Critical position ({fact.reason}) at move {fact.move_number}.",
                evidence={
                    "reason": fact.reason,
                    "severity_score": fact.severity_score,
                    "swing_cp": fact.swing_cp,
                    "classification": fact.classification.value
                    if fact.classification
                    else None,
                    "evaluation_before_white": fact.evaluation_before_white,
                    "evaluation_after_white": fact.evaluation_after_white,
                    "is_mate_related": fact.is_mate_related,
                },
            )
            for fact in self.criticals
        ]
        return CriticalMomentsSection(
            engine_critical_moments=engine_moments,
            timeline=self.build_timeline(),
            largest_swing_ply=self.turning_points.largest_swing_ply,
        )

    def build_timeline(self) -> list[TimelineEntry]:
        """The clickable critical-moment timeline (navigation target = ply)."""
        entries: list[TimelineEntry] = []

        deviation = self.opening.deviation
        if deviation.deviated and deviation.ply is not None:
            entries.append(
                TimelineEntry(
                    ply=deviation.ply,
                    move_number=deviation.move_number,
                    san=deviation.played_san,
                    side=deviation.side,
                    kind="opening_deviation",
                    label="Opening deviation",
                    statement=(
                        f"Left the {self.opening.identification.name or 'known'} line; the table "
                        f"expected {', '.join(deviation.expected_continuation_san) or 'no further moves'}."
                    ),
                    severity="low",
                    evidence={"expected_continuation_uci": deviation.expected_continuation_uci},
                )
            )

        for event in self.tactical.events:
            if event.certainty is Certainty.CANDIDATE and event.severity == "low":
                continue
            entries.append(
                TimelineEntry(
                    ply=event.ply,
                    move_number=event.move_number,
                    san=event.san,
                    side=event.side,
                    kind="tactical_event",
                    label=(
                        "Tactical opportunity"
                        if event.certainty is Certainty.CANDIDATE
                        else f"Tactical: {event.type.value.replace('_', ' ')}"
                    ),
                    statement=event.statement,
                    severity=event.severity,
                    source=event.source,
                    certainty=event.certainty,
                    evidence={"type": event.type.value, **event.evidence},
                )
            )

        for point in self.turning_points.turning_points:
            entries.append(
                TimelineEntry(
                    ply=point.ply,
                    move_number=point.move_number,
                    san=point.san,
                    side=point.side,
                    kind="turning_point",
                    label=f"Turning point: {point.type.value.replace('_', ' ')}",
                    statement=point.statement,
                    severity=point.severity,
                    source=point.source,
                    certainty=point.certainty,
                    evidence={
                        "type": point.type.value,
                        "evaluation_before_cp": point.evaluation_before_cp,
                        "evaluation_after_cp": point.evaluation_after_cp,
                    },
                )
            )

        for event in self.conversion.events:
            entries.append(
                TimelineEntry(
                    ply=event.ply,
                    move_number=event.move_number,
                    side=event.side,
                    kind="conversion",
                    label=f"Conversion: {event.type.value.replace('_', ' ')}",
                    statement=event.statement,
                    severity="medium",
                    source=event.source,
                    certainty=event.certainty,
                    evidence=event.evidence,
                )
            )

        # Engine-flagged critical positions come last so richer entries win ties.
        for fact in self.criticals:
            entries.append(
                TimelineEntry(
                    ply=fact.ply,
                    move_number=fact.move_number,
                    san=fact.san,
                    side=fact.color,
                    kind="critical_position",
                    label=f"Engine critical: {fact.reason.replace('_', ' ')}",
                    statement=fact.detail or "Engine-flagged critical position.",
                    severity=fact.severity,
                    source=EvidenceSource.ENGINE_FACT,
                    evidence={
                        "swing_cp": fact.swing_cp,
                        "severity_score": fact.severity_score,
                        "is_mate_related": fact.is_mate_related,
                    },
                )
            )

        ordered = sorted(
            entries,
            key=lambda entry: (entry.ply, -_SEVERITY_RANK.get(entry.severity, 0)),
        )
        seen: set[tuple[int, str]] = set()
        deduped: list[TimelineEntry] = []
        for entry in ordered:
            key = (entry.ply, entry.kind)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        return deduped

    # --- report ----------------------------------------------------------------

    def unavailable_sections(self) -> list[Unavailable]:
        """What could not be computed, and what would be required."""
        missing: list[Unavailable] = []
        if not self.moves:
            missing.append(
                Unavailable(
                    section="all",
                    reason="The game has no moves, so there is nothing to analyse.",
                    required="an imported game with at least one move",
                )
            )
            return missing
        evaluated = [fact for fact in self.moves if fact.evaluated]
        if not evaluated:
            missing.append(
                Unavailable(
                    section="engine_evaluation",
                    reason=(
                        "No move of this game has a stored engine evaluation, so evaluations, "
                        "classification, accuracy and trajectory are unavailable."
                    ),
                    required="a completed Stockfish analysis run for this game",
                )
            )
            return missing
        if len(evaluated) < len(self.moves):
            missing.append(
                Unavailable(
                    section="analysis_coverage",
                    reason=(
                        f"{len(self.moves) - len(evaluated)} of {len(self.moves)} moves have no "
                        "stored evaluation (an interrupted or resumed analysis leaves partial data)."
                    ),
                    required="a complete analysis run",
                )
            )
        if not self.criticals:
            missing.append(
                Unavailable(
                    section="engine_critical_moments",
                    reason=(
                        "No engine-flagged critical positions are stored for this game; the "
                        "timeline is built from Caissa detections only."
                    ),
                    required="a completed analysis run that wrote critical positions",
                )
            )
        if self.accuracy.white.scored_moves == 0 and self.accuracy.black.scored_moves == 0:
            missing.append(
                Unavailable(
                    section="accuracy",
                    reason="No move could be scored, so accuracy is reported as unavailable.",
                    required="evaluated moves outside already-decided positions",
                )
            )
        return missing

    def build_report(self) -> GameReport:
        """Build the complete structured report (no engine calls, no LLM)."""
        provenance = AnalysisProvenance(
            analysis_version=self.analysis.analysis_version,
            report_version=REPORT_VERSION,
            engine=self.analysis.engine,
            engine_version=self.analysis.engine_version,
            depth=self.analysis.depth,
            multipv=self.analysis.multipv,
            movetime_ms=self.analysis.movetime_ms,
            profile=self.analysis.profile,
            positions_analyzed=self.analysis.positions_analyzed,
            moves_in_game=len(self.moves),
            evaluated_moves=len([fact for fact in self.moves if fact.evaluated]),
            engine_calls_made_by_intelligence_layer=0,
        )
        summary = self.get_game_summary()
        lessons = build_key_lessons(
            summary=summary,
            opening=self.opening,
            performance=self.performance,
            accuracy=self.accuracy,
            tactical=self.tactical,
            positional=self.positional,
            king_safety=self.king_safety,
            categories=self.categories,
            turning_points=self.turning_points,
            conversion=self.conversion,
        )
        recommendations = build_training_recommendations(
            tactical=self.tactical,
            categories=self.categories,
            performance=self.performance,
            conversion=self.conversion,
            king_safety=self.king_safety,
            positional=self.positional,
            accuracy=self.accuracy,
        )

        return GameReport(
            report_version=REPORT_VERSION,
            generated_at=datetime.now(timezone.utc),
            game_id=self.context.game_id,
            context=self.context,
            provenance=provenance,
            summary=summary,
            opening=self.opening,
            phases=self.get_phase_section(),
            trajectory=self.get_trajectory_section(),
            material=MaterialReportSection(
                timeline=self.material, facts=self._material_facts()
            ),
            tactical=TacticalReportSection(
                analysis=self.tactical, facts=self._tactical_facts()
            ),
            positional=PositionalReportSection(
                analysis=self.positional, facts=self._positional_facts()
            ),
            king_safety=KingSafetyReportSection(
                analysis=self.king_safety, facts=self._king_safety_facts()
            ),
            accuracy=AccuracyReportSection(
                analysis=self.accuracy, facts=self._accuracy_facts()
            ),
            forecast=ForecastReportSection(
                forecast=self.forecast, facts=self.forecast.facts
            ),
            conversion=ConversionReportSection(
                analysis=self.conversion, facts=self._conversion_facts()
            ),
            error_categories=ErrorCategorySection(
                analysis=self.categories, facts=self._category_facts()
            ),
            turning_points=self.turning_points,
            critical_moments=self.get_critical_moments(),
            key_lessons=lessons,
            training_recommendations=recommendations,
            unavailable=self.unavailable_sections(),
            evidence_policy=EVIDENCE_POLICY,
        )

    # --- section facts ---------------------------------------------------------

    def _material_facts(self) -> list[Finding]:
        facts: list[Finding] = []
        for event in self.material.transitions[:10]:
            facts.append(
                Finding(
                    key=f"material_transition_{event.ply}",
                    statement=(
                        f"Material balance changed from {event.balance_before} to "
                        f"{event.balance_after} pawns on move {event.move_number} ({event.san})."
                    ),
                    ply=event.ply,
                    move_number=event.move_number,
                    side=event.side,
                    evidence=event.evidence,
                )
            )
        for event in self.material.promotions:
            facts.append(
                Finding(
                    key=f"promotion_{event.ply}",
                    statement=(
                        f"{color_label(event.side)} promoted to a {event.promoted_to} on move "
                        f"{event.move_number} ({event.san})."
                    ),
                    ply=event.ply,
                    move_number=event.move_number,
                    side=event.side,
                    evidence=event.evidence,
                )
            )
        return facts

    def _tactical_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"tactical_{event.type.value}_{event.ply}_{event.side.value}",
                statement=event.statement,
                ply=event.ply,
                move_number=event.move_number,
                side=event.side,
                source=event.source,
                evidence={
                    "certainty": event.certainty.value,
                    "severity": event.severity,
                    "affected_pieces": event.affected_pieces,
                    "squares": event.squares,
                    "engine_context": event.engine_context,
                },
            )
            for event in self.tactical.events
        ]

    def _positional_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"positional_{event.type.value}_{event.ply}_{event.side.value}",
                statement=event.statement,
                ply=event.ply,
                move_number=event.move_number,
                side=event.side,
                source=event.source,
                evidence={
                    "classification": event.classification,
                    "certainty": event.certainty.value,
                    "engine_supported": event.engine_supported,
                    **event.evidence,
                },
            )
            for event in self.positional.events
        ]

    def _king_safety_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"king_safety_{event.type.value}_{event.ply}",
                statement=event.statement,
                ply=event.ply,
                move_number=event.move_number,
                side=event.side,
                source=event.source,
                evidence={"severity": event.severity, "certainty": event.certainty.value, **event.evidence},
            )
            for event in self.king_safety.events
        ]

    def _accuracy_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"accuracy_side_{side.side.value}",
                statement=(
                    f"{color_label(side.side)} scored {side.accuracy} Caissa accuracy over "
                    f"{side.scored_moves} scored move(s); {side.unscored_moves} move(s) had no "
                    f"engine evaluation."
                ),
                side=side.side,
                evidence={
                    "accuracy": side.accuracy,
                    "average_centipawn_loss": side.average_centipawn_loss,
                    "excluded_decided_moves": side.excluded_decided_moves,
                    "small_sample": side.small_sample,
                    "methodology": self.accuracy.methodology,
                },
            )
            for side in (self.accuracy.white, self.accuracy.black)
        ]

    def _conversion_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"conversion_{event.type.value}_{event.ply}_{event.side.value}",
                statement=event.statement,
                ply=event.ply,
                move_number=event.move_number,
                side=event.side,
                source=event.source,
                evidence={
                    "certainty": event.certainty.value,
                    "peak_evaluation_white": event.peak_evaluation_white,
                    "later_evaluation_white": event.later_evaluation_white,
                    "measured_plies": event.measured_plies,
                    **event.evidence,
                },
            )
            for event in self.conversion.events
        ]

    def _category_facts(self) -> list[Finding]:
        return [
            Finding(
                key=f"error_{error.category.value}_{error.ply}_{error.side.value}",
                statement=(
                    f"Move {error.move_number} ({error.san}) was categorised as "
                    f"{error.category.value} because {error.basis}."
                ),
                ply=error.ply,
                move_number=error.move_number,
                side=error.side,
                source=error.source,
                evidence={
                    "classification": error.classification,
                    "centipawn_loss": error.centipawn_loss,
                    "phase": error.phase,
                    "basis": error.basis,
                    **error.evidence,
                },
            )
            for error in self.categories.errors
        ]


__all__ = [
    "AnalysisMeta",
    "GameIntelligence",
    "Recommendation",
    "TurningPointAnalysis",
    "TurningPointType",
]
