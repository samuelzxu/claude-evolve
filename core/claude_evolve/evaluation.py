"""Evaluation subprocess runner for claude-evolve.

Runs the user-supplied evaluate.py script against a candidate program
and parses the result metrics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EVAL_RESULT_FILENAME = "metrics.json"


async def evaluate_program(
    code: str,
    eval_program_path: str,
    results_dir: str,
    timeout: int = 120,
    language: str = "python",
    eval_python: Optional[str] = None,
) -> dict:
    """Evaluate a candidate program by running the user-supplied evaluator.

    Writes *code* to a temp file under *results_dir*, then executes:

        python <eval_program_path> --program_path <temp_file>

    The evaluator is expected to write ``metrics.json`` next to the temp
    file **or** print a JSON object to stdout.  The returned dict always
    has the shape::

        {
            "combined_score": float,
            "correct": bool,
            "metrics": dict,
            "error": str | None,
        }

    Parameters
    ----------
    code:
        Source code of the candidate program.
    eval_program_path:
        Path to the evaluator script (e.g. ``evaluate.py``).
    results_dir:
        Directory where the temp file (and metrics.json) will be written.
    timeout:
        Maximum seconds to wait for the evaluator subprocess.
    language:
        Source language – used to pick the right file extension.

    Returns
    -------
    dict
        Evaluation result with ``combined_score``, ``correct``, ``metrics``
        and ``error`` keys.
    """
    _ext = _language_extension(language)
    results_path = Path(results_dir)
    results_path.mkdir(parents=True, exist_ok=True)

    # Write candidate code to a uniquely-named temp file so concurrent
    # evaluations (future work) do not collide.
    ts = int(time.time() * 1000)
    tmp_name = f"candidate_{ts}{_ext}"
    tmp_path = results_path / tmp_name

    try:
        tmp_path.write_text(code, encoding="utf-8")
    except OSError as exc:
        return _error_result(f"Failed to write candidate file: {exc}")

    metrics_path = tmp_path.parent / _EVAL_RESULT_FILENAME

    # Clean up any stale metrics.json from a previous run
    if metrics_path.exists():
        try:
            metrics_path.unlink()
        except OSError:
            pass

    python = _resolve_eval_python(eval_program_path, eval_python)

    cmd = [
        python,
        str(Path(eval_program_path).resolve()),
        "--program_path",
        str(tmp_path.resolve()),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return _error_result(
                f"Evaluator timed out after {timeout}s",
                stdout="",
                stderr="",
            )

        stdout = stdout_bytes.decode(errors="replace").strip()
        stderr = stderr_bytes.decode(errors="replace").strip()

        if proc.returncode != 0:
            logger.warning(
                "Evaluator exited with code %d. stderr: %s",
                proc.returncode,
                stderr[:400],
            )
            return _error_result(
                f"Evaluator exited with code {proc.returncode}: {stderr[:400]}",
                stdout=stdout,
                stderr=stderr,
            )

        # Prefer metrics.json file if written
        if metrics_path.exists():
            try:
                raw = metrics_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                return _normalise_result(data, stdout=stdout, stderr=stderr)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not parse metrics.json: %s", exc)

        # Fallback: parse stdout as JSON
        if stdout:
            try:
                data = json.loads(stdout)
                return _normalise_result(data, stdout=stdout, stderr=stderr)
            except json.JSONDecodeError:
                pass

        # Final fallback: try to extract a JSON object from stdout
        extracted = _extract_json_from_text(stdout)
        if extracted is not None:
            return _normalise_result(extracted, stdout=stdout, stderr=stderr)

        return _error_result(
            "Evaluator produced no parseable metrics",
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during evaluation: %s", exc)
        return _error_result(f"Evaluation error: {exc}")
    finally:
        # Clean up temp file
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_eval_python(eval_program_path: str, eval_python: Optional[str] = None) -> str:
    """Resolve the Python interpreter for running the evaluator.

    Priority:
      1. eval_python config field (explicit override)
      2. CLAUDE_EVOLVE_EVAL_PYTHON env var
      3. Shebang in the evaluator script (e.g. #!/usr/bin/env python3)
      4. "python3" on PATH (NOT sys.executable, which is the MCP venv Python
         and likely lacks the user's domain deps like torch, numpy, etc.)
    """
    import os
    import shutil

    # 1. Explicit config
    if eval_python:
        return eval_python

    # 2. Env var
    env_python = os.environ.get("CLAUDE_EVOLVE_EVAL_PYTHON")
    if env_python:
        return env_python

    # 3. Shebang detection
    try:
        with open(eval_program_path, "r") as f:
            first_line = f.readline().strip()
        if first_line.startswith("#!"):
            shebang = first_line[2:].strip()
            # Handle "#!/usr/bin/env python3" -> "python3"
            if shebang.startswith("/usr/bin/env "):
                shebang = shebang[len("/usr/bin/env "):].strip()
            # Verify it exists
            if shutil.which(shebang.split()[0]):
                return shebang
    except (OSError, UnicodeDecodeError):
        pass

    # 4. python3 on PATH (NOT sys.executable)
    return "python3"


def _language_extension(language: str) -> str:
    """Map language name to file extension."""
    _map = {
        "python": ".py",
        "py": ".py",
        "javascript": ".js",
        "js": ".js",
        "typescript": ".ts",
        "ts": ".ts",
        "cpp": ".cpp",
        "c++": ".cpp",
        "java": ".java",
        "rust": ".rs",
        "go": ".go",
    }
    return _map.get(language.lower(), ".py")


def _normalise_result(
    data: dict,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    """Normalise raw metrics dict into the canonical result shape."""
    score = data.get("combined_score", data.get("score", 0.0))
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0

    correct = data.get("correct", data.get("is_correct", False))
    if isinstance(correct, (int, float)):
        correct = bool(correct)
    elif not isinstance(correct, bool):
        correct = False

    metrics = data.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}

    # Store stdout/stderr logs in metrics for fix-prompt context
    if stdout:
        metrics.setdefault("stdout_log", stdout[:2000])
    if stderr:
        metrics.setdefault("stderr_log", stderr[:2000])

    return {
        "combined_score": score,
        "correct": correct,
        "metrics": metrics,
        "error": None,
    }


def _error_result(
    message: str,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    """Return a failure result dict."""
    metrics: dict = {}
    if stdout:
        metrics["stdout_log"] = stdout[:2000]
    if stderr:
        metrics["stderr_log"] = stderr[:2000]
    return {
        "combined_score": 0.0,
        "correct": False,
        "metrics": metrics,
        "error": message,
    }


def _extract_json_from_text(text: str) -> Optional[dict]:
    """Try to find and parse a JSON object anywhere in *text*."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
