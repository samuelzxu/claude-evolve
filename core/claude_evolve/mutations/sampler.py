"""Prompt construction (PromptSampler).

Ported from ShinkaEvolve/shinka/core/sampler.py with adaptations for
claude-evolve's Program model (no public_metrics field).
"""

import random
import logging
from typing import List, Optional, Tuple

from ..database.models import Program
from ..config import EvolveConfig

logger = logging.getLogger(__name__)


class PromptSampler:
    """Samples mutation type and constructs (system_msg, user_msg, patch_type) tuples."""

    def __init__(self, config: EvolveConfig):
        self.config = config
        self.patch_types = list(config.patches.types)
        self.patch_type_probs = list(config.patches.probs)
        self.language = config.language
        self.task_sys_msg = config.task_description

        prob_sum = sum(self.patch_type_probs)
        if abs(prob_sum - 1.0) > 1e-6:
            raise ValueError(
                f"Patch type probabilities must sum to 1.0, got {prob_sum:.6f}"
            )

    def sample(
        self,
        parent: Program,
        inspirations: List[Program],
        meta_recommendations: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Select patch type by probability and construct prompt tuple.

        Args:
            parent: The parent program to mutate.
            inspirations: Context programs (archive + top-k combined).
            meta_recommendations: Optional recommendation string from MetaSummarizer.

        Returns:
            Tuple of (system_msg, user_msg, patch_type).
        """
        from ..prompts.base import BASE_SYSTEM_MSG, perf_str, construct_eval_history_msg
        from ..prompts.diff import DIFF_SYS_FORMAT, DIFF_ITER_MSG
        from ..prompts.full import FULL_SYS_FORMATS, FULL_ITER_MSG
        from ..prompts.cross import CROSS_SYS_FORMAT, CROSS_ITER_MSG, get_cross_component
        from ..prompts.fix import FIX_SYS_FORMAT, FIX_ITER_MSG

        # Build system message base
        if self.task_sys_msg:
            sys_msg = self.task_sys_msg
        else:
            sys_msg = BASE_SYSTEM_MSG

        # Sample patch type; exclude cross if no inspirations
        if not inspirations:
            valid_types = [t for t in self.patch_types if t != "cross"]
            valid_probs = [
                p for t, p in zip(self.patch_types, self.patch_type_probs)
                if t != "cross"
            ]
            prob_sum = sum(valid_probs)
            if prob_sum > 0:
                valid_probs = [p / prob_sum for p in valid_probs]
            else:
                valid_probs = [1.0 / len(valid_types)] * len(valid_types) if valid_types else self.patch_type_probs
                valid_types = valid_types or self.patch_types
            patch_type = random.choices(valid_types, weights=valid_probs, k=1)[0]
        else:
            patch_type = random.choices(
                self.patch_types, weights=self.patch_type_probs, k=1
            )[0]

        # Add meta-recommendations before format instructions
        if meta_recommendations and meta_recommendations != "none" and patch_type != "cross":
            sys_msg += "\n\n# Potential Recommendations"
            sys_msg += (
                "\nThe following are potential recommendations for the "
                "next program generation:\n"
            )
            sys_msg += f"\n{meta_recommendations}"
            logger.info(
                f"Added meta recommendation to system prompt: "
                f"{meta_recommendations[:80]}..."
            )

        # Append format instructions
        if patch_type == "diff":
            sys_msg += DIFF_SYS_FORMAT
        elif patch_type == "full":
            selected_format = random.choice(FULL_SYS_FORMATS)
            sys_msg += selected_format
        elif patch_type == "cross":
            sys_msg += CROSS_SYS_FORMAT
        elif patch_type == "fix":
            sys_msg += FIX_SYS_FORMAT.format(language=self.language)

        # Build evaluation history from inspirations
        if inspirations:
            eval_history_msg = construct_eval_history_msg(
                inspirations, language=self.language
            )
        else:
            eval_history_msg = ""

        # Build iteration message
        score_str = perf_str(parent.combined_score)

        if patch_type == "diff":
            iter_msg = DIFF_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=score_str,
                text_feedback_section=_format_feedback(parent.text_feedback),
            )
        elif patch_type == "full":
            iter_msg = FULL_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=score_str,
                text_feedback_section=_format_feedback(parent.text_feedback),
            )
        elif patch_type == "cross":
            iter_msg = CROSS_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                performance_metrics=score_str,
                text_feedback_section=_format_feedback(parent.text_feedback),
            )
            cross_component = get_cross_component(
                inspirations, language=self.language
            )
            iter_msg += "\n\n" + cross_component
        elif patch_type == "fix":
            metadata = parent.metadata or {}
            stdout_log = metadata.get("stdout_log", "")
            stderr_log = metadata.get("stderr_log", "")
            error_output_section = _format_error_output(stdout_log, stderr_log)
            iter_msg = FIX_ITER_MSG.format(
                language=self.language,
                code_content=parent.code,
                text_feedback_section=_format_feedback(parent.text_feedback),
                error_output_section=error_output_section,
            )
        else:
            raise ValueError(f"Invalid patch type: {patch_type}")

        user_msg = (eval_history_msg + "\n" + iter_msg).lstrip("\n")
        return sys_msg, user_msg, patch_type

    def _build_fix_prompt(
        self,
        broken_code: str,
        error_message: str,
        original_code: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Build fix prompt components for a broken mutation.

        Args:
            broken_code: The code that failed.
            error_message: The error/traceback from evaluation.
            original_code: The pre-mutation code (unused but kept for API clarity).

        Returns:
            Tuple of (system_msg, user_msg).
        """
        from ..prompts.fix import FIX_SYS_FORMAT, FIX_ITER_MSG

        if self.task_sys_msg:
            sys_msg = self.task_sys_msg
        else:
            from ..prompts.base import BASE_SYSTEM_MSG
            sys_msg = BASE_SYSTEM_MSG

        sys_msg += FIX_SYS_FORMAT.format(language=self.language)

        error_section = _format_error_output(stderr_log=error_message)
        user_msg = FIX_ITER_MSG.format(
            language=self.language,
            code_content=broken_code,
            text_feedback_section="",
            error_output_section=error_section,
        )
        return sys_msg, user_msg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_feedback(text_feedback: Optional[str]) -> str:
    """Format optional text feedback for inclusion in prompts."""
    if not text_feedback or not str(text_feedback).strip():
        return ""
    feedback = str(text_feedback).strip()
    return f"\n\nHere is additional text feedback about the current program:\n\n{feedback}\n"


def _format_error_output(stdout_log: str = "", stderr_log: str = "") -> str:
    """Format error output section for fix prompts."""
    sections = []
    if stdout_log and stdout_log.strip():
        sections.append(
            f"### Standard Output (stdout):\n\n```\n{stdout_log.strip()}\n```"
        )
    if stderr_log and stderr_log.strip():
        sections.append(
            f"### Standard Error (stderr):\n\n```\n{stderr_log.strip()}\n```"
        )
    if not sections:
        return "\n### Error Output:\n\nNo error output captured.\n"
    return "\n" + "\n\n".join(sections) + "\n"
