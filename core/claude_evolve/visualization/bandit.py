"""Bandit arm statistics visualization for claude-evolve."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ucb_score(mean: float, count: float, total: float, c: float = 1.0) -> Optional[float]:
    """Compute UCB1 score for display purposes."""
    if count <= 0 or total <= 0:
        return None
    bonus = c * math.sqrt(2.0 * math.log(max(total, 2.0)) / count)
    return mean + bonus


def generate_bandit_chart(state_path: str) -> str:
    """Read bandit state from run_state.json and render arm stats as a table.

    Args:
        state_path: Path to run_state.json (or directory containing it).

    Returns:
        Multi-line string table of arm name, count, mean reward, UCB score.
    """
    path = Path(state_path)
    if path.is_dir():
        path = path / "run_state.json"

    if not path.exists():
        return f"Bandit state file not found: {state_path}"

    try:
        with open(path) as fh:
            state: Dict[str, Any] = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return f"Failed to read bandit state: {exc}"

    bandit_state = state.get("bandit", state)

    n_submitted: List[float] = bandit_state.get("n_submitted", [])
    n_completed: List[float] = bandit_state.get("n_completed", [])
    s_vals: List[float] = bandit_state.get("s", [])
    divs: List[float] = bandit_state.get("divs", [])
    arm_names: Optional[List[str]] = state.get("arm_names") or bandit_state.get("arm_names")

    n_arms = max(len(n_submitted), len(n_completed), len(s_vals))
    if n_arms == 0:
        return "No bandit arm data found in state file."

    if arm_names is None or len(arm_names) < n_arms:
        arm_names = [str(i) for i in range(n_arms)]

    # Pad arrays to n_arms
    def _pad(lst: List[float], length: int, default: float = 0.0) -> List[float]:
        return lst + [default] * max(0, length - len(lst))

    n_submitted = _pad(n_submitted, n_arms)
    n_completed = _pad(n_completed, n_arms)
    s_vals = _pad(s_vals, n_arms)
    divs = _pad(divs, n_arms)

    total_count = sum(max(n_submitted[i], n_completed[i]) for i in range(n_arms))

    rows = []
    for i in range(n_arms):
        count = max(n_submitted[i], n_completed[i])
        denom = max(divs[i], 1e-7)
        mean_reward = s_vals[i] / denom if not math.isinf(s_vals[i]) else float(s_vals[i])
        ucb = _ucb_score(mean_reward, count, total_count)
        rows.append((arm_names[i], count, mean_reward, ucb))

    # Sort by UCB score descending (None last)
    rows.sort(key=lambda r: (r[3] is not None, r[3] or 0.0), reverse=True)

    col_name = max(len(r[0]) for r in rows)
    header = (
        f"  {'Arm':<{col_name}}  {'Count':>8}  {'MeanReward':>12}  {'UCB':>12}"
    )
    sep = "  " + "-" * (col_name + 38)

    lines = [
        "=== claude-evolve Bandit Arm Statistics ===",
        f"  Total pulls: {int(total_count)}",
        "",
        header,
        sep,
    ]

    for name, count, mean_r, ucb in rows:
        ucb_str = f"{ucb:.6f}" if ucb is not None else "       N/A"
        lines.append(
            f"  {name:<{col_name}}  {int(count):>8}  {mean_r:>12.6f}  {ucb_str:>12}"
        )

    lines.append("")
    return "\n".join(lines)
