"""Persona prompts for ensemble diversity.

Replaces temperature-based diversity: each call randomly draws a persona
that is prepended to the system prompt, steering the model toward a
different problem-solving style without needing separate API temperature
parameters.
"""

from __future__ import annotations

import random
from typing import Optional

# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------

_PERSONAS: list[tuple[str, str]] = [
    # (name, preamble)
    (
        "baseline",
        "",  # no modification — standard behaviour
    ),
    (
        "creative",
        (
            "Approach this problem as a creative and unconventional thinker. "
            "Challenge assumptions, consider non-obvious solutions, and prefer "
            "elegant simplicity over rote application of standard techniques. "
            "When in doubt, explore an unexpected angle before falling back to "
            "the conventional approach."
        ),
    ),
    (
        "methodical",
        (
            "Approach this problem as a precise and methodical software engineer. "
            "Be systematic: enumerate requirements, reason about edge cases "
            "explicitly, and favour well-established patterns and proven "
            "algorithms. Prioritise correctness and clarity over brevity."
        ),
    ),
    (
        "performance",
        (
            "Approach this problem as a performance specialist focused on "
            "computational efficiency. Identify bottlenecks, minimise allocations "
            "and copies, exploit vectorisation and cache locality, and choose "
            "algorithms with optimal asymptotic complexity. Justify every "
            "trade-off between speed and readability."
        ),
    ),
    (
        "mathematician",
        (
            "Approach this problem as a mathematician. Think in terms of "
            "invariants, symmetries, and formal proofs. Prefer solutions that "
            "can be reasoned about analytically, and express algorithmic ideas "
            "in terms of mathematical structures (graphs, sets, sequences, "
            "linear algebra) where applicable."
        ),
    ),
]

# Exported names for introspection / testing
PERSONA_NAMES: list[str] = [name for name, _ in _PERSONAS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_persona(name: str) -> str:
    """Return the preamble text for the named persona.

    Parameters
    ----------
    name:
        One of the strings in :data:`PERSONA_NAMES`.

    Raises
    ------
    ValueError
        If the name is not recognised.
    """
    for pname, preamble in _PERSONAS:
        if pname == name:
            return preamble
    raise ValueError(
        f"Unknown persona {name!r}. Available: {PERSONA_NAMES}"
    )


def random_persona() -> tuple[str, str]:
    """Return a randomly chosen (name, preamble) pair."""
    return random.choice(_PERSONAS)


def build_system_prompt(base: str, persona: Optional[str] = None) -> str:
    """Build a system prompt by prepending a persona preamble.

    Parameters
    ----------
    base:
        The core system prompt (task description, constraints, etc.).
    persona:
        Optional persona name from :data:`PERSONA_NAMES`.  If ``None``,
        a persona is selected at random (including the empty baseline).

    Returns
    -------
    str
        The combined system prompt.  When the chosen persona is the
        baseline (empty preamble) the base prompt is returned unchanged.
    """
    if persona is None:
        _, preamble = random_persona()
    else:
        preamble = get_persona(persona)

    if not preamble:
        return base

    return f"{preamble}\n\n{base}"
