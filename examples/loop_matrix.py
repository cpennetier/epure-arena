#!/usr/bin/env python
"""C2 Phase-1 evidence matrix: the thin live loop over the disruption suite.

For each (kind × severity × seed): paired same-seed runs with the two stub
gates (never-intervene / always-intervene) — the exact counterfactual Δ is
the difference of typed flows. Verifies per row: mass closure, byte-identity
(double run of the always leg), zero-divergence on all final commitments
(asserted inside the loop), and types every flow_unit. Emits a run folder.

Usage (node-engine venv, repo root):
    python examples/loop_matrix.py --seeds 7,11
    python examples/loop_matrix.py --kinds lane_outage --severities severe --seeds 7
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from epure_arena.gate import (
    AlwaysOffGate,
    AlwaysOnGate,
)

from epure_arena.harness.runlake import emit_run
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.harness.live_loop import run_live_loop


def realized_on_time(flows: dict[str, int]) -> int:
    return sum(v for k, v in flows.items() if k.endswith("_on_time"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kinds", default="lane_outage,node_outage,capacity_shock,demand_surge")
    ap.add_argument("--severities", default="mild,moderate,severe")
    ap.add_argument("--seeds", default="7,11")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--scenario-seed", type=int, default=42)
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    b = generate_scenario(ScenarioSpec(
        topology="backbone", n_nodes=200, density="moderate", capacity="tight",
        demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=args.demand, horizon_hours=24,
        seed=args.scenario_seed,
    ))

    rows = []
    fingerprint = None
    for kind in args.kinds.split(","):
        for severity in args.severities.split(","):
            for seed in (int(s) for s in args.seeds.split(",")):
                ds = make_disruptions(b, kinds=(kind,), severity=severity,
                                      horizon_hours=24, seed=seed)
                t0 = time.perf_counter()
                noop = run_live_loop(b, ds, gate=AlwaysOffGate(),
                                     horizon_hours=24)
                act = run_live_loop(b, ds, gate=AlwaysOnGate(),
                                    horizon_hours=24)
                act2 = run_live_loop(b, ds, gate=AlwaysOnGate(),
                                     horizon_hours=24)
                wall = time.perf_counter() - t0
                assert noop.mass_closed and act.mass_closed
                assert act.digest == act2.digest, "byte-identity violated"
                tr = act.epoch_traces[0]
                row = {
                    "kind": kind, "severity": severity, "disruption_seed": seed,
                    "n_demand": act.n_demand_total,
                    "n_cancelled": tr.n_cancelled,
                    "n_recommitted": tr.n_recommitted,
                    "n_refused_epoch": tr.n_refused_epoch,
                    "noop_guaranteed": noop.guaranteed_on_time,
                    "act_guaranteed": act.guaranteed_on_time,
                    "delta_guaranteed": (act.guaranteed_on_time
                                         - noop.guaranteed_on_time),
                    "noop_realized_on_time": realized_on_time(noop.flows),
                    "act_realized_on_time": realized_on_time(act.flows),
                    "delta_realized_on_time": (realized_on_time(act.flows)
                                               - realized_on_time(noop.flows)),
                    "noop_flows": noop.flows,
                    "act_flows": act.flows,
                    "byte_identical": act.digest == act2.digest,
                    "mass_closed": noop.mass_closed and act.mass_closed,
                    "wall_s": round(wall, 1),
                }
                rows.append(row)
                print(json.dumps(row))
                sys.stdout.flush()

    if not args.no_emit and rows:
        from epure_arena import _engine
        eng = _engine.PyEngine(b.node_ipc, b.lane_ipc, b.schedule_ipc,
                                  b.demand_ipc, 3_600_000, "time_aware")
        fingerprint = eng.get_schema_fingerprint()
        folder = emit_run(
            name="loop-matrix-phase1",
            config=vars(args),
            seed=args.scenario_seed,
            schema_fingerprint=fingerprint,
            metrics_rows=rows,
            repro_cmd="python examples/loop_matrix.py "
                      + " ".join(sys.argv[1:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
