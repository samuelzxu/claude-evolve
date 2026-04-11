# claude-evolve

**Evolutionary code discovery for Claude Code.** No API keys, no cloud infrastructure, no extra services — just the `claude` CLI you already use.

claude-evolve is a native Claude Code plugin that reimplements the [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve) algorithm (ICLR 2026) using Claude's built-in model tiers (opus, sonnet, haiku) at different thinking-effort levels as the LLM ensemble. It maintains populations of programs across islands, generates mutations via a UCB1 bandit over `(model × effort)` arms, and evolves code autonomously toward any fitness function you can evaluate with real code execution.

On the 26-circle packing benchmark from the ShinkaEvolve paper, claude-evolve reaches **99.94% of the reference state-of-the-art score (2.6343 vs 2.6360)** in 50 generations with 3 islands — starting from a naive initial solution scoring 1.8378.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start (3 commands)](#quick-start-3-commands)
- [Core Concepts](#core-concepts)
- [The Four Skills](#the-four-skills)
- [Writing `initial.py`](#writing-initialpy)
- [Writing `evaluate.py`](#writing-evaluatepy)
- [Writing `config.json`](#writing-configjson)
- [Running an Evolution](#running-an-evolution)
- [Monitoring and Visualization](#monitoring-and-visualization)
- [Configuration Reference](#configuration-reference)
- [Architecture](#architecture)
- [Benchmark: Circle Packing](#benchmark-circle-packing)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## What It Does

You give claude-evolve three things:

1. **An initial program** with markers around the part you want to optimize
2. **An evaluator script** that runs the program and returns a fitness score
3. **A config** specifying the evolutionary budget

It runs an autonomous loop that:

- Asks Claude models to mutate your code (diff patches, full rewrites, crossover)
- Runs your evaluator on each mutation to measure fitness
- Keeps the best programs in an archive across multiple island populations
- Migrates elites between islands periodically
- Rejects near-duplicate proposals via AST-based novelty detection
- Uses a UCB1 bandit to learn which `(model × effort)` combinations produce the biggest improvements
- Periodically generates meta-recommendations from top programs and injects them into future prompts

After N generations, the best program in the archive is your optimized solution.

---

## Requirements

- **Claude Code CLI** (the `claude` command) with an authenticated account
- **Python 3.10 or newer** on PATH
- **`pip`** to install the claude-evolve Python package
- Unix-like environment (Linux, macOS, WSL) — Windows native is not tested
- ~200 MB disk space for dependencies (numpy, scipy, aiosqlite, mcp)

You do **not** need:
- An Anthropic API key, OpenAI key, or any other external service credential
- A GPU
- Docker, SLURM, or other orchestration
- A network connection beyond what Claude Code already needs

---

## Installation

### Option 1: Claude Code plugin marketplace (recommended)

Run each command below **in its own Claude Code message** — do not paste them as a single block, since Claude Code interprets only the first slash command on a line.

**1. Add the marketplace:**

```
/plugin marketplace add https://github.com/samuelzxu/claude-evolve
```

**2. Install the plugin:**

```
/plugin install claude-evolve@evolve
```

**3. Reload plugins so the new skills and MCP server register:**

```
/reload-plugins
```

(Alternatively, restart Claude Code. Either way is required after any plugin install before its skills become callable in the current session.)

**4. Run the setup skill to install Python dependencies and verify everything works:**

```
/evolve-install
```

This will:
1. Check your Python version (≥ 3.10 required)
2. Create a venv in `core/.venv/` (or use system Python with `--global`)
3. Install the `claude_evolve` Python package in editable mode
4. Verify the Claude CLI is available
5. Confirm the MCP server exposes its five tools
6. Run a smoke test that calls Claude once to verify the bridge

### Option 2: Manual install from source

```bash
git clone https://github.com/samuelzxu/claude-evolve
cd claude-evolve
python3 -m venv core/.venv
source core/.venv/bin/activate
pip install -e core/
claude mcp add -s user claude-evolve -- python3 -m claude_evolve.server
```

Then open any Claude Code session and confirm the MCP server is connected:

```
claude mcp list
# Should show: claude-evolve: python3 -m claude_evolve.server - ✓ Connected
```

---

## Quick Start (3 commands)

Once installed, there are two paths depending on what you already have.

### Path A: You have a vague optimization goal

If you don't yet have `initial.py` and `evaluate.py`, run the guided interview:

```
/evolve-interview I want to improve the sorting algorithm in src/solver.py to minimize total wall-clock time on my benchmark dataset
```

The interview asks you questions about:
- **What exactly are we optimizing?** (fitness metric)
- **Which code is mutable?** (helps you place `# EVOLVE-BLOCK-START/END` markers)
- **How do we measure fitness without asking an LLM?** (test suite, custom evaluator, etc.)
- **What must stay unchanged?** (non-goals, time budgets, correctness constraints)

It scores your answers on four dimensions using a mathematical ambiguity metric and refuses to proceed until ambiguity drops below 20%. Once clear, it crystallizes the specification into three files (`initial.py`, `evaluate.py`, `config.json`) and offers to start the run immediately.

### Path B: You already have `initial.py` and `evaluate.py`

Just launch the evolutionary loop:

```
/evolve config.json
```

Or with inline parameters if you don't have a config file yet:

```
/evolve --init initial.py --eval evaluate.py --generations 50
```

Then check on it anytime:

```
/evolve-status
```

---

## Core Concepts

### EVOLVE-BLOCK markers

claude-evolve only mutates code between these markers:

```python
# EVOLVE-BLOCK-START
def my_algorithm(input):
    # The LLM can rewrite anything in here
    return input
# EVOLVE-BLOCK-END

# Code outside the markers is preserved verbatim
def run_experiment(**kwargs):
    return my_algorithm(kwargs['data'])
```

Put the part you want optimized **inside** the markers. Put fixed infrastructure (imports, the stable interface function, helpers you don't want touched) **outside**. The interface `run_experiment(**kwargs)` is conventionally kept outside so the evaluator has a stable entry point.

### The evolutionary loop

Each generation:

1. **Select an island** (round-robin across populations)
2. **Select a parent** from that island using weighted sampling (sigmoid of fitness)
3. **Get inspirations**: top-K elites and recent archive members from the island
4. **Select a bandit arm**: UCB1 picks a `(model, effort)` pair like `sonnet/medium` or `haiku/high`
5. **Build a prompt**: parent code + inspirations + task description + a randomly-chosen persona
6. **Query Claude**: subprocess call to `claude --model X --effort Y -p <prompt>`
7. **Apply the patch**: diff (SEARCH/REPLACE blocks), full rewrite, crossover (two parents), or fix (for broken mutations)
8. **Check novelty**: AST fingerprint + MinHash similarity rejects near-duplicates
9. **Evaluate**: run `python3 evaluate.py --program_path <candidate>` as a subprocess
10. **Update database**: store the new program, update bandit reward based on improvement over parent
11. **Periodic work**: meta-scratchpad recommendations (every 10 gens), island migration (every 10 gens)

### Islands

Multiple independent populations evolve in parallel. Elites migrate between islands every N generations. This prevents premature convergence — one island might find a good local optimum while another explores a different region of the search space. Migration then spreads good ideas.

### The `(model, effort)` bandit ensemble

claude-evolve treats each combination of `(model, effort_level)` as a distinct "arm" for a UCB1 multi-armed bandit:

- `sonnet/max`, `sonnet/high`, `sonnet/medium`, `sonnet/low`
- `haiku/high`, `haiku/medium`, `haiku/low`
- `opus/max`, `opus/high` (slow; used sparingly)

The bandit learns from actual improvement scores which arm produces the biggest fitness gains and prioritizes it. In practice, on fast-iteration tasks, `haiku/high` or `sonnet/low` usually win — they're fast enough to iterate quickly and smart enough to produce quality mutations. `sonnet/high` and above use extended thinking which can take 10–30 minutes per call and is usually not worth it.

### Patch types

- **Diff** (50% default) — LLM returns `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks. Fast, preserves most of the code, good for targeted improvements.
- **Full rewrite** (25%) — LLM returns a complete new version inside a fenced code block. Good for substantial rewrites when the current approach is wrong.
- **Crossover** (10%) — LLM receives two parents and is asked to combine their best ideas. Good for discovering hybrid strategies.
- **Fix** (15%) — When a diff or full patch produces broken code (syntax error, crash), the fix patch sends the broken code + error message back to the LLM for correction. Salvages otherwise-wasted generations.

### Novelty rejection

Before evaluating a new program, claude-evolve computes an AST-based fingerprint (structural features + MinHash) and compares it against existing programs in the same island. If similarity exceeds 0.95, the proposal is rejected as a near-duplicate. This prevents the bandit from wasting LLM calls on mutations that would produce essentially the same program.

### Meta-scratchpad

Every 10 generations, a 3-step LLM pipeline analyzes top programs and writes a short "scratchpad" of recommendations. These get injected into future mutation prompts, giving later generations high-level insights extracted from earlier successes.

---

## The Four Skills

| Skill | Purpose | When to use |
|-------|---------|-------------|
| **`/evolve-install`** | Install plugin, create venv, verify dependencies, check MCP | First time, or after a plugin update |
| **`/evolve-interview`** | Socratic interview to define a task and generate spec files | You have a goal but no `initial.py`/`evaluate.py` yet |
| **`/evolve`** | Start an autonomous evolution run | You have spec files and want to run |
| **`/evolve-status`** | Check a running evolution's progress | Anytime during a run |

### `/evolve-install`

```
/evolve-install            # auto-detect: install or verify
/evolve-install --local    # create core/.venv/ and install there (default)
/evolve-install --global   # use system Python, no venv
/evolve-install --verify   # only run the health check phase
/evolve-install --force    # reinstall from scratch
/evolve-install --help     # show options
```

The first time you run this, it walks through five phases and saves state to `state/install-state.json`. On subsequent runs, it detects the existing install and offers a quick verify instead of repeating the wizard.

### `/evolve-interview`

```
/evolve-interview "I want to optimize the ranking function in src/ranker.py to maximize NDCG@10 on my validation set"
```

The interview asks **one question at a time** targeting the weakest of four clarity dimensions:

| Dimension | Weight | Question style |
|-----------|--------|---------------|
| **Goal** | 30% | "What single number defines success?" |
| **Program** | 25% | "Which exact function/region is mutable?" |
| **Evaluation** | 30% | "How do we measure that without asking an LLM?" |
| **Constraints** | 15% | "What must remain unchanged or bounded?" |

After each answer, it computes an ambiguity score:

```
ambiguity = 1 - (goal × 0.30 + program × 0.25 + eval × 0.30 + constraints × 0.15)
```

It will not crystallize artifacts until `ambiguity ≤ 0.20`. At rounds 4, 6, and 8, special "challenge agent" modes activate: **contrarian** (tests whether your hard constraints are actually hard), **simplifier** (probes whether the spec can be reduced), and **ontologist** (forces you to name the core entity when things feel fuzzy).

When the interview completes, it writes:

- `initial.py` with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers placed based on your answers
- `evaluate.py` as a standalone script that runs real code (LLM-as-judge is explicitly forbidden)
- `config.json` with sensible defaults tuned to your task

Then it runs the baseline evaluation and reports your starting score before handing off to `/evolve`.

### `/evolve`

```
/evolve config.json                                 # use a pre-written config
/evolve --init initial.py --eval evaluate.py       # quick start with defaults
/evolve --init initial.py --eval evaluate.py --generations 100
```

Starts the evolution loop. Runs autonomously — you don't need to interact with it. Progress is logged to `state/evolve.log` (JSONL format) and state is checkpointed after every generation so you can inspect progress with `/evolve-status` or resume after an interruption.

### `/evolve-status`

```
/evolve-status
```

Reports:

- Current generation / total generations
- Best combined score and which program achieved it
- Bandit arm selection counts and mean rewards
- Active islands and their sizes
- Recent generation events from the JSONL log
- Run PID (so you can kill it with `kill -TERM <pid>` to trigger graceful shutdown)

---

## Writing `initial.py`

Your seed program has three parts:

### 1. Imports (outside EVOLVE-BLOCK)

```python
import numpy as np
from scipy.optimize import minimize
```

Put all imports outside the markers — they're part of the stable infrastructure.

### 2. The mutable algorithm (inside EVOLVE-BLOCK)

```python
# EVOLVE-BLOCK-START
def my_algorithm(input_data):
    """This is what the LLM will mutate.

    Keep the function signature stable — evaluate.py calls this.
    Everything else can change.
    """
    # Naive initial implementation
    return sum(input_data) / len(input_data)
# EVOLVE-BLOCK-END
```

Write the simplest version that could possibly work. The LLM is better at improving something that runs than at generating from scratch.

### 3. The stable interface (outside EVOLVE-BLOCK)

```python
def run_experiment(**kwargs):
    """Called by evaluate.py. Must not change."""
    data = kwargs.get('data', [1, 2, 3, 4, 5])
    result = my_algorithm(data)
    return result
```

**Important:** `run_experiment(**kwargs)` is the conventional entry point. The evaluator imports the candidate module and calls this function. Keep it outside the markers so the LLM can't accidentally break the interface.

### Real example: circle packing

```python
# EVOLVE-BLOCK-START
"""Constructor-based circle packing for n=26 circles"""
import numpy as np

def construct_packing():
    n = 26
    centers = np.zeros((n, 2))
    centers[0] = [0.5, 0.5]
    for i in range(8):
        angle = 2 * np.pi * i / 8
        centers[i + 1] = [0.5 + 0.3 * np.cos(angle), 0.5 + 0.3 * np.sin(angle)]
    for i in range(16):
        angle = 2 * np.pi * i / 16
        centers[i + 9] = [0.5 + 0.45 * np.cos(angle), 0.5 + 0.45 * np.sin(angle)]
    centers = np.clip(centers, 0.01, 0.99)
    radii = compute_max_radii(centers)
    return centers, radii

def compute_max_radii(centers):
    n = centers.shape[0]
    radii = np.ones(n)
    for i in range(n):
        x, y = centers[i]
        radii[i] = min(x, y, 1.0 - x, 1.0 - y)
    for _ in range(n * n):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                dist = float(np.linalg.norm(centers[i] - centers[j]))
                if radii[i] + radii[j] > dist:
                    scale = dist / (radii[i] + radii[j])
                    radii[i] *= scale
                    radii[j] *= scale
                    changed = True
        if not changed:
            break
    return np.maximum(radii, 0.0)
# EVOLVE-BLOCK-END

def run_experiment(**kwargs):
    """Fixed interface — not evolved."""
    centers, radii = construct_packing()
    sum_radii = float(np.sum(radii))
    return centers, radii, sum_radii
```

See `core/examples/circle_packing/initial.py` for the working version.

---

## Writing `evaluate.py`

The evaluator is a standalone Python script that:

1. Accepts `--program_path <path>` as a CLI argument
2. Dynamically imports the candidate module at that path
3. Calls `run_experiment()` (or whatever entry point you defined)
4. Validates correctness if needed
5. Computes a fitness score
6. Prints JSON to stdout with at minimum `combined_score` (float) and `correct` (bool)

### Minimal template

```python
#!/usr/bin/env python3
"""Evaluator for <your task>."""
import argparse
import importlib.util
import json
import sys


def load_program(path):
    spec = importlib.util.spec_from_file_location("candidate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program_path", required=True)
    args = p.parse_args()

    try:
        mod = load_program(args.program_path)
        result = mod.run_experiment()
        score = compute_score(result)
        correct = validate(result)
    except Exception as e:
        print(json.dumps({
            "combined_score": 0.0,
            "correct": False,
            "error": str(e),
        }))
        sys.exit(0)

    print(json.dumps({
        "combined_score": float(score),
        "correct": bool(correct),
    }))


def compute_score(result):
    """Return a scalar to maximize. Higher is better."""
    return result  # replace with your fitness function


def validate(result):
    """Return True if the result meets correctness requirements."""
    return True


if __name__ == "__main__":
    main()
```

### Critical rules

1. **Always print JSON to stdout**, never raise uncaught exceptions. Failed mutations should return `combined_score: 0.0, correct: False` with an error message so the loop can continue.
2. **Higher scores must be better.** claude-evolve maximizes `combined_score`. If your natural metric is "error rate" or "latency", return its negation or reciprocal.
3. **Never use an LLM to judge fitness.** Fitness must come from real code execution. If your problem truly has no objective metric, claude-evolve is not the right tool.
4. **Keep evaluations fast.** Each generation runs one evaluation. If each takes 30 minutes, 50 generations takes 25 hours. Aim for under 60 seconds per evaluation.
5. **Exit with code 0** even on failure. Non-zero exit codes make the runner treat the evaluator itself as broken, not the candidate.
6. **Be deterministic or well-averaged.** If your evaluation is noisy, average over N seeds so the score reflects true quality, not RNG luck.

### Example: testing with pytest

```python
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program_path", required=True)
    args = p.parse_args()

    # Copy candidate to a fixture location that the test suite imports
    import shutil
    shutil.copy(args.program_path, "src/candidate.py")

    # Run pytest and parse results
    import subprocess
    result = subprocess.run(
        ["pytest", "tests/", "--json-report", "--json-report-file=/tmp/report.json"],
        capture_output=True, timeout=60,
    )

    with open("/tmp/report.json") as f:
        report = json.load(f)

    passed = report["summary"].get("passed", 0)
    total = report["summary"]["total"]

    print(json.dumps({
        "combined_score": passed / total if total else 0.0,
        "correct": passed == total,
        "passed": passed,
        "total": total,
    }))
```

### Example: benchmark with runtime measurement

```python
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program_path", required=True)
    args = p.parse_args()

    try:
        mod = load_program(args.program_path)
        import time
        start = time.perf_counter()
        result = mod.run_experiment(data=BENCHMARK_INPUT)
        elapsed = time.perf_counter() - start

        # Verify correctness
        correct = result == EXPECTED_OUTPUT

        # Fitness: reciprocal of runtime, zero if incorrect
        score = 1.0 / elapsed if correct else 0.0
    except Exception as e:
        print(json.dumps({"combined_score": 0.0, "correct": False, "error": str(e)}))
        return

    print(json.dumps({
        "combined_score": score,
        "correct": correct,
        "runtime_ms": elapsed * 1000,
    }))
```

---

## Writing `config.json`

A minimal config:

```json
{
  "task_description": "Optimize the sort algorithm for speed on random integer arrays",
  "language": "python",
  "init_program_path": "initial.py",
  "eval_program_path": "evaluate.py",
  "num_generations": 50,
  "ensemble": {
    "arms": [
      "sonnet/medium",
      "sonnet/low",
      "haiku/high",
      "haiku/medium",
      "haiku/low"
    ]
  },
  "islands": {
    "num_islands": 2,
    "migration_interval": 10
  },
  "llm_timeout": 300,
  "eval_timeout": 120
}
```

That's enough to run. All other fields have sensible defaults. See [Configuration Reference](#configuration-reference) for the full list.

---

## Running an Evolution

### Start it

```
/evolve config.json
```

Or inline:

```
/evolve --init initial.py --eval evaluate.py --generations 50
```

The runner spawns as a background subprocess and writes state to `./state/` relative to the config file's directory. It prints `pid` and returns immediately — the evolution runs on its own.

### Check progress

```
/evolve-status
```

This reads `state/run_state.json` and reports the current generation, best score, bandit stats, and recent log events. Run it as often as you want — it doesn't disturb the running evolution.

You can also tail the JSONL log directly:

```bash
tail -f state/evolve.log
```

Each line is a JSON event like:

```json
{"ts": "2026-04-10T23:27:41Z", "gen": 0, "event": "init", "score": 1.8378, "correct": true}
{"ts": "2026-04-10T23:30:12Z", "gen": 1, "event": "generation_complete", "arm": "haiku/high", "patch_type": "full", "score": 2.0837, "correct": true}
```

### Stop it gracefully

```
/evolve-stop
```

Or directly:

```bash
kill -TERM <pid>    # pid is shown in /evolve-status
```

The runner catches SIGTERM and SIGINT, saves state, waits up to 30 seconds for in-flight work, and exits cleanly. You can resume later by launching `/evolve` with the same config — it detects existing state and continues from the last completed generation.

### After it finishes

The database at `state/programs.db` contains every program the loop generated. Query it to find the winner:

```bash
sqlite3 state/programs.db "
SELECT id, generation, combined_score, correct, patch_type
FROM programs
WHERE correct = 1
ORDER BY combined_score DESC
LIMIT 10
"
```

Or use the visualization tool:

```
/evolve-visualize progress    # score-over-time chart
/evolve-visualize genealogy   # parent-child tree of top programs
/evolve-visualize bandit      # arm selection stats
```

Extract the winning program's code:

```bash
sqlite3 state/programs.db "SELECT code FROM programs WHERE id = <best_id>" > best.py
```

---

## Monitoring and Visualization

claude-evolve includes three built-in visualizations:

### Progress chart

Shows the best score per generation as a terminal sparkline plus a table:

```
Generation: 1    5    10   15   20   25   30   35   40   45   50
Best score: ▁▂▃▄▅▅▆▆▇▇█ (1.8378 → 2.6343)

Gen  Best     Mean     Improvement
  1  1.8378   1.8378   +0.00%
  5  1.9215   1.8803   +4.55%
 10  2.4103   2.1844   +31.16%
 15  2.5943   2.3871   +41.17%
 ...
 50  2.6343   2.5102   +43.34%
```

### Genealogy tree

Shows the parent-child relationships of the top-K programs:

```
└─ id=23 gen=40 score=2.6343 full
   └─ id=16 gen=22 score=2.6296 fix
      └─ id=10 gen=14 score=2.6236 full
         └─ id=9  gen=13 score=2.5943 diff
            └─ id=1  gen=0  score=1.8378 seed
```

### Bandit arm stats

Shows which `(model × effort)` combinations were selected and how they performed:

```
Arm              Selected  Mean reward  UCB1 score
sonnet/low          15       +0.398       2.14
sonnet/medium        9       +0.262       1.97
haiku/high           8       +0.180       1.88
haiku/medium         9       +0.155       1.83
haiku/low            9       +0.088       1.72
```

---

## Configuration Reference

Full `config.json` schema with all fields and defaults:

```json
{
  "task_description": "You are an expert optimization and algorithm design assistant...",
  "language": "python",
  "init_program_path": "initial.py",
  "eval_program_path": "evaluate.py",
  "num_generations": 100,

  "ensemble": {
    "arms": [
      "opus/max", "opus/high",
      "sonnet/max", "sonnet/high", "sonnet/medium", "sonnet/low",
      "haiku/high", "haiku/medium", "haiku/low"
    ],
    "selection": "ucb1",
    "exploration_coef": 1.0,
    "epsilon": 0.2,
    "shift_by_baseline": true,
    "shift_by_parent": true,
    "adaptive_scale": true,
    "asymmetric_scaling": true
  },

  "patches": {
    "types": ["diff", "full", "cross", "fix"],
    "probs": [0.5, 0.25, 0.1, 0.15],
    "max_resamples": 3,
    "max_attempts": 1
  },

  "islands": {
    "num_islands": 2,
    "migration_interval": 10,
    "migration_rate": 0.1,
    "archive_size": 40,
    "archive_selection_strategy": "fitness",
    "parent_selection_strategy": "weighted",
    "enable_dynamic_islands": false,
    "stagnation_threshold": 100,
    "elite_selection_ratio": 0.3
  },

  "meta": {
    "rec_interval": 10,
    "max_recommendations": 5,
    "sample_single": true
  },

  "novelty": {
    "similarity_threshold": 0.95,
    "max_attempts": 3
  },

  "prompt_evo": {
    "enabled": false,
    "evolution_interval": null,
    "archive_size": 10
  },

  "logging": {
    "format": "jsonl",
    "path": "state/evolve.log",
    "level": "INFO"
  },

  "eval_timeout": 120,
  "llm_timeout": 300,
  "use_text_feedback": false,
  "inspiration_sort_order": "ascending",
  "results_dir": "state/"
}
```

### Key knobs

| Field | Default | Description |
|-------|---------|-------------|
| `num_generations` | 100 | How many mutation→evaluate cycles to run |
| `ensemble.arms` | 9 arms | Which `(model, effort)` combinations the UCB1 bandit can pick from. Drop slow arms like `sonnet/high` if you want fast iteration. |
| `ensemble.exploration_coef` | 1.0 | UCB1 exploration weight. Higher = more exploration, lower = more exploitation |
| `patches.types` | 4 types | Which mutation styles to use. Can drop `cross` if you only have 1 island or want simpler mutations. |
| `patches.probs` | `[0.5, 0.25, 0.1, 0.15]` | Sampling probabilities for each patch type. Must sum to 1.0 and match length of `patches.types`. |
| `islands.num_islands` | 2 | Number of independent populations. Use 1 for small tasks, 3+ for larger exploration. |
| `islands.migration_interval` | 10 | Migrate elites every N generations |
| `novelty.similarity_threshold` | 0.95 | Reject proposals with AST similarity above this. Lower = more novelty enforcement |
| `llm_timeout` | 300 | Max seconds per `claude` subprocess call. Set to 900+ if using `sonnet/max` or `opus/*` arms |
| `eval_timeout` | 120 | Max seconds per evaluator subprocess. Set based on your evaluator's worst-case runtime |
| `meta.rec_interval` | 10 | Generate meta-scratchpad recommendations every N generations |
| `prompt_evo.enabled` | false | Enable system-prompt co-evolution (experimental) |

### Recommended arm sets by task type

- **Fast iteration (< 30s per generation):** `["haiku/high", "haiku/medium", "haiku/low"]`
- **Balanced (default):** `["sonnet/medium", "sonnet/low", "haiku/high", "haiku/medium", "haiku/low"]`
- **Deep thinking (several minutes per generation):** `["sonnet/max", "sonnet/high", "opus/max"]` + `llm_timeout: 1800`

---

## Architecture

```
claude-evolve/
├── CLAUDE.md                          # Plugin registration + skill index
├── .claude-plugin/
│   ├── marketplace.json               # Claude Code marketplace manifest
│   └── plugin.json                    # Plugin metadata
├── .mcp.json                          # MCP server registration
├── package.json                       # Thin npm wrapper for plugin install
├── bridge/
│   └── mcp-wrapper.mjs                # Node wrapper that spawns the Python MCP server
├── skills/                            # Slash command definitions
│   ├── evolve-install.md
│   ├── evolve-interview.md
│   ├── evolve.md
│   └── evolve-status.md
├── core/                              # Python core
│   ├── pyproject.toml
│   └── claude_evolve/
│       ├── server.py                  # Python MCP server (5 tools)
│       ├── cli.py                     # `claude-evolve` command-line entry point
│       ├── runner.py                  # Main evolution orchestrator (sync-first)
│       ├── evaluation.py              # Evaluator subprocess wrapper
│       ├── config.py                  # EvolveConfig dataclass + JSON I/O
│       ├── ensemble/
│       │   ├── bandit.py              # UCB1 bandit over (model, effort) arms
│       │   ├── bridge.py              # `claude --model X --effort Y -p ...` wrapper
│       │   └── personas.py            # 5 prompt personas (replaces temperature)
│       ├── database/
│       │   ├── db.py                  # SQLite program database
│       │   ├── islands.py             # Island management + migration
│       │   ├── parents.py             # Weighted/power-law parent selection
│       │   ├── archive.py             # Elite archive maintenance
│       │   └── models.py              # Program dataclass
│       ├── mutations/
│       │   ├── apply_diff.py          # SEARCH/REPLACE patching
│       │   ├── apply_full.py          # Full rewrite patching
│       │   ├── crossover.py           # Crossover patch type
│       │   ├── apply_fix.py           # Fix patch for broken mutations
│       │   └── sampler.py             # PromptSampler (builds mutation prompts)
│       ├── prompts/                   # Prompt templates (ported from ShinkaEvolve)
│       ├── meta/
│       │   ├── summarizer.py          # Meta-scratchpad 3-step pipeline
│       │   └── prompt_evolver.py      # Prompt co-evolution
│       ├── novelty/
│       │   ├── embeddings.py          # AST fingerprint + MinHash
│       │   └── judge.py               # Novelty rejection logic
│       └── visualization/
│           ├── progress.py            # Score-over-time chart
│           ├── genealogy.py           # Parent-child tree
│           └── bandit.py              # Arm selection stats
└── examples/
    └── circle_packing/
        ├── initial.py                 # Naive 26-circle packing
        ├── evaluate.py                # Circle packing evaluator
        └── config.json                # Benchmark config
```

---

## Benchmark: Circle Packing

The reference benchmark is the 26-circle packing task from the ShinkaEvolve paper: place 26 non-overlapping circles inside the unit square `[0,1]²` to maximize the sum of their radii.

### Results

| System | Best score | Improvement over baseline |
|--------|-----------|---------------------------|
| Naive initial (ring pattern) | 1.8378 | — |
| **claude-evolve** (50 gen, 3 islands, ~3 hours) | **2.6343** | **+43.3%** |
| **ShinkaEvolve** (reference, 150 gens) | **2.6360** | **+43.4%** |

claude-evolve reached **99.94% of ShinkaEvolve's reference score** using only Claude Code models — no GPT, no Gemini, no cost-tracked API calls.

### How to reproduce

```bash
cp -r core/examples/circle_packing ./my-circle-run
cd my-circle-run
/evolve config.json
```

The discovered solution (in the database after the run completes) is a hybrid approach:

- Multiple initialization strategies: hexagonal grid, golden-angle sunflower, ring pattern
- Augmented Lagrangian method (PHR) for non-overlap constraints
- L-BFGS-B optimizer for local minimization
- Basin hopping for escaping local optima
- Adaptive temperature scheduling for multi-start

This is the textbook approach for packing problems — claude-evolve discovered it entirely through evolutionary search, starting from a naive initial solution that knew nothing about optimization theory.

---

## Troubleshooting

### `/evolve-install` fails at Phase 2: "scipy build error"

Upgrade pip and try again:

```bash
source core/.venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e core/
```

On older Linux, you may need to install system libraries:

```bash
sudo apt install python3-dev libopenblas-dev liblapack-dev gfortran
```

### `/evolve-install` says "MCP tools list empty"

The Python import failed. Debug directly:

```bash
python3 -m claude_evolve.server
# Should not raise any errors. If it does, the traceback shows what's missing.
```

Common causes: `mcp` package not installed, `numpy`/`scipy` not installed, Python version < 3.10.

### `claude mcp list` doesn't show claude-evolve

Close and re-open Claude Code. If still missing, manually register:

```bash
claude mcp add -s user claude-evolve -- python3 -m claude_evolve.server
```

### Evolution runs but every generation has `correct=False`

Your evaluator is rejecting all candidates. Run it directly on the seed to see what's happening:

```bash
python3 evaluate.py --program_path initial.py
```

If the seed itself scores `correct=False`, the evaluator's validation is wrong — fix it before running evolution. The seed must be correct for the loop to have any parent to mutate from.

### `sonnet/high` or `opus/*` arms always time out

This is expected with the default `llm_timeout: 300`. Extended thinking on complex tasks can take 10–30 minutes per call and may even exceed the default 32,000 output token limit.

**Options:**

1. **Drop slow arms** from the ensemble (recommended for most tasks):
   ```json
   "arms": ["sonnet/medium", "sonnet/low", "haiku/high", "haiku/medium", "haiku/low"]
   ```

2. **Raise `llm_timeout`** to 1800 (30 min) if you want to keep `sonnet/max`/`opus/*` and accept slow iteration:
   ```json
   "llm_timeout": 1800
   ```

3. **Raise `CLAUDE_CODE_MAX_OUTPUT_TOKENS`** env var if you see "Response exceeded 32000 output tokens" errors in logs. claude-evolve already sets this to 64000 internally, but you can go higher:
   ```bash
   export CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000
   ```

### Evolution is slow even with haiku arms

Each `claude` call has subprocess startup overhead. To minimize:

- claude-evolve already uses `--strict-mcp-config` with an empty MCP config to skip loading other plugins per call. This is built in.
- Make sure your evaluator is fast. If each evaluation takes 60 seconds, the LLM call is not the bottleneck.
- Reduce `patches.probs` for `cross` (which needs two parents and builds longer prompts).
- Consider `num_islands: 1` and `migration_interval: 999` to skip migration overhead for small runs.

### "Not logged in · Please run /login"

This happens if your Claude CLI auth has expired. Run `claude` interactively once to re-authenticate, then retry.

### Runs use too much of my Claude quota

Set `num_generations` lower and experiment with a short run first. 10 generations is usually enough to see whether your setup is working. A 50-generation run with `sonnet/medium` dominant typically costs ~$5–15 in Claude API credits if you're on a usage-based plan.

---

## FAQ

### How is this different from ShinkaEvolve?

Same algorithm, different LLM layer. ShinkaEvolve uses external API keys (OpenAI, Gemini, DeepSeek, Anthropic) through multi-provider routing. claude-evolve replaces all of that with a single `claude --model X --effort Y -p ...` subprocess call and treats `(model × effort_level)` as distinct arms in the UCB1 bandit. The core algorithm — islands, meta-scratchpads, novelty rejection, patch types, prompt co-evolution — is faithfully ported.

The practical difference: you don't manage API keys, you can't use GPT/Gemini, and the ensemble diversity comes from mixing Claude models at different thinking-effort levels plus 5 prompt personas (instead of temperature sampling, which the Claude CLI doesn't expose).

### Can I use this without Claude Code?

No. claude-evolve is specifically designed to use the `claude` CLI that ships with Claude Code. It's not a standalone library — its entire value proposition is "use the Claude Code authentication and model access you already have."

If you want a standalone Python library for LLM-driven code evolution, use ShinkaEvolve directly.

### Can I evolve code in languages other than Python?

The prompts and patch logic support multi-language output, but the evaluator subprocess is always launched as `python3 evaluate.py`. Your evaluator can be Python that compiles/runs other languages — e.g. it can `subprocess.run(["cargo", "bench"])` and parse the output. The candidate code itself can be Rust, Go, Julia, etc., as long as you set `language` in the config so the mutation prompts generate the right code.

### How do I know when to stop a run?

Watch `/evolve-status` for a few generations. If the best score plateaus for 10+ generations and the bandit has converged (one arm dominating), further iteration probably won't help much. Stop with `/evolve-stop` or let it run to `num_generations`.

### What if my task genuinely has no objective fitness function?

claude-evolve is not the right tool. Evolutionary optimization requires a scalar signal to climb. If you need subjective quality judgment, use a standard code-review workflow with Claude Code, not evolutionary search.

### Can I use this for non-code optimization (e.g. prompt engineering)?

Yes, indirectly. Put your prompt text inside a Python function that returns it, wrap that function in EVOLVE-BLOCK markers, and have your evaluator run the prompt against a benchmark and return the accuracy/score. claude-evolve will evolve the prompt string. This is how ShinkaEvolve's AIME mathematical reasoning benchmark worked in the paper.

### How does this compare to AlphaEvolve / DeepMind's approach?

Same core idea (LLM-as-mutator in an evolutionary loop), but claude-evolve is open-source, runs locally, and uses only Claude Code models. ShinkaEvolve's paper demonstrated it matches or beats AlphaEvolve on several benchmarks while being far more sample-efficient (150 evaluations for the circle packing SOTA vs thousands for AlphaEvolve). claude-evolve inherits that efficiency.

### Is this officially supported by Anthropic?

No. claude-evolve is an independent community plugin built on top of Claude Code's plugin infrastructure. It uses only public APIs (the `claude` CLI and the MCP protocol). There is no special access or coordination with Anthropic.

---

## License

MIT. See `LICENSE` for details.

## Credits

- **[ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve)** (Sakana AI, ICLR 2026) — the algorithm this plugin reimplements. Paper: [arxiv.org/abs/2509.19349](https://arxiv.org/abs/2509.19349).
- **[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode)** — the reference for skill structure, setup patterns, and the deep-interview concept.
- **Claude Code team at Anthropic** — for building the plugin infrastructure this runs on.
