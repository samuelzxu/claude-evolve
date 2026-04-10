"""SEARCH/REPLACE diff patching within EVOLVE-BLOCK regions.

Ported from ShinkaEvolve/shinka/edit/apply_diff.py with import adjustments.
"""

import re
import difflib
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

PATCH_PATTERN = re.compile(
    r"<{7}\s*SEARCH\s*\n(.*?)\n\s*={7}\s*\n(.*?)\n\s*>{7}\s*REPLACE\s*",
    re.DOTALL,
)

EVOLVE_START = re.compile(r"(?:#|//|)?\s*EVOLVE-BLOCK-START")
EVOLVE_END = re.compile(r"(?:#|//|)?\s*EVOLVE-BLOCK-END")


def _mutable_ranges(text: str) -> list[tuple[int, int]]:
    """Return index ranges that are legal to edit.

    Handles both sequential and nested EVOLVE-BLOCK markers correctly by
    processing all markers in position order.
    """
    markers = []
    for m in EVOLVE_START.finditer(text):
        markers.append((m.end(), "start"))
    for m in EVOLVE_END.finditer(text):
        markers.append((m.start(), "end"))

    markers.sort(key=lambda x: x[0])

    spans = []
    stack = []
    for pos, marker_type in markers:
        if marker_type == "start":
            stack.append(pos)
        elif marker_type == "end" and stack:
            start = stack.pop()
            spans.append((start, pos))

    return spans


