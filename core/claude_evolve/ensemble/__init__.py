"""claude_evolve.ensemble — LLM bridge, bandit, and persona diversity."""

from claude_evolve.ensemble.bandit import (
    AsymmetricUCB,
    BanditBase,
    EnsembleBandit,
    DEFAULT_ARM_NAMES,
)
from claude_evolve.ensemble.bridge import (
    LLMCallFailed,
    QueryResult,
    query_claude_async,
)
from claude_evolve.ensemble.personas import (
    PERSONA_NAMES,
    build_system_prompt,
    get_persona,
    random_persona,
)

__all__ = [
    # bandit
    "BanditBase",
    "AsymmetricUCB",
    "EnsembleBandit",
    "DEFAULT_ARM_NAMES",
    # bridge
    "LLMCallFailed",
    "QueryResult",
    "query_claude_async",
    # personas
    "PERSONA_NAMES",
    "build_system_prompt",
    "get_persona",
    "random_persona",
]
