"""BudgetedRecourseEnv + Oracle invariants (fast: one minimal suite).

Contract guards: action/budget legality, episode termination, the telescoping
identity (Σ rewards == V(S)), the certified-regime gate (§8.4), and the
grader's ceiling/regret relations against the extracted set-oracles.
"""

from __future__ import annotations

import pytest

from epure_arena.env import (
    DEFER,
    INTERVENE,
    SOLVED_REGIME,
    BudgetedRecourseEnv,
    Oracle,
    oracle_set,
    oracle_set_greedy,
)

_SPEC = dict(world="backbone", seed=42, severity="moderate", cost=0.0, budget=2)


@pytest.fixture(scope="module")
def env():
    # minimal: 2k flow units, 4 epochs — built once, reused (suite cached).
    e = BudgetedRecourseEnv(demand=2000, events_per_kind=1)
    e.reset(**_SPEC)
    return e


def test_reset_obs_shape(env):
    obs = env.reset(**_SPEC)
    assert obs["epoch"] == 0
    assert obs["n_epochs"] == env.suite.n_epochs
    assert obs["remaining_budget"] == 2
    assert obs["intervene_allowed"] is True
    for key in ("severity_score", "value_estimate_a", "value_estimate_b", "kind"):
        assert key in obs


def test_defer_only_episode_terminates_with_zero_value(env):
    env.reset(**_SPEC)
    done = False
    total = 0.0
    steps = 0
    while not done:
        _obs, r, done = env.step(DEFER)
        total += r
        steps += 1
    assert steps == env.suite.n_epochs
    assert env.selected_set == frozenset()
    assert total == 0.0
    assert env.cumulative_reward == pytest.approx(env.realized_value())


def test_cumulative_reward_equals_V_of_selected(env):
    # Intervene whenever budget allows; cumulative reward must telescope to
    # V(S) recomputed independently from the suite.
    obs = env.reset(**_SPEC)
    done = False
    while not done:
        action = INTERVENE if obs["intervene_allowed"] else DEFER
        obs, _r, done = env.step(action)
    assert env.cumulative_reward == pytest.approx(env.realized_value())
    assert env.realized_value() == pytest.approx(
        env.suite.net(env.selected_set, _SPEC["cost"]))


def test_intervene_beyond_budget_raises(env):
    env.reset(world="backbone", seed=42, severity="moderate", cost=0.0, budget=1)
    env.step(INTERVENE)  # spends the only budget unit
    # next epoch: intervene must be illegal
    with pytest.raises(ValueError):
        env.step(INTERVENE)


def test_step_after_done_raises(env):
    env.reset(**_SPEC)
    done = False
    while not done:
        _obs, _r, done = env.step(DEFER)
    with pytest.raises(RuntimeError):
        env.step(DEFER)


def test_open_regime_is_not_implemented():
    with pytest.raises(NotImplementedError):
        BudgetedRecourseEnv(recourse="partial")
    # the implemented regime is the solved one
    assert BudgetedRecourseEnv().recourse == SOLVED_REGIME


def test_oracle_ceiling_and_regret(env):
    suite = env.suite
    grader = Oracle(suite, 0.0, 2, exact=True)
    s_star, v_star = grader.best_set()
    # matches the extracted exhaustive oracle
    assert (s_star, v_star) == oracle_set(suite, 0.0, 2)
    # regret of the oracle's own set is zero
    assert grader.budgeted_regret(s_star) == pytest.approx(0.0)
    # never-set regret equals the ceiling; ceiling is non-negative
    assert v_star >= 0.0
    assert grader.budgeted_regret(frozenset()) == pytest.approx(v_star)
    # greedy is budget-feasible and non-negative-regret in this regime
    s_greedy, _ = oracle_set_greedy(suite, 0.0, 2)
    assert grader.budgeted_regret(s_greedy) >= -1e-9
