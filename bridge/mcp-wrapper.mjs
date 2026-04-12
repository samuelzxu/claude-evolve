#!/usr/bin/env node
/**
 * Thin Node.js wrapper that spawns the Python MCP server.
 *
 * Resolution order for the Python interpreter:
 *   1. $CLAUDE_EVOLVE_PYTHON (explicit override)
 *   2. <plugin>/core/.venv/bin/python (POSIX venv) or .venv/Scripts/python.exe (Windows)
 *   3. python3 on PATH
 *   4. python on PATH
 *
 * The wrapper probes each candidate synchronously with `-c "import claude_evolve"`
 * before launching the full MCP server, so a Python that doesn't have the package
 * installed is silently skipped instead of being started and then crashing with
 * ModuleNotFoundError after Claude Code has already connected to stdio.
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(__dirname, "..");
const coreDir = join(pluginRoot, "core");

// Candidate Python interpreters, in priority order
const venvPythonPosix = join(coreDir, ".venv", "bin", "python");
const venvPythonWin = join(coreDir, ".venv", "Scripts", "python.exe");

const pythonCandidates = [
  process.env.CLAUDE_EVOLVE_PYTHON,
  existsSync(venvPythonPosix) ? venvPythonPosix : null,
  existsSync(venvPythonWin) ? venvPythonWin : null,
  "python3",
  "python",
].filter(Boolean);

function canImportClaudeEvolveServer(python) {
  // Quick synchronous probe: exit 0 if the full server module can be imported
  // (which requires claude_evolve, mcp, numpy, scipy, etc. to all be present).
  // An empty __init__.py would let a bare `import claude_evolve` succeed even
  // when mcp is missing, so we probe the actual server module.
  try {
    const result = spawnSync(
      python,
      ["-c", "import importlib; importlib.import_module('claude_evolve.server')"],
      {
        cwd: coreDir,
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
        stdio: ["ignore", "ignore", "pipe"],
        timeout: 10000,
      }
    );
    return result.status === 0;
  } catch (_err) {
    return false;
  }
}

function findWorkingPython() {
  for (const candidate of pythonCandidates) {
    if (canImportClaudeEvolveServer(candidate)) {
      return candidate;
    }
  }
  return null;
}

const python = findWorkingPython();

if (!python) {
  process.stderr.write(
    "claude-evolve: No Python interpreter found with the claude_evolve package installed.\n" +
    "\n" +
    "To fix this:\n" +
    "  1. Run `/evolve-install` to create a venv and install dependencies, OR\n" +
    "  2. Manually: cd " + coreDir + " && python3 -m venv .venv && .venv/bin/pip install -e .\n" +
    "  3. Or set the CLAUDE_EVOLVE_PYTHON env var to a Python that already has\n" +
    "     `pip install -e core/` run.\n" +
    "\n" +
    "Candidates tried: " + pythonCandidates.join(", ") + "\n"
  );
  process.exit(1);
}

const child = spawn(python, ["-m", "claude_evolve.server"], {
  cwd: coreDir,
  stdio: ["pipe", "pipe", "pipe"],
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
});

// Pipe stdio for MCP protocol
process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);
child.stderr.pipe(process.stderr);

child.on("error", (err) => {
  process.stderr.write(`claude-evolve: failed to start Python MCP server with '${python}': ${err.message}\n`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 128 : 0));
});

// Forward signals to the child
process.on("SIGTERM", () => child.kill("SIGTERM"));
process.on("SIGINT", () => child.kill("SIGINT"));
