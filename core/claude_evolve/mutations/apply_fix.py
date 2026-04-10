"""Fix patch for broken mutations.

Sends broken code + error back to LLM for correction.
"""

import logging
from typing import Optional

from .apply_full import apply_full_patch

logger = logging.getLogger(__name__)


def apply_fix_patch(
    llm_response: str,
    original_code: str,
    language: str = "python",
) -> tuple[str, int, Optional[str]]:
    """Apply a fix patch from LLM response to broken code.

    The fix prompt asks the LLM to produce a corrected full program inside
    a fenced code block, so this delegates to apply_full_patch.

    Args:
        llm_response: Raw LLM output containing the fixed code in a fence.
        original_code: The broken code that was sent for fixing.
        language: Programming language (used for fence matching).

    Returns:
        Tuple of (fixed_code, num_applied, error_message).
        error_message is None on success.
    """
    updated, num_applied, error = apply_full_patch(llm_response, original_code, language)
    if error:
        logger.debug(f"Fix patch failed: {error}")
    else:
        logger.debug(f"Fix patch applied successfully ({num_applied} block(s) replaced)")
    return updated, num_applied, error
