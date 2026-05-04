"""Python MCP server for claude-evolve.

Provides tools for launching, monitoring, and managing evolution runs
from within Claude Code.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-evolve")


_ACTIVE_RUN_PATH = Path.home() / ".claude-evolve" / "active_run.json"


def _save_active_run(pid: int, state_dir: str) -> None:
    """Persist the active run location so evolve_status can find it from any cwd."""
    _ACTIVE_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_ACTIVE_RUN_PATH, "w") as f:
        json.dump({"pid": pid, "state_dir": state_dir}, f)


def _load_active_run() -> dict | None:
    """Load the persisted active run location."""
    if _ACTIVE_RUN_PATH.exists():
        try:
            with open(_ACTIVE_RUN_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _find_state_dir() -> Path:
    """Find the state directory for the current evolution run.

    Priority:
      1. Persisted active_run.json (written by evolve_start)
      2. state/ in cwd
      3. .claude-evolve/state/ in cwd
    """
    # 1. Persisted location from evolve_start
    active = _load_active_run()
    if active and Path(active["state_dir"]).exists():
        return Path(active["state_dir"])

    # 2. Relative to cwd
    cwd = Path.cwd()
    state_dir = cwd / "state"
    if state_dir.exists():
        return state_dir

    return cwd / ".claude-evolve" / "state"


def _read_run_state(run_dir: str | None = None) -> dict | None:
    """Read the current run state JSON."""
    if run_dir:
        state_path = Path(run_dir) / "run_state.json"
    else:
        state_path = _find_state_dir() / "run_state.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return None


@mcp.tool()
def evolve_start(
    config_path: str = "",
    init_program: str = "",
    evaluator: str = "",
    task_description: str = "",
    num_generations: int = 100,
) -> str:
    """Start an evolutionary code optimization run.

    Provide either a config_path to a JSON config file, or inline parameters
    (init_program, evaluator, task_description, num_generations).
    """
    # Resolve the Python executable from the venv or env var
    python = os.environ.get("CLAUDE_EVOLVE_PYTHON", sys.executable)

    cmd = [python, "-m", "claude_evolve.cli", "run"]

    if config_path:
        cmd.extend(["--config", config_path])
    else:
        if init_program:
            cmd.extend(["--init-program", init_program])
        if evaluator:
            cmd.extend(["--evaluator", evaluator])
        if task_description:
            cmd.extend(["--task-description", task_description])
        if num_generations != 100:
            cmd.extend(["--num-generations", str(num_generations)])

    try:
        # Write stderr to a log file so crash diagnostics aren't lost
        state_dir = Path.cwd() / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        stderr_log = state_dir / "launch_stderr.log"

        stderr_fh = open(stderr_log, "w")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fh,
            start_new_session=True,
        )

        # Brief pause to catch immediate crashes (bad config, missing deps)
        import time
        time.sleep(1.0)
        exit_code = proc.poll()

        if exit_code is not None:
            stderr_fh.close()
            err_text = stderr_log.read_text(errors="replace").strip()
            return json.dumps(
                {
                    "status": "crashed",
                    "pid": proc.pid,
                    "exit_code": exit_code,
                    "error": err_text[:2000] or "Process exited immediately with no output",
                    "stderr_log": str(stderr_log),
                }
            )

        # Persist the results dir so evolve_status can find it from any cwd
        _save_active_run(proc.pid, str(state_dir))

        return json.dumps(
            {
                "status": "started",
                "pid": proc.pid,
                "state_dir": str(state_dir),
                "message": f"Evolution run started (PID {proc.pid}). Use evolve_status to monitor progress.",
            }
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def evolve_status(run_dir: str = "") -> str:
    """Get the current evolution run status.

    Returns generation count, best score, bandit stats, and active state.
    """
    state = _read_run_state(run_dir or None)
    if state is None:
        return json.dumps(
            {"status": "no_run", "message": "No active evolution run found."}
        )
    return json.dumps(state, indent=2)


@mcp.tool()
def evolve_stop(run_dir: str = "") -> str:
    """Gracefully stop the current evolution run.

    Sends SIGTERM to the runner process, which saves state before exiting.
    """
    state = _read_run_state(run_dir or None)
    if state is None:
        return json.dumps(
            {"status": "no_run", "message": "No active evolution run found."}
        )

    pid = state.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            return json.dumps(
                {
                    "status": "stopping",
                    "message": f"Sent SIGTERM to PID {pid}. Run will save state and exit.",
                }
            )
        except ProcessLookupError:
            return json.dumps(
                {
                    "status": "not_running",
                    "message": f"Process {pid} not found. Run may have already completed.",
                }
            )
    return json.dumps(
        {"status": "unknown", "message": "No PID in state. Cannot stop."}
    )


@mcp.tool()
def evolve_visualize(run_dir: str = "", chart_type: str = "progress") -> str:
    """Generate an evolution progress visualization.

    chart_type: "progress" (score over time), "genealogy" (parent-child tree),
                or "bandit" (arm selection stats).
    """
    state = _read_run_state(run_dir or None)
    if state is None:
        return json.dumps(
            {"status": "no_run", "message": "No evolution run data found."}
        )

    # Delegate to visualization module
    from claude_evolve.visualization import generate_chart

    try:
        result = generate_chart(
            state_dir=run_dir or str(_find_state_dir()), chart_type=chart_type
        )
        return json.dumps({"status": "ok", "chart": result})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool()
def evaluator_create(program_path: str, language: str = "python") -> str:
    """Create an evaluator script through guided interview.

    Returns a series of questions to help the user define their evaluation
    criteria, then generates the evaluator script.
    """
    if not Path(program_path).exists():
        return json.dumps(
            {"status": "error", "message": f"Program not found: {program_path}"}
        )

    # Read the program to understand its structure
    with open(program_path) as f:
        code = f.read()

    return json.dumps(
        {
            "status": "ready",
            "program_path": program_path,
            "language": language,
            "code_preview": code[:500],
            "message": (
                "I've read your program. To create an evaluator, I need to understand:\n"
                "1. What does this program compute/produce?\n"
                "2. How should we measure success (what metric)?\n"
                "3. Are there correctness constraints (must-pass validation)?\n"
                "4. How many evaluation runs should we average over?\n\n"
                "Please describe your evaluation criteria."
            ),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
