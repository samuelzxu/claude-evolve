"""Mutation engine: patch application and prompt construction."""

from .apply_diff import apply_diff_patch, write_git_diff, PatchError, _mutable_ranges
from .apply_full import apply_full_patch
from .crossover import apply_crossover_patch, prepare_crossover_context
from .apply_fix import apply_fix_patch
from .sampler import PromptSampler

__all__ = [
    "apply_diff_patch",
    "apply_full_patch",
    "apply_crossover_patch",
    "apply_fix_patch",
    "prepare_crossover_context",
    "write_git_diff",
    "PatchError",
    "_mutable_ranges",
    "PromptSampler",
]
