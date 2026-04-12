#!/usr/bin/env node
/**
 * claude-evolve HUD — outputs a single status line for the active evolution run.
 *
 * Reads state from:
 *   1. ~/.claude-evolve/active_run.json (persisted by evolve_start)
 *   2. <state_dir>/run_state.json (written by the runner every generation)
 *
 * Output: one line of formatted text, or empty string if no active run.
 * Designed to be appended after the OMC HUD line via hud-combiner.mjs.
 */

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

// ANSI helpers
const ESC = "\x1b[";
const bold = (s) => `${ESC}1m${s}${ESC}22m`;
const dim = (s) => `${ESC}2m${s}${ESC}22m`;
const green = (s) => `${ESC}32m${s}${ESC}39m`;
const yellow = (s) => `${ESC}33m${s}${ESC}39m`;
const cyan = (s) => `${ESC}36m${s}${ESC}39m`;
const magenta = (s) => `${ESC}35m${s}${ESC}39m`;
const red = (s) => `${ESC}31m${s}${ESC}39m`;

function readJSON(path) {
  try {
    if (!existsSync(path)) return null;
    let text = readFileSync(path, "utf-8");
    // Python's json.dump writes -Infinity/Infinity/NaN which are invalid JSON.
    // Sanitize them so Node's JSON.parse doesn't crash.
    text = text.replace(/-Infinity/g, "null")
               .replace(/Infinity/g, "null")
               .replace(/\bNaN\b/g, "null");
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function isProcessAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function sparkline(scores, width = 12) {
  if (!scores || scores.length === 0) return "";
  const chars = "▁▂▃▄▅▆▇█";
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const range = max - min || 1;
  // Take last `width` scores
  const recent = scores.slice(-width);
  return recent
    .map((s) => chars[Math.min(chars.length - 1, Math.floor(((s - min) / range) * (chars.length - 1)))])
    .join("");
}

function formatArm(arm) {
  if (!arm) return "";
  // Shorten model names: sonnet/medium -> son/med, haiku/low -> hai/low
  const [model, effort] = arm.split("/");
  const shortModel = { opus: "ops", sonnet: "son", haiku: "hai" }[model] || model.slice(0, 3);
  const shortEffort = effort?.slice(0, 3) || "";
  return `${shortModel}/${shortEffort}`;
}

function main() {
  // 1. Find active run
  const activeRunPath = join(homedir(), ".claude-evolve", "active_run.json");
  const activeRun = readJSON(activeRunPath);

  if (!activeRun?.state_dir) {
    // No active run persisted — check cwd as fallback
    const cwdState = readJSON(join(process.cwd(), "state", "run_state.json"));
    if (!cwdState) return; // Nothing to show
    renderState(cwdState, null);
    return;
  }

  const stateDir = activeRun.state_dir;
  const state = readJSON(join(stateDir, "run_state.json"));
  if (!state) return; // No state file yet

  renderState(state, activeRun.pid);
}

function renderState(state, pid) {
  const gen = state.generation ?? 0;
  const bestScore = state.best_score ?? 0;
  const status = state.status ?? "unknown";

  // Check if process is still alive
  const alive = pid ? isProcessAlive(pid) : false;
  const isRunning = alive && status === "running";
  const isComplete = status === "complete" || status === "completed";

  // Status icon
  let statusIcon;
  if (isComplete) {
    statusIcon = green("✓");
  } else if (isRunning) {
    statusIcon = yellow("⟳");
  } else if (!alive && pid) {
    statusIcon = red("✗");
  } else {
    statusIcon = dim("○");
  }

  // Build parts
  const parts = [];

  // Label
  parts.push(bold(cyan("evolve")));

  // Status + generation
  parts.push(`${statusIcon} gen ${bold(String(gen))}`);

  // Best score
  if (bestScore > 0) {
    parts.push(`best ${green(bestScore.toFixed(4))}`);
  }

  // Bandit arm stats (most-used arm)
  const bandit = state.bandit_state;
  if (bandit?.n_completed) {
    const armNames = ["sonnet/max", "sonnet/high", "sonnet/med", "sonnet/low",
      "haiku/high", "haiku/med", "haiku/low"];
    const counts = bandit.n_completed;
    const maxIdx = counts.indexOf(Math.max(...counts));
    if (counts[maxIdx] > 0) {
      // Try to get arm names from config, otherwise use defaults
      const totalCalls = counts.reduce((a, b) => a + b, 0);
      parts.push(dim(`${totalCalls} calls`));
    }
  }

  // Meta state
  const meta = state.meta_state;
  if (meta?.total_programs_processed > 0) {
    parts.push(dim(`${meta.total_programs_processed} progs`));
  }

  // PID for manual kill
  if (isRunning && pid) {
    parts.push(dim(`pid:${pid}`));
  }

  // Compose the line
  const line = parts.join(dim(" │ "));
  process.stdout.write(line + "\n");
}

main();
