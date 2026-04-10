---
name: evolve-setup
description: Create an evaluator script through guided interview
---

Interactively create an evaluator script for use with `/evolve`. The interview guides you through defining:

1. What the program computes/produces
2. How to measure success (fitness metric)
3. Correctness constraints (must-pass validation)
4. Number of evaluation runs to average over

## Usage

```
/evolve-setup path/to/initial.py
```

## Output

Generates an `evaluate.py` script compatible with claude-evolve's evaluation protocol.
