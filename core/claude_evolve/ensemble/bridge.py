"""Claude CLI subprocess bridge.

Calls `claude --bare --model X --effort Y -p "prompt" ...` and parses the
JSON response.  Supports async operation, configurable timeout, and
exponential-backoff retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds: 2, 4, 8


class LLMCallFailed(Exception):
    """Raised when all retry attempts to call the Claude CLI are exhausted."""


@dataclass
class QueryResult:
    """Result returned from a Claude CLI call."""

    content: str
    model: str
    effort: str


def _parse_arm(arm: str) -> tuple[str, str]:
    """Split 'model/effort' arm name into (model, effort).

    Examples
    --------
    >>> _parse_arm("sonnet/high")
    ('sonnet', 'high')
    """
    parts = arm.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"arm must be 'model/effort', got: {arm!r}")
    return parts[0], parts[1]


def _build_claude_cmd(
    model: str,
    effort: str,
    prompt: str,
    system_prompt: Optional[str] = None,
) -> list[str]:
    """Build the claude CLI argument list."""
    # Map short model names to full Claude model IDs.
    model_map = {
        "opus": "claude-opus-4-5",
        "sonnet": "claude-sonnet-4-5",
        "haiku": "claude-haiku-4-5",
    }
    full_model = model_map.get(model, model)

    cmd = [
        "claude",
        "--bare",
        "--model", full_model,
        "--effort", effort,
        "-p", prompt,
        "--output-format", "json",
    ]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    return cmd


async def query_claude_async(
    arm: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
) -> QueryResult:
    """Call the Claude CLI subprocess and return a QueryResult.

    Parameters
    ----------
    arm:
        An arm name like ``"sonnet/high"`` or ``"opus/max"``.
    prompt:
        The user-facing prompt text (passed via ``-p``).
    system_prompt:
        Optional system prompt (passed via ``--system-prompt``).
    timeout:
        Maximum seconds to wait for the subprocess.
    max_retries:
        Total attempts before raising ``LLMCallFailed``.

    Returns
    -------
    QueryResult
        Parsed response with *content*, *model*, and *effort* fields.

    Raises
    ------
    LLMCallFailed
        When all retry attempts are exhausted without a successful response.
    """
    model, effort = _parse_arm(arm)
    cmd = _build_claude_cmd(model, effort, prompt, system_prompt)

    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries):
        if attempt > 0:
            delay = _BACKOFF_BASE ** attempt  # 2s, 4s, 8s …
            logger.debug("retry %d/%d after %.0fs (arm=%s)", attempt, max_retries - 1, delay, arm)
            await asyncio.sleep(delay)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=float(timeout)
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                last_exc = exc
                logger.warning("claude call timed out (arm=%s attempt=%d)", arm, attempt)
                continue

            if proc.returncode != 0:
                err_text = stderr.decode(errors="replace").strip()
                last_exc = RuntimeError(
                    f"claude exited {proc.returncode}: {err_text[:200]}"
                )
                logger.warning(
                    "claude non-zero exit %d (arm=%s attempt=%d): %s",
                    proc.returncode, arm, attempt, err_text[:200],
                )
                continue

            raw = stdout.decode(errors="replace").strip()
            content = _extract_content(raw)
            return QueryResult(content=content, model=model, effort=effort)

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("claude call error (arm=%s attempt=%d): %s", arm, attempt, exc)

    raise LLMCallFailed(
        f"All {max_retries} attempts failed for arm {arm!r}. "
        f"Last error: {last_exc}"
    ) from last_exc


def _extract_content(raw: str) -> str:
    """Extract text content from a JSON or plain-text claude response."""
    if not raw:
        return ""

    # Try JSON first (--output-format json)
    try:
        data = json.loads(raw)
        # Claude JSON output is typically {"result": "...", ...}
        if isinstance(data, dict):
            for key in ("result", "content", "text", "message"):
                if key in data and isinstance(data[key], str):
                    return data[key]
            # Fallback: join all string values
            parts = [v for v in data.values() if isinstance(v, str)]
            if parts:
                return "\n".join(parts)
        if isinstance(data, str):
            return data
    except json.JSONDecodeError:
        pass

    # Fallback: return raw text as-is
    return raw
