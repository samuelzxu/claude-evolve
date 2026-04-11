"""Claude CLI subprocess bridge.

Calls `claude --model X --effort Y -p "prompt" ...` and parses the
JSON response.  Supports async operation, configurable timeout, and
exponential-backoff retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds: 2, 4, 8

# Empty MCP config -- passed via --mcp-config to skip plugin loading
# per call (combined with --strict-mcp-config). Written once to a temp file.
_EMPTY_MCP_CONFIG_PATH: Optional[str] = None


def _get_empty_mcp_config() -> str:
    """Return path to an empty MCP config file, creating it once."""
    global _EMPTY_MCP_CONFIG_PATH
    if _EMPTY_MCP_CONFIG_PATH is None or not os.path.exists(_EMPTY_MCP_CONFIG_PATH):
        fd, path = tempfile.mkstemp(suffix=".json", prefix="claude-evolve-empty-mcp-")
        with os.fdopen(fd, "w") as f:
            f.write('{"mcpServers": {}}')
        _EMPTY_MCP_CONFIG_PATH = path
    return _EMPTY_MCP_CONFIG_PATH


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
        "opus": "claude-opus-4-6",
        "sonnet": "claude-sonnet-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }
    full_model = model_map.get(model, model)

    cmd = [
        "claude",
        "--model", full_model,
        "--effort", effort,
        "--strict-mcp-config",
        "--mcp-config", _get_empty_mcp_config(),
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
            # Give Claude enough output headroom for extended thinking tasks.
            # Without this, --effort high/max can exhaust the default 32k
            # output limit and return an API error instead of the response.
            env = dict(os.environ)
            env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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

            # The claude CLI may return exit 0 with an API error inside the
            # JSON payload (e.g. "Response exceeded 32000 output tokens").
            # Detect that and retry rather than passing the error as content.
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("is_error"):
                    err_text = parsed.get("result", "") or parsed.get("error", "")
                    last_exc = RuntimeError(f"claude API error: {err_text[:200]}")
                    logger.warning(
                        "claude API error (arm=%s attempt=%d): %s",
                        arm, attempt, str(err_text)[:200],
                    )
                    continue
            except json.JSONDecodeError:
                pass

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
