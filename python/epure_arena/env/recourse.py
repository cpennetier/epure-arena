"""BudgetedRecourseEnv — an episodic intervene/defer environment over a suite.

One episode is a single pass over a suite's epochs in loop order. At each
epoch the agent observes the never-leg decision context and chooses to
INTERVENE or DEFER, under a hard ``budget`` on the number of interventions and
a per-intervention ``cost``. The reward on INTERVENE is the **true realized
marginal net gain**, measured by paired rollout through the certifier:

    r_i = [v_of(S ∪ {i}) − v_of(S)] − cost_i = net(S∪{i}) − net(S)

DEFER yields 0. Cumulative episode reward telescopes exactly to the estimand

    V(S) = [v_of(S) − v_never] − cost·|S|.

The executor — the DES live loop behind ``v_of`` — is the **environment**,
queried for realized outcomes. It is never on any gradient path: rewards are
plain Python floats produced by replaying a deterministic simulation, not by
any differentiable component.

────────────────────────────────────────────────────────────────────────────
REGIME — read this before treating the env as an RL target
────────────────────────────────────────────────────────────────────────────
The realized value here comes from **certified full recourse** rollouts: the
live loop re-certifies every disrupted flow unit end to end. In that regime
``V`` is submodular-like and the **greedy-marginal policy is already
optimal** — C4 proved it; there is **no learning target**. This environment
is the *substrate*, not an open challenge: a learned policy cannot beat
greedy-marginal here, and presenting it as an open RL problem would be
dishonest.

The **open / interesting** regime is **partial / uncertified recourse**
(§8.4): re-certification limited by budget or observability, where the joint
value is no longer submodular and a learned policy *can* beat greedy. That
regime needs a different rollout and is deliberately **not implemented** —
``recourse`` other than ``"certified_full"`` raises ``NotImplementedError``
so the boundary is explicit in code, not buried in prose.
"""

from __future__ import annotations

from typing import Any, Optional

from epure_arena.env.world import Suite, build_suite

DEFER = 0
INTERVENE = 1
ACTIONS = (DEFER, INTERVENE)

# The only realized-value regime implemented. See the module docstring / §8.4.
SOLVED_REGIME = "certified_full"


class BudgetedRecourseEnv:
    """Episodic (cost, budget) intervene/defer environment over one suite.

    Construct with environment config; call :meth:`reset` with the world spec
    and the (cost, budget) to begin an episode. The suite (the expensive,
    engine-backed measurement) is cached across resets that share the same
    world/seed/severity, so sweeping (cost, budget) is cheap.

    Args:
        recourse: realized-value regime. Only ``"certified_full"`` (the
            solved, greedy-optimal regime) is implemented; anything else
            raises ``NotImplementedError`` (§8.4 — the open regime).
        demand: flow-unit count per built suite.
        events_per_kind: disruption events per kind per built suite.
    """

    action_space = ACTIONS

    def __init__(self, *, recourse: str = SOLVED_REGIME, demand: int = 2000,
                 events_per_kind: int = 1) -> None:
        if recourse != SOLVED_REGIME:
            raise NotImplementedError(_regime_message(recourse))
        self.recourse = recourse
        self.demand = demand
        self.events_per_kind = events_per_kind
        self._suite_cache: dict[tuple, Suite] = {}
        self.suite: Optional[Suite] = None
        self.cost: Any = 0.0
        self.budget: int = 0
        self._i: int = 0
        self._selected: set[int] = set()
        self._cum_reward: float = 0.0
        self._done: bool = True

    # ── suite construction (cached) ─────────────────────────────────────
    def _build_or_get(self, world: str, seed: int, severity: str,
                      disruption_seed: int) -> Suite:
        key = (world, seed, severity, disruption_seed, self.demand,
               self.events_per_kind)
        if key not in self._suite_cache:
            self._suite_cache[key] = build_suite(
                world, seed, severity, disruption_seed,
                demand=self.demand, events_per_kind=self.events_per_kind)
        return self._suite_cache[key]

    # ── episode lifecycle ───────────────────────────────────────────────
    def reset(self, world: str, seed: int, severity: str, cost: Any,
              budget: Optional[int], *,
              disruption_seed: Optional[int] = None) -> dict[str, Any]:
        """Begin an episode over ``world`` under (``cost``, ``budget``).

        ``budget`` of ``None`` means uncapped (|S| ≤ n_epochs). Returns the
        first observation.
        """
        ds = seed if disruption_seed is None else disruption_seed
        self.suite = self._build_or_get(world, seed, severity, ds)
        n = self.suite.n_epochs
        self.cost = cost
        self.budget = n if budget is None else min(int(budget), n)
        self._i = 0
        self._selected = set()
        self._cum_reward = 0.0
        self._done = n == 0
        return self._obs()

    def step(self, action: int) -> tuple[Optional[dict[str, Any]], float, bool]:
        """Apply ``action`` (DEFER or INTERVENE) to the current epoch.

        Returns ``(next_obs, reward, done)``. ``next_obs`` is ``None`` once the
        episode is done. INTERVENE when no budget remains is illegal — the
        observation flags it via ``intervene_allowed`` — and raises
        ``ValueError``.
        """
        if self._done or self.suite is None:
            raise RuntimeError("step() called on a finished/unstarted episode; call reset()")
        if action not in ACTIONS:
            raise ValueError(f"action must be DEFER(0) or INTERVENE(1), got {action!r}")

        i = self._i
        reward = 0.0
        if action == INTERVENE:
            if len(self._selected) >= self.budget:
                raise ValueError(
                    f"INTERVENE at epoch {i} exceeds budget {self.budget} "
                    "(obs.intervene_allowed was False)")
            prev = self.suite.net(self._selected, self.cost)
            self._selected.add(i)
            reward = self.suite.net(self._selected, self.cost) - prev

        self._cum_reward += reward
        self._i += 1
        self._done = self._i >= self.suite.n_epochs
        return (None if self._done else self._obs()), reward, self._done

    # ── observation + accessors ─────────────────────────────────────────
    def _obs(self) -> dict[str, Any]:
        s = self.suite
        i = self._i
        remaining = self.budget - len(self._selected)
        return {
            "epoch": i,
            "n_epochs": s.n_epochs,
            "remaining_budget": remaining,
            "n_selected": len(self._selected),
            "severity_score": s.sev_score[i],
            "value_estimate_a": s.dhat_a[i],   # Δ̂_A (dry-run value estimate)
            "value_estimate_b": s.dhat_b[i],   # Δ̂_B (n_recertifiable)
            "kind": s.kind[i],
            "intervene_allowed": remaining > 0,
        }

    @property
    def selected_set(self) -> frozenset:
        """The intervention set chosen so far this episode."""
        return frozenset(self._selected)

    @property
    def cumulative_reward(self) -> float:
        """Σ rewards this episode — equals V(selected_set) once done."""
        return self._cum_reward

    def realized_value(self) -> float:
        """V(S) of the episode's selected set, recomputed from the suite.

        Equals :attr:`cumulative_reward` at episode end (telescoping check).
        """
        if self.suite is None:
            raise RuntimeError("no episode; call reset()")
        return self.suite.net(self._selected, self.cost)


def _regime_message(recourse: str) -> str:
    return (
        f"recourse={recourse!r}: only {SOLVED_REGIME!r} is implemented — the "
        "solved, greedy-optimal regime (certified full recourse), which has no "
        "learning target. The partial / uncertified-recourse regime (§8.4), "
        "where a learned policy can beat greedy-marginal, requires a different "
        "rollout and is not provided here."
    )
