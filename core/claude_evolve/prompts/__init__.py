"""Prompt template modules."""

from .base import (
    BASE_SYSTEM_MSG,
    perf_str,
    construct_eval_history_msg,
    construct_individual_program_msg,
)
from .diff import DIFF_SYS_FORMAT, DIFF_ITER_MSG
from .full import FULL_SYS_FORMATS, FULL_ITER_MSG
from .cross import CROSS_SYS_FORMAT, CROSS_ITER_MSG, get_cross_component
from .fix import FIX_SYS_FORMAT, FIX_ITER_MSG, format_error_output_section
from .meta import (
    META_STEP1_SYSTEM_MSG,
    META_STEP1_USER_MSG,
    META_STEP2_SYSTEM_MSG,
    META_STEP2_USER_MSG,
    META_STEP3_SYSTEM_MSG,
    META_STEP3_USER_MSG,
)
from .novelty import NOVELTY_SYSTEM_MSG, NOVELTY_USER_MSG
from .prompt_evo import (
    PROMPT_EVO_DIFF_SYSTEM,
    PROMPT_EVO_DIFF_USER,
    PROMPT_EVO_FULL_SYSTEM,
    PROMPT_EVO_FULL_USER,
    format_global_scratchpad,
    format_top_programs,
)

__all__ = [
    "BASE_SYSTEM_MSG",
    "perf_str",
    "construct_eval_history_msg",
    "construct_individual_program_msg",
    "DIFF_SYS_FORMAT",
    "DIFF_ITER_MSG",
    "FULL_SYS_FORMATS",
    "FULL_ITER_MSG",
    "CROSS_SYS_FORMAT",
    "CROSS_ITER_MSG",
    "get_cross_component",
    "FIX_SYS_FORMAT",
    "FIX_ITER_MSG",
    "format_error_output_section",
    "META_STEP1_SYSTEM_MSG",
    "META_STEP1_USER_MSG",
    "META_STEP2_SYSTEM_MSG",
    "META_STEP2_USER_MSG",
    "META_STEP3_SYSTEM_MSG",
    "META_STEP3_USER_MSG",
    "NOVELTY_SYSTEM_MSG",
    "NOVELTY_USER_MSG",
    "PROMPT_EVO_DIFF_SYSTEM",
    "PROMPT_EVO_DIFF_USER",
    "PROMPT_EVO_FULL_SYSTEM",
    "PROMPT_EVO_FULL_USER",
    "format_global_scratchpad",
    "format_top_programs",
]
