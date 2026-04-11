"""Configuration for claude-evolve.

Adapted from shinka/core/config.py -- replaces Hydra YAML with JSON-native
dataclasses and replaces external LLM model lists with Claude Code
(model x effort) ensemble grid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_TASK_SYS_MSG = (
    "You are an expert optimization and algorithm design assistant. "
    "Improve the program while preserving correctness and immutable regions."
)

DEFAULT_ENSEMBLE_ARMS = [
    "opus/max",
    "opus/high",
    "sonnet/max",
    "sonnet/high",
    "sonnet/medium",
    "haiku/high",
    "haiku/medium",
    "haiku/low",
]

DEFAULT_PATCH_TYPES = ["diff", "full", "cross", "fix"]
DEFAULT_PATCH_TYPE_PROBS = [0.5, 0.25, 0.1, 0.15]

DEFAULT_PROMPT_PATCH_TYPES = ["diff", "full"]
DEFAULT_PROMPT_PATCH_TYPE_PROBS = [0.7, 0.3]


@dataclass
class EnsembleConfig:
    """Configuration for the (model x effort) ensemble with UCB1 bandit."""

    arms: list[str] = field(default_factory=lambda: list(DEFAULT_ENSEMBLE_ARMS))
    selection: str = "ucb1"  # ucb1 | epsilon_greedy | fixed
    exploration_coef: float = 1.0
    epsilon: float = 0.2
    shift_by_baseline: bool = True
    shift_by_parent: bool = True
    adaptive_scale: bool = True
    asymmetric_scaling: bool = True


@dataclass
class PatchConfig:
    """Configuration for mutation patch types."""

    types: list[str] = field(default_factory=lambda: list(DEFAULT_PATCH_TYPES))
    probs: list[float] = field(default_factory=lambda: list(DEFAULT_PATCH_TYPE_PROBS))
    max_resamples: int = 3
    max_attempts: int = 1


@dataclass
class IslandConfig:
    """Configuration for multi-population island model."""

    num_islands: int = 2
    migration_interval: int = 10
    migration_rate: float = 0.1
    archive_size: int = 40
    archive_selection_strategy: str = "fitness"  # fitness | crowding
    parent_selection_strategy: str = "weighted"  # weighted | power_law | beam_search
    enable_dynamic_islands: bool = False
    stagnation_threshold: int = 100
    elite_selection_ratio: float = 0.3


@dataclass
class MetaConfig:
    """Configuration for meta-scratchpad recommendations."""

    rec_interval: int = 10
    max_recommendations: int = 5
    sample_single: bool = True


@dataclass
class NoveltyConfig:
    """Configuration for novelty rejection sampling."""

    similarity_threshold: float = 0.95
    max_attempts: int = 3


@dataclass
class PromptEvoConfig:
    """Configuration for prompt co-evolution."""

    enabled: bool = False
    patch_types: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROMPT_PATCH_TYPES)
    )
    patch_type_probs: list[float] = field(
        default_factory=lambda: list(DEFAULT_PROMPT_PATCH_TYPE_PROBS)
    )
    evolution_interval: Optional[int] = None
    archive_size: int = 10
    ucb_exploration_constant: float = 1.0
    epsilon: float = 0.1
    top_k_programs: int = 3


@dataclass
class LoggingConfig:
    """Configuration for structured logging."""

    format: str = "jsonl"
    path: str = "state/evolve.log"
    level: str = "INFO"


@dataclass
class EvolveConfig:
    """Main configuration for an evolution run.

    Adapted from ShinkaEvolve's EvolutionConfig + DatabaseConfig.
    Replaces Hydra YAML with JSON-serializable dataclass.
    """

    # Task
    task_description: str = DEFAULT_TASK_SYS_MSG
    language: str = "python"
    init_program_path: str = "initial.py"
    eval_program_path: str = "evaluate.py"
    num_generations: int = 100

    # Ensemble (model x effort)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)

    # Patches
    patches: PatchConfig = field(default_factory=PatchConfig)

    # Islands
    islands: IslandConfig = field(default_factory=IslandConfig)

    # Meta-scratchpad
    meta: MetaConfig = field(default_factory=MetaConfig)

    # Novelty
    novelty: NoveltyConfig = field(default_factory=NoveltyConfig)

    # Prompt co-evolution
    prompt_evo: PromptEvoConfig = field(default_factory=PromptEvoConfig)

    # Logging
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Evaluation
    eval_timeout: int = 120
    llm_timeout: int = 600  # LLM subprocess timeout (larger for sonnet/opus with long prompts)
    use_text_feedback: bool = False
    inspiration_sort_order: str = "ascending"

    # Paths
    results_dir: str = "state/"

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        import dataclasses

        def _convert(obj):
            if dataclasses.is_dataclass(obj):
                return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
            return obj

        return _convert(self)

    def to_json(self, path: str | Path) -> None:
        """Write config to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_json(cls, path: str | Path) -> "EvolveConfig":
        """Load config from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "EvolveConfig":
        """Recursively construct config from a dict."""
        nested_configs = {
            "ensemble": EnsembleConfig,
            "patches": PatchConfig,
            "islands": IslandConfig,
            "meta": MetaConfig,
            "novelty": NoveltyConfig,
            "prompt_evo": PromptEvoConfig,
            "logging": LoggingConfig,
        }
        kwargs = {}
        for key, value in data.items():
            if key in nested_configs and isinstance(value, dict):
                kwargs[key] = nested_configs[key](**value)
            else:
                kwargs[key] = value
        return cls(**kwargs)
