"""The deterministic game → exercise pipeline (spec §5).

Every exercise is generated from a *stored move analysis row* — never from a
live engine call, and never from invented content. The pipeline is:

    stored move analysis → candidate → eligibility gate → difficulty
    → category (from evidence only) → dedupe → TrainingPositionCandidate

**Position BEFORE the move.** The puzzle position is ``fen_before`` of the
analysed move: the player must find what the engine found, from the same spot
the original mover faced. The played move is retained as evidence (and as a
wrong answer to avoid repeating), not as the starting point.

**Determinism.** Same input row, same exercise — byte-identical output. There
is no randomness, no timestamps, no ordering dependence in any field.

**Refusal is a result.** A move that cannot become a justifiable exercise
produces a rejection entry in the report, with the reason string from the
eligibility service. A report that says "3 generated, 11 rejected" is worth
more than fifteen weak exercises.

**Categories are earned.** The generator assigns a category only when the
stored evidence supports it (see :func:`_assign_category`):

* a forced-mate solution → tactical (a mating pattern),
* the engine's PV runs through a capture/check sequence → tactical,
* the position is in the endgame phase (from stored analysis) or has few
  pieces → endgame,
* the phase is opening (from stored analysis) → opening,
* everything else → calculation, the honest category for "the engine found a
  better move and there is no more specific provable story".

CONVERSION and RECOVERY are never assigned by the generator (see
``models.GENERATOR_CATEGORIES``): a single move analysis cannot prove "this
advantage was later lost" or "this worse position was later held" — those need
multi-game outcome context the row does not carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chess

from argus.shared.logging import get_logger
from argus.training.difficulty import assess_difficulty
from argus.training.eligibility import (
    EligibilityReport,
    TrainingEligibilityService,
    default_eligibility_service,
)
from argus.training.gamearc import ArcLabel, classify_arc
from argus.training.models import (
    TRAINING_METHODOLOGY_VERSION,
    Category,
    PositionType,
    TrainingPosition,
)

logger = get_logger(__name__)

#: Tactical motifs detectable from stored position/PV evidence. These strings
#: are stored in ``tags`` and drive hints and recommendations — they are
#: *observed*, never guessed.
TACTICAL_MOTIFS = (
    "fork",
    "pin",
    "skewer",
    "discovered_attack",
    "double_attack",
    "mating_pattern",
    "hanging_piece",
    "defensive_tactic",
)

#: Position types the generator may emit from a single move analysis.
#: ``continue_line`` is emitted when the stored principal variation is long
#: enough to grade move-by-move; ``what_went_wrong``/``reconstruction`` are
#: whole-game formats the generator still does not fabricate.
GENERATOR_POSITION_TYPES: tuple[PositionType, ...] = (
    PositionType.FIND_BEST_MOVE,
    PositionType.FIND_TACTICAL_MOVE,
    PositionType.FIND_DEFENSE,
    PositionType.CHOOSE_BETWEEN_MOVES,
    PositionType.CONTINUE_LINE,
)

#: How many plies of stored PV a CONTINUE_LINE exercise keeps (after the
#: solution). Two plies = one forced reply + one move to find; four gives the
#: solver two continuations to get right.
CONTINUE_LINE_PLIES = 4

#: Below this many pieces the position is counted as sparse (endgame-like).
SPARSE_PIECE_COUNT = 7


@dataclass
class TrainingPositionCandidate:
    """One generated exercise, ready for persistence (or its rejection)."""

    position: TrainingPosition | None
    report: EligibilityReport
    source_game_id: str | None = None
    source_ply: int | None = None

    @property
    def accepted(self) -> bool:
        return self.position is not None and self.report.accepted


@dataclass
class GenerationReport:
    """What one generation run produced, and why anything was refused."""

    candidates: list[TrainingPositionCandidate] = field(default_factory=list)
    #: Rows skipped before the eligibility gate (not analysed, not a problem
    #: move, played the solution). Each entry: {"ply", "reason"}.
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def generated(self) -> list[TrainingPositionCandidate]:
        return [candidate for candidate in self.candidates if candidate.accepted]

    @property
    def rejected(self) -> list[TrainingPositionCandidate]:
        return [candidate for candidate in self.candidates if not candidate.accepted]

    @property
    def total_seen(self) -> int:
        return len(self.candidates) + len(self.skipped)

    @property
    def accepted_count(self) -> int:
        return len(self.generated)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def reason_counts(self) -> dict[str, int]:
        """Rejection reasons → counts, for honest reporting."""
        counts: dict[str, int] = {}
        for candidate in self.rejected:
            for reason in candidate.report.reasons:
                key = reason.split(":", 1)[0]
                counts[key] = counts.get(key, 0) + 1
        return counts


class TrainingPositionGenerator:
    """Builds exercises from stored move analyses, deterministically."""

    def __init__(
        self,
        eligibility: TrainingEligibilityService | None = None,
    ) -> None:
        self.eligibility = eligibility or default_eligibility_service()

    # -- single row → candidate -------------------------------------------

    def generate_from_move_analysis(
        self,
        move_analysis: dict[str, Any],
        *,
        game_id: str | None = None,
        player_id: int | None = None,
        data_source: str = "personalized",
        duplicate: bool = False,
        arc_label: ArcLabel | None = None,
        reason_prefix: str | None = None,
    ) -> TrainingPositionCandidate:
        """Turn one stored move-analysis dict into an accepted-or-rejected candidate.

        The input is the persisted row shape (see
        ``argus_api.db.models.MoveAnalysis`` / ``MoveFact``): ``fen_before``,
        ``best_move_uci``/``best_move_san``, ``played_move_uci``/``..._san``,
        mover-perspective evaluations, ``centipawn_loss``, ``phase``,
        ``principal_variation``, ``depth``, engine provenance.
        """
        fen = str(move_analysis.get("fen_before") or "").strip()
        solution_uci = move_analysis.get("best_move_uci")
        played_uci = move_analysis.get("played_move_uci")

        # Rows that cannot even reach the gate are skipped, with reasons.
        if not fen or not solution_uci:
            return self._skipped_candidate(
                move_analysis, game_id, "no stored engine best move"
            )

        # A position where the played move WAS the best move is not a mistake:
        # there is nothing to train (spec §5 generates from mistakes).
        if played_uci and played_uci == solution_uci:
            return self._skipped_candidate(
                move_analysis, game_id, "played move was already the best move"
            )

        solution_eval_cp = move_analysis.get("evaluation_before_cp")
        solution_eval_mate = move_analysis.get("evaluation_before_mate")
        played_loss_cp = move_analysis.get("centipawn_loss")

        # Alternative-best gap: derived from candidate multipv data when the
        # caller provides it; otherwise unknown (the gate skips, not guesses).
        alternative_best_loss_cp = self._alternative_best_loss_cp(
            move_analysis, solution_uci
        )

        report = self.eligibility.check(
            fen=fen,
            solution_uci=str(solution_uci),
            solution_eval_cp=solution_eval_cp,
            solution_eval_mate=solution_eval_mate,
            played_loss_cp=played_loss_cp,
            alternative_best_loss_cp=alternative_best_loss_cp,
            duplicate=duplicate,
        )

        # The common shape: a single decision dict per row, so build the
        # position once and let rejection carry only the report.
        position = self._build_position(
            move_analysis,
            game_id=game_id,
            player_id=player_id,
            data_source=data_source,
            alternative_best_loss_cp=alternative_best_loss_cp,
            arc_label=arc_label,
            reason_prefix=reason_prefix,
        )
        if position is None:
            return self._skipped_candidate(
                move_analysis, game_id, "malformed stored analysis"
            )

        return TrainingPositionCandidate(
            position=position,
            report=report,
            source_game_id=game_id,
            source_ply=position.source_ply,
        )

    # -- batch over a game -------------------------------------------------

    def generate_from_game(
        self,
        move_analyses: list[dict[str, Any]],
        *,
        game_id: str | None = None,
        player_id: int | None = None,
        data_source: str = "personalized",
        existing_normalized_fens: set[str] | None = None,
        game_result: str | None = None,
        player_color: str | None = None,
    ) -> GenerationReport:
        """Generate candidates for every qualifying move of one game.

        ``existing_normalized_fens`` carries the player's current dedupe keys;
        newly accepted candidates are added to it as we go, so two identical
        positions inside one run cannot both pass (deterministic first-wins).

        ``game_result`` and ``player_color`` opt into whole-game arc evidence:
        when both are supplied, moves that sit at a conversion or recovery
        turning point are labelled with that category, with the reason stored in
        the exercise's provenance. Without them, no arc label can be produced —
        the category is never guessed.
        """
        report = GenerationReport()
        seen: set[str] = set(existing_normalized_fens or set())
        arc: dict[int, ArcLabel] = {}
        if game_result and player_color:
            arc = classify_arc(
                move_analyses, player_color=player_color, result=game_result
            )

        for row in move_analyses:
            ply = row.get("ply")
            if not row.get("best_move_uci"):
                report.skipped.append({"ply": ply, "reason": "no stored engine best move"})
                continue
            if row.get("played_move_uci") == row.get("best_move_uci"):
                report.skipped.append(
                    {"ply": ply, "reason": "played move was already the best move"}
                )
                continue

            board_ok, fen = self._normalizable(row.get("fen_before"))
            normalized = self._normalize_fen(fen) if board_ok else ""
            duplicate = bool(normalized) and normalized in seen

            candidate = self.generate_from_move_analysis(
                row,
                game_id=game_id,
                player_id=player_id,
                data_source=data_source,
                duplicate=duplicate,
                arc_label=arc.get(int(row.get("ply") or 0)),
            )
            report.candidates.append(candidate)
            if candidate.accepted and normalized:
                seen.add(normalized)

        return report

    # -- internals ----------------------------------------------------------

    def _build_position(
        self,
        row: dict[str, Any],
        *,
        game_id: str | None,
        player_id: int | None,
        data_source: str,
        alternative_best_loss_cp: int | None,
        arc_label: ArcLabel | None = None,
        reason_prefix: str | None = None,
    ) -> TrainingPosition | None:
        fen = str(row.get("fen_before") or "").strip()
        solution_uci = row.get("best_move_uci")
        if not fen or not solution_uci:
            return None
        try:
            board = chess.Board(fen)
            solution = chess.Move.from_uci(str(solution_uci))
            if solution not in board.legal_moves:
                return None
        except ValueError:
            return None

        solution_san = str(
            row.get("best_move_san")
            or board.san(solution)
        )

        phase = row.get("phase")
        pv = [str(m) for m in (row.get("principal_variation") or [])]

        # Difficulty — always measured, factors stored beside the label.
        assessment = assess_difficulty(
            fen=fen,
            solution_uci=str(solution_uci),
            solution_eval_cp=row.get("evaluation_before_cp"),
            alternative_best_loss_cp=alternative_best_loss_cp,
            solution_pv_length=len(pv),
        )

        # Category and type — only from evidence. A whole-game arc label, when
        # present, overrides the single-move category because it rests on more
        # evidence (the game's trajectory and outcome), never less.
        category, tags = self._assign_category(board, phase, pv, row)
        if arc_label is not None:
            category = arc_label.category
            tags = [*tags, arc_label.category.value]
        position_type = self._assign_position_type(board, row, tags)

        # The played move is evidence (and a wrong answer to anticipate).
        played_uci = row.get("played_move_uci")
        played_san = row.get("played_move_san")

        # Acceptable alternatives: the caller's multipv candidates within the
        # acceptance tolerance. The generator stores what it can prove; the
        # acceptance policy does the rest at grading time.
        acceptable = self._acceptable_moves(row, board, solution)

        # The engine line after the solution, kept only for CONTINUE_LINE: it is
        # exactly the stored PV from index 1 on, never a constructed line.
        continuation = pv[1 : 1 + CONTINUE_LINE_PLIES] if position_type is PositionType.CONTINUE_LINE else []

        side = "white" if board.turn == chess.WHITE else "black"

        source_reason = self._source_reason(
            row, played_uci, played_san, phase, tags, reason_prefix=reason_prefix
        )
        if arc_label is not None:
            source_reason = f"{source_reason}; {arc_label.reason}" if source_reason else arc_label.reason

        return TrainingPosition(
            player_id=player_id,
            source_game_id=game_id,
            source_ply=row.get("ply"),
            source_position_id=row.get("position_id"),
            side_to_move=side,
            fen=fen,
            source_fen_normalized=self._normalize_fen(fen),
            position_type=position_type,
            category=category,
            difficulty=assessment.difficulty,
            difficulty_factors=assessment.factors,
            data_source=data_source,
            source_reason=source_reason,
            tags=tags,
            solution_uci=str(solution_uci),
            solution_san=solution_san,
            acceptable_moves=acceptable,
            candidate_moves=self._candidate_moves(row),
            principal_variation=pv,
            continuation_line=continuation,
            solution_eval_cp=row.get("evaluation_before_cp"),
            solution_eval_mate=row.get("evaluation_before_mate"),
            played_move_uci=played_uci,
            played_move_san=played_san,
            played_eval_cp=row.get("played_eval_cp"),
            played_loss_cp=row.get("centipawn_loss"),
            engine=str(row.get("engine") or "stockfish"),
            engine_version=row.get("engine_version"),
            depth=int(row.get("depth") or 0),
            analysis_version=str(row.get("analysis_version") or "3.1"),
            methodology_version=TRAINING_METHODOLOGY_VERSION,
        )

    def _assign_category(
        self,
        board: chess.Board,
        phase: str | None,
        pv: list[str],
        row: dict[str, Any],
    ) -> tuple[Category, list[str]]:
        """Assign a category ONLY when the stored evidence supports it."""
        tags: list[str] = []

        is_forced_mate = bool(row.get("evaluation_before_mate"))

        # Observed tactical evidence from the position and the engine's line.
        # Motifs (fork-family, mate, hanging material) earn the TACTICAL
        # category; structural observations (a capture, a check) are recorded
        # for provenance but never earn a category on their own — almost every
        # move in chess captures or checks something.
        try:
            solution_move = chess.Move.from_uci(str(row.get("best_move_uci")))
        except ValueError:
            solution_move = None
        motifs, structural = self._tactical_evidence(board, solution_move, is_forced_mate)
        tags.extend(motifs)
        tags.extend(structural)

        if motifs:
            return Category.TACTICAL, tags

        # Phase-based categories — stored phase is analysis output, not a guess.
        if phase == "endgame":
            return Category.ENDGAME, tags
        if phase == "opening":
            return Category.OPENING, tags

        # Sparse boards are endgame-adjacent even if the phase field is absent.
        piece_count = sum(1 for sq in chess.SQUARES if board.piece_at(sq) is not None)
        if piece_count <= SPARSE_PIECE_COUNT:
            return Category.ENDGAME, tags

        # The honest default: the engine found a better move and no more
        # specific story is provable from this row.
        return Category.CALCULATION, tags

    def _tactical_evidence(
        self, board: chess.Board, solution: chess.Move, is_forced_mate: bool
    ) -> tuple[list[str], list[str]]:
        """Split observed evidence into (motifs, structural observations).

        Only motifs that can be *verified on the board* are emitted, and only
        motifs earn the tactical category. This is a deliberately conservative
        detector: it flags what it can prove and stays silent otherwise,
        because a wrong tag poisons hints and recommendations downstream.
        """
        motifs: list[str] = []
        structural: list[str] = []
        if is_forced_mate:
            motifs.append("mating_pattern")

        if solution in board.legal_moves:
            if board.is_capture(solution):
                structural.append("capture")
            if board.gives_check(solution):
                structural.append("check")

        # An attacked-and-undefended *opponent* piece is exploitable material —
        # a checkable motif. (The solver's own hanging pieces are a defence
        # story, not this exercise's evidence.)
        if self._has_hanging_piece(board, victim_color=not board.turn):
            motifs.append("hanging_piece")

        return motifs, structural

    def _has_hanging_piece(self, board: chess.Board, *, victim_color: bool) -> bool:
        """True when the given side has an attacked, undefended piece."""
        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None or piece.color != victim_color or piece.piece_type == chess.KING:
                continue
            attackers = board.attackers(not piece.color, square)
            defenders = board.attackers(piece.color, square)
            if attackers and not defenders:
                return True
        return False

    def _assign_position_type(
        self,
        board: chess.Board,
        row: dict[str, Any],
        tags: list[str],
    ) -> PositionType:
        """Choose the exercise format from evidence, never arbitrarily."""
        # A defensive resource: the mover is clearly worse and must hold on.
        eval_before = row.get("evaluation_before_cp")
        if eval_before is not None and eval_before < -300:
            return PositionType.FIND_DEFENSE

        # Multiple strong candidates recorded → a "choose between moves".
        candidates = row.get("candidate_moves") or row.get("multipv_lines") or []
        if len(candidates) >= 2:
            return PositionType.CHOOSE_BETWEEN_MOVES

        # Observed tactical motifs → the tactical format (structural tags like
        # 'check'/'capture' do not count — see _tactical_evidence).
        motif_tags = {"mating_pattern", "fork", "pin", "skewer", "discovered_attack", "double_attack", "hanging_piece"}
        if any(tag in motif_tags for tag in tags):
            return PositionType.FIND_TACTICAL_MOVE

        # A stored engine line long enough to have a forced reply and at least one
        # continuation becomes a line exercise. Every ply graded is a ply
        # Stockfish actually played in the stored PV — there is nothing to guess.
        pv = [str(move) for move in (row.get("principal_variation") or [])]
        if len(pv) >= 3:
            return PositionType.CONTINUE_LINE

        return PositionType.FIND_BEST_MOVE

    def _acceptable_moves(
        self, row: dict[str, Any], board: chess.Board, solution: chess.Move
    ) -> dict[str, str]:
        """Moves the engine scored within tolerance of the solution.

        Derived from stored multipv candidate rows when present; verified
        legal before inclusion. Values are SAN, keys are UCI.
        """
        acceptable: dict[str, str] = {}
        tolerance = 150  # mirrors MoveAcceptancePolicy.near_best_cp

        solution_eval = row.get("evaluation_before_cp")
        for candidate in row.get("candidate_moves") or []:
            uci = candidate.get("uci") if isinstance(candidate, dict) else None
            cp = candidate.get("cp") if isinstance(candidate, dict) else None
            if not uci or uci == row.get("best_move_uci"):
                continue
            try:
                move = chess.Move.from_uci(str(uci))
                if move not in board.legal_moves:
                    continue
            except ValueError:
                continue
            if solution_eval is not None and cp is not None:
                if solution_eval - cp <= tolerance:
                    acceptable[str(uci)] = board.san(move)
        return acceptable

    def _candidate_moves(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """Stored candidate rows, passed through for CHOOSE_BETWEEN_MOVES."""
        candidates = row.get("candidate_moves") or []
        out: list[dict[str, Any]] = []
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("uci"):
                out.append(dict(candidate))
        return out

    def _alternative_best_loss_cp(
        self, row: dict[str, Any], solution_uci: str
    ) -> int | None:
        """How much worse the best non-solution candidate is than the solution."""
        candidates = row.get("candidate_moves") or []
        solution_eval = row.get("evaluation_before_cp")
        if solution_eval is None:
            return None
        worst_gap: int | None = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            uci = candidate.get("uci")
            cp = candidate.get("cp")
            if not uci or uci == solution_uci or cp is None:
                continue
            gap = solution_eval - cp
            if gap >= 0 and (worst_gap is None or gap < worst_gap):
                # The BEST alternative = the smallest non-negative gap.
                worst_gap = gap
        return worst_gap

    def _source_reason(
        self,
        row: dict[str, Any],
        played_uci: str | None,
        played_san: str | None,
        phase: str | None,
        tags: list[str],
        *,
        reason_prefix: str | None = None,
    ) -> str:
        """Human-readable origin chain: game → move → mistake → exercise.

        ``reason_prefix`` leads the string when present: opponent-preparation
        exercises state the opponent and their characteristic move first, because
        that — not the played move — is why the exercise exists.
        """
        parts: list[str] = []
        if reason_prefix:
            parts.append(reason_prefix)
        classification = row.get("classification")
        if played_uci and played_san:
            parts.append(f"Played {played_san}")
            if classification:
                parts.append(f"({classification})")
        elif classification:
            parts.append(f"Classification: {classification}")
        if phase:
            parts.append(f"in the {phase}")
        if tags:
            parts.append(f"tactical evidence: {', '.join(tags)}")
        return "; ".join(parts) if parts else "Engine-verified better move exists"

    @staticmethod
    def _normalizable(fen: str | None) -> tuple[bool, str]:
        if not fen:
            return False, ""
        return True, fen.strip()

    @staticmethod
    def _normalize_fen(fen: str) -> str:
        return " ".join(fen.split()[:4])

    def _skipped_candidate(
        self,
        row: dict[str, Any],
        game_id: str | None,
        reason: str,
    ) -> TrainingPositionCandidate:
        logger.debug(
            "training generation skipped",
            extra={"game_id": game_id, "ply": row.get("ply"), "reason": reason},
        )
        return TrainingPositionCandidate(
            position=None,
            report=EligibilityReport(
                accepted=False,
                reasons=[reason],
                policy={},
            ),
            source_game_id=game_id,
            source_ply=row.get("ply"),
        )


#: Module-level convenience matching the documented API.
def generate_from_move_analysis(
    move_analysis: dict[str, Any],
    *,
    game_id: str | None = None,
    player_id: int | None = None,
    data_source: str = "personalized",
    duplicate: bool = False,
    eligibility: TrainingEligibilityService | None = None,
) -> TrainingPositionCandidate:
    """One-row convenience wrapper around :class:`TrainingPositionGenerator`."""
    return TrainingPositionGenerator(eligibility).generate_from_move_analysis(
        move_analysis,
        game_id=game_id,
        player_id=player_id,
        data_source=data_source,
        duplicate=duplicate,
    )


__all__ = [
    "GENERATOR_POSITION_TYPES",
    "GenerationReport",
    "TACTICAL_MOTIFS",
    "TrainingPositionCandidate",
    "TrainingPositionGenerator",
    "generate_from_move_analysis",
]
