"""Prompt evolution (meta-meta) templates.

Ported from ShinkaEvolve/shinka/prompts/prompts_prompt_evo.py with
import adjustments for claude-evolve's model types.
"""

from typing import List, Optional


# =============================================================================
# SHARED SYSTEM MESSAGE BASE
# =============================================================================

PROMPT_EVO_SYSTEM_BASE = (
    "You are an expert prompt engineer specializing in crafting optimal "
    "task instructions for code generation. You will be shown a system "
    "prompt and examples of top-performing programs generated using it.\n\n"
    "Your goal is to improve the system prompt so that future code "
    "generations achieve even higher scores.\n\n"
    "Analyze the successful programs to understand:\n"
    "1. What patterns or techniques led to high scores\n"
    "2. What the prompt could emphasize more clearly\n"
    "3. What aspects of the prompt may be unclear or suboptimal\n\n"
    "DO NOT RECOMMEND VISUALIZATION OR GRAPHICAL OUTPUTS. ONLY RECOMMEND "
    "TASK-SPECIFIC CODE IMPROVEMENT RECOMMENDATIONS.\n\n"
    "You MUST respond using a short summary name, description, and the "
    "new prompt:\n\n"
    "<NAME>\n"
    "A shortened name summarizing the prompt approach. Lowercase, "
    "no spaces, underscores allowed.\n"
    "</NAME>\n\n"
    "<DESCRIPTION>\n"
    "A description of your changes and the reasoning behind them.\n"
    "</DESCRIPTION>\n\n"
    "<PROMPT>\n"
    "The improved system prompt text here.\n"
    "</PROMPT>\n\n"
    "* Use the <NAME>, <DESCRIPTION>, and <PROMPT> delimiters to "
    "structure your response."
)


# =============================================================================
# DIFF-STYLE PROMPT EVOLUTION
# =============================================================================

PROMPT_EVO_DIFF_SYSTEM = (
    PROMPT_EVO_SYSTEM_BASE + "\n\n"
    "IMPORTANT: Make TARGETED modifications based on SPECIFIC patterns you "
    "observe in the successful programs. Do not just rephrase or reorganize - "
    "you must add NEW guidance derived from analyzing what made the top "
    "programs successful.\n\n"
    "For each modification, explicitly identify:\n"
    "1. A specific technique or pattern from the top programs\n"
    "2. How to encode this insight as actionable guidance in the prompt"
)

PROMPT_EVO_DIFF_USER = (
    "# Current System Prompt\n"
    "```\n{current_prompt}\n```\n\n"
    "{global_scratchpad_section}"
    "# Top Performing Programs\n"
    "{top_programs}\n\n"
    "# Instructions\n"
    "CAREFULLY analyze the top-performing programs above. Identify 1-3 "
    "SPECIFIC techniques, algorithms, or implementation patterns that "
    "contributed to their high scores.\n\n"
    "Then modify the system prompt to explicitly encourage these patterns. "
    "Your changes should:\n"
    "- Reference concrete techniques you observed (e.g., 'use vectorized "
    "operations', 'implement early pruning', 'use restart strategies')\n"
    "- Add specific algorithmic guidance based on what worked\n"
    "- NOT just rephrase existing instructions - add NEW actionable "
    "insights\n\n"
    "In your <DESCRIPTION>, explain which specific patterns from the programs "
    "inspired each change.\n\n"
    "Provide your response using the <NAME>, <DESCRIPTION>, and <PROMPT> "
    "delimiters."
)


# =============================================================================
# FULL REWRITE PROMPT EVOLUTION
# =============================================================================

PROMPT_EVO_FULL_SYSTEM = (
    PROMPT_EVO_SYSTEM_BASE + "\n\n"
    "You have freedom to completely rewrite the prompt. Your new prompt "
    "MUST incorporate specific algorithmic insights and implementation "
    "strategies extracted from the successful programs.\n\n"
    "Structure your rewrite around:\n"
    "1. Key techniques that made the top programs successful\n"
    "2. Specific algorithmic patterns to recommend\n"
    "3. Implementation strategies that led to high scores"
)

PROMPT_EVO_FULL_USER = (
    "# Current System Prompt\n"
    "```\n{current_prompt}\n```\n\n"
    "{global_scratchpad_section}"
    "# Top Performing Programs\n"
    "{top_programs}\n\n"
    "# Instructions\n"
    "First, ANALYZE the top-performing programs to extract:\n"
    "- What algorithms or data structures did they use?\n"
    "- What optimizations or clever techniques appear?\n"
    "- What implementation patterns led to high scores?\n\n"
    "Then, write a NEW system prompt that explicitly guides future code "
    "generation to use these successful approaches. Your prompt should:\n"
    "- Include specific algorithmic recommendations\n"
    "- Mention concrete techniques observed in the successful programs\n"
    "- Provide actionable implementation guidance\n\n"
    "In your <DESCRIPTION>, list the key insights you extracted from the "
    "programs and how you incorporated them.\n\n"
    "Provide your response using the <NAME>, <DESCRIPTION>, and <PROMPT> "
    "delimiters."
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def format_global_scratchpad(scratchpad: Optional[str]) -> str:
    """Format the global scratchpad section for prompt evolution."""
    if not scratchpad or not scratchpad.strip():
        return ""

    return (
        "# Global Insights from Meta-Review\n"
        "The following insights have been extracted from analyzing the "
        "evolution of programs so far. Use these to guide your prompt "
        "improvements:\n\n"
        f"{scratchpad.strip()}\n\n"
    )


def format_top_programs(
    programs: List,
    language: str = "python",
    include_text_feedback: bool = False,
) -> str:
    """Format a list of top-performing programs for prompt evolution context."""
    if not programs:
        return "No program examples available."

    parts = []
    for i, prog in enumerate(programs, 1):
        program_str = f"## Program {i}\n\n"
        program_str += f"```{language}\n{prog.code}\n```\n\n"
        program_str += f"**Score**: {prog.combined_score:.4f}\n"

        if include_text_feedback and prog.text_feedback:
            feedback_text = prog.text_feedback
            if isinstance(feedback_text, list):
                feedback_text = "\n".join(feedback_text)
            if str(feedback_text).strip():
                program_str += f"\n**Feedback**:\n{str(feedback_text).strip()}\n"

        parts.append(program_str)

    return "\n---\n\n".join(parts)
