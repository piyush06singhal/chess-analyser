"""LLM provider abstraction.

The agent never talks to a vendor SDK directly; it depends on
:class:`argus.llm.base.LLMClient`. Providers translate the agent's
provider-neutral message/tool-call format into vendor APIs. No API key is
ever read from source code — providers are constructed from configuration.
"""

from argus.llm.base import LLMClient, LLMError, LLMRequestError, LLMResponseError
from argus.llm.factory import build_llm_client

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMRequestError",
    "LLMResponseError",
    "build_llm_client",
]
