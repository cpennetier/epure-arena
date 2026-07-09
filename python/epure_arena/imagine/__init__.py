"""Imagine — consequence estimation for candidates (the canonical role).

Imagine answers "if we did this, what future would it create?" cheaply and
honestly. The reference estimator is the certified executor's own dry run
(exact feasibility, omitting only the reservation externality — the
decomposition every estimator is graded against). A zero stub anchors the
no-information baseline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ImagineBase(ABC):
    @abstractmethod
    def estimate(self, features: Any, candidate: Any) -> float:
        """Estimated value-of-acting (Δ realized on-time) for the candidate."""
        raise NotImplementedError


class ZeroImagine(ImagineBase):
    """The no-information stub (Δ̂ = 0 for every candidate)."""

    name = "zero_stub"

    def estimate(self, features: Any, candidate: Any) -> float:  # noqa: ARG002
        return 0.0
