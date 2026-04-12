#!/usr/bin/env node
/**
 * HUD Combiner — chains the existing statusLine command with evolve-hud.
 *
 * 1. Reads stdin from Claude Code (JSON with session info)
 * 2. Passes stdin to the original HUD command (OMC or whatever was configured)
 * 3. Appends evolve-hud output on a new line (if there's an active run)
 *
 * The original HUD command is read from ~/.claude-evolve/original-hud.json,
 * which is written by /evolve-install when it sets up the combiner.
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const evolveHudPath = join(__dirname, "evolve-hud.mjs");
const configPath = join(homedir(), ".claude-evolve", "original-hud.json");

async function readAllStdin() {
  const chunks = [];
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return chunks.join("");
}

function expandEnvVars(str) {
  // Expand ${VAR:-default} and ${VAR} and $VAR patterns
  return str.replace(/\$\{(\w+):-([^}]*)\}/g, (_, name, fallback) =>
    process.env[name] || fallback
  ).replace(/\$\{(\w+)\}/g, (_, name) =>
    process.env[name] || ""
  ).replace(/\$HOME/g, homedir());
}

async function runCommand(command, stdinData) {
  return new Promise((resolve) => {
    // Expand env vars in the command string
    const expanded = expandEnvVars(command);
    // Parse command into executable + args
    const parts = expanded.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
    const [cmd, ...args] = parts.map((p) => p.replace(/^"|"$/g, ""));

    try {
      const child = spawn(cmd, args, {
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env },
        timeout: 5000,
      });

      let stdout = "";
      child.stdout.on("data", (data) => { stdout += data; });
      child.stderr.on("data", () => {}); // Swallow stderr

      if (stdinData) {
        child.stdin.write(stdinData);
        child.stdin.end();
      }

      child.on("close", () => resolve(stdout.trimEnd()));
      child.on("error", () => resolve(""));
    } catch {
      resolve("");
    }
  });
}

async function main() {
  const stdinData = await readAllStdin();
  const outputs = [];

  // 1. Run original HUD (OMC or whatever was configured before evolve)
  if (existsSync(configPath)) {
    try {
      const config = JSON.parse(readFileSync(configPath, "utf-8"));
      if (config.command) {
        const originalOutput = await runCommand(config.command, stdinData);
        if (originalOutput) {
          outputs.push(originalOutput);
        }
      }
    } catch {
      // Original HUD failed — continue with just evolve
    }
  }

  // 2. Run evolve HUD (doesn't need stdin — reads state files directly)
  if (existsSync(evolveHudPath)) {
    const evolveOutput = await runCommand(`node "${evolveHudPath}"`, "");
    if (evolveOutput) {
      outputs.push(evolveOutput);
    }
  }

  // 3. Output combined (newline-separated)
  if (outputs.length > 0) {
    process.stdout.write(outputs.join("\n") + "\n");
  }
}

main().catch(() => process.exit(0));
