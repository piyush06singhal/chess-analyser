"""Agent core: context, planning, evidence, the answer object and the loop.

The dependency order here is deliberate and one-directional:

``evidence`` → ``context`` → ``response`` → ``planner`` → ``collection`` → ``loop``

Nothing in the core imports the API layer, the database, or a provider SDK. The
agent is therefore testable with fixtures, and the same loop runs in production and
under test with no branching.
"""

from argus.ai_agent.core.collection import items_from_outcome, kind_for_tool, summarize
from argus.ai_agent.core.context import AgentContext, ResponseMode, SkillContext
from argus.ai_agent.core.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidencePacket,
    MissingEvidence,
)
from argus.ai_agent.core.planner import INTENT_TOOLS, Intent, Plan, plan
from argus.ai_agent.core.response import (
    AgentAction,
    AgentAnswer,
    AnswerAction,
    Claim,
    ClaimKind,
    ValidationFinding,
    ValidationReport,
)


def __getattr__(name: str):
    """Resolve the loop lazily.

    ``core.loop`` imports the tool contract, and the tool contract imports this
    package for its context types — so importing the loop here eagerly would close a
    cycle. Deferring it keeps ``from argus.ai_agent.core import CoachingAgent``
    working without the import-order fragility.
    """
    if name in {"CoachingAgent", "LLMClient"}:
        from argus.ai_agent.core import loop

        return getattr(loop, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "INTENT_TOOLS",
    "AgentAction",
    "AgentAnswer",
    "AgentContext",
    "AnswerAction",
    "Claim",
    "ClaimKind",
    "EvidenceItem",
    "EvidenceKind",
    "EvidencePacket",
    "Intent",
    "LLMClient",
    "MissingEvidence",
    "Plan",
    "ResponseMode",
    "SkillContext",
    "ValidationFinding",
    "ValidationReport",
    "items_from_outcome",
    "kind_for_tool",
    "plan",
    "summarize",
]
