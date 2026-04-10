"""Full rewrite within EVOLVE-BLOCK regions.

Ported from ShinkaEvolve/shinka/edit/apply_full.py with import adjustments.
"""

import re
import logging
from typing import Optional

from .apply_diff import (
    _mutable_ranges,
    EVOLVE_START,
    EVOLVE_END,
    write_git_diff,
)

logger = logging.getLogger(__name__)


def _extract_code_block(text: str, language: str = "python") -> Optional[str]:
    """Extract code from a fenced code block.

    Tries the given language tag first, then falls back to plain ``` blocks.
    """
    # Common aliases
    fence_langs = [language]
    aliases = {
        "python": ["py", "python3"],
        "cpp": ["c++", "cxx", "cuda", "cu"],
        "javascript": ["js"],
        "typescript": ["ts"],
    }
    fence_langs += aliases.get(language, [])

    for lang in fence_langs:
        pattern = re.compile(
            rf"```{re.escape(lang)}\s*\n(.*?)\n\s*```",
            re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(text)
        if m:
            return m.group(1)

    # Fallback: plain ``` block
    plain = re.compile(r"```\s*\n(.*?)\n\s*```", re.DOTALL)
    m = plain.search(text)
    if m:
        return m.group(1)

    return None


def apply_full_patch(
    llm_response: str,
    original_code: str,
    language: str = "python",
) -> tuple[str, int, Optional[str]]:
    """Extract code from language fences and replace EVOLVE-BLOCK content.

    Args:
        llm_response: Raw LLM output containing a fenced code block.
        original_code: The original source code to patch.
        language: Programming language (used for fence matching).

    Returns:
        Tuple of (patched_code, num_applied, error_message).
        error_message is None on success.
    """
    patch_code = _extract_code_block(llm_response, language)
    if patch_code is None:
        return original_code, 0, "Could not extract code from LLM response"

    original = original_code
    mutable_ranges = _mutable_ranges(original)

    if not mutable_ranges:
        return original, 0, "No EVOLVE-BLOCK regions found in original content"

    try:
        patch_has_start = EVOLVE_START.search(patch_code) is not None
        patch_has_end = EVOLVE_END.search(patch_code) is not None
        patch_has_both = patch_has_start and patch_has_end
        patch_has_none = not patch_has_start and not patch_has_end

        updated_content = ""
        last_end = 0

        if patch_has_both:
            patch_mutable_ranges = _mutable_ranges(patch_code)
            for i, (start, end) in enumerate(mutable_ranges):
                updated_content += original[last_end:start]
                if i < len(patch_mutable_ranges):
                    patch_start, patch_end = patch_mutable_ranges[i]
                    replacement_content = patch_code[patch_start:patch_end]
                else:
                    replacement_content = original[start:end]
                updated_content += replacement_content
                last_end = end
            updated_content += original[mutable_ranges[-1][1]:]

        elif patch_has_none:
            if len(mutable_ranges) == 1:
                start, end = mutable_ranges[0]
                immutable_prefix = original[:start]
                immutable_suffix = original[end:]

                start_match = None
                end_match = None
                for m in EVOLVE_START.finditer(original):
                    if m.end() == start:
                        start_match = m
                        break
                for m in EVOLVE_END.finditer(original):
                    if m.start() == end:
                        end_match = m
                        break

                prefix_outside = (
                    original[: start_match.start()] if start_match else immutable_prefix
                )
                suffix_outside = (
                    original[end_match.end():] if end_match else immutable_suffix
                )

                suffix_opts = (suffix_outside, suffix_outside.rstrip("\r\n"))
                if patch_code.startswith(prefix_outside) and any(
                    patch_code.endswith(sfx) for sfx in suffix_opts
                ):
                    mid_start = len(prefix_outside)
                    sfx = next(sfx for sfx in suffix_opts if patch_code.endswith(sfx))
                    mid_end = len(patch_code) - len(sfx)
                    replacement_content = patch_code[mid_start:mid_end]
                    if (
                        start_match is not None
                        and replacement_content
                        and not replacement_content.startswith("\n")
                    ):
                        replacement_content = "\n" + replacement_content
                    if (
                        end_match is not None
                        and replacement_content
                        and not replacement_content.endswith("\n")
                    ):
                        replacement_content = replacement_content + "\n"
                    updated_content = (
                        immutable_prefix + replacement_content + immutable_suffix
                    )
                else:
                    payload = patch_code
                    if (
                        start_match is not None
                        and payload
                        and not payload.startswith("\n")
                    ):
                        payload = "\n" + payload
                    if end_match is not None and payload and not payload.endswith("\n"):
                        payload = payload + "\n"
                    updated_content = immutable_prefix + payload + immutable_suffix
            else:
                return (
                    original,
                    0,
                    "Multiple EVOLVE-BLOCK regions found but patch doesn't specify which to replace",
                )

        else:
            # Patch contains exactly one marker
            if len(mutable_ranges) != 1:
                return (
                    original,
                    0,
                    f"Patch contains only one EVOLVE-BLOCK marker, but the original "
                    f"has {len(mutable_ranges)} editable regions; cannot determine target",
                )

            start, end = mutable_ranges[0]
            immutable_prefix = original[:start]
            immutable_suffix = original[end:]

            start_match = None
            end_match = None
            for m in EVOLVE_START.finditer(original):
                if m.end() == start:
                    start_match = m
                    break
            for m in EVOLVE_END.finditer(original):
                if m.start() == end:
                    end_match = m
                    break

            prefix_outside = (
                original[: start_match.start()] if start_match else immutable_prefix
            )
            suffix_outside = (
                original[end_match.end():] if end_match else immutable_suffix
            )

            if patch_has_start and not patch_has_end:
                m = EVOLVE_START.search(patch_code)
                payload = patch_code[m.end():] if m else patch_code
                for sfx in (suffix_outside, suffix_outside.rstrip("\r\n")):
                    if sfx and payload.endswith(sfx):
                        payload = payload[: -len(sfx)]
                        break
            elif patch_has_end and not patch_has_start:
                m = EVOLVE_END.search(patch_code)
                payload = patch_code[: m.start()] if m else patch_code
                for pfx in (prefix_outside, prefix_outside.rstrip("\r\n")):
                    if pfx and payload.startswith(pfx):
                        payload = payload[len(pfx):]
                        break
            else:
                payload = patch_code

            if start_match is not None and payload and not payload.startswith("\n"):
                payload = "\n" + payload
            if end_match is not None and payload and not payload.endswith("\n"):
                payload = payload + "\n"

            updated_content = immutable_prefix + payload + immutable_suffix

        return updated_content, 1, None

    except Exception as e:
        return original, 0, f"Error applying full patch: {e}"
