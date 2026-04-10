"""Archive management for claude-evolve.

The archive holds the top-K programs by combined_score, replacing the worst
entry when a better program arrives.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from .models import Program

logger = logging.getLogger(__name__)


class ArchiveManager:
    """Manages the elite archive of best programs."""

    # ------------------------------------------------------------------
    # Eligibility / update
    # ------------------------------------------------------------------

    def should_add(self, program: Program, archive: list[Program]) -> bool:
        """Return True if *program* deserves a spot in *archive*.

        A program qualifies when:
        - It is correct, AND
        - Either the archive is not yet full, OR the program beats the
          current worst entry by combined_score.

        Args:
            program: Candidate program.
            archive: Current archive (may be empty).

        Returns:
            True if the program should be added.
        """
        if not program.correct:
            return False
        if not archive:
            return True
        worst = min(archive, key=lambda p: p.combined_score or 0.0)
        return (program.combined_score or 0.0) > (worst.combined_score or 0.0)

    def add_to_archive(
        self,
        program: Program,
        archive: list[Program],
        max_size: int,
    ) -> list[Program]:
        """Add *program* to *archive*, trimming to *max_size*.

        If the archive is already at capacity the worst entry is evicted
        before inserting the new program.

        Args:
            program: Program to add.
            archive: Current archive list (not mutated; a new list is returned).
            max_size: Maximum archive size.

        Returns:
            Updated archive list.
        """
        archive = list(archive)
        if len(archive) >= max_size:
            worst_idx = min(
                range(len(archive)),
                key=lambda i: archive[i].combined_score or 0.0,
            )
            evicted = archive.pop(worst_idx)
            logger.debug(
                "Archive full (%d/%d). Evicted program %s (score=%.4f).",
                max_size,
                max_size,
                evicted.id,
                evicted.combined_score or 0.0,
            )
        archive.append(program)
        logger.debug(
            "Added program %s to archive (score=%.4f). Size=%d/%d.",
            program.id,
            program.combined_score or 0.0,
            len(archive),
            max_size,
        )
        return archive

    # ------------------------------------------------------------------
    # DB-backed retrieval
    # ------------------------------------------------------------------

    def get_archive(
        self,
        db: "ProgramDatabase",  # noqa: F821 – avoid circular import
        island_idx: Optional[int] = None,
        top_k: int = 40,
    ) -> list[Program]:
        """Retrieve top-K programs from the archive table in *db*.

        Args:
            db: Open ProgramDatabase instance.
            island_idx: If given, restrict to programs on that island.
            top_k: Maximum number of programs to return.

        Returns:
            List of Program objects sorted best-first.
        """
        cursor: sqlite3.Cursor = db.conn.cursor()
        if island_idx is not None:
            cursor.execute(
                """
                SELECT p.* FROM programs p
                JOIN archive a ON p.id = a.program_id
                WHERE p.island_idx = ?
                ORDER BY p.combined_score DESC
                LIMIT ?
                """,
                (island_idx, top_k),
            )
        else:
            cursor.execute(
                """
                SELECT p.* FROM programs p
                JOIN archive a ON p.id = a.program_id
                ORDER BY p.combined_score DESC
                LIMIT ?
                """,
                (top_k,),
            )
        rows = cursor.fetchall()
        return [db._program_from_row(row) for row in rows]
