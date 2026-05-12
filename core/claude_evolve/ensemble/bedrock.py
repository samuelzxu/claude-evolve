"""AWS Bedrock Converse API backend for claude-evolve.

Routes arms prefixed with ``bedrock/`` to the Bedrock runtime Converse API.
Supports all model families available in Bedrock (Claude, Llama, Nova, Qwen,
DeepSeek, etc.) via a unified message format.

Arm format: ``bedrock/<model-id>/<effort>``
  - model-id: full Bedrock model ID (e.g. ``us.anthropic.claude-sonnet-4-6``)
  - effort: ``low``, ``medium``, ``high``, ``max``

Effort mapping:
  - Claude models: maps to ``thinking.budget_tokens`` (4k/16k/32k/64k)
  - Other models: maps to temperature (0.3/0.7/0.9/1.0)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

EFFORT_TO_THINKING_BUDGET = {
    "low": 4096,
    "medium": 16384,
    "high": 32768,
    "max": 65536,
}

EFFORT_TO_TEMPERATURE = {
    "low": 0.3,
    "medium": 0.7,
    "high": 0.9,
    "max": 1.0,
}

# Models with output token limits lower than our default 8192.
# Used to clamp maxTokens to avoid ValidationException.
_MODEL_MAX_TOKENS: dict[str, int] = {
    "ai21.jamba-1-5-large-v1:0": 4096,
    "ai21.jamba-1-5-mini-v1:0": 4096,
}

_BACKOFF_BASE = 2.0
DEFAULT_RETRIES = 3


@dataclass
class BedrockConfig:
    """Bedrock connection configuration."""

    profile: Optional[str] = None
    region: Optional[str] = None


class BedrockCallFailed(Exception):
    """Raised when all retry attempts to call Bedrock are exhausted."""


def _is_claude_model(model_id: str) -> bool:
    return "anthropic" in model_id.lower()


def _build_converse_params(
    model_id: str,
    effort: str,
    prompt: str,
    system_prompt: Optional[str] = None,
) -> dict:
    """Build kwargs for bedrock-runtime.converse()."""
    messages = [{"role": "user", "content": [{"text": prompt}]}]

    params: dict = {
        "modelId": model_id,
        "messages": messages,
    }

    if system_prompt:
        params["system"] = [{"text": system_prompt}]

    inference_config: dict = {}

    if _is_claude_model(model_id):
        budget = EFFORT_TO_THINKING_BUDGET.get(effort, 16384)
        params["additionalModelRequestFields"] = {
            "thinking": {"type": "enabled", "budget_tokens": budget}
        }
        inference_config["maxTokens"] = 64000
    else:
        temp = EFFORT_TO_TEMPERATURE.get(effort, 0.7)
        inference_config["temperature"] = temp
        max_tokens = _MODEL_MAX_TOKENS.get(model_id, 8192)
        inference_config["maxTokens"] = max_tokens

    if inference_config:
        params["inferenceConfig"] = inference_config

    return params


def _get_client(config: BedrockConfig):
    """Create a boto3 bedrock-runtime client (cached per config)."""
    import boto3

    session_kwargs = {}
    if config.profile:
        session_kwargs["profile_name"] = config.profile
    if config.region:
        session_kwargs["region_name"] = config.region

    session = boto3.Session(**session_kwargs)
    return session.client("bedrock-runtime")


def _extract_response_text(response: dict) -> str:
    """Extract text content from Converse API response."""
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    texts = []
    for block in content_blocks:
        if "text" in block:
            texts.append(block["text"])
    return "\n".join(texts)


async def query_bedrock_async(
    model_id: str,
    effort: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    timeout: int = 300,
    max_retries: int = DEFAULT_RETRIES,
    config: Optional[BedrockConfig] = None,
) -> str:
    """Call Bedrock Converse API with retries and return the text response.

    Runs the synchronous boto3 call in a thread executor to stay async.
    """
    if config is None:
        config = BedrockConfig()

    client = _get_client(config)
    params = _build_converse_params(model_id, effort, prompt, system_prompt)

    last_exc: Exception = RuntimeError("no attempts made")

    for attempt in range(max_retries):
        if attempt > 0:
            delay = _BACKOFF_BASE ** attempt
            logger.debug(
                "bedrock retry %d/%d after %.0fs (model=%s)",
                attempt, max_retries - 1, delay, model_id,
            )
            await asyncio.sleep(delay)

        try:
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: client.converse(**params)),
                timeout=float(timeout),
            )

            stop_reason = response.get("stopReason", "")
            if stop_reason == "error":
                err = response.get("error", {}).get("message", "unknown error")
                last_exc = RuntimeError(f"Bedrock error: {err}")
                logger.warning(
                    "bedrock error response (model=%s attempt=%d): %s",
                    model_id, attempt, err,
                )
                continue

            text = _extract_response_text(response)
            if not text:
                last_exc = RuntimeError("Bedrock returned empty response")
                logger.warning(
                    "bedrock empty response (model=%s attempt=%d)", model_id, attempt
                )
                continue

            usage = response.get("usage", {})
            logger.info(
                "bedrock ok (model=%s effort=%s tokens_in=%s tokens_out=%s)",
                model_id, effort,
                usage.get("inputTokens", "?"),
                usage.get("outputTokens", "?"),
            )
            return text

        except asyncio.TimeoutError as exc:
            last_exc = exc
            logger.warning(
                "bedrock timeout (model=%s attempt=%d timeout=%ds)",
                model_id, attempt, timeout,
            )
        except Exception as exc:
            last_exc = exc
            exc_name = type(exc).__name__
            logger.warning(
                "bedrock call error (model=%s attempt=%d): %s: %s",
                model_id, attempt, exc_name, str(exc)[:200],
            )
            # Throttling: back off harder
            if "Throttling" in exc_name or "throttl" in str(exc).lower():
                await asyncio.sleep(_BACKOFF_BASE ** (attempt + 1))

    raise BedrockCallFailed(
        f"All {max_retries} attempts failed for bedrock model {model_id!r}. "
        f"Last error: {last_exc}"
    ) from last_exc
