"""Prediction tool: the door that stays shut until a model has earned it.

Phase 6 built the gate and refused to serve anything. This tool is the agent's only
route to a probability, and it is deliberately one line of policy:

    ask the Phase 6 service → it returns a served prediction from a PRODUCTION
    model, or nothing → nothing means the agent says so

There is no fallback, no local estimate, and no "roughly". A language model asked
for a win probability will produce one; that number would be an opinion wearing a
percent sign, and this module exists so the agent has no way to obtain one that did
not come out of a validated model (spec §12, §13).

The probability and the *data coverage* stay separate all the way through to the
prose, because they answer different questions: "what does the model think" and
"how much data is that resting on". Folding them into a single "confidence" figure
is the specific fabrication Phase 6 was built to prevent.
"""

from __future__ import annotations

from typing import Any

from argus.ai_agent.core.context import AgentContext
from argus.ai_agent.tools.base import Tool, ToolPermission, ToolSchema
from argus.ai_agent.tools.providers import AgentProviders
from argus.shared.errors import NotFoundError

#: The declared tasks. Kept in sync with `argus.ml.tasks` by name only — the agent
#: never needs the definitions, just the identifiers to ask about.
KNOWN_TASKS = (
    "game_outcome",
    "position_outcome",
    "position_difficulty",
    "move_error_risk",
    "player_performance",
)


def build_prediction_tools(providers: AgentProviders) -> list[Tool]:
    """The prediction tool family."""

    def _status(task: str) -> dict[str, Any]:
        if providers.prediction_status is None:
            return {
                "available": False,
                "reason": (
                    "The prediction service is not wired into this deployment, so no "
                    "validated model can be consulted."
                ),
            }
        payload = providers.prediction_status(task) or {}
        return payload

    def get_prediction_status(
        _context: AgentContext, task: str | None = None
    ) -> dict[str, Any]:
        if task:
            if task not in KNOWN_TASKS:
                raise NotFoundError(
                    f"'{task}' is not a Caissa prediction task. Known tasks: "
                    f"{', '.join(KNOWN_TASKS)}."
                )
            status = _status(task)
            return {"task": task, "available": bool(status.get("available")), **status}
        by_task: list[dict[str, Any]] = []
        for name in KNOWN_TASKS:
            status = _status(name)
            by_task.append(
                {
                    "task": name,
                    "available": bool(status.get("available")),
                    "reason": status.get("reason"),
                }
            )
        return {
            "tasks": by_task,
            "any_available": any(entry["available"] for entry in by_task),
            "statement": (
                "Caissa serves predictions only from models that passed its production "
                "gate. It does not estimate probabilities itself."
            ),
        }

    def get_validated_prediction(
        _context: AgentContext, task: str, features: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if task not in KNOWN_TASKS:
            raise NotFoundError(
                f"'{task}' is not a Caissa prediction task. Known tasks: "
                f"{', '.join(KNOWN_TASKS)}."
            )
        if providers.prediction is None:
            raise NotFoundError(
                "No validated prediction model is available for this task. Caissa will "
                "not estimate a probability itself, so report that predictive "
                "functionality is unavailable."
            )
        result = providers.prediction(task, features or {})
        if not result or not result.get("available"):
            reason = (result or {}).get("reason") or (
                "No model has passed the production gate for this task."
            )
            raise NotFoundError(
                f"No validated prediction is available for '{task}': {reason}"
            )
        served = dict(result)
        served["task"] = task
        served["reading_note"] = (
            "Report this as the validated model's estimate for its declared setting, "
            "with the data coverage beside it. Do not present it as a certainty, and do "
            "not merge coverage into the probability."
        )
        return served

    return [
        Tool(
            name="get_prediction_status",
            description=(
                "Whether a validated prediction model exists for a task (or for all "
                "tasks). Call this before discussing any probability."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {"task": {"type": "string", "minLength": 1}},
                    "required": [],
                },
                outputs=("task", "available", "reason", "tasks", "any_available"),
            ),
            permission=ToolPermission.ANY,
            handler=get_prediction_status,
            tags=("prediction", "status"),
        ),
        Tool(
            name="get_validated_prediction",
            description=(
                "A prediction from a PRODUCTION-gated model, with its calibration and "
                "data coverage. Fails when no validated model exists — and when it "
                "fails, say that Caissa cannot predict, never invent a probability."
            ),
            schema=ToolSchema(
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "enum": list(KNOWN_TASKS)},
                        "features": {"type": "object"},
                    },
                    "required": ["task"],
                },
                outputs=(
                    "available",
                    "prediction",
                    "probabilities",
                    "data_coverage",
                    "model_id",
                    "model_status",
                ),
            ),
            permission=ToolPermission.PRODUCTION_MODEL,
            handler=get_validated_prediction,
            tags=("prediction",),
        ),
    ]


__all__ = ["KNOWN_TASKS", "build_prediction_tools"]
