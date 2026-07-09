#!/usr/bin/env python
"""Plan → execute → measure plan-fidelity on one scenario cell (Step 4).

Plans every flow_unit with CapacityAwarePlanner, installs committed plans as
SLOT-PINNED forced routes with reservations (Option A, default), runs the
DES, and joins planned vs realized per flow_unit. ``--slot-free`` keeps the
lane-pinned commitment semantics as the STANDING ABLATION (the measured
plan-decoherence result); ``--planned-only`` removes unplanned flow_units from
demand (the interference ablation). Emits a run_id folder under runs/.

Usage (node-engine venv, repo root):
    python examples/plan_fidelity.py --cadence hourly --capacity tight
    python examples/plan_fidelity.py --cadence hourly --capacity tight --slot-free
    python examples/plan_fidelity.py --cadence hourly --capacity tight --slot-free --planned-only
"""

from __future__ import annotations

import argparse
import io
import json
import sys

from epure_arena import _engine
import pyarrow as pa
import pyarrow.ipc as ipc

from epure_arena.harness.runlake import emit_run
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena import CapacityAwarePlanner
from epure_arena.optimize.fidelity import apply_plan, measure_fidelity
from epure_arena.world.graph_adapter import build_plan_context
from epure_arena.optimize.interface import PlanRequest

_H = 3_600_000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topology", default="backbone")
    ap.add_argument("--nodes", type=int, default=200)
    ap.add_argument("--density", default="moderate")
    ap.add_argument("--capacity", default="tight")
    ap.add_argument("--cadence", default="hourly")
    ap.add_argument("--pattern", default="diurnal")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--planned-only", action="store_true",
                    help="drop unplanned flow_units from demand (ablation)")
    ap.add_argument("--slot-free", action="store_true",
                    help="lane-pinned forced routes (standing ablation; "
                         "default is slot-pinned + reservations)")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    spec = ScenarioSpec(
        topology=args.topology, n_nodes=args.nodes, density=args.density,
        capacity=args.capacity, demand_pattern=args.pattern,
        schedule_cadence=args.cadence, priority_mix="mixed",
        n_demand_events=args.demand, horizon_hours=args.horizon, seed=args.seed,
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

    planner = CapacityAwarePlanner()
    results = planner.plan(reqs, ctx)

    demand_ipc = b.demand_ipc
    if args.planned_only:
        planned_ids = {r.flow_unit_id for r in results if r.status == "planned"}
        mask = pa.array([int(s) in planned_ids for s in cols["flow_unit_id"]])
        filtered = dem.filter(mask)
        sink = io.BytesIO()
        with ipc.new_stream(sink, filtered.schema) as w:
            w.write_table(filtered)
        demand_ipc = sink.getvalue()

    eng = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, demand_ipc, _H, "time_aware",
    )
    sizes = {r.flow_unit_id: r.size_units for r in reqs}
    apply_plan(eng, results, ctx, decision_trace=planner.last_decision_trace,
               sizes=sizes, pinned=not args.slot_free)
    eng.set_enforce_capacity(True)
    eng.run(args.horizon * _H)

    rep = measure_fidelity(planner.last_decision_trace, eng)
    row = {"semantics": "slot_free" if args.slot_free else "pinned",
           "mode": "planned_only" if args.planned_only else "mixed",
           **rep.as_row(), "reasons": planner.last_reason_counts}
    print(json.dumps(row))

    if not args.no_emit:
        folder = emit_run(
            name="plan-fidelity",
            config={**vars(args)},
            seed=args.seed,
            schema_fingerprint=eng.get_schema_fingerprint(),
            metrics_rows=[row],
            repro_cmd="python examples/plan_fidelity.py "
                      + " ".join(sys.argv[1:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
