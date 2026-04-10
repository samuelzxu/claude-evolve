"""Standalone evaluator for circle packing (n=26).

No shinka imports. Loads the program module directly, calls run_experiment(),
validates the packing, and outputs JSON metrics to stdout.

Usage:
    python3 evaluate.py --program_path initial.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

N_CIRCLES = 26


def validate_packing(
    result: Tuple[np.ndarray, np.ndarray, float],
    atol: float = 1e-9,
) -> Tuple[bool, Optional[str]]:
    """Validate circle packing results.

    Args:
        result: Tuple (centers, radii, reported_sum) from run_experiment().
        atol: Absolute tolerance for boundary and overlap checks.

    Returns:
        (is_valid, error_message_or_None)
    """
    centers, radii, reported_sum = result

    if not isinstance(centers, np.ndarray):
        centers = np.array(centers)
    if not isinstance(radii, np.ndarray):
        radii = np.array(radii)

    if centers.shape != (N_CIRCLES, 2):
        return False, f"centers shape {centers.shape} != ({N_CIRCLES}, 2)"
    if radii.shape != (N_CIRCLES,):
        return False, f"radii shape {radii.shape} != ({N_CIRCLES},)"

    if not (np.all(np.isfinite(centers)) and np.all(np.isfinite(radii)) and np.isfinite(reported_sum)):
        return False, "Non-finite values in centers, radii, or reported_sum"

    if np.any(radii < 0):
        bad = np.where(radii < 0)[0].tolist()
        return False, f"Negative radii at indices: {bad}"

    actual_sum = float(np.sum(radii))
    if not np.isclose(actual_sum, reported_sum, atol=1e-4):
        return False, (
            f"Sum mismatch: actual={actual_sum:.6f} reported={reported_sum:.6f}"
        )

    for i in range(N_CIRCLES):
        x, y = float(centers[i, 0]), float(centers[i, 1])
        r = float(radii[i])
        if x - r < -atol or x + r > 1.0 + atol or y - r < -atol or y + r > 1.0 + atol:
            return False, (
                f"Circle {i} (x={x:.4f}, y={y:.4f}, r={r:.4f}) outside unit square"
            )

    for i in range(N_CIRCLES):
        for j in range(i + 1, N_CIRCLES):
            dist = float(np.linalg.norm(centers[i] - centers[j]))
            if dist < radii[i] + radii[j] - atol:
                return False, (
                    f"Circles {i} & {j} overlap: dist={dist:.4f} "
                    f"sum_radii={radii[i]+radii[j]:.4f}"
                )

    return True, None


# ---------------------------------------------------------------------------
# Program loader
# ---------------------------------------------------------------------------

def load_program_module(program_path: str):
    """Dynamically import a Python file as a module.

    Args:
        program_path: Path to the .py file.

    Returns:
        Loaded module object.
    """
    path = Path(program_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Program not found: {program_path}")

    spec = importlib.util.spec_from_file_location("_evolved_program", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {program_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(program_path: str) -> Dict[str, Any]:
    """Load and evaluate a circle packing program.

    Args:
        program_path: Path to program file containing run_experiment().

    Returns:
        Metrics dict with at least 'combined_score' and 'correct' keys.
    """
    try:
        module = load_program_module(program_path)
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "correct": False,
            "error": f"Failed to load program: {exc}",
            "traceback": traceback.format_exc(),
        }

    if not hasattr(module, "run_experiment"):
        return {
            "combined_score": 0.0,
            "correct": False,
            "error": "Program has no 'run_experiment' function",
        }

    try:
        result = module.run_experiment()
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "correct": False,
            "error": f"run_experiment() raised: {exc}",
            "traceback": traceback.format_exc(),
        }

    try:
        is_valid, err_msg = validate_packing(result)
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "correct": False,
            "error": f"Validation error: {exc}",
            "traceback": traceback.format_exc(),
        }

    if not is_valid:
        return {
            "combined_score": 0.0,
            "correct": False,
            "error": err_msg,
        }

    centers, radii, reported_sum = result
    if not isinstance(radii, np.ndarray):
        radii = np.array(radii)

    combined_score = float(np.sum(radii))

    return {
        "combined_score": combined_score,
        "correct": True,
        "num_circles": N_CIRCLES,
        "sum_of_radii": combined_score,
        "min_radius": float(np.min(radii)),
        "max_radius": float(np.max(radii)),
        "mean_radius": float(np.mean(radii)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a circle packing program (n=26)"
    )
    parser.add_argument(
        "--program_path",
        type=str,
        required=True,
        help="Path to program file with run_experiment()",
    )
    args = parser.parse_args()

    metrics = evaluate(args.program_path)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
