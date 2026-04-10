# claude-evolve

Evolutionary code optimization using Claude Code models. Reimplements the ShinkaEvolve framework using Claude's built-in model access (opus/sonnet/haiku x effort levels) as the LLM ensemble.

## Skills

- /evolve - Start an autonomous evolution run
- /evolve-status - Check evolution run progress
- /evolve-setup - Create an evaluator script through guided interview

## Quick Start

1. Create an `initial.py` with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers around mutable code
2. Create an `evaluate.py` that scores solutions (or use `/evolve-setup` to generate one)
3. Run `/evolve` to start the evolutionary loop

## How It Works

The plugin maintains populations of programs across islands, uses Claude Code models as mutation operators (diff patches, full rewrites, crossover), and selects the best-performing solutions via UCB1 bandit-based model selection. Evaluation is always grounded in real code execution -- never LLM-as-judge.
