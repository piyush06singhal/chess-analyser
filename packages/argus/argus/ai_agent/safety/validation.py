"""Hallucination validation: check the answer against its own evidence.

This is a *targeted* validator, and it is important to be honest about why.

Full semantic verification of prose is not achievable, and a validator that claims
to do it is worse than none — it would license trust it cannot support. So this
module does what is actually checkable, on the claims where being wrong does real
damage (spec §23):

==============================  ==================================================
engine evaluations              ``+2.1``, ``-3.4 pawns``, ``508 centipawns``,
                                ``mate in 3`` must match a centipawn/mate value in
                                the packet, within a rounding tolerance
counts of games and errors      ``7 blunders in 20 games`` must use numbers that
                                appear somewhere in the evidence
percentages and probabilities   a claim of a probability requires prediction
                                evidence; any percentage must match a packet value
named openings                  an opening name must come from the opening tool,
                                and an unmatched lookup forbids naming one
FENs                            a quoted FEN must appear in the evidence — an
                                invented position is the worst possible failure
==========================================================  ==========================

What it deliberately does **not** do: judge whether a chess *interpretation* is
good, or whether prose is well-phrased. Interpretations are labelled as
interpretations by the response layer and carry their evidence references; they are
auditable by a human, not by a regex.

The leniency is stated once, plainly: a numeric claim is rejected only when that
number appears *nowhere* in the evidence. That catches invention. It cannot catch a
plausible recombination of two real numbers, and no claim is made that it does.
"""

from __future__ import annotations

import re
from typing import Any

from argus.ai_agent.core.evidence import EvidenceKind, EvidencePacket
from argus.ai_agent.core.response import Claim, ValidationReport

#: Centipawn tolerance when matching a claimed pawn figure (display rounding).
PAWN_TOLERANCE_CP = 12

#: Dash characters a model may use as a minus sign. A negative evaluation written
#: with a Unicode en dash or minus ("–0.87") would otherwise slip past the sign
#: check entirely — the regex only matches ASCII ``+``/``-`` — letting a fabricated
#: number pass validation. Every dash variant is normalised to ``-`` before scanning
#: (scanning only; the displayed text is not rewritten here).
_DASH_VARIANTS = "\u2212\u2013\u2014\u2012\u2010\u2011\u2015"
_DASH_TRANSLATION = {ord(char): "-" for char in _DASH_VARIANTS}

