"""Oracle — the certified ceiling and budgeted regret grader.

Wraps the exact / greedy set-oracles into the object an outside engineer uses
to grade any policy: ``best_set()`` is the (cost, budget)-constrained argmax
``V(S*)`` (the ceiling), and ``budgeted_regret(policy_set)`` is the gap a
policy leaves on the table, ``V(S*) − V(S_policy)``.
"""

from __future__ import annotations

from typing import Any, Optional

from epure_arena.env.world import Suite, oracle_set, oracle_set_greedy


class Oracle:
    """Certified best set + budgeted regret for one suite under (cost, budget).

    Args:
        suite: the measured suite.
        cost: scalar or per-epoch cost of an intervention.
        budget: max |S| (``None`` = uncapped).
        exact: if ``True`` (default) ``best_set`` is the exhaustive argmax
            (``oracle_set``); if ``False`` it is the greedy-marginal ceiling
            (``oracle_set_greedy``) — use for n beyond ~10 epochs. In the
            certified-full-recourse regime greedy is provably optimal, so the
            two agree there.
    """

    def __init__(self, suite: Suite, cost: Any, budget: Optional[int], *,
                 exact: bool = True) -> None:
        self.suite = suite
        self.cost = cost
        self.budget = budget
        self.exact = exact
        self._best: Optional[tuple[frozenset, float]] = None

    def best_set(self) -> tuple[frozenset, float]:
        """``(S*, V(S*))`` — the (cost, budget)-constrained ceiling (cached)."""
        if self._best is None:
            fn = oracle_set if self.exact else oracle_set_greedy
            self._best = fn(self.suite, self.cost, self.budget)
        return self._best

    def budgeted_regret(self, policy_set) -> float:
        """``V(S*) − V(policy_set)`` — non-negative for any budget-feasible set.

        The empty set is always feasible and scores 0, so ``V(S*) ≥ 0``; a
        budget-feasible policy therefore has regret in ``[0, V(S*)]``. A set
        that exceeds the budget is outside the oracle's feasible region and
        may yield a negative "regret" — that is a budget violation, not a
        better policy.
        """
        _, best_v = self.best_set()
        return best_v - self.suite.net(policy_set, self.cost)
