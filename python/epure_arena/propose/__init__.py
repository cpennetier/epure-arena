"""Propose — candidate intervention generation (the canonical role).

Propose generates; it never evaluates (Imagine), selects (Navigate), or
commits (Optimize + substrate). The baseline proposer re-certifies the full
affected scope — measured optimal under zero-cost full recourse — and the
scoped proposers exist as the seam for regimes where action carries cost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Candidate:
    """A proposed intervention: a re-certification scope."""

    name: str
    sids: tuple[int, ...]  # flow-unit ids to re-certify


class ProposeBase(ABC):
    @abstractmethod
    def propose(self, features: Any, affected: tuple[int, ...],
                surge: tuple[int, ...]) -> list[Candidate]:
        raise NotImplementedError


class SingleRecommitPropose(ProposeBase):
    """The baseline: one candidate — re-certify everything affected."""

    name = "single_recommit_all"

    def propose(self, features: Any, affected: tuple[int, ...],
                surge: tuple[int, ...]) -> list[Candidate]:
        return [Candidate(name="recommit_all",
                          sids=tuple(affected) + tuple(surge))]
