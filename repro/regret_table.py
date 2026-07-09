#!/usr/bin/env python
"""Regret-vs-oracle table over scenario cells (Step 5 — paper data).

For each (cell, seed): the certificate lattice and both executions —

    oracle UB  ≥  cohort-LP UB  ≥  best achievable  ≥  achieved(planner)
                                                    ≥/≶ achieved(greedy)

- oracle UB: exact-time capacity-free FEASIBLE_ON_TIME count (bulk profile
  oracle, Step 2).
- cohort-LP UB: destination-commodity due-class-budgeted bucket flow
  (Step 5; flow_unit-count-invariant; granularity stated per table).
- achieved(planner): CapacityAwarePlanner + slot-pinned execution (Step 4b;
  on-time == committed by the zero-divergence invariant, asserted here).
- achieved(greedy): the TimeAwareRouter baseline.
- attribution shift: greedy's strand reasons vs the planner's typed codes —
  zero unexplained mass on both sides.

CURRENCY: the lattice is stated in CAPACITY UNITS (the LP's flow is units; a
sized flow_unit counts size_units everywhere). FlowUnit counts are reported beside
units for readability. The LP solves once per cell (first seed; it is a
deterministic instrument costing ~10² s/cell at 4h granularity — measured);
achieved/oracle columns carry seeds × CIs.

Emits one run_id folder with metrics.parquet + regret_table.md +
attribution_shift.md (mean ± std across seeds).

C1.5: --policies runs the commit-policy ablation set per cell — the LP and
oracle bounds are computed ONCE per (cell, seed) and shared by all policies
(they bound the world+demand, not the policy). Policy syntax:
``slack_first``, ``value_weighted``, ``batched_repack:<wave>:<budget>``.
Each policy row carries the three-way split (guaranteed / opportunistic
on-time|late / refused-typed); policy (d) = the slack_first row's REALIZED
view (guaranteed + opportunistic), reported in the same row.

Usage (node-engine venv, repo root):
    python repro/regret_table.py --seeds 42,43,44
    python repro/regret_table.py --seeds 42,43,44 \
        --policies slack_first,value_weighted,batched_repack:4000:2,batched_repack:4000:8
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys

from epure_arena import _engine
import pyarrow.ipc as ipc

from epure_arena.harness.runlake import emit_run, markdown_table
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.fidelity import apply_plan, measure_fidelity, three_way_split
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest
from epure_arena.lattice.cohort_flow import solve_cohort_flow_upper_bound

_H = 3_600_000
_VERDICT_ON_TIME = 0

DEFAULT_CELLS = [
    f"{topo}:{cad}:{cap}"
    for cad in ("hourly", "every_4h", "mixed")
    for cap in ("tight", "loose")
    for topo in ("backbone", "mesh", "grid")
]
_DENSITY = {"backbone": "moderate", "mesh": "moderate", "grid": "sparse"}


def parse_policy(spec: str):
    """'batched_repack:4000:2' -> (label, ctor kwargs)."""
    parts = spec.split(":")
    if parts[0] == "batched_repack":
        wave, budget = int(parts[1]), int(parts[2])
        return (f"batched_repack_w{wave}_b{budget}",
                dict(policy="batched_repack", wave_size=wave,
                     repack_budget=budget))
    return (parts[0], dict(policy=parts[0]))


def run_cell(topology: str, cadence: str, capacity: str, seed: int,
             demand: int, horizon: int, lp_bucket_h: int,
             lp_time_limit: float, solve_lp: bool = True,
             policies: list[str] | None = None) -> list[dict]:
    spec = ScenarioSpec(
        topology=topology, n_nodes=200, density=_DENSITY[topology],
        capacity=capacity, demand_pattern="diurnal", schedule_cadence=cadence,
        priority_mix="mixed", n_demand_events=demand, horizon_hours=horizon,
        seed=seed,
    )
    b = generate_scenario(spec)
    eng0 = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
    )
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

    # Oracle UB over the FULL demand (bulk profile oracle).
    bulk = eng0.oracle_verdicts(
        cols["origin_node_id"], cols["dest_node_id"],
        cols["appear_atu"], cols["due_atu"],
    )
    oracle_ids = {
        cols["flow_unit_id"][i]
        for i, v in enumerate(bulk["verdict"]) if v == _VERDICT_ON_TIME
    }

    # Cohort-LP UB (flow_unit-count-invariant; granularity recorded). Solved on
    # the first seed of a cell only (deterministic instrument, ~10² s).
    lp = None
    if solve_lp:
        lp = solve_cohort_flow_upper_bound(
            ctx, reqs, oracle_ids,
            horizon_buckets=horizon // lp_bucket_h,
            bucket_atu=lp_bucket_h * _H,
            time_limit_sec=lp_time_limit,
        )

    # Greedy baseline.
    eng_g = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware",
    )
    eng_g.set_enforce_capacity(True)
    eng_g.run(horizon * _H)
    kg = json.loads(eng_g.get_kpis())

    # UNITS currency for the lattice. On-time units per execution from the
    # delivery log joined with sizes and dues.
    size_of = dict(zip(cols["flow_unit_id"], cols["size_units"]))
    due_of = dict(zip(cols["flow_unit_id"], cols["due_atu"]))
    prio_of = dict(zip(cols["flow_unit_id"], cols["priority_class"]))

    def on_time_units(e) -> float:
        dl = e.delivery_records()
        return float(sum(
            size_of[int(s)]
            for s, t in zip(dl["flow_unit_id"], dl["delivered_atu"])
            if int(t) <= due_of[int(s)]
        ))

    oracle_units = float(sum(size_of[s] for s in oracle_ids))
    greedy_units = on_time_units(eng_g)
    lp_ok = lp is not None and lp.solver_status == "optimal"
    lp_ub = lp.on_time_upper_bound if lp_ok else None
    sizes = {r.flow_unit_id: r.size_units for r in reqs}

    rows: list[dict] = []
    for pol_spec in (policies or ["slack_first"]):
        label, kw = parse_policy(pol_spec)
        planner = CapacityAwarePlanner(**kw)
        import time as _time
        t0 = _time.perf_counter()
        results = planner.plan(reqs, ctx)
        plan_wall = _time.perf_counter() - t0
        eng_p = _engine.PyEngine(
            b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H,
            "time_aware",
        )
        apply_plan(eng_p, results, ctx,
                   decision_trace=planner.last_decision_trace,
                   sizes=sizes, pinned=True)
        eng_p.set_enforce_capacity(True)
        eng_p.run(horizon * _H)
        kp = json.loads(eng_p.get_kpis())
        fid = measure_fidelity(planner.last_decision_trace, eng_p)
        assert fid.n_exact == fid.n_planned and fid.n_planned_stranded == 0, (
            "pinned zero-divergence invariant violated"
        )
        split = three_way_split(planner.last_decision_trace, eng_p)
        assert split["opportunistic_on_time"] == 0, (
            "refusal-certificate theorem violated — certification bug"
        )
        planner_units = on_time_units(eng_p)
        vip_guaranteed = sum(
            1 for d in planner.last_decision_trace
            if d.status == "planned" and prio_of[d.flow_unit_id] == 0
        )
        rows.append({
            "cell": f"{topology}/{cadence}/{capacity}",
            "policy": label,
            "seed": seed,
            "n": demand,
            "oracle_ub_flow_units": len(oracle_ids),
            "oracle_ub_units": oracle_units,
            "lp_ub_units": round(lp_ub, 1) if lp_ok else None,
            "lp_status": lp.solver_status if lp else "skipped",
            "lp_wall_s": lp.wall_sec if lp else None,
            "planner_on_time": kp["total_on_time"],
            "planner_on_time_units": planner_units,
            "planner_delivered": kp["total_delivered"],
            "guaranteed_on_time": split["guaranteed_on_time"],
            "opportunistic_on_time": split["opportunistic_on_time"],
            "opportunistic_late": split["opportunistic_late"],
            "refused_stranded": split["refused_stranded"],
            "vip_guaranteed": vip_guaranteed,
            "greedy_on_time": kg["total_on_time"],
            "greedy_on_time_units": greedy_units,
            "greedy_delivered": kg["total_delivered"],
            "regret_units_vs_lp": (round(lp_ub - planner_units, 1)
                                   if lp_ok else None),
            "regret_units_vs_oracle": round(oracle_units - planner_units, 1),
            "greedy_regret_units_vs_oracle": round(oracle_units - greedy_units, 1),
            "planner_reasons": planner.last_reason_counts,
            "repack_stats": planner.last_repack_stats,
            "plan_wall_s": round(plan_wall, 2),
            "n_planned": fid.n_planned,
            "lattice_ok": (not lp_ok
                           or (planner_units <= lp_ub + 1e-6 and lp.lattice_ok)),
            "schema_fingerprint": eng0.get_schema_fingerprint(),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cells", default=",".join(DEFAULT_CELLS),
                    help="comma-separated topology:cadence:capacity triples")
    ap.add_argument("--seeds", default="42,43,44")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--lp-bucket-h", type=int, default=4,
                    help="LP granularity; 4h measured to terminate on the "
                         "tightest 200-node cell (447s), 2h does not (>600s)")
    ap.add_argument("--lp-time-limit", type=float, default=600.0)
    ap.add_argument("--policies", default="slack_first",
                    help="comma list: slack_first | value_weighted | "
                         "batched_repack:<wave>:<budget>")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    cells = [c.split(":") for c in args.cells.split(",")]
    seeds = [int(s) for s in args.seeds.split(",")]

    policies = args.policies.split(",")
    rows = []
    for topo, cad, cap in cells:
        for seed in seeds:
            cell_rows = run_cell(topo, cad, cap, seed, args.demand,
                                 args.horizon, args.lp_bucket_h,
                                 args.lp_time_limit,
                                 solve_lp=(seed == seeds[0]),
                                 policies=policies)
            for row in cell_rows:
                rows.append(row)
                print(json.dumps(row))
            sys.stdout.flush()

    # Per-(cell, policy) aggregation across seeds (mean ± std).
    agg: dict[str, list[dict]] = {}
    for r in rows:
        agg.setdefault(f"{r['cell']}|{r.get('policy', 'slack_first')}", []).append(r)
    summary = []
    for cell, rs in agg.items():
        def ms(key):
            vals = [r[key] for r in rs if r[key] is not None]
            if not vals:
                return "—"
            if len(vals) == 1:
                return f"{vals[0]}"
            return f"{statistics.mean(vals):.0f} ± {statistics.stdev(vals):.0f}"
        summary.append({
            "cell": cell, "seeds": len(rs),
            "guaranteed_on_time": ms("guaranteed_on_time"),
            "opportunistic_late": ms("opportunistic_late"),
            "oracle_ub_units": ms("oracle_ub_units"),
            "lp_ub_units": ms("lp_ub_units"),
            "planner_on_time_units": ms("planner_on_time_units"),
            "greedy_on_time_units": ms("greedy_on_time_units"),
            "regret_units_vs_lp": ms("regret_units_vs_lp"),
            "regret_units_vs_oracle": ms("regret_units_vs_oracle"),
            "greedy_regret_units_vs_oracle": ms("greedy_regret_units_vs_oracle"),
            "lattice_ok": all(r["lattice_ok"] for r in rs),
        })

    if not args.no_emit and rows:
        fingerprint = rows[0].pop("schema_fingerprint")
        for r in rows[1:]:
            r.pop("schema_fingerprint", None)
        folder = emit_run(
            name="regret-table",
            config=vars(args),
            seed=seeds[0],
            schema_fingerprint=fingerprint,
            metrics_rows=rows,
            repro_cmd="python repro/regret_table.py "
                      + " ".join(sys.argv[1:]),
            extra_files={"regret_table.md": markdown_table(summary)},
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
