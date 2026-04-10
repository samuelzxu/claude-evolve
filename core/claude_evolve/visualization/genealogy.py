"""Parent-child genealogy tree visualization for claude-evolve."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _fetch_top_programs(
    conn: sqlite3.Connection, top_k: int
) -> List[Dict]:
    """Fetch top-K correct programs ordered by combined_score descending."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, combined_score, generation, parent_id, patch_type
        FROM programs
        WHERE correct = 1
        ORDER BY combined_score DESC
        LIMIT ?
        """,
        (top_k,),
    )
    return [dict(r) for r in cur.fetchall()]


def _fetch_program(conn: sqlite3.Connection, program_id: int) -> Optional[Dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, combined_score, generation, parent_id, patch_type FROM programs WHERE id = ?",
        (program_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _build_lineage(conn: sqlite3.Connection, program_id: int) -> List[Dict]:
    """Walk parent chain from program_id to root, returning ancestor list."""
    lineage: List[Dict] = []
    current_id: Optional[int] = program_id
    seen = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        prog = _fetch_program(conn, current_id)
        if prog is None:
            break
        lineage.append(prog)
        current_id = prog["parent_id"]
    lineage.reverse()  # root first
    return lineage


def generate_genealogy(db_path: str, top_k: int = 10) -> str:
    """Show the lineage of top-K programs as a text tree.

    Args:
        db_path: Path to the SQLite database file.
        top_k: Number of top programs to display lineages for.

    Returns:
        Multi-line string with genealogy tree.
    """
    path = Path(db_path)
    if not path.exists():
        return f"Database not found: {db_path}"

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        top_programs = _fetch_top_programs(conn, top_k)
        if not top_programs:
            return "No correct programs found in database."

        lines = [
            "=== claude-evolve Genealogy ===",
            f"  Top-{len(top_programs)} programs by score",
            "",
        ]

        for rank, prog in enumerate(top_programs, 1):
            lineage = _build_lineage(conn, prog["id"])
            lines.append(
                f"  #{rank}  id={prog['id']}  score={prog['combined_score']:.6f}"
                f"  gen={prog['generation']}  patch={prog['patch_type']}"
            )
            if lineage:
                for depth, ancestor in enumerate(lineage):
                    is_self = ancestor["id"] == prog["id"]
                    prefix = "  " + ("    " * depth)
                    marker = "└─" if depth > 0 else "  "
                    parent_str = (
                        f"parent={ancestor['parent_id']}"
                        if ancestor["parent_id"] is not None
                        else "root"
                    )
                    label = "[self]" if is_self else f"[gen {ancestor['generation']}]"
                    lines.append(
                        f"{prefix}{marker} id={ancestor['id']}  "
                        f"score={ancestor['combined_score']:.6f}  "
                        f"{parent_str}  patch={ancestor['patch_type']}  {label}"
                    )
            lines.append("")

    finally:
        conn.close()

    return "\n".join(lines)
