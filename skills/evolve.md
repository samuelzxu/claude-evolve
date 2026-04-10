---
name: evolve
description: Start an autonomous evolutionary code optimization run
---

Launch an evolution run that optimizes code using Claude Code models as mutation operators.

## Usage

```
/evolve [config.json]
/evolve --init initial.py --eval evaluate.py --generations 50
```

## Requirements

- `initial.py`: Seed program with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers
- `evaluate.py`: Evaluator script that returns metrics and correctness

## What Happens

1. Loads the initial program and evaluator
2. Runs N generations of evolutionary optimization:
   - Selects parent programs from island populations
   - Queries Claude models (via UCB1 bandit) to generate mutations
   - Applies diff/full/crossover patches
   - Evaluates fitness via the user's evaluator script
   - Updates the archive of best solutions
3. Returns the best-performing solution

The run is autonomous -- it proceeds without interaction until complete or stopped.
