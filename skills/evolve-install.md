---
name: evolve-install
description: Install, verify, or refresh the claude-evolve plugin (Python deps, venv, MCP server)
level: 2
---

# claude-evolve Install

This is the setup command for claude-evolve. Run it once after installing the plugin to verify Python dependencies, create a virtual environment if needed, and confirm the MCP server is registered.

**When this skill is invoked, immediately execute the workflow below. Do not only restate or summarize these instructions back to the user.**

## Flag Parsing

Check for flags in the user's invocation:
- `--help` → Show Help Text (below) and stop
- `--force` → Skip Pre-Setup Check, run full setup from scratch
- `--verify` → Only run the verification phase (Phase 4), skip install/env setup
- `--local` → Install into a project-local `.venv`
- `--global` → Use the system Python (no venv)
- No flags → Auto-detect and run full setup if needed

## Help Text

When user runs with `--help`, display this and stop:

```
claude-evolve Install - Set up evolutionary code discovery plugin

USAGE:
  /evolve-install              Install or verify (auto-detect)
  /evolve-install --local      Install into a project-local .venv
  /evolve-install --global     Use system Python (no venv)
  /evolve-install --verify     Only verify existing install
  /evolve-install --force      Force full reinstall
  /evolve-install --help       Show this help

PHASES:
  1. Python check       Verify Python >= 3.10 is available
  2. Environment setup  Create venv (if --local or auto) and install claude-evolve
  3. Claude CLI check   Verify `claude` is on PATH and authenticated
  4. MCP verification   Confirm claude-evolve MCP server registers and exposes tools
  5. Smoke test         Run a trivial claude call to confirm the bridge works

NEXT STEPS:
  After install completes, run `/evolve-interview` to define your first
  evolution task, or `/evolve` if you already have initial.py + evaluate.py.
```

## Pre-Setup Check: Already Installed?

Before doing anything else, check if the plugin is already installed and working. Read the state file:

```
CLAUDE_EVOLVE_STATE="${CLAUDE_PLUGIN_ROOT:-.}/state/install-state.json"
```

If the state file exists AND reports `"status": "ok"` AND no `--force` flag is set, use `AskUserQuestion` to prompt:

**Question:** "claude-evolve is already installed. What would you like to do?"

**Options:**
1. **Verify install** - Run the verification phase only (quick health check)
2. **Reinstall** - Run the full setup wizard again
3. **Cancel** - Exit without changes

On "Verify install" → jump to Phase 4.
On "Reinstall" → continue with Phase 1 below.
On "Cancel" → exit.

## Phase 1: Python Check

Use Bash to verify Python:

```bash
python3 --version 2>&1 || python --version 2>&1
```

Requirements:
- Python >= 3.10
- If not available, tell the user to install Python 3.10+ from python.org or their package manager and stop

Report the detected Python path and version.

## Phase 2: Environment Setup

**Determine installation mode:**
- If `--global`: use system Python, skip venv creation
- If `--local` or default: create `core/.venv/` in the plugin directory

**Steps:**

1. Check if `core/.venv/` exists. If not (and mode is local), create it:
   ```bash
   python3 -m venv "${CLAUDE_PLUGIN_ROOT:-.}/core/.venv"
   ```

2. Install claude-evolve into that Python:
   ```bash
   PYTHON="${CLAUDE_PLUGIN_ROOT:-.}/core/.venv/bin/python"   # or system python3 if --global
   "$PYTHON" -m pip install -e "${CLAUDE_PLUGIN_ROOT:-.}/core"
   ```

3. Check for install errors. Common issues:
   - Missing build tools (`setuptools`, `wheel`)
   - Missing `mcp` package on PyPI
   - scipy/numpy compilation failures on older systems

If install fails, report the stderr tail and stop.

## Phase 3: Claude CLI Check

Verify the Claude CLI is available and authenticated:

```bash
claude --version 2>&1
```

If not installed, tell the user:
> The `claude` CLI is required. Install it from https://claude.com/code/install

Then run a tiny non-authenticated smoke:

```bash
claude --help 2>&1 | head -5
```

## Phase 4: MCP Verification

Verify the MCP server starts and exposes all expected tools:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}' | timeout 10 "$PYTHON" -m claude_evolve.server 2>&1 | tail -1
```

Parse the `tools/list` response. It must include all five tools:
- `evolve_start`
- `evolve_status`
- `evolve_stop`
- `evolve_visualize`
- `evaluator_create`

If any are missing, report which and stop.

Also run `claude mcp list 2>&1` and confirm `claude-evolve` (or `e`) appears in the output with `✓ Connected`. If it doesn't, the user may need to re-open Claude Code or run `claude mcp add` manually — provide the exact command.

## Phase 5: Smoke Test (Optional)

Run one real Claude CLI call through the bridge to confirm end-to-end:

```bash
"$PYTHON" -c "
import asyncio
from claude_evolve.ensemble.bridge import query_claude_async
result = asyncio.run(query_claude_async(arm='haiku/low', prompt='Respond with just OK', timeout=60))
print('Bridge smoke test:', result.content[:50])
"
```

If this succeeds, the full stack works.

If it fails with an auth error, tell the user to run `claude` once interactively to authenticate.

## Save State

After Phase 4 (and optionally 5) succeeds, write state to `state/install-state.json`:

```json
{
  "status": "ok",
  "installed_at": "<ISO timestamp>",
  "python_path": "<path>",
  "python_version": "<version>",
  "venv_path": "<path or null>",
  "mcp_tools": 5,
  "claude_cli_version": "<version>",
  "plugin_version": "0.1.0"
}
```

## Report Success

Tell the user:

> ✅ claude-evolve installed and verified.
>
> - Python: <version> at <path>
> - Venv: <path or "system">
> - MCP tools: 5 (evolve_start, evolve_status, evolve_stop, evolve_visualize, evaluator_create)
> - Claude CLI: <version>
>
> **Next step:** Run `/evolve-interview` to define your first optimization task interactively, or `/evolve` if you already have `initial.py` and `evaluate.py`.

## Graceful Interrupt Handling

If any phase fails, save partial state to `state/install-state.json` with `"status": "partial"` and the phase that failed. On next invocation, offer to resume from the failed phase.

## Troubleshooting

- **"Module mcp not found"** → `pip install mcp` into the target Python
- **"scipy build error"** → install a pre-built wheel or upgrade pip
- **MCP tools list empty** → the Python import failed; check `python -m claude_evolve.server` directly for traceback
- **Claude CLI "Not logged in"** → remove `--bare` flag if present; run `claude` interactively once
