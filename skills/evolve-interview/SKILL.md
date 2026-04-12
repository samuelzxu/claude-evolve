---
name: evolve-interview
description: Socratic deep interview with mathematical ambiguity gating to crystallize an evolution task (produces initial.py, evaluate.py, config.json)
argument-hint: "<describe what you want to optimize>"
level: 3
---

# claude-evolve Interview

Ouroboros-inspired Socratic interview that refuses to start an expensive evolution run until the task specification is mathematically clear. Asks targeted questions across four dimensions, scores ambiguity after every answer, and only crystallizes into concrete artifacts (`initial.py`, `evaluate.py`, `config.json`) once ambiguity drops below 20%.

**When this skill is invoked, immediately execute the workflow below. Do not only restate or summarize these instructions back to the user.**

## Purpose

Evolutionary code discovery is expensive. Each generation burns real LLM calls and wall-clock time. If the fitness function is ambiguous, the wrong code region is marked mutable, or the evaluator is unreliable, the entire run produces garbage. This skill prevents that by forcing specification clarity *before* execution.

Analogous to oh-my-claudecode's deep-interview, but specialized for the four dimensions that actually matter for evolution:
1. **Goal Clarity** — What are we optimizing?
2. **Program Clarity** — Which code is mutable?
3. **Evaluation Clarity** — How is fitness measured (without LLM-as-judge)?
4. **Constraint Clarity** — What must be preserved? What's the budget?

## Use When

- User wants to evolve code but hasn't defined `initial.py` / `evaluate.py` yet
- User has a vague optimization goal ("make this faster", "improve accuracy")
- User has code but isn't sure what to mark as mutable with EVOLVE-BLOCK markers
- User says "help me set up an evolution run", "interview me", "I want to evolve X"

## Do Not Use When

- User already has `initial.py` with EVOLVE-BLOCK markers AND a working `evaluate.py` → run `/evolve` directly
- User just wants to check status of a running evolution → run `/evolve-status`
- User wants to install the plugin → run `/evolve-install`

## Execution Policy

- Ask **ONE question at a time** — never batch
- Target the **weakest clarity dimension** with each question
- State, in one sentence before the question, *why* that dimension is the bottleneck
- Gather codebase facts via the `Explore` subagent BEFORE asking the user about them
- For brownfield tasks, cite repo evidence (file path, function, line) in questions
- Score ambiguity after every answer and display it transparently
- Do NOT proceed to crystallization until ambiguity ≤ 0.20 (or the user explicitly exits early with a warning)
- Persist interview state for resume across interruptions
- Challenge agent modes activate at specific round thresholds to break fixation

## Dimensions and Weights

Ambiguity score computed as:

```
ambiguity = 1 - (goal * 0.30 + program * 0.25 + evaluation * 0.30 + constraints * 0.15)
```

Each sub-score is in [0.0, 1.0]:

| Dimension | Weight | What "1.0" looks like |
|-----------|--------|-----------------------|
| **Goal Clarity** | 0.30 | A single scalar fitness function stated in one sentence. No qualifiers. |
| **Program Clarity** | 0.25 | Exact file path + exact function/region to mark mutable. EVOLVE-BLOCK boundaries known. |
| **Evaluation Clarity** | 0.30 | Concrete command that runs real code and returns a number. Deterministic or well-averaged. |
| **Constraint Clarity** | 0.15 | Hard bounds on what must not change, time budget, correctness checks. |

Threshold: **0.20** ambiguity (i.e. 0.80 total clarity) before crystallization.

## Phase 1: Initialize

1. **Parse the user's idea** from the invocation args.
2. **Detect brownfield vs greenfield**:
   - Brownfield: user mentions an existing file or function in the current cwd
   - Greenfield: user describes a problem without existing code
3. **For brownfield**, spawn an `Explore` agent to map the relevant area and store `codebase_context`. Examples of what to extract: function signatures, test files, existing benchmarks, language/runtime, external deps.
4. **Initialize state** at `state/interview-state.json`:

```json
{
  "active": true,
  "phase": "interview",
  "interview_id": "<uuid-or-timestamp>",
  "type": "greenfield|brownfield",
  "initial_idea": "<user input>",
  "rounds": [],
  "current_ambiguity": 1.0,
  "threshold": 0.20,
  "codebase_context": null,
  "challenge_modes_used": [],
  "ontology_snapshots": []
}
```

