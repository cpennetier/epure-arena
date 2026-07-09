"""Constrained-selection environment: the budgeted (cost, budget) world.

A thin, domain-neutral layer over the certifier: it measures a *suite* (one
disruption scenario and its epochs) through the pinned live loop, exposes the
true paired-rollout value ``v_of(S)`` and the net estimand
``V(S) = [v_of(S) − v_never] − cost·|S|``, and provides the exact and greedy
oracles plus the ranking baselines used to grade any policy by budgeted
regret ``V(S*) − V(S_policy)``.

Main exports:
    :class:`Suite`, :func:`build_suite`, :func:`oracle_set`,
    :func:`oracle_set_greedy`, :func:`baselines` — the world + grading API.
    :func:`additivity_gap`, :func:`pair_competes`, :func:`disjoint_count`,
    :func:`ranking_additive_gap` — the Phase-1 diagnostics.
    :class:`BudgetedRecourseEnv` — the episodic intervene/defer environment.
    :class:`Oracle` — the certified ceiling + budgeted-regret grader.
"""

from __future__ import annotations

from epure_arena.env.grader import Oracle
from epure_arena.env.recourse import (
    ACTIONS,
    DEFER,
    INTERVENE,
    SOLVED_REGIME,
    BudgetedRecourseEnv,
)
from epure_arena.env.world import (
    KINDS,
    WORLDS,
    Suite,
    additivity_gap,
    baselines,
    build_suite,
    bundle_for,
    disjoint_count,
    oracle_set,
    oracle_set_greedy,
    pair_competes,
    ranking_additive_gap,
    realized_on_time,
)

__all__ = [
    "ACTIONS",
    "DEFER",
    "INTERVENE",
    "KINDS",
    "SOLVED_REGIME",
    "WORLDS",
    "BudgetedRecourseEnv",
    "Oracle",
    "Suite",
    "additivity_gap",
    "baselines",
    "build_suite",
    "bundle_for",
    "disjoint_count",
    "oracle_set",
    "oracle_set_greedy",
    "pair_competes",
    "ranking_additive_gap",
    "realized_on_time",
]
