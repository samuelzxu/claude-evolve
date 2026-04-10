#!/usr/bin/env node
/**
 * Thin Node.js wrapper that spawns the Python MCP server.
 * This exists solely to make claude-evolve installable via the
 * Claude Code plugin system (which expects a Node.js entry point).
 *
 * The actual MCP server is Python: claude_evolve.server
 */

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const coreDir = join(__dirname, "..", "core");

// Try CLAUDE_EVOLVE_PYTHON first, then python3, then python
const pythonCandidates = [
  process.env.CLAUDE_EVOLVE_PYTHON,
  "python3",
  "python",
].filter(Boolean);

function trySpawn(candidates) {
  const python = candidates.shift();
  if (!python) {
    process.stderr.write(
      "Error: No Python interpreter found. Install Python 3.10+ and ensure it's on PATH.\n"
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
    if (err.code === "ENOENT" && candidates.length > 0) {
      trySpawn(candidates);
    } else {
      process.stderr.write(`Failed to start Python MCP server: ${err.message}\n`);
      process.exit(1);
    }
  });

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });

  // Forward signals
  process.on("SIGTERM", () => child.kill("SIGTERM"));
  process.on("SIGINT", () => child.kill("SIGINT"));
}

trySpawn([...pythonCandidates]);
