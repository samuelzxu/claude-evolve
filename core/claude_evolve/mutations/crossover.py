"""Crossover patch type -- combine two parent programs.

New file based on ShinkaEvolve's crossover prompt structure.
"""

import logging
from typing import Optional

from .apply_full import apply_full_patch

logger = logging.getLogger(__name__)


def prepare_crossover_context(
    parent1_code: str,
    parent2_code: str,
    language: str = "python",
) -> str:
    """Format both parents' code for inclusion in a crossover LLM prompt.

    Returns a formatted string ready to append to a user message.
    """
    return (
        "# Crossover Inspiration Program\n\n"
        f"```{language}\n{parent2_code}\n```\n"
    )


def apply_crossover_patch(
    llm_response: str,
    original_code: str,
    language: str = "python",
) -> tuple[str, int, Optional[str]]:
    """Apply the crossover result (same mechanics as full patch, logged differently).

    Args:
        llm_response: Raw LLM output containing a fenced code block.
        original_code: The original source code (parent1).
        language: Programming language (used for fence matching).

    Returns:
        Tuple of (patched_code, num_applied, error_message).
    """
    updated, num_applied, error = apply_full_patch(llm_response, original_code, language)
    if error:
        logger.debug(f"Crossover patch failed: {error}")
    else:
        logger.debug(f"Crossover patch applied ({num_applied} block(s) replaced)")
    return updated, num_applied, error
