# claude-evolve

Evolutionary code optimization using Claude Code models. Reimplements the ShinkaEvolve framework using Claude's built-in model access (opus/sonnet/haiku x effort levels) as the LLM ensemble -- no external API keys, no extra infrastructure.

## Skills

- **`/evolve-install`** — Install or verify the plugin (Python deps, venv, MCP server health check)
- **`/evolve-interview`** — Socratic deep interview with mathematical ambiguity gating. Produces `initial.py`, `evaluate.py`, and `config.json` for any optimization task.
- **`/evolve`** — Start an autonomous evolution run from existing spec files
- **`/evolve-status`** — Check a running evolution's progress (generation, best score, bandit arm stats)

## Quick Start

**First time:**

```
/evolve-install     # sets up Python env, verifies Claude CLI, registers MCP server
/evolve-interview   # guided interview to define your task -> writes spec files
/evolve             # launches the evolution run
```

**Already have `initial.py` + `evaluate.py`:**

```
/evolve             # just launches the run
/evolve-status      # check progress at any time
```

## Anatomy of an Evolution Task

An evolution task needs three things:

1. **`initial.py`** — the seed program, with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers around the code you want the LLM to mutate. Code outside those markers is preserved verbatim.
2. **`evaluate.py`** — a standalone evaluator script that runs the program and outputs a JSON dict with at minimum `combined_score` (float) and `correct` (bool). Fitness must come from real code execution, never LLM judgment.
3. **`config.json`** — an `EvolveConfig` JSON file specifying the bandit ensemble, island model, patch types, novelty threshold, and generation budget.

`/evolve-interview` generates all three through a Socratic dialogue. Use it when you don't have a clear spec yet.

## How It Works

The plugin maintains populations of programs across islands. Each generation:

1. **Select arm**: UCB1 bandit picks a `(model, effort)` pair from the ensemble (e.g. `haiku/high`, `sonnet/medium`)
2. **Select parent**: weighted or power-law sampling from an island population
3. **Build prompt**: includes parent code, inspirations from the archive, and a randomly-chosen persona (replaces temperature diversity)
4. **Query Claude**: subprocess call to `claude --model X --effort Y -p <prompt>` with large output token budget
5. **Apply patch**: diff (SEARCH/REPLACE), full rewrite, crossover, or fix (for broken mutations)
6. **Check novelty**: AST fingerprint + MinHash similarity rejects near-duplicate proposals
7. **Evaluate**: run `python3 evaluate.py --program_path <candidate>` as subprocess, parse JSON
8. **Update database**: store the program, update bandit reward based on improvement over parent
9. **Periodic**: meta-scratchpad recommendations, prompt co-evolution, island migration

The evolution loop is sync-first (one proposal at a time) for correctness and debuggability. Concurrent mode is designed but not the default.

## Benchmark

On the 26-circle packing benchmark from ShinkaEvolve:
- **Initial (naive ring packing)**: 1.8378
- **claude-evolve (50 gen, 3 islands)**: **2.6343** (+43.3%)
- **ShinkaEvolve (reference)**: 2.6360

claude-evolve reaches **99.94%** of the reference score using only Claude Code models.
