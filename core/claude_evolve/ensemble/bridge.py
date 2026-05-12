"""LLM bridge — routes arms to Claude CLI or AWS Bedrock Converse API.

Arm format:
  - ``"sonnet/high"`` or ``"opus/max"`` — Claude Code CLI (legacy, default)
  - ``"bedrock/<model-id>/<effort>"`` — Bedrock Converse API

Calls `claude --model X --effort Y -p "prompt" ...` for CLI arms and
the Bedrock Converse API for ``bedrock/`` arms. Both support async
operation, configurable timeout, and exponential-backoff retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from claude_evolve.ensemble.bedrock import (
    BedrockCallFailed,
    BedrockConfig,
    query_bedrock_async,
)

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


def _parse_arm(arm: str) -> tuple[str, str, str]:
    """Split arm name into (provider, model, effort).

    Formats
    -------
    - ``"sonnet/high"`` → ``("claude", "sonnet", "high")``
    - ``"bedrock/us.anthropic.claude-sonnet-4-6/high"`` → ``("bedrock", "us.anthropic.claude-sonnet-4-6", "high")``
    """
    if arm.startswith("bedrock/"):
        # bedrock/<model-id>/<effort>
        parts = arm.split("/")
        if len(parts) < 3:
            raise ValueError(
                f"bedrock arm must be 'bedrock/<model-id>/<effort>', got: {arm!r}"
            )
        # model-id may contain dots but not slashes; effort is the last segment
        effort = parts[-1]
        model_id = "/".join(parts[1:-1])
        return "bedrock", model_id, effort

    # Legacy: model/effort (e.g. sonnet/high)
    parts = arm.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"arm must be 'model/effort', got: {arm!r}")
    return "claude", parts[0], parts[1]


def _build_claude_cmd(
    model: str,
    effort: str,
    prompt: str,
    system_prompt: Optional[str] = None,
) -> tuple[list[str], str]:
    """Build the claude CLI argument list. Returns (cmd, stdin_text).

    The prompt is passed via stdin (using `-p -`) to avoid OS ARG_MAX limits
    when programs are large (50K+ chars).
    """
    # Use short model names — compatible with both direct API and Bedrock.
    # The claude CLI resolves these to the appropriate model ID for the backend.
    full_model = model

    cmd = [
        "claude",
        "--model", full_model,
        "--effort", effort,
        "--strict-mcp-config",
        "--mcp-config", _get_empty_mcp_config(),
        "-p", "-",
        "--output-format", "json",
    ]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    return cmd, prompt


async def query_claude_async(
    arm: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
    bedrock_config: Optional[BedrockConfig] = None,
) -> QueryResult:
    """Call Claude CLI or Bedrock Converse API and return a QueryResult.

    Parameters
    ----------
    arm:
        An arm name like ``"sonnet/high"`` (Claude CLI) or
        ``"bedrock/us.anthropic.claude-sonnet-4-6/high"`` (Bedrock).
    prompt:
        The user-facing prompt text.
    system_prompt:
        Optional system prompt.
    timeout:
        Maximum seconds to wait.
    max_retries:
        Total attempts before raising ``LLMCallFailed``.
    bedrock_config:
        Optional Bedrock connection config (profile, region).

    Returns
    -------
    QueryResult
        Parsed response with *content*, *model*, and *effort* fields.

    Raises
    ------
    LLMCallFailed
        When all retry attempts are exhausted without a successful response.
    """
    provider, model, effort = _parse_arm(arm)

    # --- Bedrock route ---
    if provider == "bedrock":
        try:
            content = await query_bedrock_async(
                model_id=model,
                effort=effort,
                prompt=prompt,
                system_prompt=system_prompt,
                timeout=timeout,
                max_retries=max_retries,
                config=bedrock_config,
            )
            return QueryResult(content=content, model=model, effort=effort)
        except BedrockCallFailed as exc:
            raise LLMCallFailed(str(exc)) from exc

    # --- Claude CLI route ---
    cmd, stdin_text = _build_claude_cmd(model, effort, prompt, system_prompt)

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
            # Prevent AWS SDK from loading ~/.aws/credentials which conflicts with OAuth
            env["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
            env["AWS_CONFIG_FILE"] = "/dev/null"
            env.pop("AWS_PROFILE", None)
            env.pop("AWS_ACCESS_KEY_ID", None)
            env.pop("AWS_SECRET_ACCESS_KEY", None)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.expanduser("~"),
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_text.encode()), timeout=float(timeout)
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