def _inside(span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    """True if span is fully contained in one of the ranges."""
    return any(span[0] >= a and span[1] <= b for a, b in ranges)


def _strip_trailing_whitespace(text: str) -> str:
    """Strip trailing whitespace from each line in the text."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def _find_indented_match(search_text: str, original_text: str) -> tuple[str, int]:
    """Try to find search_text in original_text with indentation tolerance.

    Returns (matched_text, position) or ("", -1).
    """
    if not search_text.strip():
        return "", -1

    pos = original_text.find(search_text)
    if pos != -1:
        return search_text, pos

    search_lines = search_text.splitlines()
    if not search_lines:
        return "", -1

    first_search_line = search_lines[0].strip()
    if not first_search_line:
        return "", -1

    original_lines = original_text.splitlines()
    for line in original_lines:
        if line.strip() == first_search_line:
            line_indent = len(line) - len(line.lstrip())
            indent_str = line[:line_indent]

            indented_search_lines = []
            for j, search_line in enumerate(search_lines):
                if j == 0:
                    indented_search_lines.append(indent_str + search_line.strip())
                else:
                    search_line_indent = len(search_line) - len(search_line.lstrip())
                    if search_line.strip():
                        indented_search_lines.append(
                            indent_str + " " * search_line_indent + search_line.strip()
                        )
                    else:
                        indented_search_lines.append("")

            indented_search = "\n".join(indented_search_lines)
            indented_pos = original_text.find(indented_search)
            if indented_pos != -1:
                return indented_search, indented_pos

    return "", -1


def _apply_indentation_to_replace(replace_text: str, indent_str: str) -> str:
    """Apply the same indentation pattern to replace text."""
    if not replace_text.strip():
        return replace_text

    replace_lines = replace_text.splitlines()
    indented_replace_lines = []

    for line in replace_lines:
        if line.strip():
            line_indent = len(line) - len(line.lstrip())
            indented_replace_lines.append(indent_str + " " * line_indent + line.strip())
        else:
            indented_replace_lines.append("")

    return "\n".join(indented_replace_lines)


def _clean_evolve_markers(text: str) -> str:
    """Remove EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers from text if present."""
    patterns_to_remove = [
        r"^\s*#\s*EVOLVE-BLOCK-START\s*$",
        r"^\s*//\s*EVOLVE-BLOCK-START\s*$",
        r"^\s*<!--\s*EVOLVE-BLOCK-START\s*-->\s*$",
        r"^\s*EVOLVE-BLOCK-START\s*$",
        r"^\s*#\s*EVOLVE-BLOCK-END\s*$",
        r"^\s*//\s*EVOLVE-BLOCK-END\s*$",
        r"^\s*<!--\s*EVOLVE-BLOCK-END\s*-->\s*$",
        r"^\s*EVOLVE-BLOCK-END\s*$",
    ]

    cleaned_text = text
    markers_found = False

    for pattern in patterns_to_remove:
        if re.search(pattern, cleaned_text, flags=re.MULTILINE):
            markers_found = True
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.MULTILINE)

    if markers_found:
        logger.debug("Removed EVOLVE-BLOCK markers from patch text")

    return cleaned_text


def redact_immutable(text: str, no_state: bool = False) -> str:
    out = []
    for a, b in _mutable_ranges(text):
        if not no_state:
            out.append("<... non-evolvable code omitted ...>")
        out.append(text[a:b])
    if not no_state:
        out.append("<... non-evolvable tail omitted ...>")
    return "".join(out)


class PatchError(RuntimeError):
    pass


def _find_similar_lines(
    search_line: str, original_text: str, max_suggestions: int = 3
) -> List[Tuple[str, int]]:
    """Find similar lines in the original text for suggestions."""
    search_line_clean = search_line.strip()
    if not search_line_clean:
        return []

    original_lines = original_text.splitlines()
    similarities = []

    for i, line in enumerate(original_lines):
        line_clean = line.strip()
        if not line_clean:
            continue
        ratio = difflib.SequenceMatcher(None, search_line_clean, line_clean).ratio()
        if ratio > 0.6:
            similarities.append((line, i + 1, ratio))

    similarities.sort(key=lambda x: x[2], reverse=True)
    return [(line, line_num) for line, line_num, _ in similarities[:max_suggestions]]


def _find_best_match_with_diff(
    search_text: str, original_text: str
) -> Optional[Tuple[list, int, List[str]]]:
    """Find the best matching block and return a diff comparison."""
    search_lines = search_text.strip().splitlines()
    if not search_lines:
        return None

    original_lines = original_text.splitlines()
    search_len = len(search_lines)

    best_match = None
    best_ratio = 0.0
    best_start_line = 0

    for i in range(len(original_lines) - search_len + 1):
        candidate_lines = original_lines[i : i + search_len]
        candidate_text = "\n".join(candidate_lines)
        search_block = "\n".join(search_lines)
        ratio = difflib.SequenceMatcher(None, search_block, candidate_text).ratio()

        if ratio > best_ratio and ratio > 0.7:
            best_ratio = ratio
            best_match = candidate_lines
            best_start_line = i + 1

    if best_match is None:
        return None

    search_lines_labeled = [f"  {line}" for line in search_lines]
    match_lines_labeled = [f"  {line}" for line in best_match]

    diff_lines = list(
        difflib.unified_diff(
            search_lines_labeled,
            match_lines_labeled,
            fromfile="Search Pattern",
            tofile=f"Actual Code (line {best_start_line})",
            lineterm="",
            n=0,
        )
    )

    clean_diff = []
    for line in diff_lines:
        if (
            not line.startswith("---")
            and not line.startswith("+++")
            and not line.startswith("@@")
        ):
            clean_diff.append(line)

    return best_match, best_start_line, clean_diff


def _get_line_position(text: str, line_num: int) -> int:
    """Get character position of the start of a specific line number (1-based)."""
    lines = text.splitlines(keepends=True)
    if line_num < 1 or line_num > len(lines):
        return 0

    char_pos = 0
    for i in range(line_num - 1):
        char_pos += len(lines[i])
    return char_pos


def _char_to_line_num(text: str, char_pos: int) -> int:
    """Convert character position to line number (1-based)."""
    if char_pos < 0:
        return 1

    lines = text.splitlines(keepends=True)
    current_pos = 0
    for i, line in enumerate(lines):
        if current_pos + len(line) > char_pos:
            return i + 1
        current_pos += len(line)

    return len(lines) if lines else 1


def _create_search_not_found_error(
    search_text: str, original_text: str, mutable_ranges: List[Tuple[int, int]]
) -> str:
    """Create a detailed error message when search text is not found."""
    search_lines = search_text.strip().splitlines()
    if not search_lines:
        return "Empty search text provided"

    first_line = search_lines[0].strip()
    similar_lines = _find_similar_lines(first_line, original_text)

    error_parts = ["SEARCH text not found in editable regions", ""]

    if len(search_lines) == 1:
        error_parts.extend([f"Looking for: {first_line!r}", ""])
    else:
        line_count = len(search_lines)
        error_parts.extend(
            [
                f"Looking for {line_count}-line block starting with: {first_line!r}",
                "",
                "Full search pattern:",
                "```",
                search_text.strip(),
                "```",
                "",
            ]
        )

    best_match_result = _find_best_match_with_diff(search_text, original_text)

    if best_match_result:
        best_match, start_line, diff_lines = best_match_result
        match_start_pos = _get_line_position(original_text, start_line)
        match_text = "\n".join(best_match)
        match_span = (match_start_pos, match_start_pos + len(match_text))
        in_editable = _inside(match_span, mutable_ranges)
        region_status = "editable" if in_editable else "immutable"

        error_parts.extend(
            [
                f"Found similar code block at line {start_line} ({region_status}):",
                "",
                "Differences between search pattern and actual code:",
                "```diff",
            ]
        )
        error_parts.extend(diff_lines)
        error_parts.extend(["```", ""])

    elif similar_lines:
        error_parts.extend(["Found similar text (but not exact match):"])
        for line, line_num in similar_lines:
            line_pos = _get_line_position(original_text, line_num)
            span = (line_pos, line_pos + len(line))
            in_editable = _inside(span, mutable_ranges)
            region_status = "editable" if in_editable else "immutable"
            line_content = line.strip()
            error_parts.append(f"  Line {line_num}: {line_content} ({region_status})")
        error_parts.append("")

    if mutable_ranges:
        error_parts.extend(["Editable regions where you can make changes:"])
        for i, (start, end) in enumerate(mutable_ranges[:2]):
            start_line = _char_to_line_num(original_text, start)
            end_line = _char_to_line_num(original_text, end)
            error_parts.append(f"  Region {i + 1} (lines {start_line}-{end_line}):")
            region_text = original_text[start:end].strip()
            region_lines = region_text.splitlines()
            if region_lines:
                if len(region_lines) <= 6:
                    for line in region_lines:
                        error_parts.append(f"    {line}")
                else:
                    for line in region_lines[:3]:
                        error_parts.append(f"    {line}")
                    error_parts.append(f"    ... ({len(region_lines) - 6} more lines)")
                    for line in region_lines[-3:]:
                        error_parts.append(f"    {line}")
                error_parts.append("")

        if len(mutable_ranges) > 2:
            error_parts.append(f"  ... and {len(mutable_ranges) - 2} more regions")
            error_parts.append("")

    if similar_lines:
        error_parts.extend(
            [
                "Quick fixes:",
                "- Check indentation - search text must match exactly including spaces/tabs",
                "- Look for typos in the search text",
                "- Try searching for just the first line instead of the full block",
            ]
        )
    else:
        error_parts.extend(
            [
                "Quick fixes:",
                "- Verify the text exists in the file",
                "- Check that you're searching within EVOLVE-BLOCK regions",
                "- Try a smaller, more specific search pattern",
            ]
        )

    return "\n".join(error_parts)


def apply_search_replace(
    patch_text: str,
    original: str,
    strict: bool = True,
) -> tuple[str, int]:
    """Apply SEARCH/REPLACE blocks but only inside EVOLVE regions.

    Mutable ranges are recalculated after each replacement to account for
    text changes.
    """
    new_text = original
    num_applied = 0
    for block in PATCH_PATTERN.finditer(patch_text):
        search, replace = block.group(1), block.group(2)
        search = _clean_evolve_markers(search)
        replace = _clean_evolve_markers(replace)
        search = _strip_trailing_whitespace(search)
        replace = _strip_trailing_whitespace(replace)

        mutable = _mutable_ranges(new_text)

        if not search.strip():
            if not mutable:
                raise PatchError("No EVOLVE-BLOCK regions found for insertion")
            a, b = mutable[-1]
            new_text = new_text[:b] + replace + new_text[b:]
            num_applied += 1
            continue

        matched_search, pos = _find_indented_match(search, new_text)

        if pos == -1:
            if strict:
                msg = _create_search_not_found_error(search, new_text, mutable)
                raise PatchError(msg)
            continue

        span = (pos, pos + len(matched_search))
        if not _inside(span, mutable):
            raise PatchError(
                f"Attempted to edit outside EVOLVE-BLOCK regions at position {pos}"
            )

        if matched_search != search:
            matched_lines = matched_search.splitlines()
            if matched_lines:
                first_matched_line = matched_lines[0]
                indent_len = len(first_matched_line) - len(first_matched_line.lstrip())
                indent_str = first_matched_line[:indent_len]
                replace = _apply_indentation_to_replace(replace, indent_str)
                logger.debug("Applied indentation correction to search/replace block")

        new_text = new_text.replace(matched_search, replace, 1)
        num_applied += 1
    return new_text, num_applied


def write_git_diff(
    original: str,
    updated: str,
    filename: str = "program",
    out_path: Optional[Union[str, Path]] = None,
    context: int = 9999,
) -> str:
    """Generate unified diff between original and updated content.

    If out_path is provided, also writes the diff to that file.
    Returns the diff string.
    """
    patch_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    diff_str = "".join(patch_lines)
    if out_path is not None:
        out = Path(out_path)
        out.write_text(diff_str, encoding="utf-8")
    return diff_str


def apply_diff_patch(
    llm_response: str,
    original_code: str,
    strict: bool = True,
) -> tuple[str, int, Optional[str]]:
    """Extract SEARCH/REPLACE patches from LLM response and apply to code.

    Only applies patches within EVOLVE-BLOCK regions.

    Args:
        llm_response: Raw LLM output containing SEARCH/REPLACE blocks.
        original_code: The original source code to patch.
        strict: If True, raise PatchError when search text is not found.

    Returns:
        Tuple of (patched_code, num_patches_applied, error_message).
        error_message is None on success.
    """
    original = _strip_trailing_whitespace(original_code)
    patch_str = _strip_trailing_whitespace(llm_response)
    patch_str = _clean_evolve_markers(patch_str)

    try:
        updated, num_applied = apply_search_replace(patch_str, original, strict=strict)
        return updated, num_applied, None
    except PatchError as e:
        return original, 0, str(e)
