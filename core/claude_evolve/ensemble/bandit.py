"""UCB1 bandit adapted from ShinkaEvolve's AsymmetricUCB.

Ported from shinka/llm/prioritization.py.  Cost-aware components removed;
get_state/set_state use JSON-serialisable dicts instead of pickle.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
from scipy.special import logsumexp

logger = logging.getLogger(__name__)

Arm = Union[int, str]
Subset = Optional[Union[np.ndarray, Sequence[Arm]]]

DEFAULT_ARM_NAMES: List[str] = [
    "opus/max",
    "opus/high",
    "sonnet/max",
    "sonnet/high",
    "sonnet/medium",
    "haiku/high",
    "haiku/medium",
    "haiku/low",
]

# ---------------------------------------------------------------------------
# Log-space arithmetic helpers (ported verbatim from ShinkaEvolve)
# ---------------------------------------------------------------------------


def _logadd(x_log: float, y_log: float, w1: float = 1.0, w2: float = 1.0) -> float:
    x = np.asarray(x_log, dtype=float) + np.log(w1)
    y = np.asarray(y_log, dtype=float) + np.log(w2)
    a = np.stack([x, y], axis=0)
    return logsumexp(a, axis=0)


def _logdiffexp(a_log: float, b_log: float) -> float:
    a = np.asarray(a_log, float)
    b = np.asarray(b_log, float)
    d = a - b
    with np.errstate(over="ignore", invalid="ignore"):
        v = a + np.log1p(-np.exp(-d))
    return np.where(d >= 0, v, -np.inf)


def _logexpm1(z: float) -> float:
    z = np.asarray(z, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(z > 50.0, z, np.log(np.expm1(z)))


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BanditBase(ABC):
    """Abstract base class for bandit strategies."""

    def __init__(
        self,
        n_arms: Optional[int] = None,
        seed: Optional[int] = None,
        arm_names: Optional[List[str]] = None,
        auto_decay: Optional[float] = None,
        shift_by_baseline: bool = True,
        shift_by_parent: bool = True,
    ) -> None:
        self.rng = np.random.default_rng(seed)

        if arm_names is None and n_arms is None:
            raise ValueError("provide n_arms or arm_names")
        if arm_names is not None:
            if n_arms is not None and int(n_arms) != len(arm_names):
                raise ValueError("len(arm_names) must equal n_arms")
            self._arm_names: Optional[List[str]] = list(arm_names)
            self._name_to_idx: Dict[str, int] = {n: i for i, n in enumerate(self._arm_names)}
            self._n_arms = len(self._arm_names)
        else:
            self._arm_names = None
            self._name_to_idx = {}
            self._n_arms = int(n_arms)  # type: ignore[arg-type]

        self._baseline: float = 0.0
        self._shift_by_baseline = bool(shift_by_baseline)
        self._shift_by_parent = bool(shift_by_parent)
        if auto_decay is not None and not (0.0 < auto_decay <= 1.0):
            raise ValueError("auto_decay must be in (0, 1]")
        self._auto_decay = auto_decay

    @property
    def n_arms(self) -> int:
        return self._n_arms

    def set_baseline_score(self, baseline: float) -> None:
        self._baseline = float(baseline)

    def _resolve_arm(self, arm: Arm) -> int:
        if isinstance(arm, int):
            return int(arm)
        if self._arm_names is None:
            try:
                return int(arm)
            except Exception as exc:
                raise ValueError("string arm requires arm_names") from exc
        if arm not in self._name_to_idx:
            raise ValueError(f"unknown arm name '{arm}'")
        return self._name_to_idx[arm]

    def _resolve_subset(self, subset: Subset) -> np.ndarray:
        if subset is None:
            return np.arange(self.n_arms, dtype=np.int64)
        if isinstance(subset, np.ndarray) and np.issubdtype(subset.dtype, np.integer):
            return subset.astype(np.int64)
        idxs = [self._resolve_arm(a) for a in subset]
        return np.asarray(idxs, dtype=np.int64)

    def _maybe_decay(self) -> None:
        if self._auto_decay is not None:
            self.decay(self._auto_decay)

    @abstractmethod
    def update_submitted(self, arm: Arm) -> float:
        raise NotImplementedError

    @abstractmethod
    def update(
        self,
        arm: Arm,
        reward: Optional[float],
        baseline: Optional[float] = None,
    ) -> tuple[float, float]:
        raise NotImplementedError

    @abstractmethod
    def posterior(
        self,
        subset: Subset = None,
        samples: Optional[int] = None,
        **kwargs: Any,
    ) -> np.ndarray:
        raise NotImplementedError

    def select_llm(
        self,
        subset: Subset = None,
        samples: Optional[int] = None,
        **kwargs: Any,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (one_hot, probabilities) after sampling from posterior."""
        probabilities = self.posterior(subset=subset, samples=samples, **kwargs)
        one_hot = np.zeros(self.n_arms, dtype=np.float64)
        chosen = self.rng.choice(self.n_arms, size=1, p=probabilities)
        one_hot[chosen[0]] = 1.0
        return one_hot, probabilities

    @abstractmethod
    def decay(self, factor: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return JSON-serialisable state dict."""
        raise NotImplementedError

    @abstractmethod
    def set_state(self, state: Dict[str, Any]) -> None:
        """Restore state from a dict produced by get_state()."""
        raise NotImplementedError

    def save_state(self, path: Union[str, Path]) -> None:
        """Persist bandit state to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = self.get_state()
        with open(path, "w") as fh:
            json.dump(state, fh, indent=2)

    def load_state(self, path: Union[str, Path]) -> None:
        """Load bandit state from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Bandit state file not found: {path}")
        with open(path) as fh:
            state = json.load(fh)
        self.set_state(state)


# ---------------------------------------------------------------------------
# AsymmetricUCB
# ---------------------------------------------------------------------------


class AsymmetricUCB(BanditBase):
    """Asymmetric UCB1 with ε-exploration and adaptive scaling.

    Ported from ShinkaEvolve's AsymmetricUCB.  Cost-aware components
    (per-token cost tracking) have been removed.  State serialisation uses
    JSON instead of pickle.
    """

    def __init__(
        self,
        n_arms: Optional[int] = None,
        seed: Optional[int] = None,
        exploration_coef: float = 1.0,
        epsilon: float = 0.2,
        arm_names: Optional[List[str]] = None,
        auto_decay: Optional[float] = 0.95,
        shift_by_baseline: bool = True,
        shift_by_parent: bool = True,
        adaptive_scale: bool = True,
        asymmetric_scaling: bool = True,
        exponential_base: Optional[float] = 1.0,
    ) -> None:
        super().__init__(
            n_arms=n_arms,
            seed=seed,
            arm_names=arm_names,
            auto_decay=auto_decay,
            shift_by_baseline=shift_by_baseline,
            shift_by_parent=shift_by_parent,
        )
        if asymmetric_scaling:
            assert shift_by_baseline or shift_by_parent, (
                "asymmetric scaling requires at least one of "
                "shift_by_baseline or shift_by_parent to be True"
            )
        if not (0.0 <= epsilon <= 1.0):
            raise ValueError("epsilon must be in [0, 1]")

        self.c = float(exploration_coef)
        self.epsilon = float(epsilon)
        self.adaptive_scale = bool(adaptive_scale)
        self.asymmetric_scaling = bool(asymmetric_scaling)
        self.exponential_base = exponential_base
        self.use_exponential_scaling = exponential_base is not None
        if self.use_exponential_scaling:
            assert self.exponential_base > 0.0, "exponential_base must be > 0"  # type: ignore[operator]
            self.exponential_base = float(exponential_base)  # type: ignore[arg-type]

        n = self.n_arms
        self.n_submitted = np.zeros(n, dtype=np.float64)
        self.n_completed = np.zeros(n, dtype=np.float64)
        if self.use_exponential_scaling:
            self.s = np.full(n, -np.inf, dtype=np.float64)
        else:
            self.s = np.zeros(n, dtype=np.float64)
        self.divs = np.zeros(n, dtype=np.float64)

        if self.asymmetric_scaling:
            if self.use_exponential_scaling:
                self._obs_max: float = -np.inf
                self._obs_min: float = -np.inf
            else:
                self._obs_min = 0.0
                self._obs_max = 0.0
        else:
            self._obs_max = -np.inf
            self._obs_min = np.inf

    # ------------------------------------------------------------------
    # Properties / helpers
    # ------------------------------------------------------------------

    @property
    def n(self) -> np.ndarray:
        return np.maximum(self.n_submitted, self.n_completed)

    def _mean(self) -> np.ndarray:
        denom = np.maximum(self.divs, 1e-7)
        if self.use_exponential_scaling:
            return self.s - np.log(denom)
        return self.s / denom

    def _update_obs_range(self, r: float) -> None:
        if r > self._obs_max:
            self._obs_max = r
        if not (self.use_exponential_scaling and self.asymmetric_scaling):
            if r < self._obs_min:
                self._obs_min = r

    def _have_obs_range(self) -> bool:
        if self.use_exponential_scaling and self.asymmetric_scaling:
            return bool(np.isfinite(self._obs_max))
        return (
            bool(np.isfinite(self._obs_min))
            and bool(np.isfinite(self._obs_max))
            and (self._obs_max - self._obs_min) > 0.0
        )

    def _impute_worst_reward(self) -> float:
        if self.asymmetric_scaling:
            return -np.inf if self.use_exponential_scaling else 0.0
        seen = self.n > 0
        if not np.any(seen):
            return 0.0
        denom = np.maximum(self.divs[seen], 1e-7)
        mu = self.s[seen] / denom
        mu_min = float(mu.min())
        if mu.size >= 2:
            s = float(mu.std(ddof=1))
            sigma = 1.0 if (not np.isfinite(s) or s <= 0.0) else s
        else:
            sigma = 1.0
        return mu_min - sigma

    def _normalized_means(self, idx: np.ndarray) -> np.ndarray:
        if not self.adaptive_scale or not self._have_obs_range():
            m = self._mean()[idx]
            return np.exp(m) if self.use_exponential_scaling else m
        if self.use_exponential_scaling and self.asymmetric_scaling:
            mlog = self._mean()[idx]
            return np.exp(mlog - self._obs_max)
        if self.use_exponential_scaling:
            means_log = self._mean()[idx]
            rng_log = _logdiffexp(self._obs_max, self._obs_min)
            num_log = _logdiffexp(means_log, self._obs_min)
            return np.exp(num_log - rng_log)
        means = self._mean()[idx]
        rng = max(self._obs_max - self._obs_min, 1e-9)
        return (means - self._obs_min) / rng

    # ------------------------------------------------------------------
    # Core bandit operations
    # ------------------------------------------------------------------

    def update_submitted(self, arm: Arm) -> float:
        """Record that arm was submitted (in-flight tracking)."""
        i = self._resolve_arm(arm)
        self.n_submitted[i] += 1.0
        return float(self.n[i])

    def update(
        self,
        arm: Arm,
        reward: Optional[float],
        baseline: Optional[float] = None,
    ) -> tuple[float, float]:
        """Record observed reward for arm.

        Parameters
        ----------
        arm:
            Arm index or name.
        reward:
            Observed reward, or ``None`` to impute the worst observed reward.
        baseline:
            Parent/reference score used to shift the reward.  Required when
            ``shift_by_baseline`` or ``shift_by_parent`` is True.

        Returns
        -------
        (shifted_reward, effective_baseline)
        """
        i = self._resolve_arm(arm)
        is_real = reward is not None
        r_raw = float(reward) if is_real else self._impute_worst_reward()

        if self._shift_by_parent and self._shift_by_baseline:
            baseline = (
                self._baseline if baseline is None else max(baseline, self._baseline)
            )
        elif self._shift_by_baseline:
            baseline = self._baseline
        elif not self._shift_by_parent:
            baseline = 0.0
        if baseline is None:
            raise ValueError("baseline required when shift_by_parent is active")

        r = r_raw - baseline  # type: ignore[operator]

        if self.asymmetric_scaling:
            r = max(r, 0.0)

        self.divs[i] += 1.0
        self.n_completed[i] += 1.0

        if self.use_exponential_scaling and self.asymmetric_scaling:
            z = r * self.exponential_base  # type: ignore[operator]
            contrib_log = _logexpm1(z) if self._shift_by_baseline else z
            self.s[i] = _logadd(self.s[i], contrib_log)
            if self.adaptive_scale and is_real:
                self._update_obs_range(float(contrib_log))
        else:
            self.s[i] += r
            if self.adaptive_scale and is_real:
                self._update_obs_range(r)

        self._maybe_decay()
        return r, baseline  # type: ignore[return-value]

    def posterior(
        self,
        subset: Subset = None,
        samples: Optional[int] = None,
        **kwargs: Any,
    ) -> np.ndarray:
        """Compute selection probabilities for each arm.

        Returns a probability vector of length ``n_arms``.
        """
        idx = self._resolve_subset(subset)
        if samples is None or int(samples) <= 1:
            n_sub = self.n[idx]
            probs = np.zeros(self._n_arms, dtype=np.float64)

            if idx.size == 0:
                return probs

            if np.all(n_sub <= 0.0):
                p = np.ones(idx.size) / idx.size
                probs[idx] = p
                return probs

            unseen = np.where(n_sub <= 0.0)[0]
            if unseen.size > 0:
                p = np.ones(unseen.size) / unseen.size
                probs[idx[unseen]] = p
                return probs

            t = float(self.n.sum())
            base = self._normalized_means(idx)
            num = 2.0 * np.log(max(t, 2.0))
            base_bonus = np.sqrt(num / n_sub)
            scores = base + self.c * base_bonus

            winners = np.where(scores == scores.max())[0]
            rem = idx.size - winners.size
            p_sub = np.zeros(idx.size, dtype=np.float64)
            if rem == 0:
                p_sub[:] = 1.0 / idx.size
            else:
                p_sub[winners] = (1.0 - self.epsilon) / winners.size
                mask = np.ones(idx.size, dtype=bool)
                mask[winners] = False
                p_sub[mask] = self.epsilon / rem
            probs[idx] = p_sub
            return probs

        return self._posterior_batch(idx, int(samples))

    def _posterior_batch(self, idx: np.ndarray, k: int) -> np.ndarray:
        A = idx.size
        probs = np.zeros(self._n_arms, dtype=np.float64)
        if k <= 0 or A == 0:
            return probs

        n_sub = self.n[idx].astype(np.float64)
        v = np.zeros(A, dtype=np.int64)

        if np.all(n_sub <= 0.0):
            probs[idx] = np.ones(A, dtype=np.float64) / A
            return probs

        unseen = np.where(n_sub <= 0.0)[0]
        if unseen.size > 0:
            if k >= unseen.size:
                v[unseen] += 1
                k -= unseen.size
            else:
                sel = self.rng.choice(unseen, size=int(k), replace=False)
                v[sel] += 1
                k = 0
            if k == 0:
                alloc = v.astype(np.float64)
                probs[idx] = alloc / alloc.sum()
                return probs

        base = self._normalized_means(idx)
        t0 = float(self.n.sum())
        step = int(v.sum()) + 1

        while k > 0:
            num = 2.0 * np.log(max(t0 + step, 2.0))
            den = np.maximum(n_sub + v, 1.0)
            base_bonus = np.sqrt(num / den)
            scores = base + self.c * base_bonus

            winners = np.where(scores == scores.max())[0]
            p = np.zeros(A, dtype=np.float64)
            if winners.size == A:
                p[:] = 1.0 / A
            else:
                p[winners] = (1.0 - self.epsilon) / winners.size
                mask = np.ones(A, dtype=bool)
                mask[winners] = False
                others = np.where(mask)[0]
                if others.size > 0:
                    p[others] = self.epsilon / others.size

            i = int(self.rng.choice(A, p=p))
            v[i] += 1
            step += 1
            k -= 1

        alloc = v.astype(np.float64)
        probs[idx] = alloc / alloc.sum()
        return probs

    def select_llm(
        self,
        subset: Subset = None,
        samples: Optional[int] = None,
        **kwargs: Any,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Sample an arm and return (one_hot, probabilities)."""
        probabilities = self.posterior(subset=subset, samples=samples, **kwargs)
        one_hot = np.zeros(self.n_arms, dtype=np.float64)
        chosen = self.rng.choice(self.n_arms, size=1, p=probabilities)
        one_hot[chosen[0]] = 1.0
        return one_hot, probabilities

    def decay(self, factor: float) -> None:
        if not (0.0 < factor <= 1.0):
            raise ValueError("factor must be in (0, 1]")
        self.divs = self.divs * factor
        one_minus_factor = 1.0 - factor
        if self.use_exponential_scaling and self.asymmetric_scaling:
            s = self.s
            with np.errstate(divide="ignore", invalid="ignore"):
                log1p_term = np.where(
                    s > 0.0,
                    s + np.log(one_minus_factor + np.exp(-s)),
                    np.log1p(one_minus_factor * np.exp(s)),
                )
                self.s = s + np.log(factor) - log1p_term
            if self.adaptive_scale and np.isfinite(self._obs_max):
                means_log = self._mean()
                mmax = float(np.max(means_log))
                om = self._obs_max
                log1p_obs = (
                    om + np.log(one_minus_factor + np.exp(-om))
                    if om > 0.0
                    else np.log1p(one_minus_factor * np.exp(om))
                )
                obs_new = om + np.log(factor) - log1p_obs
                self._obs_max = max(obs_new, mmax)
        else:
            self.s = self.s * factor
            if self.adaptive_scale and self._have_obs_range():
                means = self._mean()
                self._obs_max = max(
                    self._obs_max * factor + one_minus_factor * float(np.max(means)),
                    float(np.max(means)),
                )
                self._obs_min = min(
                    self._obs_min * factor + one_minus_factor * float(np.min(means)),
                    float(np.min(means)),
                )

    # ------------------------------------------------------------------
    # Serialisation (JSON instead of pickle)
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Return a JSON-serialisable state dict."""
        return {
            "n_submitted": self.n_submitted.tolist(),
            "n_completed": self.n_completed.tolist(),
            "s": self.s.tolist(),
            "divs": self.divs.tolist(),
            "baseline": self._baseline,
            "obs_max": float(self._obs_max),
            "obs_min": float(self._obs_min),
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        """Restore state from a dict produced by get_state()."""
        self.n_submitted = np.array(state["n_submitted"], dtype=np.float64)
        self.n_completed = np.array(state["n_completed"], dtype=np.float64)
        self.s = np.array(state["s"], dtype=np.float64)
        self.divs = np.array(state["divs"], dtype=np.float64)
        self._baseline = float(state["baseline"])
        self._obs_max = float(state["obs_max"])
        self._obs_min = float(state["obs_min"])


# ---------------------------------------------------------------------------
# EnsembleBandit — thin wrapper with named arms
# ---------------------------------------------------------------------------


class EnsembleBandit(AsymmetricUCB):
    """AsymmetricUCB pre-configured with claude-evolve arm names.

    This is the primary public API for the ensemble bandit.

    Parameters
    ----------
    arm_names:
        List of ``"model/effort"`` strings.  Defaults to
        :data:`DEFAULT_ARM_NAMES`.
    """

    def __init__(
        self,
        arm_names: Optional[List[str]] = None,
        seed: Optional[int] = None,
        exploration_coef: float = 1.0,
        epsilon: float = 0.2,
        auto_decay: float = 0.95,
        shift_by_baseline: bool = True,
        shift_by_parent: bool = True,
        adaptive_scale: bool = True,
        asymmetric_scaling: bool = True,
        exponential_base: Optional[float] = 1.0,
    ) -> None:
        super().__init__(
            arm_names=arm_names if arm_names is not None else DEFAULT_ARM_NAMES,
            seed=seed,
            exploration_coef=exploration_coef,
            epsilon=epsilon,
            auto_decay=auto_decay,
            shift_by_baseline=shift_by_baseline,
            shift_by_parent=shift_by_parent,
            adaptive_scale=adaptive_scale,
            asymmetric_scaling=asymmetric_scaling,
            exponential_base=exponential_base,
        )

    def select_arm(self, subset: Subset = None) -> str:
        """Select an arm and return its name (or index string if unnamed).

        Parameters
        ----------
        subset:
            Optional list of arm names/indices to restrict selection.

        Returns
        -------
        str
            The chosen arm name, e.g. ``"sonnet/high"``.
        """
        one_hot, _ = self.select_llm(subset=subset)
        idx = int(np.argmax(one_hot))
        if self._arm_names is not None:
            return self._arm_names[idx]
        return str(idx)

    def arm_names(self) -> List[str]:
        """Return the list of arm names."""
        return list(self._arm_names) if self._arm_names is not None else [
            str(i) for i in range(self._n_arms)
        ]
