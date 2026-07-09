#!/usr/bin/env python
"""Step-6 confirm-or-kill benchmark: the planner at 1M flow_units (paper data).

Per (regime, seed): scenario generation, context build, planner phases
(setup / one-time destination profiles / commit loop), per-flow_unit search
percentiles, pinned-apply wall, DES execution wall, peak RSS, the
zero-divergence invariant, and a same-seed double-plan byte-identity check.
Saturation sweep = capacity regimes {loose, balanced, tight} (the testkit's
saturation knob) at the hourly cadence.

Pass gate (execution brief): planner wall ≤ 60 s at 1M in the UNSATURATED
(loose) regime, in Python. The Rust-port decision defers to these numbers.

Usage (node-engine venv, repo root):
    python repro/benchmark_1m.py --seeds 42
    python repro/benchmark_1m.py --demand 1000000 --seeds 42,43,44
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time

from epure_arena import _engine
import pyarrow.ipc as ipc

from epure_arena.harness.runlake import emit_run
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.fidelity import apply_plan, measure_fidelity, three_way_split
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest

_H = 3_600_000

REGIMES = [
    # (label, cadence, capacity) — A and B are the plan report's headline
    # regimes; balanced/loose complete the saturation sweep.
    ("B_hourly_tight", "hourly", "tight"),
    ("hourly_balanced", "hourly", "balanced"),
    ("hourly_loose", "hourly", "loose"),
    ("A_every4h_loose", "every_4h", "loose"),
]


def _trace_digest(trace) -> str:
    h = hashlib.sha256()
    for d in trace:
        h.update(
            f"{d.flow_unit_id}|{d.status}|{d.reason}|{d.planned_arrival_atu}|"
            f"{d.path_slots}".encode()
        )
    return h.hexdigest()


def _pct(xs: list[int], p: float) -> float:
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0.0


def parse_policy(spec: str):
    parts = spec.split(":")
    if parts[0] == "batched_repack":
        return (f"batched_repack_w{parts[1]}_b{parts[2]}",
                dict(policy="batched_repack", wave_size=int(parts[1]),
                     repack_budget=int(parts[2])))
    return (parts[0], dict(policy=parts[0]))


def run_regime(label: str, cadence: str, capacity: str, demand: int,
               seed: int, policy_spec: str = "slack_first") -> dict:
    spec = ScenarioSpec(
        topology="backbone", n_nodes=200, density="moderate",
        capacity=capacity, demand_pattern="diurnal", schedule_cadence=cadence,
        priority_mix="mixed", n_demand_events=demand, horizon_hours=24,
        seed=seed,
    )
    t0 = time.perf_counter()
    b = generate_scenario(spec)
    t_gen = time.perf_counter() - t0

    eng0 = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
    )
    t0 = time.perf_counter()
    ctx = build_plan_context(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, eng0.get_lane_id_to_csr(),
    )
    dem = ipc.open_stream(b.demand_ipc).read_all()
    cols = {k: dem[k].to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class",
    )}
    reqs = [
        PlanRequest(
            cols["flow_unit_id"][i], cols["origin_node_id"][i],
            cols["dest_node_id"][i], cols["appear_atu"][i], cols["due_atu"][i],
            cols["size_units"][i], cols["priority_class"][i],
        )
        for i in range(dem.num_rows)
    ]
    t_ctx = time.perf_counter() - t0

    # Plan (timed), then re-plan for the determinism digest.
    pol_label, pol_kw = parse_policy(policy_spec)
    planner = CapacityAwarePlanner(**pol_kw)
    t0 = time.perf_counter()
    results = planner.plan(reqs, ctx)
    t_plan = time.perf_counter() - t0
    digest1 = _trace_digest(planner.last_decision_trace)
    timings = dict(planner.last_timings)

    planner2 = CapacityAwarePlanner(**pol_kw)
    results2 = planner2.plan(reqs, ctx)
    digest2 = _trace_digest(planner2.last_decision_trace)
    del results2

    search_us = sorted(
        d.search_ns / 1000.0 for d in planner.last_decision_trace if d.search_ns
    )

    # Pinned execution end-to-end.
    eng = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
    )
    sizes = {r.flow_unit_id: r.size_units for r in reqs}
    t0 = time.perf_counter()
    n_pinned = apply_plan(eng, results, ctx,
                          decision_trace=planner.last_decision_trace,
                          sizes=sizes, pinned=True)
    t_apply = time.perf_counter() - t0
    eng.set_enforce_capacity(True)
    t0 = time.perf_counter()
    eng.run(24 * _H)
    t_des = time.perf_counter() - t0
    fid = measure_fidelity(planner.last_decision_trace, eng)
    k = json.loads(eng.get_kpis())
    split = three_way_split(planner.last_decision_trace, eng)

    planned = sum(1 for r in results if r.status == "planned")
    return {
        "regime": label, "cadence": cadence, "capacity": capacity,
        "demand": demand, "seed": seed, "policy": pol_label,
        "guaranteed_on_time": split["guaranteed_on_time"],
        "opportunistic_on_time": split["opportunistic_on_time"],
        "opportunistic_late": split["opportunistic_late"],
        "refused_stranded_total": sum(split["refused_stranded"].values()),
        "repack_stats": planner.last_repack_stats,
        "planned": planned,
        "reasons": planner.last_reason_counts,
        "t_gen_s": round(t_gen, 2), "t_ctx_s": round(t_ctx, 2),
        "t_plan_s": round(t_plan, 2), **timings,
        "search_us_p50": round(_pct(search_us, 0.50), 1),
        "search_us_p95": round(_pct(search_us, 0.95), 1),
        "search_us_p99": round(_pct(search_us, 0.99), 1),
        "us_per_flow_unit": round(1e6 * t_plan / max(len(reqs), 1), 1),
        "t_apply_s": round(t_apply, 2), "t_des_s": round(t_des, 2),
        "delivered": k["total_delivered"], "on_time": k["total_on_time"],
        "stranded": k["stranded_flow_units"],
        "zero_divergence": (fid.n_exact == fid.n_planned
                            and fid.n_planned_stranded == 0),
        "deterministic": digest1 == digest2,
        "trace_digest": digest1[:16],
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20), 0
        ),
        "schema_fingerprint": eng.get_schema_fingerprint(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demand", type=int, default=1_000_000)
    ap.add_argument("--seeds", default="42")
    ap.add_argument("--regimes", default=",".join(r[0] for r in REGIMES))
    ap.add_argument("--policy", default="slack_first",
                    help="slack_first | value_weighted | "
                         "batched_repack:<wave>:<budget>")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    wanted = set(args.regimes.split(","))
    seeds = [int(s) for s in args.seeds.split(",")]
    rows = []
    for label, cadence, capacity in REGIMES:
        if label not in wanted:
            continue
        for seed in seeds:
            row = run_regime(label, cadence, capacity, args.demand, seed,
                             policy_spec=args.policy)
            rows.append(row)
            print(json.dumps(row))
            sys.stdout.flush()

    if not args.no_emit and rows:
        fingerprint = rows[0].pop("schema_fingerprint")
        for r in rows[1:]:
            r.pop("schema_fingerprint", None)
        folder = emit_run(
            name=f"benchmark-1m-{args.policy.replace(':', '_')}",
            config=vars(args),
            seed=seeds[0],
            schema_fingerprint=fingerprint,
            metrics_rows=rows,
            repro_cmd="python repro/benchmark_1m.py "
                      + " ".join(sys.argv[1:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
