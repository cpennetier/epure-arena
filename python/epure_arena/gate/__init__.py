"""Gate — epoch admission and ranking (the canonical role).

The Gate decides whether acting at a decision epoch is worth it, and under
budget, WHICH epochs to act on. It does not generate candidates (Propose),
estimate consequences (Imagine), or execute (Optimize).

Reference implementations are deliberately simple; the seam is the
architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class GateBase(ABC):
    """Predicate over epoch features: act or hold."""

    @abstractmethod
    def should_invoke(self, features: Any) -> bool:
        raise NotImplementedError


class AlwaysOnGate(GateBase):
    """Always intervene."""

    def should_invoke(self, features: Any) -> bool:  # noqa: ARG002
        return True


class AlwaysOffGate(GateBase):
    """Never intervene (the control)."""

    def should_invoke(self, features: Any) -> bool:  # noqa: ARG002
        return False


class BudgetedRankingGate:
    """Offline top-k selection over scored epochs (the budgeted regime).

    Scores come from any per-epoch scoring function (severity proxies, value
    estimates from Imagine, or exact paired counterfactuals); ties break on
    epoch index — deterministic.
    """

    def __init__(self, k: int):
        if k < 0:
            raise ValueError("k must be >= 0")
        self.k = k

    def select(self, scores: Sequence[float]) -> frozenset[int]:
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return frozenset(order[: self.k])