5. **Announce the interview** to the user:

> Starting evolution task interview. I'll ask targeted questions to understand what you want to evolve before committing to an expensive run. After each answer I'll show your clarity score. We'll proceed to spec crystallization once ambiguity drops below 20%.
>
> **Your idea:** "<initial_idea>"
> **Project type:** <greenfield|brownfield>
> **Current ambiguity:** 100%

## Phase 2: Interview Loop

Repeat until `ambiguity ≤ 0.20` OR user early-exits:

### Step 2a: Generate Next Question

Select the dimension with the lowest clarity score. If tied, use weight order: Goal > Evaluation > Program > Constraints.

**Question styles by dimension:**

| Dimension | Question template | Example |
|-----------|-------------------|---------|
| Goal | "What single number defines success?" | "You said 'faster' — are we minimizing wall-clock seconds, CPU time, or something else?" |
| Program | "Which exact region is mutable?" | "I see three functions in `solver.py`. Which one is the algorithm you want to replace vs. fixed interface?" |
| Evaluation | "How do we measure that without asking an LLM?" | "Do you have a test suite we can run, or do we need a dedicated evaluator that runs the code on sample inputs?" |
| Constraints | "What must remain unchanged or bounded?" | "Is there a memory limit, runtime cap per evaluation, or an API surface that must stay stable?" |

**If the scope feels conceptually fuzzy (user keeps redefining the target), switch to an ontology question before returning to normal dimensions:**

> "Across the last few rounds you've described this as X, Y, and Z. Which of those IS the thing we're evolving, and which are inputs or metrics?"

### Step 2b: Ask via AskUserQuestion

Use `AskUserQuestion` to present the question with clickable options when possible. Header format:

```
Round {n} | Targeting: {weakest_dim} | Why now: {one-sentence rationale} | Ambiguity: {pct}%

{question}
```

Options should be concrete candidates (e.g. three distinct fitness metric choices) plus "Other" for free text.

### Step 2c: Score Ambiguity

After the user answers, compute scores for all four dimensions.

**Scoring rubric** (apply to every dimension):

- **0.0–0.3**: no concrete information — still metaphor, vague adjective, or contradiction
- **0.3–0.6**: direction clear but operational details missing
- **0.6–0.8**: one more detail needed (e.g. exact file name, exact test command)
- **0.8–0.95**: fully specified, minor rewording possible
- **0.95–1.0**: publishable, unambiguous

Be conservative. If you can write multiple different valid specifications from the user's answers, the score is **not** above 0.8.

Compute:
```
clarity = goal*0.30 + program*0.25 + evaluation*0.30 + constraints*0.15
ambiguity = 1 - clarity
```

### Step 2d: Ontology Tracking

Each round, extract the **key entities** mentioned (nouns with meaningful structure): Program, Evaluator, Input, Output, Metric, Constraint, Test, etc.

Track stability across rounds:
- `stable_entities` — present in both current and previous rounds
- `changed_entities` — renamed (same type, >50% field overlap)
- `new_entities` — first seen this round
- `removed_entities` — in previous but not current

`stability_ratio = (stable + changed) / total`. Round 1 is N/A (no previous to compare).

Append the snapshot to `state.ontology_snapshots[]`.

### Step 2e: Report Progress

After scoring, show the user:

```
Round {n} complete.

| Dimension | Score | Weight | Weighted | Gap |
|-----------|-------|--------|----------|-----|
| Goal | {g} | 0.30 | {g*0.30} | {gap} |
| Program | {p} | 0.25 | {p*0.25} | {gap} |
| Evaluation | {e} | 0.30 | {e*0.30} | {gap} |
| Constraints | {c} | 0.15 | {c*0.15} | {gap} |
| **Total clarity** | | | **{total}** | |
| **Ambiguity** | | | **{1-total}** | |

**Ontology:** {n} entities | Stability: {ratio}% | New: {n} | Changed: {n} | Stable: {n}

**Next target:** {weakest} — {rationale}
```

### Step 2f: Update State

Append the round to `state.rounds[]` and rewrite `state/interview-state.json`.

### Step 2g: Check Soft Limits

- **Round 3+**: allow early exit if user says "enough", "let's go", "just build it"
- **Round 10**: soft warning — "We're at 10 rounds, ambiguity {pct}%. Continue?"
- **Round 20**: hard cap — proceed with current clarity, loudly warning the user

