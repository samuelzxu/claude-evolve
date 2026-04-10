"""Visualization utilities for claude-evolve."""

from __future__ import annotations

from pathlib import Path

from .progress import generate_progress_chart, generate_progress_chart_html
from .genealogy import generate_genealogy
from .bandit import generate_bandit_chart

__all__ = [
    "generate_chart",
    "generate_progress_chart",
    "generate_progress_chart_html",
    "generate_genealogy",
    "generate_bandit_chart",
]


def generate_chart(state_dir: str, chart_type: str) -> str:
    """Dispatcher that calls the appropriate visualization function.

    Args:
        state_dir: Directory containing run state (state/ folder with
                   evolve.db, run_state.json, etc.) or a direct path.
        chart_type: One of "progress", "genealogy", "bandit".

    Returns:
        Text-based chart as a string.

    Raises:
        ValueError: If chart_type is not recognized.
    """
    state_path = Path(state_dir)

    if chart_type == "progress":
        db_path = state_path / "evolve.db" if state_path.is_dir() else state_path
        return generate_progress_chart(str(db_path))

    if chart_type == "genealogy":
        db_path = state_path / "evolve.db" if state_path.is_dir() else state_path
        return generate_genealogy(str(db_path))

    if chart_type == "bandit":
        return generate_bandit_chart(str(state_path))

    raise ValueError(
        f"Unknown chart_type '{chart_type}'. "
        "Valid options: 'progress', 'genealogy', 'bandit'."
    )
