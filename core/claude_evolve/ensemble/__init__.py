"""claude_evolve.ensemble — LLM bridge, bandit, and persona diversity."""

from claude_evolve.ensemble.bandit import (
    AsymmetricUCB,
    BanditBase,
    EnsembleBandit,
    DEFAULT_ARM_NAMES,
)
from claude_evolve.ensemble.bedrock import (
    BedrockCallFailed,
    BedrockConfig,
    query_bedrock_async,
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
    # bedrock
    "BedrockCallFailed",
    "BedrockConfig",
    "query_bedrock_async",
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
