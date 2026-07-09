#!/usr/bin/env python3
"""GATE P1b — greedy-raw regret vs wait-cost steepness (the decision gap).

R_raw(s) = V_oracle(s) − V_greedy(s), where

  greedy   = CapacityAwarePlanner(policy="value_weighted") — the
             feasibility-optimal certified executor (ratified pin). Its
             plan is θ-INDEPENDENT: one plan+execution per seed, priced
             at every θ(s).
  oracle   = the conservative wait-pricing ILP (Gate P1a): certified
             optimum of the restricted region, executed through the DES
             (committed pinned, remainder reactive — same execution
             semantics as greedy).

Both values are FULL-execution realized v_realized totals (each policy's
uncommitted remainder runs reactively and may contribute negative value
under steep θ — symmetric, and every quantity is realized, so any
positive R_raw is a real, achievable improvement). S-restricted views
are reported as secondary data.

Steepness s ∈ [0,1]: a(s) linear 0.01→0.02, b(s) geometric 0.002→0.15,
linear-tolerance sharpness tol(s) geometric 12h→0.25h; s=0 is exactly
SLACK_ELASTIC. The hard-tolerance STEEP_SCARCE anchor is run as a 7th
point ("steep_hard"). World + demand are FIXED across the sweep (θ is the
only variable within a seed); seeds vary the world draw.

SCOPE (must accompany any result): measured at the ILP-CERTIFIABLE scale
(grid n12/d120, HiGHS ~50s/solve) on a CONTROLLED-BOTTLENECK world (knob
ρ=2.0 on the uncensored free-flow cut) — a certified oracle and a large
natural-congestion world are mutually exclusive at current solver
capability.

FALSIFICATION CONTROL: units whose routes touch no binding slot and no
cut lane in EITHER execution must show ≈0 per-unit |Δv_realized| at
every s.

Reproduce (from a clean clone; ~25 min — 30 HiGHS solves):
    make reproduce-decision-gap      # --verify byte-matches the golden
    python repro/decision_gap.py --write   # regenerate the golden
Wall-times are REPORTED (machine-dependent); the golden byte-asserts only
the deterministic fields. Raw run artifacts are not retained; this
reproduces the committed golden repro/expected/decision_gap.json.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time

import pyarrow.ipc as ipc

from epure_arena import _engine
from epure_arena.lattice.ilp_wait import solve_wait_ilp
from epure_arena.optimize.capacity_aware import CapacityAwarePlanner
from epure_arena.optimize.interface import PlanRequest
from epure_arena.pricing import (
    SLACK_ELASTIC, STEEP_SCARCE, WaitCostProfile, value_report,
)
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.world.graph_adapter import build_plan_context

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bottleneck as knob  # noqa: E402

_H = 3_600_000
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "repro" / "expected" / "decision_gap.json"

SEEDS = (42, 43, 44, 45, 46)
S_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
N_NODES, N_DEMAND = 12, 120
RHO = 2.0
ILP_TIME_LIMIT = 300.0
BINDING_UTIL = 0.999  # slot counts as binding at usage >= capacity

_TIMING_KEYS = {"wall_sec", "ilp_wall_sec", "wall_total_sec"}


def theta_sweep(s: float) -> WaitCostProfile:
    """Interpolate SLACK_ELASTIC → (steep, sharp-linear) anchors."""
    lo, hi = SLACK_ELASTIC, STEEP_SCARCE
    a = lo.a_per_hour + s * (hi.a_per_hour - lo.a_per_hour)
    b = lo.b_per_hour2 * (hi.b_per_hour2 / lo.b_per_hour2) ** s
    tol_h = 12.0 * (0.25 / 12.0) ** s
    return WaitCostProfile(
        name=f"sweep_s{s:.2f}", a_per_hour=a, b_per_hour2=b,
        late_mode="linear", late_tol_atu=int(tol_h * _H),
        description=f"steepness sweep point s={s:.2f}")


THETAS: list[tuple[str, float | None, WaitCostProfile]] = (
    [(f"s{s:.2f}", s, theta_sweep(s)) for s in S_GRID]
    + [("steep_hard", None, STEEP_SCARCE)]
)


def _strip_timing(obj):
    """Recursively drop machine-dependent wall-time keys for the golden."""
    if isinstance(obj, dict):
        return {k: _strip_timing(v) for k, v in obj.items()
                if k not in _TIMING_KEYS}
    if isinstance(obj, list):
        return [_strip_timing(v) for v in obj]
    return obj


def _cols(demand_ipc: bytes) -> dict[str, list]:
    dem = ipc.open_stream(demand_ipc).read_all()
    return {k: dem.column(k).to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class")}


def _reqs(cols) -> list[PlanRequest]:
    return [PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                        cols["dest_node_id"][i], cols["appear_atu"][i],
                        cols["due_atu"][i], cols["size_units"][i],
                        cols["priority_class"][i])
            for i in range(len(cols["flow_unit_id"]))]


def _execute_pinned_routes(bundle, routes) -> tuple[dict, dict, dict, set]:
    """Pin routes, run, return (delivery, strands, slot_state, unit→lanes)."""
    eng = _engine.PyEngine(bundle.node_ipc, bundle.lane_ipc,
                           bundle.schedule_ipc, bundle.demand_ipc,
                           _H, "time_aware")
    eng.set_enforce_capacity(True)
    eng.set_record_hop_trace(True)
    if routes:
        eng.set_forced_routes_pinned(routes)
    eng.run(24 * _H)
    hops = eng.hop_records()
    e2c = eng.get_lane_id_to_csr()
    csr_to_lane = {int(c): int(lid) for lid, c in enumerate(e2c)}
    unit_lanes: dict[int, set] = {}
    unit_slots: dict[int, set] = {}
    for sid, csr, dep in zip(hops["flow_unit_id"], hops["csr_lane"],
                             hops["depart_atu"]):
        lid = csr_to_lane[int(csr)]
        unit_lanes.setdefault(int(sid), set()).add(lid)
        unit_slots.setdefault(int(sid), set()).add((lid, int(dep)))
    ss = eng.slot_state()
    binding = {
        (int(l), int(d))
        for l, d, u, c in zip(ss["lane_id"], ss["depart_atu"],
                              ss["usage"], ss["capacity"])
        if c > 0 and u >= BINDING_UTIL * c
    }
    return (eng.delivery_records(), eng.strand_records(),
            {"unit_slots": unit_slots, "binding": binding,
             "unit_lanes": unit_lanes}, set())


def run_seed(seed: int) -> dict:
    t0 = time.perf_counter()
    b0 = generate_scenario(ScenarioSpec(
        topology="grid", n_nodes=N_NODES, density="moderate",
        capacity="loose", demand_pattern="diurnal",
        schedule_cadence="hourly", priority_mix="mixed",
        n_demand_events=N_DEMAND, horizon_hours=24, seed=seed))
    ffd = knob.free_flow_slot_demand(b0)
    cut = knob.select_cut(ffd, window_h=(6, 12), n_lanes=2)
    hot = knob.apply_rho(b0, cut, RHO)
    cols = _cols(hot.demand_ipc)
    reqs = _reqs(cols)

    # ── greedy: one θ-independent plan + execution ──────────────────────
    eng0 = _engine.PyEngine(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                            hot.demand_ipc, _H, "time_aware")
    ctx = build_plan_context(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                             eng0.get_lane_id_to_csr())
    planner = CapacityAwarePlanner(policy="value_weighted")
    planner.plan(reqs, ctx)
    trace = planner.last_decision_trace
    e2c = ctx.lane_id_to_csr
    g_routes = [
        (d.flow_unit_id,
         [(int(e2c[eid]), int(ctx.schedule_departures[eid][sp][0]))
          for eid, sp in d.path_slots])
        for d in trace if d.status == "planned" and d.path_slots
    ]
    g_dl, g_st, g_obs, _ = _execute_pinned_routes(hot, g_routes)
    S_greedy = {sid for sid, _ in g_routes}

    rows = []
    for label, s, theta in THETAS:
        t1 = time.perf_counter()
        sol = solve_wait_ilp(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                             hot.demand_ipc, horizon_hours=24, theta=theta,
                             time_limit_sec=ILP_TIME_LIMIT)
        o_routes = [
            (sid, [(int(e2c[eid]), int(dep)) for eid, dep in hops])
            for sid, hops in sorted(sol.committed.items())
        ]
        o_dl, o_st, o_obs, _ = _execute_pinned_routes(hot, o_routes)

        rep_o = value_report(cols, o_dl, o_st, theta)
        rep_g = value_report(cols, g_dl, g_st, theta)
        V_o, V_g = rep_o.v_realized_total, rep_g.v_realized_total
        # S-restricted secondary views.
        V_o_S = value_report(cols, o_dl, o_st, theta,
                             restrict_ids=set(sol.committed)).v_realized_total
        V_g_S = value_report(cols, g_dl, g_st, theta,
                             restrict_ids=S_greedy).v_realized_total

        # Round-trip guard on the oracle leg (P1a property, re-asserted).
        rep_o_S = value_report(cols, o_dl, o_st, theta,
                               restrict_ids=set(sol.committed))
        rt_ok = rep_o_S.v_realized_total == sol.claimed_v_realized

        # ── falsification control: non-competing units ──────────────────
        cut_lanes = set(cut.lane_ids)
        binding = g_obs["binding"] | o_obs["binding"]
        binding_lanes = {l for l, _ in binding}
        control_ids = []
        for sid in cols["flow_unit_id"]:
            lanes_used = (g_obs["unit_lanes"].get(sid, set())
                          | o_obs["unit_lanes"].get(sid, set()))
            if not lanes_used:
                continue  # stranded in both — not a control
            if lanes_used & cut_lanes or lanes_used & binding_lanes:
                continue
            control_ids.append(sid)
        pu_o = rep_o.per_unit
        pu_g = rep_g.per_unit
        ctl_deltas = [abs(pu_o.get(sid, 0.0) - pu_g.get(sid, 0.0))
                      for sid in control_ids]
        ctl_max = max(ctl_deltas) if ctl_deltas else 0.0

        rows.append({
            "label": label, "s": s, "theta": theta.name,
            "a": theta.a_per_hour, "b": theta.b_per_hour2,
            "late_mode": theta.late_mode, "tol_atu": theta.late_tol_atu,
            "V_oracle_full": V_o, "V_greedy_full": V_g,
            "R_raw": V_o - V_g,
            "V_oracle_S": V_o_S, "V_greedy_S": V_g_S,
            "oracle_claimed": sol.claimed_v_realized,
            "oracle_certified": sol.certified,
            "roundtrip_ok": rt_ok,
            "n_committed_oracle": len(sol.committed),
            "n_committed_greedy": len(S_greedy),
            "n_control_units": len(control_ids),
            "control_max_abs_delta": ctl_max,
            "ilp_wall_sec": sol.wall_sec,
            "wall_sec": round(time.perf_counter() - t1, 1),
        })
        print(f"  [seed {seed}] {label}: R_raw={V_o - V_g:+.3f} "
              f"(V_o={V_o:.2f} V_g={V_g:.2f}) cert={sol.certified} "
              f"rt={rt_ok} ctl_max={ctl_max:.4f} n_ctl={len(control_ids)} "
              f"({rows[-1]['wall_sec']}s)", flush=True)

    return {"seed": seed, "cut_lanes": list(cut.lane_ids),
            "cut_D_total": cut.total_demand(), "rows": rows,
            "wall_sec": round(time.perf_counter() - t0, 1)}


def compute() -> dict:
    per_seed = [run_seed(seed) for seed in SEEDS]

    # Aggregate: mean ± 95% CI per θ point (t critical, n=len(SEEDS)).
    t_crit = {4: 2.776, 5: 2.571}[len(SEEDS) - 1] if len(SEEDS) in (5, 6) \
        else 2.776
    agg = []
    for i, (label, s, theta) in enumerate(THETAS):
        vals = [ps["rows"][i]["R_raw"] for ps in per_seed]
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        half = t_crit * math.sqrt(var / n)
        ctl = max(ps["rows"][i]["control_max_abs_delta"] for ps in per_seed)
        agg.append({
            "label": label, "s": s, "b": theta.b_per_hour2,
            "R_raw_mean": mean, "ci95_half": half,
            "ci_lo": mean - half, "ci_hi": mean + half,
            "excludes_zero": (mean - half) > 0 or (mean + half) < 0,
            "control_max_abs_delta": ctl,
            "per_seed": vals,
        })
        print(f"[agg] {label}: R_raw = {mean:+.3f} ± {half:.3f} "
              f"(CI [{mean - half:+.3f}, {mean + half:+.3f}]) "
              f"ctl_max={ctl:.4f}", flush=True)

    return {
        "scope": ("ILP-certifiable scale (grid n12/d120, HiGHS) with a "
                  "controlled bottleneck (knob rho=2.0 on the uncensored "
                  "free-flow cut); greedy pinned = CapacityAwarePlanner("
                  "value_weighted); oracle = conservative wait-pricing ILP "
                  "(certified lower bound)"),
        "seeds": list(SEEDS), "thetas": [t[0] for t in THETAS],
        "aggregate": agg, "per_seed": per_seed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="decision-gap sweep (Gate P1b)")
    ap.add_argument("--write", action="store_true",
                    help="(re)generate repro/expected/decision_gap.json")
    ap.add_argument("--verify", action="store_true",
                    help="byte-match deterministic fields against the golden")
    args = ap.parse_args()

    t0 = time.perf_counter()
    out = compute()
    golden = json.dumps(_strip_timing(out), indent=1, sort_keys=True)
    print(f"[wall] total={round(time.perf_counter() - t0, 1)}s "
          "(reported; not byte-asserted)", flush=True)

    if args.write:
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(golden + "\n")
        print(f"[write] {_GOLDEN}", flush=True)
        return 0
    if args.verify:
        expected = _GOLDEN.read_text()
        if golden + "\n" == expected:
            print(f"[verify] byte-identical to {_GOLDEN.name} ✓", flush=True)
            return 0
        print(f"[verify] MISMATCH vs {_GOLDEN.name}", file=sys.stderr)
        return 1
    print(golden)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