## Phase 3: Challenge Agent Modes

At specific rounds, inject a perspective shift into the question generation prompt. Each mode is used **once** and then normal questioning resumes.

### Round 4+: Contrarian Mode

> CONTRARIAN MODE: Your next question should challenge the user's framing. Ask "what if the opposite were true?" or "what if the constraint you're treating as hard were actually negotiable?" Target the assumption most likely to be wrong.

Example: "You've said we must preserve the existing API surface. What if we didn't — would that unlock a dramatically better algorithm?"

### Round 6+: Simplifier Mode

> SIMPLIFIER MODE: Your next question should probe whether the spec can be reduced. Ask "what's the smallest version that would still be valuable?" or "which dimension could we drop and still ship?" Look for aspirational complexity.

Example: "You mentioned wanting both speed and accuracy. If we optimized purely for speed with a minimum accuracy floor, would that still be useful?"

### Round 8+ (only if ambiguity > 0.3): Ontologist Mode

> ONTOLOGIST MODE: Ambiguity is still high. We may be solving the wrong problem. Look at the ontology: {entities}. Ask "which entity is the core thing we're evolving, and which are context or environment?" Force the user to pick a noun.

Example: "You've mentioned the parser, the AST, and the optimizer. Which one is the evolution target? The other two are constraints or fixtures."

Track used modes in `state.challenge_modes_used[]` to prevent repetition.

## Phase 4: Crystallize Spec

When `ambiguity ≤ 0.20` (or early exit), generate the concrete artifacts:

### 4a. Write `initial.py`

Based on the interview answers:
- Copy or create the seed program
- Wrap the mutable region with `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers
- Include a `run_experiment(**kwargs)` function as the stable interface
- Preserve all immutable infrastructure (imports, helpers, interface)

If the user provided an existing file, read it and insert EVOLVE-BLOCK markers around the region they identified. Do not rewrite code outside the markers.

### 4b. Write `evaluate.py`

Based on the evaluation answers, produce a standalone evaluator that:
- Accepts `--program_path <path>` as an argument
- Dynamically loads the program module
- Calls `run_experiment()` (or the specified entry point)
- Validates correctness (if constraints were defined)
- Computes the fitness metric
- Outputs JSON to stdout with at minimum `combined_score` and `correct`

**Important:** The evaluator must NEVER use `claude` or any LLM to judge fitness. It must be pure code execution. If the user tried to specify an LLM-as-judge, reject it and ask for an objective metric.

Example template:

```python
#!/usr/bin/env python3
"""Evaluator for <task description>."""
import argparse
import importlib.util
import json
import sys
import time


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
        # <user-specific: call run_experiment, validate, compute metric>
        result = mod.run_experiment()
        score = _compute_score(result)
        correct = _validate(result)
    except Exception as e:
        print(json.dumps({"combined_score": 0.0, "correct": False, "error": str(e)}))
        sys.exit(0)

    print(json.dumps({
        "combined_score": float(score),
        "correct": bool(correct),
        # <additional metrics>
    }))


if __name__ == "__main__":
    main()
```

### 4c. Write `config.json`

Generate an `EvolveConfig`-compatible JSON. Set reasonable defaults based on the interview:

- `task_description`: built from the Goal and Constraints answers
- `init_program_path`: `"initial.py"`
- `eval_program_path`: `"evaluate.py"`
- `num_generations`: default 50; user can override
- `ensemble.arms`: default `["sonnet/medium", "sonnet/low", "haiku/high", "haiku/medium", "haiku/low"]` (skip `sonnet/high` and above unless the user explicitly wants extended thinking — they're slow and often exceed token limits)
- `patches.types`: `["diff", "full", "cross", "fix"]` with default probs
- `islands.num_islands`: 2 (default) or 3 for larger search
- `novelty.similarity_threshold`: 0.95
- `llm_timeout`: 300 (5 min) — generous for medium effort, not so generous that failures waste hours
- `eval_timeout`: derived from the user's stated time budget per evaluation (default 120)

### 4d. Show a Final Summary

Display:

```
✅ Spec crystallized (ambiguity: {pct}%)

Files written:
  - initial.py       ({lines} lines, EVOLVE-BLOCK marks {startline}-{endline})
  - evaluate.py      ({lines} lines)
  - config.json      ({gens} generations, {arms} bandit arms, {islands} islands)

