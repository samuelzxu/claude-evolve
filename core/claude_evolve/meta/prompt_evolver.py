"""System prompt co-evolution with UCB-based selection.

Ported from ShinkaEvolve/shinka/core/prompt_evolver.py with adaptations
for claude-evolve's config and database interfaces.
"""

import json
import logging
import random
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

from ..config import EvolveConfig

logger = logging.getLogger(__name__)


@dataclass
class PromptVariant:
    """A system prompt variant tracked in the prompt archive."""

    id: str
    prompt_text: str
    generation: int = 0
    parent_id: Optional[str] = None
    patch_type: str = "full"
    fitness: float = 0.0
    program_count: int = 0
    scores: List[float] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class PromptEvolver:
    """Co-evolves the task system prompt alongside the programs.

    Uses a SQLite-backed archive for persistence and UCB1 for selection.
    """

    def __init__(self, config: EvolveConfig, db_path: str):
        self.config = config
        self.db_path = db_path
        self.patch_types = list(config.prompt_evo.patch_types)
        self.patch_type_probs = list(config.prompt_evo.patch_type_probs)
        self.evolution_interval = config.prompt_evo.evolution_interval
        self.archive_size = config.prompt_evo.archive_size
        self.ucb_c = config.prompt_evo.ucb_exploration_constant
        self.epsilon = config.prompt_evo.epsilon
        self.language = config.language

        self._init_db()

        # Seed with the initial task prompt if archive is empty
        if not self._get_all_variants():
            initial = PromptVariant(
                id=str(uuid.uuid4()),
                prompt_text=config.task_description,
                generation=0,
                patch_type="seed",
            )
            self._save_variant(initial)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_evolve(self, generation: int) -> bool:
        """Check whether evolution_interval has been reached."""
        if not self.config.prompt_evo.enabled:
            return False
        if self.evolution_interval is None:
            return False
        return generation > 0 and generation % self.evolution_interval == 0

    async def evolve_prompt(
        self,
        current_prompt: PromptVariant,
        query_fn: Callable,
        top_programs: Optional[List] = None,
        global_scratchpad: Optional[str] = None,
    ) -> Optional[PromptVariant]:
        """Generate a new prompt variant via diff or full mutation.

        Args:
            current_prompt: The PromptVariant to mutate.
            query_fn: Async callable ``async def query_fn(sys_msg, user_msg) -> str``.
            top_programs: Top-k programs to include as context.
            global_scratchpad: Global insights from MetaSummarizer.

        Returns:
            New PromptVariant, or None on failure.
        """
        from ..prompts.prompt_evo import (
            PROMPT_EVO_DIFF_SYSTEM,
            PROMPT_EVO_DIFF_USER,
            PROMPT_EVO_FULL_SYSTEM,
            PROMPT_EVO_FULL_USER,
            format_global_scratchpad,
            format_top_programs,
        )

        patch_type = self._sample_patch_type()

        top_programs_str = format_top_programs(
            top_programs or [], self.language
        )
        scratchpad_section = format_global_scratchpad(global_scratchpad)

        context = {
            "current_prompt": current_prompt.prompt_text,
            "top_programs": top_programs_str,
            "global_scratchpad_section": scratchpad_section,
        }

        if patch_type == "diff":
            sys_msg = PROMPT_EVO_DIFF_SYSTEM
            user_msg = PROMPT_EVO_DIFF_USER.format(**context)
        else:
            sys_msg = PROMPT_EVO_FULL_SYSTEM
            user_msg = PROMPT_EVO_FULL_USER.format(**context)

        try:
            response = await query_fn(sys_msg, user_msg)
            if not response:
                return None

            name, description, new_text = _parse_prompt_response(response)
            if not new_text or len(new_text) < 50:
                logger.warning("Generated prompt too short, discarding")
                return None

            new_variant = PromptVariant(
                id=str(uuid.uuid4()),
                prompt_text=new_text,
                generation=self._next_generation(),
                parent_id=current_prompt.id,
                patch_type=patch_type,
                metadata={"name": name, "description": description},
            )

            self._save_variant(new_variant)

            # Evict if archive exceeds max size
            self._evict_if_needed()

            logger.info(
                f"Evolved prompt via {patch_type}: {new_variant.id[:8]}... "
                f"(gen={new_variant.generation})"
            )
            return new_variant

        except Exception as e:
            logger.error(f"Prompt evolution failed: {e}")
            return None

    def select_prompt(self) -> PromptVariant:
        """UCB-based selection from the prompt archive.

        Falls back to the initial prompt if archive is empty.
        """
        variants = self._get_all_variants()
        if not variants:
            raise RuntimeError("Prompt archive is empty")

        if len(variants) == 1:
            return variants[0]

        # Epsilon-greedy: random uniform with probability epsilon
        if random.random() < self.epsilon:
            return random.choice(variants)

        # UCB1 selection
        total_programs = sum(max(v.program_count, 1) for v in variants)
        best_variant = None
        best_score = float("-inf")

        for v in variants:
            n = max(v.program_count, 1)
            avg = v.fitness if v.program_count > 0 else 0.0
            ucb = avg + self.ucb_c * np.sqrt(np.log(total_programs + 1) / n)
            if ucb > best_score:
                best_score = ucb
                best_variant = v

        return best_variant or variants[0]

    def update_prompt_fitness(self, prompt_id: str, program_score: float) -> None:
        """Track which prompts produce good programs.

        Updates running average fitness and program count.
        """
        variant = self._get_variant(prompt_id)
        if variant is None:
            logger.warning(f"Prompt variant {prompt_id} not found")
            return

        variant.scores.append(program_score)
        variant.program_count += 1
        variant.fitness = sum(variant.scores) / len(variant.scores)
        self._save_variant(variant)

    def get_best_prompt(self) -> Optional[PromptVariant]:
        """Return the highest-fitness prompt variant."""
        variants = self._get_all_variants()
        if not variants:
            return None
        return max(variants, key=lambda v: v.fitness)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_variants (
                    id TEXT PRIMARY KEY,
                    prompt_text TEXT NOT NULL,
                    generation INTEGER DEFAULT 0,
                    parent_id TEXT,
                    patch_type TEXT DEFAULT 'full',
                    fitness REAL DEFAULT 0.0,
                    program_count INTEGER DEFAULT 0,
                    scores_json TEXT DEFAULT '[]',
                    metadata_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.commit()

    def _save_variant(self, variant: PromptVariant) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO prompt_variants
                (id, prompt_text, generation, parent_id, patch_type,
                 fitness, program_count, scores_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant.id,
                    variant.prompt_text,
                    variant.generation,
                    variant.parent_id,
                    variant.patch_type,
                    variant.fitness,
                    variant.program_count,
                    json.dumps(variant.scores),
                    json.dumps(variant.metadata),
                ),
            )
            conn.commit()

    def _get_variant(self, variant_id: str) -> Optional[PromptVariant]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM prompt_variants WHERE id = ?", (variant_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_variant(row)

    def _get_all_variants(self) -> List[PromptVariant]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_variants ORDER BY generation ASC"
            ).fetchall()
        return [self._row_to_variant(r) for r in rows]

    def _next_generation(self) -> int:
        variants = self._get_all_variants()
        if not variants:
            return 0
        return max(v.generation for v in variants) + 1

    def _evict_if_needed(self) -> None:
        """Remove lowest-fitness variants if archive exceeds max size."""
        variants = self._get_all_variants()
        if len(variants) <= self.archive_size:
            return

        # Sort by fitness ascending; evict lowest (but never the seed)
        evict_candidates = [v for v in variants if v.patch_type != "seed"]
        evict_candidates.sort(key=lambda v: v.fitness)
        to_remove = evict_candidates[: len(variants) - self.archive_size]

        with sqlite3.connect(self.db_path) as conn:
            for v in to_remove:
                conn.execute("DELETE FROM prompt_variants WHERE id = ?", (v.id,))
            conn.commit()

    @staticmethod
    def _row_to_variant(row: tuple) -> PromptVariant:
        (
            vid, prompt_text, generation, parent_id, patch_type,
            fitness, program_count, scores_json, metadata_json,
        ) = row
        return PromptVariant(
            id=vid,
            prompt_text=prompt_text,
            generation=generation,
            parent_id=parent_id,
            patch_type=patch_type,
            fitness=fitness,
            program_count=program_count,
            scores=json.loads(scores_json or "[]"),
            metadata=json.loads(metadata_json or "{}"),
        )

    def _sample_patch_type(self) -> str:
        return random.choices(self.patch_types, weights=self.patch_type_probs, k=1)[0]


# ---------------------------------------------------------------------------
# Parsing helper
# ---------------------------------------------------------------------------

def _parse_prompt_response(
    content: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse NAME, DESCRIPTION, and PROMPT tags from LLM response."""

    def _extract(text: str, start: str, end: str) -> Optional[str]:
        pattern = f"{re.escape(start)}\\s*(.*?)\\s*{re.escape(end)}"
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else None

    name = _extract(content, "<NAME>", "</NAME>")
    description = _extract(content, "<DESCRIPTION>", "</DESCRIPTION>")
    prompt_text = _extract(content, "<PROMPT>", "</PROMPT>")

    if prompt_text is None:
        logger.warning("No <PROMPT> tag found in response, using raw content")
        prompt_text = content.strip()

    return name, description, prompt_text
