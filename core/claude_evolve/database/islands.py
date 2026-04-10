"""Island management for claude-evolve.

Two classes:
  IslandManager  – island assignment, migration, and dynamic spawning.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)


class IslandManager:
    """Manages multi-population island model.

    Responsibilities:
    - Assign programs to islands (children inherit; initial programs are
      distributed round-robin).
    - Detect when migration should occur and perform elite migration.
    - Detect stagnation and spawn new islands from archive programs.

    Args:
        config: An IslandConfig (or any object with the relevant attributes).
        conn: Open sqlite3 connection used for DB-backed operations.
    """

    def __init__(self, config, conn: sqlite3.Connection) -> None:
        self.config = config
        self.conn = conn
        self._best_score_ever: Optional[float] = None
        self._best_score_generation: int = 0

    # ------------------------------------------------------------------
    # Island assignment
    # ------------------------------------------------------------------

    def assign_island(self, parent_island_idx: Optional[int], generation: int) -> int:
        """Determine the island index for a new program.

        Rules (in priority order):
        1. Children inherit parent's island.
        2. Initial programs (generation == 0) are distributed round-robin
           across configured islands.
        3. Fallback: random island.

        Args:
            parent_island_idx: Island of the parent, or None for initial programs.
            generation: Generation number of the new program.

        Returns:
            Assigned island index.
        """
        num_islands = max(1, self.config.num_islands)

        # Children inherit parent island
        if parent_island_idx is not None:
            return parent_island_idx

        # Initial program (no parent): round-robin by current count
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM programs WHERE generation = 0")
        row = cursor.fetchone()
        count = row[0] if row else 0
        return count % num_islands

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------

    def get_initialized_islands(self) -> list[int]:
        """Return island indices that have at least one correct program.

        Returns:
            Sorted list of island indices.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT island_idx FROM programs WHERE correct = 1"
        )
        rows = cursor.fetchall()
        return sorted(r[0] for r in rows if r[0] is not None)

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def should_migrate(self, generation: int) -> bool:
        """Return True if migration should occur at this generation.

        Args:
            generation: Current generation number.

        Returns:
            True when generation > 0 and (generation % migration_interval == 0).
        """
        interval = self.config.migration_interval
        return (
            generation > 0
            and interval > 0
            and generation % interval == 0
        )

    def migrate(self, db: "ProgramDatabase") -> int:  # noqa: F821
        """Copy a fraction of elite programs between islands.

        For each source island, selects `migration_rate` fraction of its
        correct programs (excluding the top-1 elite and generation-0 seeds)
        and copies them to a random destination island.

        Args:
            db: Open ProgramDatabase for archive access.

        Returns:
            Number of programs migrated.
        """
        num_islands = self.config.num_islands
        migration_rate = self.config.migration_rate

        if num_islands < 2 or migration_rate <= 0:
            return 0

        cursor = self.conn.cursor()
        total_migrated = 0

        for src in range(num_islands):
            # Count eligible programs (correct, not gen-0)
            cursor.execute(
                "SELECT COUNT(*) FROM programs WHERE island_idx=? AND generation>0 AND correct=1",
                (src,),
            )
            row = cursor.fetchone()
            eligible = row[0] if row else 0
            if eligible == 0:
                continue

            num_migrants = max(1, int(eligible * migration_rate))

            # Protect the single best program (elite) on this island
            cursor.execute(
                """SELECT id FROM programs
                   WHERE island_idx=? AND generation>0 AND correct=1
                   ORDER BY combined_score DESC LIMIT 1""",
                (src,),
            )
            elite_row = cursor.fetchone()
            elite_id = elite_row[0] if elite_row else None

            if elite_id:
                cursor.execute(
                    """SELECT id FROM programs
                       WHERE island_idx=? AND generation>0 AND correct=1 AND id!=?
                       ORDER BY RANDOM() LIMIT ?""",
                    (src, elite_id, num_migrants),
                )
            else:
                cursor.execute(
                    """SELECT id FROM programs
                       WHERE island_idx=? AND generation>0 AND correct=1
                       ORDER BY RANDOM() LIMIT ?""",
                    (src, num_migrants),
                )
            migrant_ids = [r[0] for r in cursor.fetchall()]

            dest_options = [i for i in range(num_islands) if i != src]
            for mid in migrant_ids:
                dest = random.choice(dest_options)
                cursor.execute(
                    "UPDATE programs SET island_idx=? WHERE id=?",
                    (dest, mid),
                )
                total_migrated += 1
                logger.debug("Migrated program %s: island %d -> %d", mid, src, dest)

        self.conn.commit()
        logger.info("Island migration complete: %d programs moved.", total_migrated)
        return total_migrated

    # ------------------------------------------------------------------
    # Dynamic island spawning
    # ------------------------------------------------------------------

    def should_spawn_island(self, db: "ProgramDatabase", generation: int) -> bool:  # noqa: F821
        """Return True if stagnation threshold has been exceeded.

        Stagnation is defined as no improvement in combined_score for
        `stagnation_threshold` consecutive generations.

        Args:
            db: Open ProgramDatabase.
            generation: Current generation.

        Returns:
            True if a new island should be spawned.
        """
        if not self.config.enable_dynamic_islands:
            return False
        if generation == 0:
            return False

        # Update best score tracking
        best = db.get_best_program()
        if best is not None:
            score = best.combined_score or 0.0
            if self._best_score_ever is None or score > self._best_score_ever:
                self._best_score_ever = score
                self._best_score_generation = generation

        stagnant_gens = generation - self._best_score_generation
        if stagnant_gens >= self.config.stagnation_threshold:
            logger.info(
                "Stagnation detected: no improvement for %d generations "
                "(threshold=%d). Suggesting island spawn.",
                stagnant_gens,
                self.config.stagnation_threshold,
            )
            return True
        return False

    def spawn_island(self, db: "ProgramDatabase") -> Optional[int]:  # noqa: F821
        """Create a new island seeded from archive programs.

        Copies `elite_selection_ratio` fraction of the top archive programs
        to a fresh island index.

        Args:
            db: Open ProgramDatabase.

        Returns:
            New island index, or None if spawning failed.
        """
        cursor = self.conn.cursor()

        # Determine the next island index
        cursor.execute("SELECT MAX(island_idx) FROM programs")
        row = cursor.fetchone()
        max_idx = row[0] if row and row[0] is not None else -1
        new_island_idx = max_idx + 1

        # Gather archive programs to seed the new island
        archive_size = self.config.archive_size
        elite_k = max(1, int(archive_size * self.config.elite_selection_ratio))

        cursor.execute(
            """SELECT p.* FROM programs p
               JOIN archive a ON p.id = a.program_id
               ORDER BY p.combined_score DESC
               LIMIT ?""",
            (elite_k,),
        )
        rows = cursor.fetchall()
        if not rows:
            logger.warning("spawn_island: no archive programs found; skipping spawn.")
            return None

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        import json

        spawned = 0
        for row in rows:
            row_dict = dict(zip([d[0] for d in cursor.description], row)) if not hasattr(row, "keys") else dict(row)
            # Determine correct description for row
            new_id = db._next_id()
            meta = {}
            if row_dict.get("metadata"):
                try:
                    meta = json.loads(row_dict["metadata"])
                except Exception:
                    meta = {}
            meta["_spawned_island"] = True
            meta["_spawned_from_id"] = row_dict["id"]
            meta["_spawn_island_idx"] = new_island_idx

            cursor.execute(
                """INSERT INTO programs
                   (code, combined_score, correct, generation, parent_id,
                    island_idx, patch_type, metadata, embedding,
                    text_feedback, status, created_at, children_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_dict["code"],
                    row_dict["combined_score"],
                    row_dict["correct"],
                    row_dict["generation"],
                    None,  # no parent in new island
                    new_island_idx,
                    row_dict.get("patch_type", "full"),
                    json.dumps(meta),
                    row_dict.get("embedding"),
                    row_dict.get("text_feedback"),
                    row_dict.get("status", "ok"),
                    now,
                    0,
                ),
            )
            spawned += 1

        self.conn.commit()
        # Reset stagnation clock
        self._best_score_generation = self._best_score_generation  # no change; user should track
        logger.info(
            "Spawned new island %d with %d seed programs.", new_island_idx, spawned
        )
        return new_island_idx
