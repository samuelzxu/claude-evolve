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

Verify the MCP server starts and exposes all expected tools.

**Important:** Do NOT use the `timeout` or `gtimeout` shell commands here. They are Linux-only and GNU-coreutils-on-macOS respectively, and neither is guaranteed to exist on a fresh install. Use Python's built-in subprocess timeout instead, which works everywhere Python works (and Python is guaranteed present by Phase 2).

Run the probe by inlining a short Python script that drives the MCP handshake. Use `Popen` + a deadline reader — do **not** use `subprocess.run(input=...)`, because closing stdin triggers FastMCP shutdown before the `tools/list` response is flushed.

```bash
"$PYTHON" - <<'PYEOF'
import json, subprocess, sys, time

init = {"jsonrpc":"2.0","id":1,"method":"initialize",
        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                  "clientInfo":{"name":"evolve-install","version":"1.0"}}}
notif = {"jsonrpc":"2.0","method":"notifications/initialized"}
list_req = {"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}

payload = "\n".join(json.dumps(m) for m in [init, notif, list_req]) + "\n"

proc = subprocess.Popen(
    [sys.executable, "-m", "claude_evolve.server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

proc.stdin.write(payload)
proc.stdin.flush()

deadline = time.time() + 15
tools_line = None
try:
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if '"id":3' in line and '"tools"' in line:
            tools_line = line
            break
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()

if tools_line is None:
    err = proc.stderr.read()[:500] if proc.stderr else ""
    print(f"MCP_PROBE_FAIL: no tools/list response within deadline. stderr={err}")
    sys.exit(1)

data = json.loads(tools_line)
tools = [t["name"] for t in data["result"]["tools"]]
expected = {"evolve_start", "evolve_status", "evolve_stop", "evolve_visualize", "evaluator_create"}
missing = expected - set(tools)

if missing:
    print(f"MCP_PROBE_FAIL: missing tools {sorted(missing)}. Got: {tools}")
    sys.exit(1)

print(f"MCP_PROBE_OK: {len(tools)} tools exposed: {sorted(tools)}")
PYEOF
```

Parse the output — look for `MCP_PROBE_OK` on success or `MCP_PROBE_FAIL:` on failure. If it fails, report the stderr tail and stop.

Also run `claude mcp list 2>&1` and confirm an entry matching `claude-evolve` (or the short name `e`) appears with `✓ Connected`. If it doesn't, the user may need to restart Claude Code or run `claude mcp add` manually — provide the exact command.

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

## Phase 6: HUD Setup (Status Line)

Set up the claude-evolve HUD that displays evolution progress in the Claude Code status bar. This appends a second line below any existing HUD (e.g. OMC's).

**Steps:**

1. Read the current `statusLine.command` from `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`).

2. If a statusLine already exists (e.g. OMC's HUD), save it to `~/.claude-evolve/original-hud.json` so the combiner can chain it:
   ```json
   {"command": "<the existing statusLine.command value>"}
   ```

3. If no statusLine exists yet, write `~/.claude-evolve/original-hud.json` with `{"command": null}`.

4. Set the statusLine command to the claude-evolve combiner:
   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "node ${CLAUDE_PLUGIN_ROOT}/bridge/hud-combiner.mjs"
     }
   }
   ```

   **IMPORTANT:** Use the `statusline-setup` agent or directly patch `~/.claude/settings.json` to update the `statusLine` field. Do NOT overwrite other settings.json fields.

5. If the current statusLine already points to `hud-combiner.mjs`, skip (already configured).

**What the HUD shows during an active evolution run:**
```
evolve │ ⟳ gen 15 │ best 2.4103 │ 30 calls │ 12 progs
```

Status icons: `⟳` (yellow, running) | `✓` (green, complete) | `✗` (red, crashed/dead)

When no evolution run is active, the evolve line is hidden — only the original HUD shows.

## Save State

After all phases succeed, write state to `state/install-state.json`:

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
> - HUD: <configured / already configured / skipped>
>
> **Next step:** Run `/evolve-interview` to define your first optimization task interactively, or `/evolve` if you already have `initial.py` and `evaluate.py`.

## Graceful Interrupt Handling

If any phase fails, save partial state to `state/install-state.json` with `"status": "partial"` and the phase that failed. On next invocation, offer to resume from the failed phase.

## Troubleshooting

- **"Module mcp not found"** → `pip install mcp` into the target Python
- **"scipy build error"** → install a pre-built wheel or upgrade pip
- **MCP tools list empty** → the Python import failed; check `python -m claude_evolve.server` directly for traceback
- **Claude CLI "Not logged in"** → remove `--bare` flag if present; run `claude` interactively once