Baseline fitness: run `python3 evaluate.py --program_path initial.py` to verify

Next step: run `/evolve` to start the evolutionary loop.
```

Then run the baseline evaluation yourself (via Bash) and report the baseline score so the user can see what they're starting from.

## Phase 5: Handoff

After the spec is ready, use `AskUserQuestion` to present:

**Question:** "Spec ready. How would you like to proceed?"

**Options:**
1. **Start evolution now** — run `/evolve` with the generated config (recommended)
2. **Review artifacts first** — show the generated files and let the user edit
3. **Run more interview rounds** — lower the ambiguity further before committing

On option 1, invoke the `evolve_start` MCP tool (or equivalent) with the generated config path.

## Tool Usage

- `AskUserQuestion` for every interview question — one at a time, never batch
- `Agent(subagent_type="Explore")` (haiku, short timeout) for brownfield codebase exploration BEFORE asking the user
- `Write` to create `initial.py`, `evaluate.py`, `config.json` in Phase 4
- `Bash` to run the baseline evaluation in Phase 4d
- `Skill("claude-evolve:evolve")` or the `evolve_start` MCP tool to hand off in Phase 5

## State Persistence

Write to `state/interview-state.json` after every round. On resume, re-read the file and continue from the last completed round. If the user edits artifacts manually between sessions, detect that and offer to re-score with the new artifacts in mind.

## Stop Conditions

- Hard cap at round 20 — proceed with current clarity, warn loudly
- Soft warning at round 10 — offer to continue or commit
- Early exit allowed from round 3+ if user explicitly says "stop" / "good enough" / "let's build it"
- User says "cancel" / "abort" — save state and exit
- Ambiguity stalls (same ±0.05 for 3 rounds) — activate Ontologist mode to reframe
- All dimensions ≥ 0.9 — skip directly to Phase 4 even if below round minimum

## Final Checklist

- [ ] Interview produced a spec with ambiguity ≤ 0.20 (or user chose early exit with warning)
- [ ] Ambiguity displayed after every round
- [ ] Each round explicitly named the targeted dimension and why
- [ ] Challenge agent modes activated at their thresholds when appropriate
- [ ] `initial.py` written with EVOLVE-BLOCK markers around the mutable region
- [ ] `evaluate.py` written and returns JSON with `combined_score` and `correct`
- [ ] `config.json` written and loads correctly via `EvolveConfig.from_json()`
- [ ] Baseline score reported to the user
- [ ] Evaluator runs real code — NO LLM-as-judge paths
- [ ] State file saved for resume
- [ ] Handoff option presented via AskUserQuestion

## Examples

### Good: targeting the weakest dimension

```
Round 3 | Targeting: Evaluation | Why now: we know the fitness metric
(sum of radii) and the mutable region (the packing algorithm), but
haven't specified HOW we measure it — ambiguity of the evaluator is
our biggest remaining gap. | Ambiguity: 38%

Do you have a test suite that validates circle packings, or should
I generate a dedicated evaluator that runs the algorithm once and
computes the sum of radii with overlap/boundary checks?
```

### Good: citing repo evidence

```
[explore agent finds: solver.py has parse(), plan(), and execute()]

Round 2 | Targeting: Program | Why now: you said "evolve the planner"
but solver.py has three functions — we need to know exactly which to
wrap in EVOLVE-BLOCK markers. | Ambiguity: 60%

I see three functions in solver.py: parse() (130 lines),
plan() (340 lines), and execute() (45 lines). Is `plan()` the
algorithm we're evolving, with parse() and execute() as fixed
interface?
```

### Bad: batching questions

```
"What's the fitness metric, and what's the mutable region, and how
long per evaluation, and how many generations should we run?"
```

Four questions at once → shallow answers, inaccurate scoring.

### Bad: LLM-as-judge

```
User: "Have Claude rate the code quality on a scale of 1 to 10."
Interviewer: (accepts this)
```

Wrong. Push back and explain that claude-evolve requires **real code
execution** for fitness. Ask for an objective metric: does the code
pass tests, produce correct output, run faster, use less memory, etc.

## Troubleshooting

- **User keeps giving vague answers** → activate Ontologist mode early
- **User wants LLM-as-judge** → firmly redirect to real execution; this is non-negotiable
- **No existing code** → the Program dimension can stay low until Phase 4 writes a seed
- **Very specialized domain** → read the codebase more aggressively before asking
