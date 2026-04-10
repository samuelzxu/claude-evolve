"""Novelty rejection sampling for evolutionary code optimization.

Adapted from ShinkaEvolve's NoveltyJudge. Uses local AST-based embeddings
instead of external embedding APIs. Removes LLM-based novelty checking
to avoid spending Claude budget on novelty assessment.
"""

from __future__ import annotations

import logging
from typing import Optional

from claude_evolve.database.models import Program
from claude_evolve.novelty.embeddings import CodeEmbedder

logger = logging.getLogger(__name__)


class NoveltyJudge:
    """Handles novelty assessment using local code embeddings.

    Rejects proposals that are too similar to existing programs in the
    same island, preventing the evolutionary loop from wasting LLM calls
    on redundant mutations.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.95,
        max_attempts: int = 3,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_attempts = max_attempts
        self.embedder = CodeEmbedder()

    def embed_code(self, code: str) -> dict:
        """Compute embedding for a code string."""
        return self.embedder.embed_for_similarity(code)

    def should_check_novelty(
        self,
        code_embedding: Optional[dict],
        generation: int,
        parent_program: Optional[Program],
    ) -> bool:
        """Check if novelty assessment should be performed.

        Skip on first generation, if no embedding, or if no parent.
        """
        if not code_embedding or generation == 0 or not parent_program:
            return False
        # Need at least the tfidf or minhash component
        tfidf = code_embedding.get("tfidf", {})
        minhash = code_embedding.get("minhash", [])
        return bool(tfidf) or bool(minhash)

    def assess_novelty(
        self,
        code: str,
        code_embedding: dict,
        island_idx: int,
        db,
    ) -> tuple[bool, dict]:
        """Assess whether a proposed program is novel enough to keep.

        Computes similarity against existing programs in the same island.
        Rejects if max similarity exceeds threshold.

        Args:
            code: The proposed code string
            code_embedding: Pre-computed embedding of the proposed code
            island_idx: Island to compare against
            db: ProgramDatabase instance

        Returns:
            (should_accept, metadata_dict)
        """
        metadata = {
            "max_similarity": 0.0,
            "num_compared": 0,
            "threshold": self.similarity_threshold,
        }

        # Get programs in the same island that have embeddings
        island_programs = db.get_island_programs(island_idx)
        programs_with_embeddings = [
            p for p in island_programs
            if p.embedding is not None
        ]

        if not programs_with_embeddings:
            logger.info("NOVELTY: Accepting -- no programs with embeddings in island")
            return True, metadata

        metadata["num_compared"] = len(programs_with_embeddings)

        # Compute similarities
        similarities = []
        for prog in programs_with_embeddings:
            if isinstance(prog.embedding, dict):
                sim = CodeEmbedder.similarity(code_embedding, prog.embedding)
            else:
                # Fallback: quick text comparison
                sim = CodeEmbedder.quick_similarity(code, prog.code)
            similarities.append(sim)

        max_sim = max(similarities) if similarities else 0.0
        metadata["max_similarity"] = max_sim

        # Sort for logging
        top_5 = sorted(similarities, reverse=True)[:5]
        logger.info(f"NOVELTY: Top-5 similarities: {[f'{s:.3f}' for s in top_5]}")

        if max_sim <= self.similarity_threshold:
            logger.info(
                f"NOVELTY: Accepting -- max similarity {max_sim:.3f} "
                f"<= threshold {self.similarity_threshold}"
            )
            return True, metadata

        logger.info(
            f"NOVELTY: Rejecting -- max similarity {max_sim:.3f} "
            f"> threshold {self.similarity_threshold}"
        )
        return False, metadata

    def assess_with_rejection_sampling(
        self,
        code: str,
        island_idx: int,
        db,
    ) -> tuple[bool, dict]:
        """Full rejection sampling loop.

        Embeds the code and checks novelty. Unlike ShinkaEvolve, we don't
        retry with different parents here -- that's handled by the runner.

        Returns:
            (should_accept, metadata_dict)
        """
        embedding = self.embed_code(code)
        return self.assess_novelty(code, embedding, island_idx, db)
