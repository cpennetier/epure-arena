"""Consumer-contract test — the public symbol surface external code imports.

epure-arena is consumed by downstream applications that ``import epure_arena``
and pull specific symbols from ``epure_arena.env`` and ``epure_arena.pricing``.
Those symbols are a CONTRACT: renaming or removing one silently breaks a
consumer whose own code is NOT in this repo's CI (as a domain-name → neutral
rename in fact did, passing every other suite). This test freezes the surface
so any such change fails HERE, at the seam that owns the name.

A rename (e.g. ``STEEP_SCARCE`` → ``X``), or reintroducing a removed
domain-named anchor, MUST fail this test.
"""

from __future__ import annotations

import epure_arena
from epure_arena import env as tc_env
from epure_arena import pricing as tc_pricing

# ── frozen top-level API (epure_arena.__all__) ──────────────────────────
EXPECTED_TOPLEVEL = {
    "CapacityAwarePlanner", "PlanContext", "PlanRequest", "PlanResult",
    "Planner", "PlannerRegistry", "StaticPlanner", "default_registry",
}

# ── frozen env surface (epure_arena.env.__all__) ────────────────────────
EXPECTED_ENV = {
    "ACTIONS", "DEFER", "INTERVENE", "KINDS", "SOLVED_REGIME", "WORLDS",
    "BudgetedRecourseEnv", "Oracle", "Suite", "additivity_gap", "baselines",
    "build_suite", "bundle_for", "disjoint_count", "oracle_set",
    "oracle_set_greedy", "pair_competes", "ranking_additive_gap",
    "realized_on_time",
}

# ── frozen pricing consumer symbols (module has no __all__; pin the
#    contract set explicitly — the neutral anchors and the functional) ───
EXPECTED_PRICING = {
    "v_realized", "WaitCostProfile",
    "STEEP_SCARCE", "SLACK_ELASTIC", "DEMO_ZERO",
}

# The domain-named anchors neutralization removed; they must NOT return as
# aliases (that would break neutrality AND re-skew the consumer). Assembled
# from fragments so no banned token appears as a contiguous string in this
# file — the no-domain-vocabulary guard stays strictly ABSOLUTE (no per-file
# exemption); the test still asserts these exact names are absent.
FORBIDDEN_PRICING = {
    "".join(("GPU_", "SCHEDULING")),
    "".join(("LOGI", "STICS", "_MIDMILE")),
}


def test_toplevel_surface_is_frozen():
    assert set(epure_arena.__all__) == EXPECTED_TOPLEVEL
    for name in EXPECTED_TOPLEVEL:
        assert hasattr(epure_arena, name), f"missing top-level export: {name}"


def test_env_surface_is_frozen():
    assert set(tc_env.__all__) == EXPECTED_ENV
    for name in EXPECTED_ENV:
        assert hasattr(tc_env, name), f"missing env export: {name}"
    # importable exactly as a consumer writes it, with the action encoding pinned
    from epure_arena.env import (  # noqa: F401
        DEFER,
        INTERVENE,
        BudgetedRecourseEnv,
        Oracle,
    )
    assert int(DEFER) == 0
    assert int(INTERVENE) == 1


def test_pricing_consumer_symbols_present():
    for name in EXPECTED_PRICING:
        assert hasattr(tc_pricing, name), f"missing pricing symbol: {name}"
    from epure_arena.pricing import (  # noqa: F401
        DEMO_ZERO,
        SLACK_ELASTIC,
        STEEP_SCARCE,
        WaitCostProfile,
        v_realized,
    )
    # anchors are pinned by TYPE too, so a name kept but re-typed still fails
    for anchor in (STEEP_SCARCE, SLACK_ELASTIC, DEMO_ZERO):
        assert isinstance(anchor, WaitCostProfile)


def test_neutralized_names_do_not_return():
    for banned in FORBIDDEN_PRICING:
        assert not hasattr(tc_pricing, banned), (
            f"{banned} reappeared — neutralization regressed; "
            "consumers must import the STEEP_SCARCE / SLACK_ELASTIC names")
