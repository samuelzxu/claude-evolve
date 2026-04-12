"""Evolution orchestrator for claude-evolve.

Ties together: database, ensemble bandit, prompt sampler, meta-summarizer,
novelty judge, island manager, and prompt co-evolution into a single
sync-first generation loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .config import EvolveConfig
from .database.db import ProgramDatabase
from .database.models import Program
from .ensemble.bandit import EnsembleBandit
from .ensemble.bridge import LLMCallFailed, QueryResult, query_claude_async
from .ensemble.personas import build_system_prompt
from .evaluation import evaluate_program
from .meta.summarizer import MetaSummarizer
from .mutations.apply_diff import apply_diff_patch
from .mutations.apply_fix import apply_fix_patch
from .mutations.apply_full import apply_full_patch
from .mutations.crossover import apply_crossover_patch
from .mutations.sampler import PromptSampler
from .novelty.judge import NoveltyJudge

logger = logging.getLogger(__name__)


class EvolutionRunner:
    """Sync-first (one proposal per generation) evolution orchestrator.

    Usage::

        runner = EvolutionRunner(config)
        asyncio.run(runner.run())
    """

    def __init__(self, config: EvolveConfig) -> None:
        self.config = config

        # Resolved paths
        self._results_dir = Path(config.results_dir)
        self._state_dir = self._results_dir
        self._db_path = self._results_dir / "programs.db"
        self._log_path = self._results_dir / "evolve.log"
        self._state_path = self._results_dir / "run_state.json"
        self._meta_state_path = str(self._results_dir / "meta_state.json")
        self._prompt_db_path = str(self._results_dir / "prompts.db")

        # Core components — fully initialised in _setup()
        self.db: Optional[ProgramDatabase] = None
        self.bandit: Optional[EnsembleBandit] = None
        self.sampler: Optional[PromptSampler] = None
        self.meta_summarizer: Optional[MetaSummarizer] = None
        self.novelty_judge: Optional[NoveltyJudge] = None
        self.prompt_evolver = None  # Optional[PromptEvolver]

        # Runtime state
        self._generation: int = 0
        self._best_score: float = 0.0
        self._best_program_id: Optional[int] = None
        self._shutdown_requested: bool = False
        self._current_island: int = 0
        self._log_file = None  # open file handle for JSONL log

        self._setup_logging()
        self._register_signal_handlers()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main entry point: setup → evolution loop → finalize."""
        try:
            await self._setup()
            await self._evolution_loop()
        finally:
            self._finalize()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def _setup(self) -> None:
        """Create directories, initialise DB, load or create initial program."""
        self._results_dir.mkdir(parents=True, exist_ok=True)

        # Database
        self.db = ProgramDatabase(
            db_path=self._db_path,
            config=self.config.islands,
        )

        # Bandit
        self.bandit = EnsembleBandit(
            arm_names=self.config.ensemble.arms,
            exploration_coef=self.config.ensemble.exploration_coef,
            epsilon=self.config.ensemble.epsilon,
            shift_by_baseline=self.config.ensemble.shift_by_baseline,
            shift_by_parent=self.config.ensemble.shift_by_parent,
            adaptive_scale=self.config.ensemble.adaptive_scale,
            asymmetric_scaling=self.config.ensemble.asymmetric_scaling,
        )

        # Prompt sampler
        self.sampler = PromptSampler(self.config)

        # Meta-summarizer
        self.meta_summarizer = MetaSummarizer(self.config)

        # Novelty judge
        self.novelty_judge = NoveltyJudge(
            similarity_threshold=self.config.novelty.similarity_threshold,
            max_attempts=self.config.novelty.max_attempts,
        )

        # Optional prompt co-evolution
        if self.config.prompt_evo.enabled:
            from .meta.prompt_evolver import PromptEvolver
            self.prompt_evolver = PromptEvolver(self.config, self._prompt_db_path)

        # Resume detection
        state = self._load_state()
        if state is not None:
            self._resume_from_state(state)
            logger.info("Resuming from generation %d", self._generation)
            return

        # Fresh start: load initial program and evaluate it
        init_path = Path(self.config.init_program_path)
        if not init_path.exists():
            raise FileNotFoundError(
                f"Initial program not found: {init_path}"
            )
        init_code = init_path.read_text(encoding="utf-8")

        logger.info("Evaluating initial program...")
        eval_result = await self._evaluate(init_code)

        if not eval_result.get("correct"):
            error_msg = eval_result.get("error", "unknown error")
            metrics = eval_result.get("metrics", {})
            stderr_log = metrics.get("stderr_log", "")
            logger.error(
                "Initial program evaluation FAILED (correct=False). "
                "Error: %s | stderr: %s",
                error_msg,
                stderr_log[:500],
            )
            # Retry once in case of transient failure
            logger.info("Retrying initial evaluation once...")
            eval_result = await self._evaluate(init_code)

            if not eval_result.get("correct"):
                error_msg = eval_result.get("error", "unknown error")
                raise RuntimeError(
                    f"Initial program failed evaluation twice. "
                    f"The evolution cannot proceed without a correct seed program.\n"
                    f"Error: {error_msg}\n"
                    f"Fix your initial.py or evaluate.py and try again.\n"
                    f"Run: python3 {self.config.eval_program_path} "
                    f"--program_path {self.config.init_program_path}"
                )

        # Store initial program
        init_program = Program(
            id=0,
            code=init_code,
            combined_score=eval_result["combined_score"],
            correct=eval_result["correct"],
            generation=0,
            parent_id=None,
            island_idx=0,
            patch_type="seed",
            metadata=eval_result.get("metrics", {}),
        )
        prog_id = self.db.add_program(init_program)
        self._best_program_id = prog_id
        self._best_score = eval_result["combined_score"]

        # Update bandit baseline
        self.bandit.set_baseline_score(self._best_score)

        self._log_event(
            gen=0,
            event="init",
            score=eval_result["combined_score"],
            correct=eval_result["correct"],
            program_id=prog_id,
        )
        logger.info(
            "Initial program stored (id=%d score=%.4f correct=%s)",
            prog_id,
            eval_result["combined_score"],
            eval_result["correct"],
        )

        self._save_state()

    def _resume_from_state(self, state: dict) -> None:
        """Restore runner fields from a persisted state dict."""
        self._generation = state.get("generation", 0)
        self._best_score = state.get("best_score", 0.0)
        self._best_program_id = state.get("best_program_id")

        bandit_state = state.get("bandit_state")
        if bandit_state and self.bandit is not None:
            try:
                self.bandit.set_state(bandit_state)
            except Exception as exc:
                logger.warning("Could not restore bandit state: %s", exc)

        if self.meta_summarizer is not None:
            self.meta_summarizer.load_state(self._meta_state_path)

        # Rebuild bandit baseline from DB
        if self.db is not None:
            best = self.db.get_best_program()
            if best is not None:
                self.bandit.set_baseline_score(best.combined_score or 0.0)
                self._best_score = best.combined_score or 0.0
                self._best_program_id = best.id

    # ------------------------------------------------------------------
    # Evolution loop
    # ------------------------------------------------------------------

    async def _evolution_loop(self) -> None:
        """Run generations sequentially from _generation to num_generations."""
        start_gen = self._generation
        num_gens = self.config.num_generations

        logger.info(
            "Starting evolution loop: gen %d -> %d", start_gen, num_gens
        )

        for gen in range(start_gen, num_gens):
            if self._shutdown_requested:
                logger.info("Shutdown requested; stopping at generation %d.", gen)
                break

            self._generation = gen
            await self._run_generation(gen)
            self._save_state()

    # ------------------------------------------------------------------
    # Single generation
    # ------------------------------------------------------------------

    async def _run_generation(self, gen: int) -> None:
        """Execute one full proposal → evaluate → store cycle."""
        num_islands = self.config.islands.num_islands

        # 1. Select island (round-robin)
        island_idx = gen % max(1, num_islands)
        self._current_island = island_idx

        # 2. Select parent
        parent = self.db.select_parent(island_idx=island_idx)
        if parent is None:
            # Fallback: any program in DB
            parent = self.db.get_best_program()
        if parent is None:
            logger.warning("gen=%d: No parent found, skipping.", gen)
            self._log_event(gen=gen, event="skip", reason="no_parent")
            return

        # 3. Get inspirations
        archive_progs, top_k_progs = self.db.get_inspirations(
            island_idx=island_idx, num_archive=2, num_top_k=2
        )
        inspirations = list({p.id: p for p in (archive_progs + top_k_progs)}.values())

        # 4. Select model arm from bandit
        arm = self.bandit.select_arm()
        self.bandit.update_submitted(arm)

        # 5. Select persona for diversity
        # Build system prompt with random persona
        meta_rec = None
        if self.meta_summarizer is not None and self.config.meta.sample_single:
            meta_rec = self.meta_summarizer.get_sampled_recommendation()
        elif self.meta_summarizer is not None:
            meta_rec = self.meta_summarizer.get_recommendations()

        # 6. Construct prompt
        try:
            sys_msg, user_msg, patch_type = self.sampler.sample(
                parent=parent,
                inspirations=inspirations,
                meta_recommendations=meta_rec,
            )
        except Exception as exc:
            logger.warning("gen=%d: Prompt construction failed: %s", gen, exc)
            self._log_event(gen=gen, event="prompt_failed", error=str(exc))
            self.bandit.update(arm, reward=None, baseline=parent.combined_score)
            return

        # Apply persona to system message
        sys_msg_with_persona = build_system_prompt(sys_msg)

        # 7. Query Claude with retry/backoff
        try:
            result: QueryResult = await query_claude_async(
                arm=arm,
                prompt=user_msg,
                system_prompt=sys_msg_with_persona,
                timeout=self.config.llm_timeout,
                max_retries=3,
            )
            llm_response = result.content
        except LLMCallFailed as exc:
            logger.warning("gen=%d: LLM call failed: %s", gen, exc)
            self._log_event(
                gen=gen,
                event="llm_failed",
                arm=arm,
                patch_type=patch_type,
                error=str(exc),
            )
            self.bandit.update(arm, reward=None, baseline=parent.combined_score)
            return

        # 8. Apply patch
        new_code, patch_error = await self._apply_patch(
            llm_response=llm_response,
            parent=parent,
            patch_type=patch_type,
            arm=arm,
        )
        if new_code is None:
            self._log_event(
                gen=gen,
                event="patch_failed",
                arm=arm,
                patch_type=patch_type,
                error=patch_error,
            )
            self.bandit.update(arm, reward=None, baseline=parent.combined_score)
            return

        # 9. Evaluate new program
        eval_result = await self._evaluate(new_code)
        if eval_result.get("error") and eval_result["combined_score"] == 0.0:
            logger.warning(
                "gen=%d: Evaluation failed: %s", gen, eval_result["error"]
            )
            # Still continue — store the failed program
            new_program = Program(
                id=0,
                code=new_code,
                combined_score=0.0,
                correct=False,
                generation=gen,
                parent_id=parent.id,
                island_idx=island_idx,
                patch_type=patch_type,
                metadata=eval_result.get("metrics", {}),
                status="eval_failed",
            )
            prog_id = self.db.add_program(new_program)
            self._log_event(
                gen=gen,
                event="eval_failed",
                arm=arm,
                patch_type=patch_type,
                program_id=prog_id,
                error=eval_result.get("error"),
            )
            self.bandit.update(arm, reward=None, baseline=parent.combined_score)
            return

        score = eval_result["combined_score"]
        correct = eval_result["correct"]

        # 10. Compute novelty embedding
        embedding: Optional[dict] = None
        try:
            embedding = self.novelty_judge.embed_code(new_code)
        except Exception as exc:
            logger.debug("gen=%d: Embedding failed: %s", gen, exc)

        # 11. Check novelty
        if (
            embedding is not None
            and self.novelty_judge.should_check_novelty(embedding, gen, parent)
        ):
            should_accept, novelty_meta = self.novelty_judge.assess_novelty(
                code=new_code,
                code_embedding=embedding,
                island_idx=island_idx,
                db=self.db,
            )
            if not should_accept:
                logger.info(
                    "gen=%d: Novelty rejected (max_sim=%.3f)",
                    gen,
                    novelty_meta.get("max_similarity", 0.0),
                )
                self._log_event(
                    gen=gen,
                    event="novelty_rejected",
                    arm=arm,
                    patch_type=patch_type,
                    max_similarity=novelty_meta.get("max_similarity"),
                )
                self.bandit.update(arm, reward=None, baseline=parent.combined_score)
                return

        # 12. Store program in database
        metrics = eval_result.get("metrics", {})
        new_program = Program(
            id=0,
            code=new_code,
            combined_score=score,
            correct=correct,
            generation=gen,
            parent_id=parent.id,
            island_idx=island_idx,
            patch_type=patch_type,
            metadata=metrics,
            embedding=embedding,
            status="ok",
        )
        prog_id = self.db.add_program(new_program)
        new_program.id = prog_id

        # 13. Update bandit reward (improvement over parent)
        reward = score if score > 0.0 else None
        self.bandit.update(
            arm=arm,
            reward=reward,
            baseline=parent.combined_score,
        )

        # Update best tracking
        if score > self._best_score:
            self._best_score = score
            self._best_program_id = prog_id
            self.bandit.set_baseline_score(score)
            logger.info(
                "gen=%d: New best! score=%.4f correct=%s (id=%d)",
                gen, score, correct, prog_id,
            )

        # 14. Update meta-summarizer
        if self.meta_summarizer is not None:
            self.meta_summarizer.add_evaluated_program(new_program)

        # 15. Check meta update interval
        if (
            self.meta_summarizer is not None
            and self.meta_summarizer.should_update_meta(gen)
        ):
            logger.info("gen=%d: Running meta-analysis...", gen)
            try:
                await self.meta_summarizer.update_meta(
                    query_fn=self._make_query_fn(arm)
                )
                self.meta_summarizer.write_meta_output(str(self._results_dir))
                self.meta_summarizer.save_state(self._meta_state_path)
            except Exception as exc:
                logger.warning("gen=%d: Meta update failed: %s", gen, exc)

        # 16. Check prompt evolution interval
        if self.prompt_evolver is not None and self.prompt_evolver.should_evolve(gen):
            logger.info("gen=%d: Running prompt evolution...", gen)
            try:
                current_prompt = self.prompt_evolver.select_prompt()
                top_programs = self.db.get_archive(
                    top_k=self.config.prompt_evo.top_k_programs
                )
                scratchpad = (
                    self.meta_summarizer.meta_scratch_pad
                    if self.meta_summarizer else None
                )
                await self.prompt_evolver.evolve_prompt(
                    current_prompt=current_prompt,
                    query_fn=self._make_query_fn(arm),
                    top_programs=top_programs,
                    global_scratchpad=scratchpad,
                )
                # Update fitness of the current prompt
                self.prompt_evolver.update_prompt_fitness(current_prompt.id, score)
            except Exception as exc:
                logger.warning("gen=%d: Prompt evolution failed: %s", gen, exc)

        # 17. Check migration interval
        if self.db.island_manager.should_migrate(gen):
            logger.info("gen=%d: Running island migration...", gen)
            try:
                n_migrated = self.db.island_manager.migrate(self.db)
                self._log_event(gen=gen, event="migration", n_migrated=n_migrated)
            except Exception as exc:
                logger.warning("gen=%d: Migration failed: %s", gen, exc)

        # Dynamic island spawning
        if self.db.island_manager.should_spawn_island(self.db, gen):
            logger.info("gen=%d: Spawning new island (stagnation detected)...", gen)
            try:
                new_island = self.db.island_manager.spawn_island(self.db)
                if new_island is not None:
                    self._log_event(
                        gen=gen, event="island_spawn", new_island=new_island
                    )
            except Exception as exc:
                logger.warning("gen=%d: Island spawn failed: %s", gen, exc)

        # 18. Log generation result
        self._log_event(
            gen=gen,
            event="generation_complete",
            program_id=prog_id,
            arm=arm,
            patch_type=patch_type,
            score=score,
            correct=correct,
            parent_id=parent.id,
            island_idx=island_idx,
            best_score=self._best_score,
        )

        logger.info(
            "gen=%d: score=%.4f correct=%s arm=%s patch=%s island=%d",
            gen, score, correct, arm, patch_type, island_idx,
        )

    # ------------------------------------------------------------------
    # Patch application
    # ------------------------------------------------------------------

    async def _apply_patch(
        self,
        llm_response: str,
        parent: Program,
        patch_type: str,
        arm: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """Apply an LLM-generated patch to the parent code.

        On failure, attempts a single fix-patch retry.

        Returns
        -------
        (new_code, error_message)
            new_code is None on unrecoverable failure.
        """
        lang = self.config.language

        if patch_type == "diff":
            new_code, n_applied, error = apply_diff_patch(llm_response, parent.code)
        elif patch_type == "full":
            new_code, n_applied, error = apply_full_patch(
                llm_response, parent.code, language=lang
            )
        elif patch_type == "cross":
            new_code, n_applied, error = apply_crossover_patch(
                llm_response, parent.code, language=lang
            )
        elif patch_type == "fix":
            new_code, n_applied, error = apply_fix_patch(
                llm_response, parent.code, language=lang
            )
        else:
            return None, f"Unknown patch type: {patch_type}"

        if error or n_applied == 0:
            logger.debug(
                "Initial patch failed (type=%s): %s. Attempting fix...",
                patch_type,
                error,
            )
            # One fix-patch retry
            fixed_code = await self._try_fix_patch(
                broken_code=llm_response,
                parent_code=parent.code,
                error_message=error or "No patches applied",
                arm=arm,
            )
            if fixed_code is not None:
                return fixed_code, None
            return None, error or "No patches applied"

        return new_code, None

    async def _try_fix_patch(
        self,
        broken_code: str,
        parent_code: str,
        error_message: str,
        arm: str,
    ) -> Optional[str]:
        """Ask the LLM to fix a broken mutation.

        Returns fixed code or None on failure.
        """
        try:
            fix_sys_msg, fix_user_msg = self.sampler._build_fix_prompt(
                broken_code=broken_code,
                error_message=error_message,
                original_code=parent_code,
            )
        except Exception as exc:
            logger.debug("Could not build fix prompt: %s", exc)
            return None

        try:
            result = await query_claude_async(
                arm=arm,
                prompt=fix_user_msg,
                system_prompt=fix_sys_msg,
                timeout=self.config.llm_timeout,
                max_retries=1,
            )
            fix_response = result.content
        except LLMCallFailed as exc:
            logger.debug("Fix LLM call failed: %s", exc)
            return None

        fixed_code, n_applied, error = apply_fix_patch(
            fix_response, parent_code, language=self.config.language
        )
        if error or n_applied == 0:
            logger.debug("Fix patch also failed: %s", error)
            return None
        return fixed_code

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def _evaluate(self, code: str) -> dict:
        """Evaluate code using the configured evaluator script."""
        return await evaluate_program(
            code=code,
            eval_program_path=self.config.eval_program_path,
            results_dir=str(self._results_dir),
            timeout=self.config.eval_timeout,
            language=self.config.language,
            eval_python=self.config.eval_python,
        )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Write run_state.json checkpoint after each generation."""
        bandit_state = {}
        if self.bandit is not None:
            try:
                bandit_state = self.bandit.get_state()
            except Exception:
                pass

        meta_state: dict = {}
        if self.meta_summarizer is not None:
            meta_state = {
                "total_programs_processed": (
                    self.meta_summarizer.get_total_programs_processed()
                ),
                "unprocessed_count": (
                    self.meta_summarizer.get_unprocessed_program_count()
                ),
            }

        state = {
            "pid": os.getpid(),
            "status": "running",
            "generation": self._generation,
            "best_score": self._best_score,
            "best_program_id": self._best_program_id,
            "bandit_state": bandit_state,
            "meta_state": meta_state,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

        tmp_path = self._state_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp_path.replace(self._state_path)
        except OSError as exc:
            logger.warning("Could not save run state: %s", exc)

    def _load_state(self) -> Optional[dict]:
        """Read run_state.json; return None if not found or corrupt."""
        if not self._state_path.exists():
            return None
        try:
            text = self._state_path.read_text(encoding="utf-8")
            state = json.loads(text)
            # Only resume if this is a real prior run (has a stored generation)
            if state.get("generation", 0) > 0:
                return state
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load run state: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        """Configure standard Python logging and open the JSONL log file."""
        self._results_dir.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )

        # Open the JSONL log file (append mode for resumability)
        try:
            self._log_file = open(  # noqa: WPS515
                self._log_path, "a", encoding="utf-8", buffering=1
            )
        except OSError as exc:
            logger.warning("Could not open log file %s: %s", self._log_path, exc)

    def _log_event(self, gen: int, event: str, **kwargs) -> None:
        """Append a JSONL entry to the structured event log."""
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": "INFO",
            "gen": gen,
            "event": event,
            **kwargs,
        }
        if self._log_file is not None:
            try:
                self._log_file.write(json.dumps(entry) + "\n")
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Register SIGTERM and SIGINT handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_signal)
            except (OSError, ValueError):
                # May fail in non-main threads; silently ignore
                pass

    def _handle_signal(self, signum: int, frame) -> None:
        """Set shutdown flag on SIGTERM/SIGINT."""
        sig_name = signal.Signals(signum).name
        logger.info("Received signal %s – requesting graceful shutdown.", sig_name)
        self._shutdown_requested = True
        self._log_event(gen=self._generation, event="signal", signal=sig_name)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def _finalize(self) -> None:
        """Log completion, save state, and close resources."""
        self._save_state()

        if self.meta_summarizer is not None:
            try:
                self.meta_summarizer.save_state(self._meta_state_path)
            except Exception as exc:
                logger.warning("Could not save meta state: %s", exc)

        best = None
        if self.db is not None:
            best = self.db.get_best_program()
            try:
                self.db.close()
            except Exception:
                pass

        self._log_event(
            gen=self._generation,
            event="run_complete",
            best_score=self._best_score,
            best_program_id=self._best_program_id,
            total_generations=self._generation,
        )

        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass

        if best is not None:
            logger.info(
                "Evolution complete. Best program: id=%d score=%.4f correct=%s",
                best.id,
                best.combined_score,
                best.correct,
            )
        else:
            logger.info("Evolution complete. No programs stored.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_query_fn(self, arm: str) -> Callable:
        """Return an async callable suitable for MetaSummarizer / PromptEvolver.

        Signature: async def query_fn(system_msg: str, user_msg: str) -> str
        """
        async def query_fn(system_msg: str, user_msg: str) -> str:
            try:
                result = await query_claude_async(
                    arm=arm,
                    prompt=user_msg,
                    system_prompt=system_msg,
                    timeout=self.config.llm_timeout,
                    max_retries=2,
                )
                return result.content
            except LLMCallFailed as exc:
                logger.warning("Meta/prompt query failed: %s", exc)
                return ""

        return query_fn
