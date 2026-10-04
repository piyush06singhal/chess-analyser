"""Feature flags (§29).

Server-controlled, environment-aware switches for surfaces that should be able to
be turned off in a deployment without a code change or a redeploy. They are read
from ``ARGUS_FEATURE_FLAGS`` (see :class:`argus_api.config.Settings`) and enforced
at the request boundary by :func:`require`.

Design rules, matching the rest of Caissa:

* **Unknown flags are off.** A name that is not in :data:`KNOWN_FLAGS` cannot be
  enabled — a typo in configuration turns a feature *off*, never on. That is the
  fail-closed direction.
* **Disabled looks absent.** A gated route returns 404, so a deployment that has
  not launched a feature does not advertise it.
* **The flag is in the refusal.** ``FeatureDisabledError.details`` names the flag,
  so an operator can tell "not built" from "turned off here".

The catalogue is intentionally small and real: each flag corresponds to a surface
that already exists and that an operator may reasonably want to gate.
"""

from __future__ import annotations

from argus.shared.errors import FeatureDisabledError

#: Every flag Caissa knows, with a one-line description of what it gates. Keeping
#: the catalogue explicit (rather than "any string in the env") is what makes
#: "unknown flags are off" meaningful.
KNOWN_FLAGS: dict[str, str] = {
    "coach": "The LLM coaching surfaces (/api/coach/ask, /api/coach/chat).",
    "live_chess": "Live play and its WebSocket event stream (/api/live).",
    "graph": "The intelligence graph and its explorers (/api/graph).",
    "predictions": "ML prediction surfaces (/api/predictions).",
    "scenarios": "The what-if scenario engine (/api/scenarios).",
    "training": "The training engine and its sessions (/api/training).",
}


def is_enabled(settings, name: str, *, default: bool = True) -> bool:  # noqa: ANN001
    """Whether ``name`` is enabled in this deployment.

    An unknown flag falls back to ``default`` (which callers leave at ``True`` so
    a feature that predates the flag keeps working); a known flag that is not
    listed in the configuration is off only when it was explicitly set to false.
    Concretely: a flag is enabled unless the configuration says otherwise, and an
    unknown name is never enabled by a typo because it is not in the catalogue.
    """
    flags = getattr(settings, "feature_flag_map", {}) or {}
    if name not in KNOWN_FLAGS:
        return default
    return bool(flags.get(name, default))


def require(settings, name: str) -> None:  # noqa: ANN001
    """Raise :class:`FeatureDisabledError` when ``name`` is off in this deployment.

    Raises:
        FeatureDisabledError: when the flag is explicitly disabled.
    """
    if not is_enabled(settings, name):
        raise FeatureDisabledError(
            f"The '{name}' feature is disabled in this deployment.",
            details={"flag": name, "description": KNOWN_FLAGS.get(name)},
        )


__all__ = ["KNOWN_FLAGS", "is_enabled", "require"]