_EVAL_SIGNED = re.compile(r"(?<![\w.])([+-]\d+(?:\.\d+)?)\s*(pawns?|points?)?")
_MAGNITUDE_PAWNS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(pawns?|points?)")
_CENTIPAWNS = re.compile(r"(?<![\w.])(\d+)\s*centipawns?")
_MATE = re.compile(r"\b(?:mate in|m|#)\s*(\d+)\b", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")
_COUNT_CLAIM = re.compile(
    r"(?<![\w.])(\d+)\s+"
    r"(analysed|analyzed|games?|blunders?|mistakes?|inaccuracies?|errors?|moves?|plies|"
    r"occurrences?|wins?|losses|draws?)\b",
    re.IGNORECASE,
)
_OF_CLAIM = re.compile(
    r"(?<![\w.])(\d+)\s+of\s+(\d+)\s+(?:your\s+|my\s+|the\s+)?"
    r"(analysed|analyzed|games?|moves?)\b",
    re.IGNORECASE,
)
_FEN_CLAIM = re.compile(
    r"\b([rnbqkpRNBQKP1-8]+(?:/[rnbqkpRNBQKP1-8]+){7}\s+[wb]\s+(?:-|[KQkq]{1,4})\s+"
    r"(?:-|[a-h][1-8])\s+\d+\s+\d+)\b"
)
_PROBABILITY_LANGUAGE = re.compile(
    r"\b(probabilit(?:y|ies)|chance|chances|odds|likely to win|expected score)\b",
    re.IGNORECASE,
)

_opening_names_cache: tuple[str, ...] | None = None


def _opening_names() -> tuple[str, ...]:
    """The curated opening names, imported lazily.

    The import is deferred because the tools package imports this module's package
    (safety) on its way to the core types, so a top-level import here would close a
    cycle. The names are only needed when validating prose.
    """
    global _opening_names_cache
    if _opening_names_cache is None:
        from argus.ai_agent.tools.opening import _OPENINGS

        _opening_names_cache = tuple({name for name, _eco, _line in _OPENINGS})
    return _opening_names_cache


def _numbers_in(payload: Any, *, into: set[float] | None = None) -> set[float]:
    """Every numeric value anywhere in a payload (ints and floats)."""
    found: set[float] = set() if into is None else into
    if isinstance(payload, bool):
        return found
    if isinstance(payload, (int, float)):
        found.add(round(float(payload), 3))
        return found
    if isinstance(payload, str):
        for token in re.findall(r"-?\d+(?:\.\d+)?", payload):
            try:
                found.add(round(float(token), 3))
            except ValueError:  # pragma: no cover - defensive
                continue
        return found
    if isinstance(payload, dict):
        for value in payload.values():
            _numbers_in(value, into=found)
        return found
    if isinstance(payload, (list, tuple)):
        for value in payload:
            _numbers_in(value, into=found)
        return found
    return found


def _centipawn_values(packet: EvidencePacket) -> set[float]:
    """Centipawn and mate values the answer is allowed to quote."""
    allowed: set[float] = set()
    for item in packet.items:
        if item.kind is EvidenceKind.ENGINE:
            for key, value in _walk_items(item.data):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    key_lower = key.lower()
                    if "cp" in key_lower or "score" in key_lower or "eval" in key_lower:
                        allowed.add(float(value))
        if item.kind is EvidenceKind.MOVE_ANALYSIS:
            for key in (
                "eval_before_cp",
                "eval_after_cp",
                "eval_change_cp",
                "centipawn_loss",
                "played_eval_cp",
            ):
                value = item.data.get(key)
                if isinstance(value, (int, float)):
                    allowed.add(float(value))
        if item.kind is EvidenceKind.CRITICAL_MOMENT:
            evidence = item.data.get("evidence")
            if isinstance(evidence, dict):
                for key in ("swing_cp", "evaluation_before_white", "evaluation_after_white"):
                    value = evidence.get(key)
                    if isinstance(value, (int, float)):
                        allowed.add(float(value))
                allowed.add(float(evidence.get("severity_score") or 0.0))
        if item.kind is EvidenceKind.GAME_ANALYSIS:
            report = item.data.get("report")
            if isinstance(report, dict):
                for key, value in _walk_items(report):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if "cp" in key.lower() or "eval" in key.lower():
                            allowed.add(float(value))
    return allowed


def _walk_items(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten ``(key, value)`` pairs, keeping the key that produced each value."""
    pairs: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            pairs.extend(_walk_items(value, key))
    elif isinstance(payload, list):
        for value in payload:
            pairs.extend(_walk_items(value, prefix))
    else:
        pairs.append((prefix, payload))
    return pairs


def _mate_values(packet: EvidencePacket) -> set[int]:
    mates: set[int] = set()
    for item in packet.items:
        for key, value in _walk_items(item.data):
            if "mate" in key.lower() and isinstance(value, int) and not isinstance(value, bool):
                mates.add(int(value))
    return mates


def _percent_values(packet: EvidencePacket) -> set[float]:
    allowed: set[float] = set()
    for item in packet.items:
        if item.kind is EvidenceKind.PREDICTION:
            probabilities = item.data.get("probabilities") or {}
            if isinstance(probabilities, dict):
                for value in probabilities.values():
                    if isinstance(value, (int, float)):
                        allowed.add(round(float(value) * 100, 1))
    for value in _numbers_in([item.data for item in packet.items]):
        allowed.add(value)
    return allowed


def validate_answer(
    text: str,
    packet: EvidencePacket,
    *,
    claims: list[Claim] | None = None,
    max_findings: int = 40,
) -> ValidationReport:
    """Check the high-value claims in ``text`` against ``packet``."""
    report = ValidationReport()
    if not text:
        return report
    # Normalise dash variants so a Unicode minus is checked like an ASCII one.
    text = text.translate(_DASH_TRANSLATION)
    allowed_numbers = _numbers_in([item.data for item in packet.items])
    allowed_cp = _centipawn_values(packet)
    allowed_mates = _mate_values(packet)
    allowed_percent = _percent_values(packet)
    has_engine = bool(packet.of_kind(EvidenceKind.ENGINE, EvidenceKind.MOVE_ANALYSIS))

    findings = 0

    def record(claim: str, kind: str, ok: bool, detail: str = "") -> None:
        nonlocal findings
        if findings >= max_findings:
            return
        findings += 1
        report.add(claim, kind, ok, detail)

    # --- engine evaluations --------------------------------------------------
    for match in _EVAL_SIGNED.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "")
        if not unit and "." not in raw:
            # A bare "+2" is usually a move annotation or a list marker, not an eval.
            continue
        claimed_cp = float(raw) * 100
        if not allowed_cp:
            record(
                match.group(0),
                "engine_eval",
                False,
                "no engine evidence in this turn, so no evaluation can be quoted"
                if not has_engine
                else "no stored evaluation to match",
            )
            continue
        ok = any(abs(claimed_cp - value) <= PAWN_TOLERANCE_CP for value in allowed_cp)
        record(
            match.group(0),
            "engine_eval",
            ok,
            "" if ok else f"closest stored centipawn value differs by more than {PAWN_TOLERANCE_CP}cp",
        )
    for match in _MAGNITUDE_PAWNS.finditer(text):
        # Skip when the same span was already checked as a signed evaluation.
        span = text[max(0, match.start() - 1) : match.start()]
        if span.endswith(("+", "-")):
            continue
        claimed_cp = float(match.group(1)) * 100
        if not allowed_cp:
            record(match.group(0), "engine_eval_magnitude", False,
                   "no stored evaluation to match this figure against")
            continue
        ok = any(abs(claimed_cp - value) <= PAWN_TOLERANCE_CP for value in allowed_cp)
        record(match.group(0), "engine_eval_magnitude", ok,
               "" if ok else "no stored evaluation is within tolerance of this figure")
    for match in _CENTIPAWNS.finditer(text):
        claimed = float(match.group(1))
        if not allowed_cp:
            record(match.group(0), "engine_eval_cp", False, "no stored evaluation to match")
            continue
        ok = any(abs(claimed - value) <= PAWN_TOLERANCE_CP for value in allowed_cp)
        record(match.group(0), "engine_eval_cp", ok,
               "" if ok else "no stored evaluation is within tolerance of this value")
    for match in _MATE.finditer(text):
        claimed_mate = int(match.group(1))
        if not allowed_mates:
            # "mate in 3" is also how a human describes a known finish; only flag it
            # when the turn had no engine evidence at all to be consistent about.
            record(match.group(0), "mate_claim", True, "no engine mate score to compare (not checked)")
            continue
        record(match.group(0), "mate_claim", claimed_mate in allowed_mates,
               "" if claimed_mate in allowed_mates else "no stored mate score matches this")

    # --- counts --------------------------------------------------------------
    for match in _COUNT_CLAIM.finditer(text):
        number = float(match.group(1))
        if number not in allowed_numbers:
            record(match.group(0), "count", False,
                   "this number does not appear anywhere in the evidence for this turn")
        else:
            record(match.group(0), "count", True)
    for match in _OF_CLAIM.finditer(text):
        for group in (match.group(1), match.group(2)):
            number = float(group)
            if number not in allowed_numbers:
                record(match.group(0), "count", False,
                       f"the value {group} does not appear in the evidence")
                break
        else:
            record(match.group(0), "count", True)

    # --- percentages and probabilities --------------------------------------
    for match in _PERCENT.finditer(text):
        number = float(match.group(1))
        if number not in allowed_percent:
            record(match.group(0), "percentage", False,
                   "no stored value in this turn is equal to this percentage")
            continue
        record(match.group(0), "percentage", True)
    if _PROBABILITY_LANGUAGE.search(text):
        served = packet.of_kind(EvidenceKind.PREDICTION)
        if not served:
            record(
                "probability language",
                "probability",
                False,
                "the answer discusses a probability but this turn has no validated "
                "prediction evidence",
            )
        else:
            record("probability language", "probability", True)

    # --- openings -----------------------------------------------------------
    for name in _opening_names():
        if len(name) < 6 or name.lower() not in text.lower():
            continue
        opening_items = packet.of_kind(EvidenceKind.OPENING)
        if not opening_items:
            record(name, "opening", False,
                   "an opening is named but the opening tool was not consulted")
            continue
        matched = False
        for item in opening_items:
            if item.data.get("matched") is False:
                continue
            if str(item.data.get("name") or "").lower() == name.lower():
                matched = True
            elif name.lower() in str(item.data.get("name") or "").lower():
                matched = True
            elif str(item.data.get("eco") or "") and str(item.data.get("eco")) in item.summary:
                matched = True
        record(name, "opening", matched,
               "" if matched else "the opening tool did not return this name")

    # --- FENs ---------------------------------------------------------------
    for match in _FEN_CLAIM.finditer(text):
        fen = " ".join(match.group(1).split())
        known = any(
            fen == str(item.data.get(key) or "")
            for item in packet.items
            for key in ("fen", "fen_before", "fen_after", "current_fen")
        )
        record(fen, "fen", known,
               "" if known else "this position was not returned by any tool in this turn")

    # --- structured claims --------------------------------------------------
    if claims:
        refs = {item.ref() for item in packet.items}
        for claim in claims:
            if claim.kind.value in ("fact", "observation") and claim.evidence_refs:
                missing = [ref for ref in claim.evidence_refs if ref not in refs]
                record(claim.text[:60], "claim_reference", not missing,
                       "" if not missing else f"references evidence not in the packet: {missing}")
    return report


__all__ = ["PAWN_TOLERANCE_CP", "validate_answer"]
