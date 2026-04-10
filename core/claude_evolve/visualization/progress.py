"""Score-over-time visualization for claude-evolve."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


_SPARKS = "▁▂▃▄▅▆▇█"


def _spark_char(value: float, lo: float, hi: float) -> str:
    if hi <= lo:
        return _SPARKS[0]
    frac = (value - lo) / (hi - lo)
    idx = min(int(frac * len(_SPARKS)), len(_SPARKS) - 1)
    return _SPARKS[idx]


def generate_progress_chart(db_path: str) -> str:
    """Read programs from SQLite and return a text chart of scores per generation.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Multi-line string with a text-based progress chart.
    """
    path = Path(db_path)
    if not path.exists():
        return f"Database not found: {db_path}"

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT generation,
                   MAX(combined_score) AS best_score,
                   AVG(combined_score) AS mean_score,
                   COUNT(*)            AS count
            FROM programs
            WHERE correct = 1
            GROUP BY generation
            ORDER BY generation ASC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "No correct programs found in database."

    rows = [dict(r) for r in rows]
    best_scores = [r["best_score"] for r in rows]
    mean_scores = [r["mean_score"] for r in rows]

    lo = min(best_scores + mean_scores)
    hi = max(best_scores + mean_scores)

    initial_best = best_scores[0] if best_scores else 0.0
    final_best = best_scores[-1] if best_scores else 0.0
    improvement = final_best - initial_best

    lines = [
        "=== claude-evolve Progress Chart ===",
        "",
        f"  Generations tracked : {len(rows)}",
        f"  Initial best score  : {initial_best:.6f}",
        f"  Final best score    : {final_best:.6f}",
        f"  Improvement         : {improvement:+.6f}",
        "",
        "  Best score sparkline:",
        "  " + "".join(_spark_char(s, lo, hi) for s in best_scores),
        "",
        "  Mean score sparkline:",
        "  " + "".join(_spark_char(s, lo, hi) for s in mean_scores),
        "",
        f"  {'Gen':>5}  {'Best':>12}  {'Mean':>12}  {'Count':>6}",
        "  " + "-" * 42,
    ]

    for r in rows:
        gen = r["generation"]
        best = r["best_score"]
        mean = r["mean_score"]
        cnt = r["count"]
        spark = _spark_char(best, lo, hi)
        lines.append(f"  {gen:>5}  {best:>12.6f}  {mean:>12.6f}  {cnt:>6}  {spark}")

    lines.append("")
    return "\n".join(lines)


def generate_progress_chart_html(db_path: str) -> str:
    """Return an SVG/HTML progress chart.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        HTML string containing an inline SVG chart.
    """
    path = Path(db_path)
    if not path.exists():
        return f"<p>Database not found: {db_path}</p>"

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT generation,
                   MAX(combined_score) AS best_score,
                   AVG(combined_score) AS mean_score
            FROM programs
            WHERE correct = 1
            GROUP BY generation
            ORDER BY generation ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not rows:
        return "<p>No correct programs found in database.</p>"

    width, height, pad = 600, 300, 40
    gens = [r["generation"] for r in rows]
    best = [r["best_score"] for r in rows]
    means = [r["mean_score"] for r in rows]

    all_vals = best + means
    y_min, y_max = min(all_vals), max(all_vals)
    x_min, x_max = min(gens), max(gens)
    x_range = max(x_max - x_min, 1)
    y_range = max(y_max - y_min, 1e-9)

    def to_svg_x(g: float) -> float:
        return pad + (g - x_min) / x_range * (width - 2 * pad)

    def to_svg_y(v: float) -> float:
        return height - pad - (v - y_min) / y_range * (height - 2 * pad)

    def polyline(vals: list, color: str) -> str:
        pts = " ".join(f"{to_svg_x(g):.1f},{to_svg_y(v):.1f}" for g, v in zip(gens, vals))
        return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" style="background:#1e1e2e">
  <text x="{width//2}" y="20" text-anchor="middle" fill="#cdd6f4" font-size="14">Score over Generations</text>
  {polyline(best, "#a6e3a1")}
  {polyline(means, "#89b4fa")}
  <text x="{pad}" y="{height - 5}" fill="#89b4fa" font-size="11">gen {x_min}</text>
  <text x="{width - pad}" y="{height - 5}" fill="#89b4fa" font-size="11" text-anchor="end">gen {x_max}</text>
  <text x="5" y="{to_svg_y(y_max):.1f}" fill="#a6e3a1" font-size="10">{y_max:.4f}</text>
  <text x="5" y="{to_svg_y(y_min):.1f}" fill="#a6e3a1" font-size="10">{y_min:.4f}</text>
  <text x="{width - 10}" y="40" fill="#a6e3a1" font-size="11" text-anchor="end">— best</text>
  <text x="{width - 10}" y="55" fill="#89b4fa" font-size="11" text-anchor="end">— mean</text>
</svg>"""

    return f"<!DOCTYPE html><html><body>{svg}</body></html>"
