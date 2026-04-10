"""Meta-scratchpad: 3-step summarization and recommendation pipeline.

Ported from ShinkaEvolve/shinka/core/summarizer.py with adaptations for
claude-evolve's async query_fn interface (no LLMClient dependency).
"""

import json
import logging
import random
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..database.models import Program
from ..config import EvolveConfig

logger = logging.getLogger(__name__)


class MetaSummarizer:
    """Handles meta-level summarization and recommendation generation.

    Uses a 3-step pipeline:
      1. Individual program summaries (can be parallelized)
      2. Global insights scratchpad
      3. Actionable recommendations

    The query_fn passed to update_meta() is an async callable with signature:
        async def query_fn(system_msg: str, user_msg: str) -> str
    """

    def __init__(self, config: EvolveConfig):
        self.config = config
        self.language = config.language
        self.max_recommendations = config.meta.max_recommendations
        self.rec_interval = config.meta.rec_interval

        # Meta state
        self.meta_summary: Optional[str] = None
        self.meta_scratch_pad: Optional[str] = None
        self.meta_recommendations: Optional[str] = None
        self.meta_recommendations_history: List[str] = []

        # Programs evaluated since last meta update
        self.evaluated_since_last_meta: List[Program] = []
        self.total_programs_processed: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_evaluated_program(self, program: Program) -> None:
        """Track a newly evaluated program for the next meta update."""
        self.evaluated_since_last_meta.append(program)
        logger.debug(
            f"Meta memory: {len(self.evaluated_since_last_meta)} programs tracked "
            f"(added id={program.id}, correct={program.correct})"
        )

    def should_update_meta(self, generation: int) -> bool:
        """Check if rec_interval has been reached based on unprocessed program count."""
        unprocessed = len(self.evaluated_since_last_meta)
        return unprocessed >= self.rec_interval

    async def update_meta(self, query_fn: Callable) -> Optional[str]:
        """Run the 3-step meta pipeline and update internal state.

        Args:
            query_fn: Async callable with signature
                      async def query_fn(system_msg: str, user_msg: str) -> str

        Returns:
            Updated recommendation string, or None on failure.
        """
        from ..prompts.meta import (
            META_STEP1_SYSTEM_MSG,
            META_STEP1_USER_MSG,
            META_STEP2_SYSTEM_MSG,
            META_STEP2_USER_MSG,
            META_STEP3_SYSTEM_MSG,
            META_STEP3_USER_MSG,
        )
        from ..prompts.base import construct_individual_program_msg

        programs_to_analyze = list(self.evaluated_since_last_meta)
        if not programs_to_analyze:
            logger.info("No programs evaluated since last meta query, skipping")
            return None

        try:
            # Step 1: Individual summaries
            individual_summaries = await self._step1_individual_summaries(
                programs_to_analyze, query_fn
            )
            if not individual_summaries:
                logger.error("Step 1 failed - no individual summaries generated")
                return None

            # Step 2: Global insights
            global_insights = await self._step2_global_insights(
                individual_summaries, query_fn
            )
            if not global_insights:
                logger.error("Step 2 failed - no global insights generated")
                return None

            # Step 3: Recommendations
            recommendations = await self._step3_generate_recommendations(
                global_insights, query_fn
            )
            if not recommendations:
                logger.error("Step 3 failed - no recommendations generated")
                return None

            # Update internal state
            if self.meta_summary:
                self.meta_summary += "\n\n" + individual_summaries
            else:
                self.meta_summary = individual_summaries

            self.meta_scratch_pad = global_insights
            self.meta_recommendations = recommendations
            self.meta_recommendations_history.append(recommendations)

            num_processed = len(self.evaluated_since_last_meta)
            self.total_programs_processed += num_processed
            self.evaluated_since_last_meta = []

            logger.info(
                f"Meta-analysis completed: processed {num_processed} programs "
                f"(total: {self.total_programs_processed})"
            )
            return recommendations

        except Exception as e:
            logger.error(f"Failed to complete meta-analysis: {e}")
            return None

    def get_recommendations(self) -> Optional[str]:
        """Return current recommendation strings."""
        return self.meta_recommendations

    def get_sampled_recommendation(self) -> Optional[str]:
        """Sample a single recommendation from the numbered list."""
        if not self.meta_recommendations or self.meta_recommendations == "none":
            return None

        pattern = r"^\d+\.\s+"
        lines = self.meta_recommendations.strip().split("\n")

        recommendations = []
        current_rec: List[str] = []
        for line in lines:
            if re.match(pattern, line):
                if current_rec:
                    recommendations.append("\n".join(current_rec))
                current_rec = [line]
            elif current_rec:
                current_rec.append(line)
        if current_rec:
            recommendations.append("\n".join(current_rec))

        if not recommendations:
            return None

        sampled = random.choice(recommendations)
        sampled = re.sub(r"^\d+\.\s*", "", sampled)
        return sampled

    def get_unprocessed_program_count(self) -> int:
        """Get count of unprocessed programs awaiting meta analysis."""
        return len(self.evaluated_since_last_meta)

    def get_total_programs_processed(self) -> int:
        """Get total count of programs processed in meta updates."""
        return self.total_programs_processed

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_state(self, filepath: str) -> None:
        """Save meta state to a JSON file."""
        try:
            unprocessed_data = []
            for prog in self.evaluated_since_last_meta:
                try:
                    unprocessed_data.append(prog.to_dict())
                except Exception as e:
                    logger.warning(f"Failed to serialize program {prog.id}: {e}")

            meta_data = {
                "unprocessed_programs": unprocessed_data,
                "meta_summary": self.meta_summary,
                "meta_scratch_pad": self.meta_scratch_pad,
                "meta_recommendations": self.meta_recommendations,
                "meta_recommendations_history": self.meta_recommendations_history,
                "total_programs_meta_processed": self.total_programs_processed,
            }

            filepath_obj = Path(filepath)
            filepath_obj.parent.mkdir(parents=True, exist_ok=True)
            temp_path = filepath_obj.with_suffix(".tmp")

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2, default=str)

            temp_path.replace(filepath_obj)
            logger.info(f"Saved meta state to {filepath}")

        except Exception as e:
            logger.error(f"Failed to save meta state: {e}")

    def load_state(self, filepath: str) -> bool:
        """Load meta state from a JSON file."""
        filepath_obj = Path(filepath)
        if not filepath_obj.exists():
            return False

        try:
            with open(filepath_obj, "r", encoding="utf-8") as f:
                meta_data = json.load(f)

            prog_list = meta_data.get("unprocessed_programs", [])
            restored = []
            for prog_dict in prog_list:
                try:
                    restored.append(Program.from_dict(prog_dict))
                except Exception as e:
                    logger.warning(f"Failed to restore program: {e}")

            self.evaluated_since_last_meta = restored
            self.meta_summary = meta_data.get("meta_summary")
            self.meta_scratch_pad = meta_data.get("meta_scratch_pad")
            self.meta_recommendations = meta_data.get("meta_recommendations")
            self.meta_recommendations_history = meta_data.get(
                "meta_recommendations_history", []
            )
            self.total_programs_processed = meta_data.get(
                "total_programs_meta_processed", 0
            )
            logger.info(f"Loaded meta state from {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to load meta state: {e}")
            return False

    def write_meta_output(self, results_dir: str) -> None:
        """Write meta summary, scratchpad, and recommendations to a text file."""
        output_str = ""

        if self.meta_summary:
            output_str += "# INDIVIDUAL PROGRAM SUMMARIES\n\n"
            output_str += str(self.meta_summary) + "\n\n"

        if self.meta_scratch_pad:
            output_str += "# GLOBAL INSIGHTS SCRATCHPAD\n\n"
            output_str += str(self.meta_scratch_pad) + "\n\n"

        if self.meta_recommendations:
            output_str += "# META RECOMMENDATIONS\n\n"
            output_str += str(self.meta_recommendations)

        if output_str:
            meta_dir = Path(results_dir) / "meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            meta_path = meta_dir / f"meta_{self.total_programs_processed}.txt"
            meta_path.write_text(output_str, encoding="utf-8")
            logger.info(f"Wrote meta output to {meta_path}")

    # ------------------------------------------------------------------
    # Pipeline steps (private)
    # ------------------------------------------------------------------

    async def _step1_individual_summaries(
        self,
        programs: List[Program],
        query_fn: Callable,
    ) -> Optional[str]:
        """Step 1: Create individual summaries for each program."""
        import asyncio
        from ..prompts.meta import META_STEP1_SYSTEM_MSG, META_STEP1_USER_MSG
        from ..prompts.base import construct_individual_program_msg

        async def summarize_one(prog: Program) -> Optional[Tuple[int, str]]:
            individual_msg = construct_individual_program_msg(
                prog, language=self.language
            )
            user_msg = META_STEP1_USER_MSG.replace(
                "{individual_program_msg}", individual_msg
            )
            try:
                response = await query_fn(META_STEP1_SYSTEM_MSG, user_msg)
                if response:
                    summary = response.strip()
                    patch_name = (prog.metadata or {}).get("patch_name", "unknown")
                    summary += (
                        f"\n**Program Identifier:** Generation {prog.generation} "
                        f"- Patch Name {patch_name} - Correct Program: {prog.correct}"
                    )
                    return prog.generation, summary
            except Exception as e:
                logger.warning(f"Step 1: Failed to summarize program {prog.id}: {e}")
            return None

        tasks = [summarize_one(p) for p in programs]
        results = await asyncio.gather(*tasks)

        valid = [(gen, s) for r in results if r is not None for gen, s in [r]]
        if not valid:
            return None

        valid.sort(key=lambda x: x[0])
        combined = "\n\n".join(s for _, s in valid)
        logger.info(f"Step 1: {len(valid)}/{len(programs)} summaries generated")
        return combined

    async def _step2_global_insights(
        self,
        individual_summaries: str,
        query_fn: Callable,
        best_program: Optional[Program] = None,
    ) -> Optional[str]:
        """Step 2: Generate global insights from individual summaries."""
        from ..prompts.meta import META_STEP2_SYSTEM_MSG, META_STEP2_USER_MSG
        from ..prompts.base import construct_individual_program_msg

        previous_insights = self.meta_scratch_pad or "*No previous insights available.*"

        if best_program:
            best_program_info = construct_individual_program_msg(
                best_program, language=self.language
            )
        else:
            best_program_info = "*No best program information available.*"

        user_msg = (
            META_STEP2_USER_MSG
            .replace("{individual_summaries}", individual_summaries)
            .replace("{previous_insights}", previous_insights)
            .replace("{best_program_info}", best_program_info)
        )

        try:
            response = await query_fn(META_STEP2_SYSTEM_MSG, user_msg)
            if response:
                logger.info("Step 2: Global insights generated")
                return response.strip()
        except Exception as e:
            logger.error(f"Step 2 failed: {e}")
        return None

    async def _step3_generate_recommendations(
        self,
        global_insights: str,
        query_fn: Callable,
        best_program: Optional[Program] = None,
    ) -> Optional[str]:
        """Step 3: Generate recommendations from global insights."""
        from ..prompts.meta import META_STEP3_SYSTEM_MSG, META_STEP3_USER_MSG
        from ..prompts.base import construct_individual_program_msg

        previous_recs = self.meta_recommendations or "*No previous recommendations available.*"

        if best_program:
            best_program_info = construct_individual_program_msg(
                best_program, language=self.language
            )
        else:
            best_program_info = "*No best program information available.*"

        user_msg = (
            META_STEP3_USER_MSG
            .replace("{global_insights}", global_insights)
            .replace("{previous_recommendations}", previous_recs)
            .replace("{max_recommendations}", str(self.max_recommendations))
            .replace("{best_program_info}", best_program_info)
        )

        try:
            response = await query_fn(META_STEP3_SYSTEM_MSG, user_msg)
            if response:
                logger.info("Step 3: Recommendations generated")
                return response.strip()
        except Exception as e:
            logger.error(f"Step 3 failed: {e}")
        return None
