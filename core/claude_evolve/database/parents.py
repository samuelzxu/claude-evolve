"""Parent selection strategies for claude-evolve.

Three strategies are supported:
  - weighted   : sigmoid-weighted by score (primary, from ShinkaEvolve)
  - power_law  : rank-biased power-law sampling
  - archive_bias: 20 % from archive, 80 % from pool
"""

from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _stable_sigmoid(x: float) -> float:
    """Numerically stable sigmoid that avoids float overflow."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2.0 if n % 2 == 0 else s[mid]


def _sample_index(probs: list[float]) -> int:
    """Sample an index given a list of probabilities (must sum to 1)."""
    import random

    r = random.random()
    cumulative = 0.0
    for i, p in enumerate(probs):
        cumulative += p
        if r <= cumulative:
            return i
    return len(probs) - 1


def _power_law_probs(n: int, alpha: float = 1.5) -> list[float]:
    """Return rank-biased probabilities for *n* items (best-first ordering)."""
    raw = [(i + 1) ** (-alpha) for i in range(n)]
    total = sum(raw)
    if total == 0:
        return [1.0 / n] * n
    return [v / total for v in raw]


# ---------------------------------------------------------------------------
# ParentSelector
# ---------------------------------------------------------------------------


class ParentSelector:
    """Dispatches to the appropriate parent-selection strategy.

    Usage::

        selector = ParentSelector(strategy="weighted")
        parent = selector.select_parent(programs)
    """

    def __init__(
        self,
        strategy: str = "weighted",
        lambda_param: float = 1.0,
        alpha: float = 1.5,
        archive_prob: float = 0.2,
    ) -> None:
        self.strategy = strategy
        self.lambda_param = lambda_param
        self.alpha = alpha
        self.archive_prob = archive_prob

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def select_parent(
        self,
        programs: list,
        archive: Optional[list] = None,
        strategy: Optional[str] = None,
    ):
        """Select and return one parent from *programs*.

        Args:
            programs: Candidate pool (Program objects with .combined_score
                      and .children_count attributes).
            archive: Optional archive pool for archive_bias strategy.
            strategy: Override the instance strategy for this call.

        Returns:
            A Program object or None if *programs* is empty.
        """
        if not programs:
            logger.warning("select_parent called with empty pool.")
            return None

        used = strategy or self.strategy

        if used == "weighted":
            return self.weighted_selection(programs, lambda_param=self.lambda_param)
        if used == "power_law":
            return self.power_law_selection(programs, alpha=self.alpha)
        if used == "archive_bias":
            return self.archive_bias_selection(
                programs, archive=archive or [], archive_prob=self.archive_prob
            )

        logger.warning("Unknown strategy '%s'; falling back to weighted.", used)
        return self.weighted_selection(programs, lambda_param=self.lambda_param)

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    def weighted_selection(
        self,
        programs: list,
        lambda_param: float = 1.0,
    ):
        """Sigmoid-weighted selection (primary strategy).

        Each candidate gets weight  w_i = sigmoid(λ * (α_i - α_0) / scale) * 1/(1+n_i)
        where α_0 is the median score, scale is the MAD, and n_i is the
        children count.  This is identical to ShinkaEvolve's WeightedSamplingStrategy.

        Args:
            programs: Candidate pool sorted or unsorted.
            lambda_param: Sharpness of the sigmoid.

        Returns:
            A Program object.
        """
        scores = [p.combined_score or 0.0 for p in programs]
        alpha_0 = _median(scores)
        deviations = [abs(s - alpha_0) for s in scores]
        mad = _median(deviations)
        scale = max(mad, 1e-6)

        weights: list[float] = []
        for p in programs:
            alpha_i = p.combined_score or 0.0
            n_i = getattr(p, "children_count", 0) or 0
            normalized = (alpha_i - alpha_0) / scale
            s_i = _stable_sigmoid(lambda_param * normalized)
            h_i = 1.0 / (1.0 + n_i)
            weights.append(s_i * h_i)

        total = sum(weights)
        if total <= 0:
            probs = [1.0 / len(programs)] * len(programs)
        else:
            probs = [w / total for w in weights]

        idx = _sample_index(probs)
        selected = programs[idx]
        logger.debug(
            "weighted_selection: chose program %s (score=%.4f, children=%d).",
            selected.id,
            selected.combined_score or 0.0,
            getattr(selected, "children_count", 0) or 0,
        )
        return selected

    def power_law_selection(
        self,
        programs: list,
        alpha: float = 1.5,
    ):
        """Rank-biased power-law sampling.

        Programs should be ordered best-first; the first entry is most
        likely to be chosen.  If the list is unordered, it is sorted
        descending by combined_score internally.

        Args:
            programs: Candidate pool.
            alpha: Power-law exponent (0 = uniform, higher = more exploitation).

        Returns:
            A Program object.
        """
        sorted_programs = sorted(
            programs,
            key=lambda p: p.combined_score or 0.0,
            reverse=True,
        )
        probs = _power_law_probs(len(sorted_programs), alpha=alpha)
        idx = _sample_index(probs)
        selected = sorted_programs[idx]
        logger.debug(
            "power_law_selection: chose program %s (rank=%d, score=%.4f).",
            selected.id,
            idx,
            selected.combined_score or 0.0,
        )
        return selected

    def archive_bias_selection(
        self,
        programs: list,
        archive: list,
        archive_prob: float = 0.2,
    ):
        """Sample from archive with probability *archive_prob*, else from pool.

        Args:
            programs: Full candidate pool.
            archive: Archive subset (elite programs).
            archive_prob: Probability of drawing from the archive.

        Returns:
            A Program object.
        """
        import random

        pool: list
        if archive and random.random() < archive_prob:
            pool = archive
            label = "archive"
        else:
            pool = programs
            label = "pool"

        selected = self.weighted_selection(pool, lambda_param=self.lambda_param)
        logger.debug(
            "archive_bias_selection: drew from %s -> program %s.",
            label,
            selected.id if selected else None,
        )
        return selected
