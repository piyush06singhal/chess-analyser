"""The agent loop: plan → call tools → build evidence → answer → validate.

This is the whole Phase 7 contract in one function, and the shape of the loop is
the point:

    question
      → resolve context (which game, which ply)
      → plan (what is being asked, which tools could answer it)
      → [deterministic fast path? answer from tools, no model]
      → tool calls, budgeted, results collected as evidence
      → generate the answer from the evidence
      → validate the answer against the evidence
      → answer, with its evidence, trace and validation attached

Four properties are worth stating plainly, because each is a deliberate choice:

**No model, no fabrication.** When no LLM provider is configured the agent does not
degrade into guessing or into a fake conversation: it answers from tools when the
question is a stored fact, and otherwise returns an honest unavailable result that
says what is missing.

**Bounded.** Iterations, tool calls, engine searches, prompt size and response size
all have ceilings (:mod:`argus.ai_agent.safety.limits`). Hitting one stops the loop
and records it; it never silently continues.

**Evidence-first.** Tools run *before* generation. The model receives the tool
results as evidence and is told explicitly that it may not add to them. This is what
makes "never invent an evaluation" enforceable rather than aspirational — the
guardrail is the prompt *and* the validator, and the validator has the last word.

**Self-reporting.** The answer carries which claims were machine-checked and whether
they agreed, which tool calls happened and how long they took, and what could not be
retrieved. A reader can audit the turn instead of trusting it.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Protocol

from argus.ai_agent.core.collection import items_from_outcome
from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.core.evidence import EvidenceKind, EvidencePacket
from argus.ai_agent.core.planner import Plan, plan as build_plan
from argus.ai_agent.core.response import (
    AgentAction,
    AgentAnswer,
    AnswerAction,
    Claim,
    ClaimKind,
    ValidationReport,
)
from argus.ai_agent.memory.context import resolve_context
from argus.ai_agent.memory.conversation import ConversationMemory
from argus.ai_agent.observability import AgentTrace, ToolCallRecord
from argus.ai_agent.prompts import PROMPT_VERSION, build_messages
from argus.ai_agent.safety.limits import AgentLimits, Budget, TurnClock
from argus.ai_agent.safety.validation import validate_answer
from argus.ai_agent.streaming import TurnEventKind
from argus.ai_agent.tools.base import Tool, ToolOutcome, Toolbox
from argus.shared.errors import ArgusError
from argus.shared.logging import get_logger

logger = get_logger(__name__)


class LLMClient(Protocol):
    """The provider contract (see :mod:`argus.llm.base`)."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]: ...


#: Fallback when a question is recognised as a stored fact but has no handler yet.
_FAST_PATH_TOOLS: dict[str, tuple[str, ...]] = {
    "player_counts": ("get_player_statistics", "get_player_profile"),
    "prediction_status": ("get_prediction_status",),
}


