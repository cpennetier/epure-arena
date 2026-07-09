"""Constrained-selection world invariants (fast: one minimal suite).

Guards the budgeted cost/budget layer: determinism + byte-identity,
mass-closure (asserted inside every rollout), greedy ≤ exact oracle, and
the baseline/oracle selection contracts. Kept to a tiny suite so it runs in
the standing gate.
"""

from __future__ import annotations

import pytest

from epure_arena.env import world as cw


@pytest.fixture(scope="module")
def suite():
    # minimal: 2k flow units, 4 epochs — a few seconds.
    return cw.build_suite("backbone", 42, "moderate", 6, demand=2000,
                          events_per_kind=1)


def test_deterministic_byte_identical(suite):
    again = cw.build_suite("backbone", 42, "moderate", 6, demand=2000,
                           events_per_kind=1)
    assert again.digest_never == suite.digest_never
    assert again.delta == suite.delta
    assert again.v_never == suite.v_never


def test_per_epoch_delta_is_paired(suite):
    # Δ_i is exactly v_of({i}) − v_never (paired-rollout definition).
    for i in range(suite.n_epochs):
        assert suite.delta[i] == suite.v_of({i}) - suite.v_never


def test_greedy_oracle_never_exceeds_exact(suite):
    for B in (1, 2, suite.n_epochs):
        _, exact = cw.oracle_set(suite, 0.0, B)
        _, greedy = cw.oracle_set_greedy(suite, 0.0, B)
        assert greedy <= exact + 1e-9
        assert exact >= 0.0  # empty set is always feasible


def test_oracle_respects_budget(suite):
    s, _ = cw.oracle_set(suite, 0.0, 2)
    assert len(s) <= 2


def test_baselines_present_and_budgeted(suite):
    base = cw.baselines(suite, cost=0.0, budget=2)
    assert set(base) == {"never", "always", "severity", "value_A",
                         "value_B", "oracle_rank"}
    assert base["never"] == (frozenset(), 0.0)
    for name, (sel, _net) in base.items():
        assert len(sel) <= 2, f"{name} exceeded budget"


def test_cost_reduces_net(suite):
    # adding a flat cost cannot increase any set's net value
    s_all = set(range(suite.n_epochs))
    assert suite.net(s_all, 0.0) >= suite.net(s_all, 10.0)


def test_per_kind_cost_vector(suite):
    # per-epoch cost vector is summed over the selected set
    cost = [5.0] * suite.n_epochs
    assert suite.cost_of({0, 1}, cost) == pytest.approx(10.0)
    assert suite.cost_of({0, 1}, 5.0) == pytest.approx(10.0)
