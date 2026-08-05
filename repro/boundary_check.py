#!/usr/bin/env python
"""Check the installed Ephemeris Kernel boundary, end to end.

Epure Arena owns the arena layer — scenarios, gate, certification,
experiments — and owns no compiled code. The deterministic event runtime is
the ``ephemeris-kernel`` dependency, whose Rust engine is exposed as
``ephemeris._engine`` and re-exported here as ``epure_arena._engine``. That
boundary is the single most load-bearing assumption in the package, and it is
also the one a broken or mis-resolved install silently changes.

So this script asserts, against the *installed* dependency:

  1. ``ephemeris`` imports, and reports which file it resolved to;
  2. the engine Epure Arena uses is the one Ephemeris ships — not a stray
     local build, and not something vendored into this repository;
  3. a complete arena run crosses that boundary and closes its mass: scenario
     generation, the compiled engine, a disruption epoch, the gate, and the
     typed flow accounting, on a deliberately small world.

It is the first thing to run after a fresh install, and the first thing CI
runs after installing the package — a failure here localizes the fault to the
dependency boundary before any experiment has spent a minute.

Usage (repo root):
    python repro/boundary_check.py
"""

from __future__ import annotations

import sys

import ephemeris

from epure_arena import _engine
from epure_arena.harness.live_loop import run_live_loop
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario


def main() -> int:
    """Run the boundary check. Returns a process exit status."""
    print(f"ephemeris        : {ephemeris.__file__}")
    print(f"engine module    : {_engine.__name__}")
    print(f"engine binary    : {getattr(_engine, '__file__', '<builtin>')}")

    owner = _engine.__name__.split(".")[0]
    if owner != "ephemeris":
        print(f"FAIL: the engine is provided by {owner!r}, not by the "
              f"ephemeris-kernel dependency — Epure Arena must not carry a "
              f"compiled engine of its own", file=sys.stderr)
        return 1
    if not hasattr(_engine, "PyEngine"):
        print("FAIL: the installed engine exposes no PyEngine", file=sys.stderr)
        return 1

    bundle = generate_scenario(ScenarioSpec(
        topology="backbone", n_nodes=40, density="moderate", capacity="tight",
        demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=2_000, horizon_hours=24,
        seed=42,
    ))
    engine = _engine.PyEngine(bundle.node_ipc, bundle.lane_ipc,
                              bundle.schedule_ipc, bundle.demand_ipc)
    print(f"wire fingerprint : {engine.get_schema_fingerprint()}")

    disruptions = make_disruptions(
        bundle, kinds=("lane_outage",), severity="moderate",
        horizon_hours=24, seed=1)

    result = run_live_loop(
        bundle, disruptions, intervene_epochs={0}, horizon_hours=24)

    print(f"demand units     : {result.n_demand_total}")
    print(f"guaranteed_on_time: {result.guaranteed_on_time}")
    print(f"run digest       : {result.digest}")

    if not result.mass_closed:
        print(f"FAIL: mass did not close — flows sum to "
              f"{sum(result.flows.values())} against {result.n_demand_total} "
              f"demand units", file=sys.stderr)
        return 1
    if not result.epoch_traces:
        print("FAIL: the disruption produced no epoch", file=sys.stderr)
        return 1

    print("boundary OK: the arena runs end to end on the installed engine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
