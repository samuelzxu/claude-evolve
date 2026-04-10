"""CLI entry point for claude-evolve.

Provides commands for running evolution and checking status.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="claude-evolve: Evolutionary code discovery with Claude Code"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run an evolution loop")
    run_parser.add_argument("--config", type=str, help="Path to JSON config file")
    run_parser.add_argument("--init-program", type=str, help="Path to initial program")
    run_parser.add_argument("--evaluator", type=str, help="Path to evaluator script")
    run_parser.add_argument(
        "--task-description", type=str, help="Task description for LLM"
    )
    run_parser.add_argument(
        "--num-generations", type=int, default=100, help="Number of generations"
    )
    run_parser.add_argument(
        "--results-dir", type=str, default="state/", help="Results directory"
    )

    # Status command
    subparsers.add_parser("status", help="Check evolution run status")

    args = parser.parse_args()

    if args.command == "run":
        _run_evolution(args)
    elif args.command == "status":
        _check_status()
    else:
        parser.print_help()
        sys.exit(1)


def _run_evolution(args):
    """Start an evolution run."""
    from claude_evolve.config import EvolveConfig

    if args.config:
        config = EvolveConfig.from_json(args.config)
    else:
        defaults = EvolveConfig()
        config = EvolveConfig(
            init_program_path=args.init_program or "initial.py",
            eval_program_path=args.evaluator or "evaluate.py",
            task_description=args.task_description or defaults.task_description,
            num_generations=args.num_generations,
            results_dir=args.results_dir,
        )

    from claude_evolve.runner import EvolutionRunner

    runner = EvolutionRunner(config)
    asyncio.run(runner.run())


def _check_status():
    """Print current run status."""
    import json

    state_path = Path("state/run_state.json")
    if not state_path.exists():
        print("No active evolution run found.")
        sys.exit(1)

    with open(state_path) as f:
        state = json.load(f)

    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
