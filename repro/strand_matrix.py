#!/usr/bin/env python
"""Strand-attribution matrix across scenario regimes (paper-data emitter).

Promoted from the 2026-06-10 investigation scratch driver (plan report §3.4).
Runs the DES on a grid of synthetic scenarios, classifies every strand through
the offline feasibility oracle, maps each to its typed ReasonCode
(epure_arena.reasons), and emits a run_id folder under runs/ with
metrics.parquet + a paper-ready table.md.

Usage (node-engine venv, repo root):
    python repro/strand_matrix.py matrix            # 18-cell grid @40k
    python repro/strand_matrix.py cell --cadence hourly --capacity tight
    python repro/strand_matrix.py scale --cadence hourly --capacity tight \
        --demands 100000,1000000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

from epure_arena import _engine

from epure_arena.harness.feasibility_oracle import classify_engine
from epure_arena.harness.runlake import emit_run, markdown_table
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.reasons import attribution_table

_ATU_PER_HOUR = 3_600_000
_REASON = {0: "topo", 1: "sched", 2: "cap", 3: "deadline", 4: "lane_out", 5: "node_out"}
_SITE = {0: "RouterNone", 1: "ChosenLaneNoSlot", 2: "QueueDeath",
         3: "TopologyPrecheck", 4: "NodeOutageArrival", 5: "DeadlineFinalize"}
# Reasons the oracle classifies (need a schedule-supply verdict); topo and
# outage strands are attributed directly by epure_arena.reasons.
_ORACLE_REASONS = (1, 2, 3)


def run_cell(topology: str, nodes: int, density: str, capacity: str, cadence: str,
             demand: int, horizon: int, seed: int = 42, pattern: str = "diurnal",
             enforce_capacity: bool = True) -> dict:
    """Run one scenario cell; return metrics row incl. the attribution table."""
    spec = ScenarioSpec(
        topology=topology, n_nodes=nodes, density=density, capacity=capacity,
        demand_pattern=pattern, schedule_cadence=cadence, priority_mix="mixed",
        n_demand_events=demand, horizon_hours=horizon, seed=seed,
    )
    t0 = time.perf_counter()
    bundle = generate_scenario(spec)
    t_gen = time.perf_counter() - t0

    t0 = time.perf_counter()
    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        _ATU_PER_HOUR, "time_aware",
    )
    eng.set_enforce_capacity(enforce_capacity)
    eng.run(horizon * _ATU_PER_HOUR)
    t_des = time.perf_counter() - t0

    kpis = json.loads(eng.get_kpis())
    sr = eng.strand_records()
    n = len(sr["reason"])
    by_reason = Counter(sr["reason"])
    site_of_sched = Counter(
        _SITE.get(sr["site"][i], sr["site"][i]) for i in range(n) if sr["reason"][i] == 1
    )

    t0 = time.perf_counter()
    report = classify_engine(eng, reasons=_ORACLE_REASONS)
    t_oracle = time.perf_counter() - t0

    rows = [(v.reason, v.verdict, v.genuine_kind) for v in report.verdicts]
    rows += [(int(sr["reason"][i]), None, None) for i in range(n)
             if sr["reason"][i] not in _ORACLE_REASONS]
    attribution = attribution_table(rows)

    sched_verdicts = [v for v in report.verdicts if v.reason == 1]
    sc = Counter(v.verdict for v in sched_verdicts)
    sk = Counter(v.genuine_kind for v in sched_verdicts if v.genuine_kind)

    return {
        "topology": topology, "nodes": nodes, "density": density, "capacity": capacity,
        "cadence": cadence, "pattern": pattern, "demand": demand, "horizon": horizon,
        "seed": seed, "enforce_capacity": enforce_capacity,
        "injected": kpis["total_injected"], "delivered": kpis["total_delivered"],
        "stranded": kpis["stranded_flow_units"],
        "strand_pct": round(100 * kpis["stranded_flow_units"] / max(kpis["total_injected"], 1), 1),
        "by_reason": {_REASON.get(r, r): c for r, c in sorted(by_reason.items())},
        "site_of_sched": dict(site_of_sched),
        "sched_router_induced": sc["FEASIBLE_ON_TIME"],
        "sched_deadline_bound": sc["FEASIBLE_LATE"],
        "sched_genuine_cliff": sk["CLIFF"],
        "sched_genuine_structural": sk["STRUCTURAL"],
        "attribution": attribution,
        "schema_fingerprint": eng.get_schema_fingerprint(),
        "t_gen_s": round(t_gen, 3), "t_des_s": round(t_des, 3),
        "t_oracle_s": round(t_oracle, 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("matrix", "cell", "scale"))
    ap.add_argument("--topology", default="backbone")
    ap.add_argument("--nodes", type=int, default=200)
    ap.add_argument("--density", default="moderate")
    ap.add_argument("--capacity", default="loose")
    ap.add_argument("--cadence", default="every_4h")
    ap.add_argument("--pattern", default="diurnal")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--demands", default="100000,300000,1000000",
                    help="scale mode: comma-separated demand ladder")
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-capacity", action="store_true",
                    help="run with enforce_capacity(False) (ablation)")
    ap.add_argument("--no-emit", action="store_true", help="skip run-folder emission")
    args = ap.parse_args()

    cells: list[dict] = []
    if args.mode == "matrix":
        for cadence in ("hourly", "every_4h", "mixed"):
            for capacity in ("tight", "loose"):
                for topology, density in (("backbone", "moderate"),
                                          ("mesh", "moderate"), ("grid", "sparse")):
                    cells.append(dict(topology=topology, nodes=args.nodes,
                                      density=density, capacity=capacity,
                                      cadence=cadence, demand=args.demand,
                                      horizon=args.horizon, seed=args.seed))
    elif args.mode == "cell":
        cells.append(dict(topology=args.topology, nodes=args.nodes, density=args.density,
                          capacity=args.capacity, cadence=args.cadence,
                          demand=args.demand, horizon=args.horizon, seed=args.seed,
                          pattern=args.pattern,
                          enforce_capacity=not args.no_capacity))
    else:  # scale
        for d in (int(x) for x in args.demands.split(",")):
            cells.append(dict(topology=args.topology, nodes=args.nodes,
                              density=args.density, capacity=args.capacity,
                              cadence=args.cadence, demand=d,
                              horizon=args.horizon, seed=args.seed))

    rows = []
    for c in cells:
        row = run_cell(**c)
        rows.append(row)
        print(json.dumps(row))
        sys.stdout.flush()

    if not args.no_emit and rows:
        folder = emit_run(
            name=f"strand-{args.mode}",
            config={"mode": args.mode, "cells": cells},
            seed=args.seed,
            schema_fingerprint=rows[0]["schema_fingerprint"],
            metrics_rows=rows,
            repro_cmd="python repro/strand_matrix.py " + " ".join(sys.argv[1:]),
            extra_files={"attribution.md": markdown_table(
                [{"cell": f"{r['topology']}/{r['cadence']}/{r['capacity']}/d={r['demand']}",
                  **r["attribution"]} for r in rows])},
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
