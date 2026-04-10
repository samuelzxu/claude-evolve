"""SQLite-backed program database for claude-evolve.

Sync sqlite3 with WAL mode.  All public methods are thread-safe via the
connection's built-in serialisation (single writer pattern).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..config import IslandConfig
from .models import Program, _clean_dict
from .archive import ArchiveManager
from .islands import IslandManager
from .parents import ParentSelector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_PROGRAMS = """
CREATE TABLE IF NOT EXISTS programs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL,
    combined_score  REAL    NOT NULL DEFAULT 0.0,
    correct         INTEGER NOT NULL DEFAULT 0,
    generation      INTEGER NOT NULL DEFAULT 0,
    parent_id       INTEGER REFERENCES programs(id),
    island_idx      INTEGER NOT NULL DEFAULT 0,
    patch_type      TEXT    NOT NULL DEFAULT 'full',
    metadata        TEXT,
    embedding       TEXT,
    text_feedback   TEXT,
    status          TEXT    NOT NULL DEFAULT 'ok',
    created_at      TEXT    NOT NULL,
    children_count  INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_ARCHIVE = """
CREATE TABLE IF NOT EXISTS archive (
    program_id INTEGER PRIMARY KEY REFERENCES programs(id) ON DELETE CASCADE
)
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_programs_generation  ON programs(generation)",
    "CREATE INDEX IF NOT EXISTS idx_programs_island_idx  ON programs(island_idx)",
    "CREATE INDEX IF NOT EXISTS idx_programs_correct     ON programs(correct)",
    "CREATE INDEX IF NOT EXISTS idx_programs_parent_id   ON programs(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_programs_score       ON programs(combined_score)",
]

_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 60000",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA cache_size = -32000",
    "PRAGMA foreign_keys = ON",
]


# ---------------------------------------------------------------------------
# ProgramDatabase
# ---------------------------------------------------------------------------


class ProgramDatabase:
    """SQLite-backed store for evolved programs.

    Args:
        db_path: Path to the SQLite file.  Pass ``None`` or ``':memory:'``
                 for an in-memory database (useful for testing).
        config: IslandConfig (or any compatible object) driving island and
                archive behaviour.
    """

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        config: Optional[IslandConfig] = None,
    ) -> None:
        self.config = config or IslandConfig()

        if db_path is None or str(db_path) == ":memory:":
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            logger.info("ProgramDatabase: using in-memory SQLite.")
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), timeout=60.0, check_same_thread=False)
            logger.info("ProgramDatabase: connected to %s.", path)

        self.conn.row_factory = sqlite3.Row
        self._create_tables()

        self.archive_manager = ArchiveManager()
        self.island_manager = IslandManager(self.config, self.conn)
        self.parent_selector = ParentSelector(
            strategy=self.config.parent_selection_strategy,
        )

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        cur = self.conn.cursor()
        for pragma in _PRAGMAS:
            cur.execute(pragma)
        cur.execute(_CREATE_PROGRAMS)
        cur.execute(_CREATE_ARCHIVE)
        for idx in _INDICES:
            cur.execute(idx)
        self.conn.commit()

    def _next_id(self) -> int:
        """Return the next autoincrement id without inserting a row.

        Used by IslandManager.spawn_island to pre-allocate IDs; in practice
        the INSERT uses AUTOINCREMENT so this is only needed when an explicit
        id is required before insertion.
        """
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(id) FROM programs")
        row = cur.fetchone()
        return (row[0] or 0) + 1

    # ------------------------------------------------------------------
    # Row <-> Program helpers
    # ------------------------------------------------------------------

    def _program_from_row(self, row: sqlite3.Row) -> Program:
        d = dict(row)
        # Deserialise JSON columns
        for col in ("metadata", "embedding"):
            raw = d.get(col)
            if raw:
                try:
                    d[col] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    d[col] = {} if col == "metadata" else None
            else:
                d[col] = {} if col == "metadata" else None
        # Booleans stored as integers in SQLite
        d["correct"] = bool(d.get("correct", 0))
        return Program.from_dict(d)

    def _serialize_program(self, program: Program) -> tuple:
        """Return the values tuple for INSERT."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        created_at = program.created_at or now
        metadata_json = json.dumps(_clean_dict(program.metadata or {}))
        embedding_json = (
            json.dumps(program.embedding) if program.embedding is not None else None
        )
        return (
            program.code,
            _clean_score(program.combined_score),
            int(program.correct),
            program.generation,
            program.parent_id,
            program.island_idx,
            program.patch_type,
            metadata_json,
            embedding_json,
            program.text_feedback,
            program.status,
            created_at,
            program.children_count,
        )

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def add_program(self, program: Program) -> int:
        """Insert a program into the database.

        Island assignment, archive update, and best-program tracking are
        performed automatically.

        Args:
            program: Program to insert.  ``program.id`` is ignored; the
                     real id is assigned by SQLite and returned.

        Returns:
            The new program's integer id.
        """
        # Assign island before insert
        program.island_idx = self.island_manager.assign_island(
            program.parent_id if program.parent_id is not None
            else None,  # parent_island resolved below
            program.generation,
        )

        # If there is a parent, inherit parent's island
        if program.parent_id is not None:
            parent = self.get_program(program.parent_id)
            if parent is not None:
                program.island_idx = parent.island_idx

        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO programs
               (code, combined_score, correct, generation, parent_id,
                island_idx, patch_type, metadata, embedding,
                text_feedback, status, created_at, children_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._serialize_program(program),
        )
        new_id: int = cur.lastrowid  # type: ignore[assignment]

        # Increment parent's children_count
        if program.parent_id is not None:
            cur.execute(
                "UPDATE programs SET children_count = children_count + 1 WHERE id = ?",
                (program.parent_id,),
            )

        self.conn.commit()
        program.id = new_id

        # Update archive
        self._update_archive(program)

        logger.debug(
            "Added program id=%d gen=%d island=%d score=%.4f correct=%s.",
            new_id,
            program.generation,
            program.island_idx,
            program.combined_score or 0.0,
            program.correct,
        )
        return new_id

    def get_program(self, program_id: int) -> Optional[Program]:
        """Fetch a program by id.

        Args:
            program_id: Integer primary key.

        Returns:
            Program object or None if not found.
        """
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM programs WHERE id = ?", (program_id,))
        row = cur.fetchone()
        return self._program_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_best_program(self, island_idx: Optional[int] = None) -> Optional[Program]:
        """Return the correct program with the highest combined_score.

        Args:
            island_idx: If given, restrict to programs on that island.

        Returns:
            Best Program or None.
        """
        cur = self.conn.cursor()
        if island_idx is not None:
            cur.execute(
                """SELECT * FROM programs
                   WHERE correct = 1 AND island_idx = ?
                   ORDER BY combined_score DESC LIMIT 1""",
                (island_idx,),
            )
        else:
            cur.execute(
                """SELECT * FROM programs
                   WHERE correct = 1
                   ORDER BY combined_score DESC LIMIT 1"""
            )
        row = cur.fetchone()
        return self._program_from_row(row) if row else None

    def get_archive(
        self, top_k: int = 40, island_idx: Optional[int] = None
    ) -> list[Program]:
        """Retrieve top-K programs from the archive.

        Args:
            top_k: Maximum number of programs to return.
            island_idx: If given, restrict to programs on that island.

        Returns:
            List sorted best-first.
        """
        return self.archive_manager.get_archive(self, island_idx=island_idx, top_k=top_k)

    def get_programs_by_generation(self, gen: int) -> list[Program]:
        """Return all programs in a given generation.

        Args:
            gen: Generation number.

        Returns:
            List of Program objects (unordered).
        """
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM programs WHERE generation = ?", (gen,))
        return [self._program_from_row(r) for r in cur.fetchall()]

    def get_island_programs(self, island_idx: int) -> list[Program]:
        """Return all programs on a specific island.

        Args:
            island_idx: Island index.

        Returns:
            List of Program objects.
        """
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM programs WHERE island_idx = ? ORDER BY combined_score DESC",
            (island_idx,),
        )
        return [self._program_from_row(r) for r in cur.fetchall()]

    def get_generation_count(self) -> int:
        """Return the current maximum generation number.

        Returns:
            Maximum generation or 0 if no programs exist.
        """
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(generation) FROM programs")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0

    def get_program_count(self, island_idx: Optional[int] = None) -> int:
        """Return total number of programs, optionally filtered by island.

        Args:
            island_idx: If given, count only programs on that island.

        Returns:
            Integer count.
        """
        cur = self.conn.cursor()
        if island_idx is not None:
            cur.execute(
                "SELECT COUNT(*) FROM programs WHERE island_idx = ?", (island_idx,)
            )
        else:
            cur.execute("SELECT COUNT(*) FROM programs")
        row = cur.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Parent selection
    # ------------------------------------------------------------------

    def select_parent(self, island_idx: Optional[int] = None) -> Optional[Program]:
        """Sample a parent program using the configured selection strategy.

        Delegates to ParentSelector after fetching the archive pool for the
        requested island.

        Args:
            island_idx: If given, restrict candidates to that island.

        Returns:
            A Program object or None if no suitable candidates exist.
        """
        archive = self.get_archive(
            top_k=self.config.archive_size, island_idx=island_idx
        )
        if not archive:
            # Fallback: any correct program on the island
            cur = self.conn.cursor()
            if island_idx is not None:
                cur.execute(
                    "SELECT * FROM programs WHERE correct=1 AND island_idx=? "
                    "ORDER BY combined_score DESC",
                    (island_idx,),
                )
            else:
                cur.execute(
                    "SELECT * FROM programs WHERE correct=1 "
                    "ORDER BY combined_score DESC"
                )
            rows = cur.fetchall()
            if not rows:
                logger.warning(
                    "select_parent: no correct programs found (island=%s).", island_idx
                )
                return None
            archive = [self._program_from_row(r) for r in rows]

        return self.parent_selector.select_parent(archive)

    # ------------------------------------------------------------------
    # Inspiration context
    # ------------------------------------------------------------------

    def get_inspirations(
        self,
        island_idx: int,
        num_archive: int = 1,
        num_top_k: int = 1,
    ) -> tuple[list[Program], list[Program]]:
        """Build inspiration context for a mutation prompt.

        Args:
            island_idx: Island to pull context from.
            num_archive: Number of archive programs to include.
            num_top_k: Number of top-K correct programs to include.

        Returns:
            Tuple of (archive_programs, top_k_programs).
        """
        archive_progs = self.get_archive(top_k=num_archive, island_idx=island_idx)

        cur = self.conn.cursor()
        cur.execute(
            """SELECT * FROM programs
               WHERE correct=1 AND island_idx=?
               ORDER BY combined_score DESC LIMIT ?""",
            (island_idx, num_top_k),
        )
        top_k_progs = [self._program_from_row(r) for r in cur.fetchall()]

        return archive_progs, top_k_progs

    # ------------------------------------------------------------------
    # Novelty / embedding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_similarity(
        embedding1: list[float], embedding2: list[float]
    ) -> float:
        """Cosine similarity between two embedding vectors.

        Args:
            embedding1: First embedding.
            embedding2: Second embedding.

        Returns:
            Cosine similarity in [-1, 1], or 0.0 on degenerate inputs.
        """
        if not embedding1 or not embedding2:
            return 0.0
        if len(embedding1) != len(embedding2):
            return 0.0
        dot = sum(a * b for a, b in zip(embedding1, embedding2))
        norm1 = math.sqrt(sum(a * a for a in embedding1))
        norm2 = math.sqrt(sum(b * b for b in embedding2))
        if norm1 < 1e-8 or norm2 < 1e-8:
            return 0.0
        return dot / (norm1 * norm2)

    def get_most_similar_program(
        self,
        embedding: list[float],
        island_idx: Optional[int] = None,
    ) -> Optional[Program]:
        """Find the program with the highest cosine similarity to *embedding*.

        Scans all programs that have a stored embedding.  This is a linear
        scan – suitable for small-to-medium databases (< 10 K programs).

        Args:
            embedding: Query embedding vector.
            island_idx: If given, restrict search to that island.

        Returns:
            Most similar Program or None.
        """
        if not embedding:
            return None

        cur = self.conn.cursor()
        if island_idx is not None:
            cur.execute(
                "SELECT * FROM programs WHERE embedding IS NOT NULL AND island_idx=?",
                (island_idx,),
            )
        else:
            cur.execute("SELECT * FROM programs WHERE embedding IS NOT NULL")

        rows = cur.fetchall()
        if not rows:
            return None

        best_sim = -float("inf")
        best_prog: Optional[Program] = None

        for row in rows:
            prog = self._program_from_row(row)
            if not prog.embedding:
                continue
            sim = self.compute_similarity(embedding, prog.embedding)
            if sim > best_sim:
                best_sim = sim
                best_prog = prog

        return best_prog

    # ------------------------------------------------------------------
    # Archive maintenance (internal)
    # ------------------------------------------------------------------

    def _update_archive(self, program: Program) -> None:
        """Add *program* to archive table if it qualifies."""
        if not program.correct:
            return

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM archive")
        count = cur.fetchone()[0]

        if count < self.config.archive_size:
            cur.execute(
                "INSERT OR IGNORE INTO archive (program_id) VALUES (?)", (program.id,)
            )
            self.conn.commit()
            return

        # Find worst in archive
        cur.execute(
            """SELECT p.id, p.combined_score FROM programs p
               JOIN archive a ON p.id = a.program_id
               ORDER BY p.combined_score ASC LIMIT 1"""
        )
        worst_row = cur.fetchone()
        if worst_row is None:
            return
        worst_id, worst_score = worst_row[0], worst_row[1] or 0.0

        if (program.combined_score or 0.0) > worst_score:
            cur.execute("DELETE FROM archive WHERE program_id = ?", (worst_id,))
            cur.execute(
                "INSERT OR IGNORE INTO archive (program_id) VALUES (?)", (program.id,)
            )
            self.conn.commit()
            logger.debug(
                "Archive: replaced program %d (score=%.4f) with %d (score=%.4f).",
                worst_id,
                worst_score,
                program.id,
                program.combined_score or 0.0,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.debug("ProgramDatabase connection closed.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_score(value) -> float:
    """Replace NaN/Inf with 0.0 for safe DB storage."""
    if value is None:
        return 0.0
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0
