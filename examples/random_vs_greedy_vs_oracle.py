"""Policy demo: random vs greedy-marginal vs oracle, graded by budgeted regret.

Instantiates the :class:`BudgetedRecourseEnv`, runs three policies through it,
and grades each against the certified ceiling with :class:`Oracle`. One
command:

    python examples/random_vs_greedy_vs_oracle.py
    python examples/random_vs_greedy_vs_oracle.py --world mesh --budget 3 --cost 2.0

What it shows — and what it deliberately does NOT claim:
    In this regime (certified full recourse) the realized value is
    submodular-like, so **greedy-marginal reaches zero regret**: it ties the
    exhaustive oracle. That is the point — the regime is *solved*, with no
    learning target. Random is the only policy that leaves regret on the
    table. The open regime where a learned policy could win is partial /
    uncertified recourse (§8.4), which this environment does not implement.
"""

from __future__ import annotations

import argparse
import random

from epure_arena.env import (
    INTERVENE,
    DEFER,
    BudgetedRecourseEnv,
    Oracle,
    oracle_set,
    oracle_set_greedy,
)


def run_episode(env, decide, spec) -> tuple[frozenset, float, float]:
    """Run one policy through the env; return (selected, cum_reward, V(S))."""
    obs = env.reset(**spec)
    done = False
    while not done:
        obs, _reward, done = env.step(decide(obs))
    return env.selected_set, env.cumulative_reward, env.realized_value()


def replay(target: frozenset):
    """Decide INTERVENE exactly on the epochs in ``target`` (budget-safe)."""
    def decide(obs):
        if obs["epoch"] in target and obs["intervene_allowed"]:
            return INTERVENE
        return DEFER
    return decide


def random_decider(rng: random.Random):
    def decide(obs):
        if obs["intervene_allowed"] and rng.random() < 0.5:
            return INTERVENE
        return DEFER
    return decide


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="backbone",
                    choices=["backbone", "mesh", "grid", "tree"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--severity", default="moderate")
    ap.add_argument("--cost", type=float, default=1.0)
    ap.add_argument("--budget", type=int, default=2)
    ap.add_argument("--demand", type=int, default=2000)
    ap.add_argument("--policy-seed", type=int, default=0)
    args = ap.parse_args()

    spec = dict(world=args.world, seed=args.seed, severity=args.severity,
                cost=args.cost, budget=args.budget)

    env = BudgetedRecourseEnv(demand=args.demand, events_per_kind=1)
    # First reset builds (and caches) the suite; reuse it for the oracles.
    env.reset(**spec)
    suite = env.suite
    grader = Oracle(suite, args.cost, args.budget, exact=True)

    s_oracle, _ = oracle_set(suite, args.cost, args.budget)
    s_greedy, _ = oracle_set_greedy(suite, args.cost, args.budget)
    rng = random.Random(args.policy_seed)

    policies = {
        "random": run_episode(env, random_decider(rng), spec),
        "greedy-marginal": run_episode(env, replay(s_greedy), spec),
        "oracle (exact)": run_episode(env, replay(s_oracle), spec),
    }

    print(f"\nBudgetedRecourseEnv — world={args.world} seed={args.seed} "
          f"severity={args.severity} cost={args.cost} budget={args.budget} "
          f"(n_epochs={suite.n_epochs})")
    print(f"regime: {env.recourse}  (greedy-marginal is optimal here — no learning target; "
          f"open regime = partial/uncertified recourse, §8.4)\n")
    print(f"{'policy':<18}{'|S|':>4}{'V(S)':>12}{'regret':>12}   env-check")
    print("-" * 62)
    for name, (sel, cum, vS) in policies.items():
        regret = grader.budgeted_regret(sel)
        ok = "ok" if abs(cum - vS) < 1e-9 else f"MISMATCH {cum} != {vS}"
        print(f"{name:<18}{len(sel):>4}{vS:>12.3f}{regret:>12.3f}   cum==V(S): {ok}")
    s_star, v_star = grader.best_set()
    print("-" * 62)
    print(f"oracle ceiling V(S*) = {v_star:.3f} at S* = {sorted(s_star)}\n")


if __name__ == "__main__":
    main()