class CoachingAgent:
    """The Phase 7 agent: tools first, prose second, validation last."""

    def __init__(
        self,
        toolbox: Toolbox,
        *,
        llm_client: LLMClient | None = None,
        limits: AgentLimits | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.toolbox = toolbox
        self.llm_client = llm_client
        self.limits = limits or AgentLimits()
        self.provider_name = provider_name
        self.model_name = model_name
        #: Per-turn streaming sink (see :mod:`argus.ai_agent.streaming`). An instance
        #: answers one turn at a time — the API builds one per request — so the sink
        #: can live on the instance for the duration of a turn without a race.
        self._event_sink: Any | None = None

    # --- public API ----------------------------------------------------------

    def ask(
        self,
        question: str,
        *,
        context: AgentContext | None = None,
        memory: ConversationMemory | None = None,
        request_id: str | None = None,
        on_event: Any | None = None,
    ) -> AgentAnswer:
        """Answer one question end to end.

        ``on_event``, when given, receives the turn's lifecycle frames as they
        happen (see :mod:`argus.ai_agent.streaming`). It is scoped to this call and
        cleared afterwards, so a later turn never leaks frames to an earlier sink.
        """
        self._event_sink = on_event
        try:
            return self._turn(question, context=context, memory=memory, request_id=request_id)
        finally:
            self._event_sink = None

    def _emit(self, kind: TurnEventKind, **data: Any) -> None:
        """Publish one lifecycle frame to this turn's sink, if it has one.

        A subscriber is a UI concern, so a broken one must never break an answer:
        failures are logged and swallowed.
        """
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink({"event": kind.value, **data})
        except Exception:  # noqa: BLE001 — the answer must survive a bad subscriber
            logger.exception("Agent event sink raised; the turn continues")

    def _turn(
        self,
        question: str,
        *,
        context: AgentContext | None = None,
        memory: ConversationMemory | None = None,
        request_id: str | None = None,
    ) -> AgentAnswer:
        """The body of one turn (split out so ``ask`` can scope the event sink)."""
        clock = TurnClock(self.limits.timeout_seconds)
        trace = AgentTrace(
            request_id=request_id or uuid.uuid4().hex[:12],
            question=question,
            mode=(context or AgentContext()).mode.value,
        )
        resolved = resolve_context(explicit=context, memory=memory, question=question)
        trace.context = resolved.compact()
        started = time.perf_counter()
        plan = build_plan(
            question,
            has_game=resolved.has_game(),
            has_player=bool(resolved.player_id),
        )
        trace.time("plan", (time.perf_counter() - started) * 1000)
        trace.intents = [intent.value for intent in plan.intents]
        self._emit(
            TurnEventKind.PLAN,
            intents=trace.intents,
            suggested_tools=list(plan.suggested_tools),
            deterministic=plan.deterministic_candidate,
            notes=list(plan.notes),
        )

        packet = EvidencePacket(
            question=question,
            context_summary=resolved.compact(),
        )
        budget = Budget(self.limits)

        answer = self._answer(
            question,
            context=resolved,
            plan=plan,
            packet=packet,
            budget=budget,
            trace=trace,
            clock=clock,
            memory=memory,
        )
        answer.evidence = packet
        answer.mode = resolved.mode
        answer.deterministic = answer.deterministic or self.llm_client is None
        answer.prompt_version = PROMPT_VERSION if not answer.deterministic else None
        answer.trace = {
            **trace.to_dict(),
            "budget": budget.to_dict(),
            "evidence_items": len(packet.items),
        }
        # The closing frames, in the order the consumer needs them: what was
        # retrieved, the answer itself, what the validator concluded, then done.
        self._emit(
            TurnEventKind.EVIDENCE,
            items=len(packet.items),
            missing=[{"tool": entry.tool, "reason": entry.reason} for entry in packet.missing],
            kinds=sorted({item.kind.value for item in packet.items}),
        )
        self._emit(
            TurnEventKind.ANSWER,
            message=answer.message,
            deterministic=answer.deterministic,
            provider=answer.provider,
            model=answer.model,
            mode=answer.mode.value,
            actions=[action.action.value for action in answer.actions],
        )
        self._emit(
            TurnEventKind.VALIDATION,
            passed=answer.validation.passed,
            checked=answer.validation.checked,
            summary=answer.validation.summary(),
        )
        self._emit(TurnEventKind.DONE, status=trace.status, total_ms=trace.total_ms)
        return answer

    # --- the turn ------------------------------------------------------------

    def _answer(
        self,
        question: str,
        *,
        context: AgentContext,
        plan: Plan,
        packet: EvidencePacket,
        budget: Budget,
        trace: AgentTrace,
        clock: TurnClock,
        memory: ConversationMemory | None,
    ) -> AgentAnswer:
        # 1. Deterministic fast path: a stored fact needs no generation.
        if plan.deterministic_candidate and plan.fast_path:
            fast = self._fast_path(plan, context=context, packet=packet, trace=trace)
            if fast is not None:
                return fast

        # 2. No provider: gather what the deterministic tools can prove, then be honest.
        if self.llm_client is None:
            self._call_suggested_tools(plan, context=context, packet=packet, budget=budget, trace=trace)
            trace.status = "llm_unavailable"
            return self._no_provider_answer(plan, packet=packet, context=context, trace=trace)

        # 3. Gather initial evidence from the plan's suggestions.
        self._call_suggested_tools(plan, context=context, packet=packet, budget=budget, trace=trace)

        # 4. Generate, with tool calls allowed inside the loop.
        return self._generate(
            question,
            context=context,
            plan=plan,
            packet=packet,
            budget=budget,
            trace=trace,
            clock=clock,
            memory=memory,
        )

    # --- deterministic paths -------------------------------------------------

    def _fast_path(
        self,
        plan: Plan,
        *,
        context: AgentContext,
        packet: EvidencePacket,
        trace: AgentTrace,
    ) -> AgentAnswer | None:
        """Answer a stored-fact question from tools, with no model call."""
        if plan.fast_path == "prediction_status":
            outcome = self.toolbox.call("get_prediction_status", {}, context)
            self._collect(outcome, packet=packet, trace=trace)
            if outcome.ok:
                available = [
                    entry["task"] for entry in outcome.data.get("tasks", []) if entry.get("available")
                ]
                # A model is available: the refusal is not the answer. Hand the turn
                # to generation so the model's own output (through the gated tool)
                # is what the reader sees.
                if available:
                    return None
                message = (
                    "Caissa cannot provide a win probability. No prediction model has "
                    "passed its production gate yet, so the system serves no "
                    "probabilities at all — and it will not estimate one. The "
                    "declared tasks and their requirements are available in the "
                    "Predictions view."
                )
                return self._deterministic_answer(message, packet=packet, trace=trace)

        if plan.fast_path == "player_counts":
            tools = _FAST_PATH_TOOLS["player_counts"]
            for name in tools:
                if name not in self.toolbox.names(context):
                    continue
                outcome = self.toolbox.call(name, {}, context)
                self._collect(outcome, packet=packet, trace=trace)
                if not outcome.ok:
                    continue
                analyzed = outcome.data.get("analyzed_games")
                imported = outcome.data.get("imported_games")
                coverage = outcome.data.get("coverage")
                games = outcome.data.get("games") or {}
                parts = [f"Caissa has analysed {analyzed} game(s) for this player"]
                if imported is not None:
                    parts.append(f"out of {imported} imported")
                parts.append(f"(coverage: {coverage})")
                if games:
                    parts.append(
                        f"— {games.get('wins')}W / {games.get('draws')}D / {games.get('losses')}L"
                    )
                message = " ".join(parts) + "."
                return self._deterministic_answer(message, packet=packet, trace=trace)
        return None

    def _deterministic_answer(
        self, message: str, *, packet: EvidencePacket, trace: AgentTrace
    ) -> AgentAnswer:
        refs = [item.ref() for item in packet.items]
        answer = AgentAnswer(
            message=message,
            claims=[
                Claim(
                    kind=ClaimKind.OBSERVATION,
                    text=message,
                    evidence_refs=refs,
                    verified=True,
                    note="read directly from stored Caissa data",
                )
            ],
            deterministic=True,
            limitations=list(packet.limitations),
            trace={"status": trace.status},
        )
        answer.actions = self.actions_for(packet, context=None)
        answer.validation = validate_answer(message, packet, claims=answer.claims)
        return answer

    def _no_provider_answer(
        self,
        plan: Plan,
        *,
        packet: EvidencePacket,
        context: AgentContext,
        trace: AgentTrace,
    ) -> AgentAnswer:
        """The honest answer when no LLM provider is configured.

        Deliberately not a fake coaching reply: it says what Caissa can and cannot do
        right now, and it still reports the deterministic evidence it did gather.
        """
        found = len(packet.items)
        lines = [
            "Caissa has no LLM provider configured, so it cannot write a coaching "
            "explanation right now. The chess analysis itself is unaffected."
        ]
        if found:
            lines.append(
                f"Caissa did retrieve {found} piece(s) of stored evidence for this "
                "question:"
            )
            lines.extend(f"• {item.summary}" for item in packet.items[:5])
        else:
            lines.append("No stored evidence matched this question either.")
        if packet.missing:
            lines.append(
                "Unavailable: " + "; ".join(f"{entry.tool} ({entry.reason})" for entry in packet.missing[:4])
            )
        lines.append(
            "Set ARGUS_LLM_PROVIDER (openai | anthropic | groq | echo) and ARGUS_LLM_API_KEY "
            "for full coaching."
        )
        message = "\n".join(lines)
        answer = AgentAnswer(
            message=message,
            deterministic=True,
            limitations=[
                "No LLM provider is configured, so no explanation was generated.",
                *packet.limitations,
            ],
            trace={"status": trace.status, "intents": [i.value for i in plan.intents]},
        )
        answer.actions = self.actions_for(packet, context=context)
        answer.validation = validate_answer(message, packet)
        return answer

    # --- generation -----------------------------------------------------------

    def _tool_budget_for(self, plan: Plan, context: AgentContext) -> list[str]:
        """The tools offered to the model this turn.

        A focused list, not the whole catalogue: an agent handed twenty tools calls
        more of them, which costs latency and invites irrelevant searches. When the
        planner recognises nothing specific, the model gets everything it may use —
        a general question deserves a general toolset.
        """
        usable = self.toolbox.names(context)
        suggested = [name for name in plan.suggested_tools if name in usable]
        if not suggested:
            return sorted(usable)
        # Capability questions still need the honesty tools available.
        for extra in ("get_prediction_status", "get_training_requirements", "search_chess_knowledge"):
            if extra in usable and extra not in suggested:
                suggested.append(extra)
        return suggested

    def _generate(
        self,
        question: str,
        *,
        context: AgentContext,
        plan: Plan,
        packet: EvidencePacket,
        budget: Budget,
        trace: AgentTrace,
        clock: TurnClock,
        memory: ConversationMemory | None,
    ) -> AgentAnswer:
        tool_names = self._tool_budget_for(plan, context)
        history = memory.render(max_chars=self.limits.max_history_chars) if memory else ""
        messages = build_messages(
            context=context,
            question=question,
            history=history,
            plan=plan,
            tool_names=tool_names,
            evidence=packet,
        )
        specs = [spec for spec in self.toolbox.to_tool_specs(context) if spec["function"]["name"] in set(tool_names)]
        response: dict[str, Any] = {}
        text = ""

        while True:
            if clock.expired():
                budget.note_reached(
                    f"time budget of {self.limits.timeout_seconds}s exhausted"
                )
                trace.note("Turn time budget exhausted; answering with gathered evidence.")
                break
            if not budget.can_iterate():
                budget.note_reached(
                    f"iteration budget reached ({self.limits.max_iterations})"
                )
                break
            budget.note_iteration()
            started = time.perf_counter()
            try:
                response = self.llm_client.complete(messages, tools=specs)  # type: ignore[union-attr]
            except ArgusError as exc:
                trace.status = "llm_error"
                trace.note(f"LLM provider error: {exc.message}")
                packet.note_limitation(
                    "The language model call failed, so this answer reports evidence "
                    "only."
                )
                return self._evidence_only_answer(
                    packet, context=context, trace=trace, plan=plan, reason=exc.message
                )
            trace.time("llm", (time.perf_counter() - started) * 1000)
            trace.provider = self.provider_name
            trace.model = self.model_name

            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                text = str(response.get("message") or "")
                break

            # The model asked for tools: run them, record them, feed them back.
            # Each result carries the call id so the provider can match it to its
            # request (required by the OpenAI wire format).
            messages.append({"role": "assistant", "content": response.get("message") or "", "tool_calls": tool_calls})
            for index, call in enumerate(tool_calls):
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or f"call_{index}")
                arguments = call.get("arguments") or {}
                tool = self.toolbox.get(name) if name in self.toolbox.names(context) else None
                can, why = budget.can_call_tool(
                    name, uses_engine=bool(tool and tool.schema.uses_engine)
                )
                if not can:
                    budget.note_reached(why)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": {"status": "error", "error": {"code": "budget_exhausted", "message": why}},
                        }
                    )
                    continue
                budget.note_tool_call(uses_engine=bool(tool and tool.schema.uses_engine))
                outcome = self.toolbox.call(name, arguments, context)
                self._collect(outcome, packet=packet, trace=trace, arguments=arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": (
                            {"status": "ok", "result": _bounded(outcome.data, self.limits.max_tool_result_chars)}
                            if outcome.ok
                            else {"status": "error", "error": outcome.error}
                        ),
                    }
                )
            # Refresh the evidence block so the next iteration sees what was fetched.
            messages.append(
                {
                    "role": "system",
                    "content": "EVIDENCE NOW AVAILABLE\n" + packet.as_prompt_block(
                        max_chars=self.limits.max_evidence_chars
                    ),
                }
            )

        if not text:
            return self._evidence_only_answer(packet, context=context, trace=trace, plan=plan)

        text = _sanitize_answer_text(text)
        if len(text) > self.limits.max_response_chars:
            text = text[: self.limits.max_response_chars].rstrip() + "…"
        trace.status = "ok" if not budget.exhausted else "ok_with_limits"
        validation = validate_answer(text, packet)
        trace.validation_passed = validation.passed
        trace.validation_checked = validation.checked
        answer = AgentAnswer(
            message=text,
            claims=_claims_from_validation(validation),
            actions=self.actions_for(packet, context=context),
            validation=validation,
            provider=self.provider_name,
            model=self.model_name,
            prompt_version=PROMPT_VERSION,
            limitations=list(packet.limitations)
            + (
                [f"Answer produced within a reduced budget: {', '.join(budget.reached)}."]
                if budget.exhausted
                else []
            ),
            trace={"status": trace.status},
        )
        return answer

    def _evidence_only_answer(
        self,
        packet: EvidencePacket,
        *,
        context: AgentContext,
        trace: AgentTrace,
        plan: Plan,
        reason: str | None = None,
    ) -> AgentAnswer:
        """What to return when generation failed but tools succeeded.

        The provider's own reason is carried through: a rate-limited provider and
        an unconfigured one are different problems with different fixes, and the
        reader should be able to tell them apart.
        """
        header = "Caissa could not generate an explanation this turn."
        if reason:
            header = f"Caissa could not generate an explanation this turn ({reason})."
        lines = [header + " The stored evidence is:"]
        lines.extend(f"• {item.summary}" for item in packet.items[:6])
        if not packet.items:
            lines = [
                header + " No stored evidence matched this question either."
            ]
        message = "\n".join(lines)
        answer = AgentAnswer(
            message=message,
            deterministic=True,
            limitations=[
                f"Generation failed: {reason}" if reason else "Generation failed.",
                "Only retrieved evidence is reported.",
            ],
            trace={"status": trace.status, "intents": [i.value for i in plan.intents]},
        )
        answer.actions = self.actions_for(packet, context=context)
        return answer

    # --- tool execution helpers ----------------------------------------------

    def _call_suggested_tools(
        self,
        plan: Plan,
        *,
        context: AgentContext,
        packet: EvidencePacket,
        budget: Budget,
        trace: AgentTrace,
        max_tools: int = 3,
        max_missing_notes: int = 8,
    ) -> None:
        """Run the plan's first few suggested tools up front.

        Evidence before generation is what keeps the model anchored: it receives
        real numbers rather than being asked to decide whether to fetch them.

        The tools the plan wanted but *cannot* have are recorded too — both those
        missing from this deployment and those whose context is absent (no active
        player, no selected move). Recording them is what lets the answer say "Caissa
        has no player profile open" instead of the model inventing a plausible
        reason, and it costs no budget because nothing executes.
        """
        usable = self.toolbox.names(context)
        count = 0
        missing_notes = 0
        for name in plan.suggested_tools:
            if count >= max_tools:
                break
            if name not in usable:
                if missing_notes < max_missing_notes:
                    missing_notes += 1
                    self._note_unavailable(packet, name, context)
                continue
            tool = self.toolbox.get(name)
            can, why = budget.can_call_tool(name, uses_engine=tool.schema.uses_engine)
            if not can:
                budget.note_reached(why)
                break
            budget.note_tool_call(uses_engine=tool.schema.uses_engine)
            outcome = self.toolbox.call(name, self._context_arguments(tool, context), context)
            self._collect(outcome, packet=packet, trace=trace)
            count += 1
            # A successful lookup of the thing the question is about is enough; no
            # need to spend the budget on the rest of the shortlist.
            if outcome.ok and name in ("get_move_analysis", "get_player_insights"):
                break

        # Tools the planner wanted but discarded because this context cannot support
        # them: "no active player" is exactly the sentence the user needs.
        for name in plan.discouraged_tools[:max_missing_notes]:
            self._note_unavailable(packet, name, context)

    def _context_arguments(self, tool: Tool, context: AgentContext) -> dict[str, Any]:
        """Fill a tool's *required* arguments from the board the user is looking at.

        The pre-generation gather has no model to supply arguments. When a suggested
        tool needs a FEN, a game or a ply, the context already holds it — so pass it,
        and "what is the evaluation here?" can actually reach the engine (or report
        its absence) instead of failing with a bare "missing argument" that hides the
        real gap. Only required fields are filled; nothing is invented.
        """
        parameters = tool.schema.parameters or {}
        properties = parameters.get("properties") or {}
        supplied: dict[str, Any] = {}
        for name in parameters.get("required") or []:
            if name not in properties:
                continue
            if name == "fen" and context.current_fen:
                supplied[name] = context.current_fen
            elif name == "game_id" and context.active_game_id:
                supplied[name] = context.active_game_id
            elif name == "ply" and context.selected_ply is not None:
                supplied[name] = context.selected_ply
        return supplied

    def _note_unavailable(self, packet: EvidencePacket, name: str, context: AgentContext) -> None:
        """Record why a tool the plan wanted cannot run.

        The reason comes from the tool itself — "no active player in this conversation",
        "Caissa cannot reach stored move analysis in this deployment" — so the answer
        describes the real gap rather than a guess at one.
        """
        if any(entry.tool == name for entry in packet.missing):
            return
        try:
            tool = self.toolbox.get(name)
        except ArgusError:
            return
        _usable, reason = tool.is_usable(context)
        resolved = reason or tool.reason or "not available in this turn"
        packet.note_missing(name, resolved)
        self._emit(TurnEventKind.MISSING, tool=name, reason=resolved)

    def _collect(
        self,
        outcome: ToolOutcome,
        *,
        packet: EvidencePacket,
        trace: AgentTrace,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        """Record a tool outcome as trace + evidence (or as a recorded absence)."""
        trace.add_tool_call(
            ToolCallRecord(
                name=outcome.tool,
                ok=outcome.ok,
                duration_ms=outcome.duration_ms,
                error_code=outcome.error_code or None,
                error_message=outcome.error_message or None,
                arguments=arguments or {},
                result_bytes=len(str(outcome.data)),
            )
        )
        self._emit(
            TurnEventKind.TOOL_CALL,
            tool=outcome.tool,
            ok=outcome.ok,
            duration_ms=round(outcome.duration_ms, 1),
            error_code=outcome.error_code or None,
        )
        if not outcome.ok:
            reason = outcome.error_message or "tool call failed"
            packet.note_missing(outcome.tool, reason)
            self._emit(TurnEventKind.MISSING, tool=outcome.tool, reason=reason)
            return
        for item in items_from_outcome(outcome.tool, outcome.data):
            packet.add(item)

    # --- actions ---------------------------------------------------------------

    def actions_for(
        self, packet: EvidencePacket, *, context: AgentContext | None
    ) -> list[AgentAction]:
        """The buttons this answer can actually back with data (spec §29).

        An action is emitted only when the data exists: a "show best line" button on
        an answer with no principal variation would navigate to an empty board, which
        is precisely the fake functionality the spec forbids.
        """
        actions: list[AgentAction] = []
        move_items = packet.of_kind(EvidenceKind.MOVE_ANALYSIS)
        # Prefer the game the evidence actually came from; fall back to the game the
        # conversation is standing in, so a deterministic answer (which retrieves no
        # game evidence) still offers the actions that game supports.
        game_id = None
        for item in packet.items:
            if item.game_id:
                game_id = item.game_id
                break
        if game_id is None and context is not None:
            game_id = context.active_game_id
        ply = move_items[0].ply if move_items and move_items[0].ply is not None else (
            context.selected_ply if context else None
        )

        if game_id and ply is not None:
            actions.append(
                AgentAction(
                    action=AnswerAction.SHOW_POSITION,
                    label=f"Show position (move {((ply + 1) // 2)})",
                    href=f"/game/{game_id}?ply={ply}",
                    params={"game_id": game_id, "ply": ply},
                )
            )
        if move_items:
            data = move_items[0].data
            variation = data.get("principal_variation") or []
            best_san = data.get("best_move_san")
            if variation and game_id and ply is not None:
                actions.append(
                    AgentAction(
                        action=AnswerAction.SHOW_BEST_LINE,
                        label=f"Show best line ({best_san or 'engine choice'})",
                        href=f"/game/{game_id}?ply={ply}",
                        params={
                            "game_id": game_id,
                            "ply": ply,
                            "variation": variation[:8],
                            "best_move": best_san,
                        },
                    )
                )
            if best_san and data.get("san"):
                actions.append(
                    AgentAction(
                        action=AnswerAction.COMPARE_MOVES,
                        label=f"Compare {data.get('san')} with {best_san}",
                        params={
                            "game_id": game_id,
                            "ply": ply,
                            "played": data.get("san"),
                            "best": best_san,
                        },
                    )
                )
        moments = packet.of_kind(EvidenceKind.CRITICAL_MOMENT)
        if moments and game_id:
            moment_ply = next((item.ply for item in moments if item.ply is not None), None)
            if moment_ply is not None:
                actions.append(
                    AgentAction(
                        action=AnswerAction.VIEW_CRITICAL_MOMENT,
                        label="View critical moment",
                        href=f"/game/{game_id}?ply={moment_ply}",
                        params={"game_id": game_id, "ply": moment_ply},
                    )
                )
        insights = packet.of_kind(EvidenceKind.PLAYER_INSIGHT)
        if insights:
            player = str(insights[0].data.get("player_id") or (context.player_id if context else "") or "")
            insight_id = str(insights[0].data.get("id") or "")
            if player:
                actions.append(
                    AgentAction(
                        action=AnswerAction.VIEW_PLAYER_PATTERN,
                        label="View player pattern",
                        href=f"/players/{player}",
                        params={"player_id": player, "insight_id": insight_id},
                    )
                )
        profile_item = next(iter(packet.of_kind(EvidenceKind.PLAYER_PROFILE)), None)
        if profile_item and not insights:
            player = str(profile_item.data.get("player_id") or "")
            if player:
                actions.append(
                    AgentAction(
                        action=AnswerAction.OPEN_PLAYER,
                        label="Open player dashboard",
                        href=f"/players/{player}",
                        params={"player_id": player},
                    )
                )
        if game_id:
            actions.append(
                AgentAction(
                    action=AnswerAction.OPEN_GAME,
                    label="Open the game",
                    href=f"/game/{game_id}",
                    params={"game_id": game_id},
                )
            )
        # Training is built: a game context can generate exercises straight from
        # this game's own analysed mistakes. The action points at the real training
        # surface (`/training?game=<id>` generates and shows them), so it is a live
        # control rather than a promise.
        if game_id:
            actions.append(
                AgentAction(
                    action=AnswerAction.CREATE_PUZZLE,
                    label="Practise this game",
                    href=f"/training?game={game_id}",
                    params={"game_id": game_id},
                )
            )
        return actions


def _claims_from_validation(validation: ValidationReport) -> list[Claim]:
    """Re-express the validator's findings as typed claims.

    The claims are what was *checked*, not a parse of the prose: claiming to have
    typed every sentence would overstate what this layer can do.
    """
    kind_by_check = {
        "engine_eval": ClaimKind.FACT,
        "engine_eval_magnitude": ClaimKind.FACT,
        "engine_eval_cp": ClaimKind.FACT,
        "mate_claim": ClaimKind.FACT,
        "count": ClaimKind.OBSERVATION,
        "percentage": ClaimKind.OBSERVATION,
        "probability": ClaimKind.OBSERVATION,
        "opening": ClaimKind.OBSERVATION,
        "fen": ClaimKind.FACT,
        "claim_reference": ClaimKind.INTERPRETATION,
    }
    claims: list[Claim] = []
    for finding in validation.findings:
        claims.append(
            Claim(
                kind=kind_by_check.get(finding.kind, ClaimKind.OBSERVATION),
                text=finding.claim,
                evidence_refs=[],
                verified=finding.ok,
                note=finding.detail or None,
            )
        )
    return claims


#: Provider-invented citation markers. Some models (Groq's gpt-oss family among
#: them) append their own bracketed source tags to sentences — ``【2†data】``,
#: ``[3]``, ``[source]``. Caissa does not render those tokens, so they arrive in the
#: user's message as noise that looks like a broken reference. They are stripped
#: rather than shown; the real provenance is the evidence packet the answer carries.
_CITATION_MARKER = re.compile(
    r"\s*(?:【[^】]*】|\[\[?\d+\]?\]|\[(?:data|source|citation|ref|cite)\])",
    re.IGNORECASE,
)

#: A Unicode minus/en dash/em dash used as a minus sign (directly before a digit).
#: It is normalised to ASCII ``-`` so the reader sees a consistent minus and the
#: validator's sign check cannot be sidestepped by a lookalike character.
_UNICODE_MINUS = re.compile(r"[\u2212\u2013\u2014](?=\d)")


def _sanitize_answer_text(text: str) -> str:
    """Remove provider citation artifacts and tidy the whitespace they leave.

    This touches presentation only. It never adds, removes or rewrites a chess
    fact, so it cannot make the answer less faithful to the evidence; the validator
    still sees the same numbers.
    """
    cleaned = _CITATION_MARKER.sub("", text)
    cleaned = _UNICODE_MINUS.sub("-", cleaned)
    # A marker removed from the end of a word can leave a space before punctuation.
    cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _bounded(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Keep a tool result inside the per-call character budget.

    Truncation is declared in the payload rather than applied silently: a model fed
    half a result without being told will happily describe the missing half.
    """
    rendered = str(payload)
    if len(rendered) <= max_chars:
        return payload
    return {
        "truncated": True,
        "note": (
            f"Tool result was {len(rendered)} characters and has been trimmed for the "
            f"model. Do not describe the parts you cannot see."
        ),
        "excerpt": rendered[:max_chars],
    }


__all__ = ["CoachingAgent", "LLMClient"]
