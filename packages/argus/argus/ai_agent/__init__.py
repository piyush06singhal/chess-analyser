"""Caissa AI chess agent.

The agent is **not** a chatbot with chess opinions. It is a tool-using layer over
services Caissa already trusts: Stockfish, Game Intelligence, Player Intelligence,
the opening and concept knowledge bases, and — only where a model has passed its
production gate — validated predictions. The language model supplies language. It
supplies no chess facts, and the architecture is arranged so that it cannot:

    question
      → resolve context (which game, which ply)
      → plan (intent, shortlisted tools)
      → tools (validated, authorized, budgeted)
      → EvidencePacket (typed, with provenance and recorded absences)
      → generate (from the packet, under an explicit prohibition)
      → validate (high-value claims checked against the packet)
      → answer + evidence + trace + validation

Two generations of API live in this package, and both work:

**Phase 7** — :class:`~argus.ai_agent.core.loop.CoachingAgent` with
:func:`~argus.ai_agent.tools.build_agent_toolbox`, context awareness, evidence
packets, permissions, limits and validation. This is what the coach chat uses.

**Phase 1** — :class:`~argus.ai_agent.agent.ChessCoachAgent` with
:func:`~argus.ai_agent.tools.build_default_registry`: the original engine-only
tool shell. It is kept unchanged because it is the registry to use when no data
layer is wired in, and because its behaviour is pinned by tests.
"""

from argus.ai_agent.agent import ChessCoachAgent, LLMClient, LLMNotConfiguredError
from argus.ai_agent.core.context import AgentContext, ResponseMode, SkillContext
from argus.ai_agent.core.evidence import EvidenceItem, EvidenceKind, EvidencePacket
from argus.ai_agent.core.loop import CoachingAgent
from argus.ai_agent.core.response import AgentAction, AgentAnswer, AnswerAction, Claim, ClaimKind
from argus.ai_agent.memory.conversation import ConversationMemory, Focus
from argus.ai_agent.observability import AgentTrace, ToolCallRecord, scrub
from argus.ai_agent.prompts import PROMPT_VERSION, build_context_block, load_system_prompt
from argus.ai_agent.safety.limits import AgentLimits
from argus.ai_agent.safety.validation import validate_answer
from argus.ai_agent.streaming import TurnEvent, TurnEventKind, turn_event_stream
from argus.ai_agent.tools import (
    AgentProviders,
    AgentTool,
    Tool,
    ToolOutcome,
    ToolPermission,
    ToolRegistry,
    ToolSchema,
    Toolbox,
    build_agent_toolbox,
    build_default_registry,
)

__all__ = [
    "PROMPT_VERSION",
    "AgentAction",
    "AgentAnswer",
    "AgentContext",
    "AgentLimits",
    "AgentProviders",
    "AgentTool",
    "AgentTrace",
    "AnswerAction",
    "ChessCoachAgent",
    "Claim",
    "ClaimKind",
    "CoachingAgent",
    "ConversationMemory",
    "EvidenceItem",
    "EvidenceKind",
    "EvidencePacket",
    "Focus",
    "LLMClient",
    "LLMNotConfiguredError",
    "ResponseMode",
    "SkillContext",
    "Tool",
    "ToolCallRecord",
    "ToolOutcome",
    "ToolPermission",
    "ToolRegistry",
    "ToolSchema",
    "Toolbox",
    "TurnEvent",
    "TurnEventKind",
    "build_agent_toolbox",
    "build_context_block",
    "build_default_registry",
    "load_system_prompt",
    "scrub",
    "turn_event_stream",
    "validate_answer",
]
