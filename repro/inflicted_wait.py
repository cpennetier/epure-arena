#!/usr/bin/env python3
"""GATE P1c — own-wait vs inflicted-wait decomposition.

Two legs, both neutralized (STEEP_SCARCE / SLACK_ELASTIC in code):

A. FULL-SYSTEM at s=0.75 (domain-agnostic primary; remainders negligible,
   no admission model):
     R_raw  = V_oracle − V_greedy-raw   (feasibility-optimal, wait-blind)
     R_wait = V_oracle − V_greedy-wait  (sequential OWN-wait-adjusted
                                         marginals at the real commit seam)
     own-wait recoverable = R_raw − R_wait   (a heuristic captures it)
     inflicted-wait gap   = R_wait           (invisible to any single-unit
                                              marginal; the learning target)

B. ADMISSION model at steep_hard (STEEP_SCARCE, hard tolerance): each
   policy plans EX-ANTE on the full world, then ONLY its committed set S
   runs — demand is filtered to S before execution (refused units genuinely
   do not run; the semantics that resolves the P1b steep-end remainder
   divergence). Measured on the committed basis: R_raw_S, R_wait_S, ± 95% CI.

Guards (both legs): the admission filter is ONE function applied identically
to each policy's own S (asserted exact); oracle round-trip (realized V(S) ==
claimed, P1a); greedy-wait round-trip (realized V == Σ sequential marginals,
bit-exact); ordering V_raw ≤ V_gw ≤ V_o; and a falsification control
(units touching no binding slot / cut lane realize ≈0 |Δ|).

SCOPE (rides every number): ILP-certifiable scale (grid n12/d120, HiGHS),
controlled bottleneck (knob ρ=2.0); greedy-raw = CapacityAwarePlanner(
value_weighted); oracle = P1a conservative wait-pricing ILP (a certified
LOWER bound — every positive R_wait is a guaranteed-real lower bound on the
inflicted-wait externality).

Reproduce (from a clean clone; ~10 min — 10 HiGHS solves):
    make reproduce-inflicted-wait    # --verify byte-matches the golden
    python repro/inflicted_wait.py --write   # regenerate the golden
Wall-times are REPORTED (machine-dependent); the golden byte-asserts only
deterministic fields. Raw run artifacts are not retained; this reproduces
the committed golden repro/expected/inflicted_wait.json.
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
from epure_arena.pricing import STEEP_SCARCE, plan_times, v_realized, value_report
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.scenarios.serializer import to_demand_ipc
from epure_arena.world.graph_adapter import build_plan_context

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bottleneck as knob  # noqa: E402
from decision_gap import theta_sweep  # noqa: E402
from greedy_wait import plan_greedy_wait  # noqa: E402

_H = 3_600_000
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "repro" / "expected" / "inflicted_wait.json"

SEEDS = (42, 43, 44, 45, 46)
S_POINT = 0.75
N_NODES, N_DEMAND, RHO = 12, 120, 2.0
ILP_TIME_LIMIT = 300.0
BINDING_UTIL = 0.999
THETA_HARD = STEEP_SCARCE

_TIMING_KEYS = {"wall_sec", "ilp_wall_sec", "wall_total_sec"}
_ALL_FIELDS = ("flow_unit_id", "appear_atu", "due_atu", "origin_node_id",
               "dest_node_id", "origin_cell_idx", "dest_cell_idx",
               "size_units", "priority_class", "handling_flags")


def _strip_timing(obj):
    if isinstance(obj, dict):
        return {k: _strip_timing(v) for k, v in obj.items()
                if k not in _TIMING_KEYS}
    if isinstance(obj, list):
        return [_strip_timing(v) for v in obj]
    return obj


def _cols(demand_ipc):
    dem = ipc.open_stream(demand_ipc).read_all()
    return {k: dem.column(k).to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class")}


def _reqs(cols):
    return [PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                        cols["dest_node_id"][i], cols["appear_atu"][i],
                        cols["due_atu"][i], cols["size_units"][i],
                        cols["priority_class"][i])
            for i in range(len(cols["flow_unit_id"]))]


def _routes_from_slots(items, ctx):
    e2c = ctx.lane_id_to_csr
    return [
        (sid, [(int(e2c[eid]), int(ctx.schedule_departures[eid][sp][0]))
               for eid, sp in path_slots])
        for sid, path_slots in items
    ]


def _make_world(seed):
    b0 = generate_scenario(ScenarioSpec(
        topology="grid", n_nodes=N_NODES, density="moderate",
        capacity="loose", demand_pattern="diurnal",
        schedule_cadence="hourly", priority_mix="mixed",
        n_demand_events=N_DEMAND, horizon_hours=24, seed=seed))
    ffd = knob.free_flow_slot_demand(b0)
    cut = knob.select_cut(ffd, window_h=(6, 12), n_lanes=2)
    hot = knob.apply_rho(b0, cut, RHO)
    return hot, cut


def _ci(vals):
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    half = 2.776 * math.sqrt(var / n)  # t(0.975, df=4)
    return mean, half


# ── LEG A: full-system decomposition at s=0.75 ─────────────────────────────
def _exec(bundle, routes):
    eng = _engine.PyEngine(bundle.node_ipc, bundle.lane_ipc,
                           bundle.schedule_ipc, bundle.demand_ipc,
                           _H, "time_aware")
    eng.set_enforce_capacity(True)
    if routes:
        eng.set_forced_routes_pinned(routes)
    eng.run(24 * _H)
    return eng.delivery_records(), eng.strand_records()


def run_seed_full(seed: int, theta) -> dict:
    t0 = time.perf_counter()
    hot, cut = _make_world(seed)
    cols = _cols(hot.demand_ipc)
    reqs = _reqs(cols)
    eng0 = _engine.PyEngine(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                            hot.demand_ipc, _H, "time_aware")
    ctx = build_plan_context(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                             eng0.get_lane_id_to_csr())

    # greedy-raw (θ-blind).
    planner = CapacityAwarePlanner(policy="value_weighted")
    planner.plan(reqs, ctx)
    raw_trace = [(d.flow_unit_id, d.path_slots)
                 for d in planner.last_decision_trace
                 if d.status == "planned" and d.path_slots]
    raw_dl, raw_st = _exec(hot, _routes_from_slots(raw_trace, ctx))
    S_raw = {sid for sid, _ in raw_trace}

    # greedy-wait (own-wait-adjusted sequential marginals).
    commits = plan_greedy_wait(reqs, ctx, theta)
    gw_items = [(c.flow_unit_id, c.path_slots) for c in commits]
    gw_dl, gw_st = _exec(hot, _routes_from_slots(gw_items, ctx))
    S_gw = {c.flow_unit_id for c in commits}

    # oracle (P1a conservative wait-pricing ILP).
    sol = solve_wait_ilp(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                         hot.demand_ipc, horizon_hours=24, theta=theta,
                         time_limit_sec=ILP_TIME_LIMIT)
    e2c = ctx.lane_id_to_csr
    o_routes = [(sid, [(int(e2c[eid]), int(dep)) for eid, dep in hops])
                for sid, hops in sorted(sol.committed.items())]
    o_dl, o_st = _exec(hot, o_routes)
    S_o = set(sol.committed)

    def rep(dl, st, restrict=None):
        return value_report(cols, dl, st, theta, restrict_ids=restrict)

    V = {
        "raw_full": rep(raw_dl, raw_st).v_realized_total,
        "gw_full": rep(gw_dl, gw_st).v_realized_total,
        "o_full": rep(o_dl, o_st).v_realized_total,
        "raw_S": rep(raw_dl, raw_st, S_raw).v_realized_total,
        "gw_S": rep(gw_dl, gw_st, S_gw).v_realized_total,
        "o_S": rep(o_dl, o_st, S_o).v_realized_total,
    }
    rt_ok = V["o_S"] == sol.claimed_v_realized
    ordering_ok = V["raw_S"] <= V["gw_S"] + 1e-9 and V["gw_S"] <= V["o_S"] + 1e-9

    row = {
        "seed": seed, **V,
        "R_raw_full": V["o_full"] - V["raw_full"],
        "R_wait_full": V["o_full"] - V["gw_full"],
        "own_wait_recoverable_full": V["gw_full"] - V["raw_full"],
        "R_raw_S": V["o_S"] - V["raw_S"],
        "R_wait_S": V["o_S"] - V["gw_S"],
        "n_committed": {"raw": len(S_raw), "gw": len(S_gw), "o": len(S_o)},
        "roundtrip_ok": rt_ok, "ordering_ok_S": ordering_ok,
        "oracle_certified": sol.certified,
        "wall_sec": round(time.perf_counter() - t0, 1),
    }
    print(f"  [A seed {seed}] R_raw={row['R_raw_full']:+.2f} "
          f"R_wait={row['R_wait_full']:+.2f} "
          f"own_wait={row['own_wait_recoverable_full']:+.2f} | "
          f"S raw/gw/o = {V['raw_S']:.1f}/{V['gw_S']:.1f}/{V['o_S']:.1f} "
          f"ord={ordering_ok} rt={rt_ok} n={row['n_committed']} "
          f"({row['wall_sec']}s)", flush=True)
    return row


def leg_full_system() -> dict:
    theta = theta_sweep(S_POINT)
    print(f"[A theta] {theta.name}: a={theta.a_per_hour:.4f} "
          f"b={theta.b_per_hour2:.4f} tol={theta.late_tol_atu / _H:.2f}h",
          flush=True)
    rows = [run_seed_full(seed, theta) for seed in SEEDS]
    agg = {}
    for key in ("R_raw_full", "R_wait_full", "own_wait_recoverable_full",
                "R_raw_S", "R_wait_S"):
        mean, half = _ci([r[key] for r in rows])
        agg[key] = {"mean": mean, "ci95_half": half,
                    "ci": [mean - half, mean + half],
                    "excludes_zero": (mean - half) > 0 or (mean + half) < 0}
        print(f"[A agg] {key}: {mean:+.3f} ± {half:.3f} "
              f"(CI [{mean - half:+.3f}, {mean + half:+.3f}])", flush=True)
    verdict = ("INFLICTED-WAIT EXTERNALITY CONFIRMED — learning justified"
               if agg["R_wait_full"]["excludes_zero"]
               and agg["R_wait_full"]["mean"] > 0 else
               "own-wait heuristic captures the gap — learning adds nothing"
               if not agg["R_wait_full"]["excludes_zero"] else
               "R_wait CI excludes zero NEGATIVELY — investigate")
    return {
        "scope": ("ILP-certifiable scale (grid n12/d120, HiGHS), controlled "
                  "bottleneck (knob rho=2.0); greedy-raw = "
                  "CapacityAwarePlanner(value_weighted); greedy-wait = "
                  "sequential own-wait-adjusted marginals at the same seam; "
                  "oracle = P1a conservative wait-pricing ILP"),
        "theta": {"s": S_POINT, "a": theta.a_per_hour,
                  "b": theta.b_per_hour2, "tol_atu": theta.late_tol_atu},
        "seeds": list(SEEDS), "rows": rows, "aggregate": agg,
        "verdict": verdict,
    }


# ── LEG B: admission model at steep_hard (STEEP_SCARCE) ─────────────────────
def filter_demand(demand_ipc: bytes, S: set[int]) -> bytes:
    """THE admission filter — one code path for every policy.

    Keeps exactly the rows whose flow_unit_id ∈ S, preserving appear
    order; asserts the surviving id set equals S (a mismatch would bias
    the comparison — halt)."""
    dem = ipc.open_stream(demand_ipc).read_all()
    rows = [
        {f: dem.column(f)[i].as_py() for f in _ALL_FIELDS}
        for i in range(dem.num_rows)
        if int(dem.column("flow_unit_id")[i].as_py()) in S
    ]
    kept = {int(r["flow_unit_id"]) for r in rows}
    assert kept == set(S), (
        f"admission filter mismatch: kept {len(kept)} != |S|={len(S)} "
        f"(missing={sorted(set(S) - kept)[:5]})")
    rows.sort(key=lambda r: (r["appear_atu"], r["flow_unit_id"]))
    return to_demand_ipc(rows)


def _exec_admitted(bundle, filtered_demand: bytes, routes):
    """Execute S-only demand with the policy's pinned routes."""
    eng = _engine.PyEngine(bundle.node_ipc, bundle.lane_ipc,
                           bundle.schedule_ipc, filtered_demand,
                           _H, "time_aware")
    eng.set_enforce_capacity(True)
    eng.set_record_hop_trace(True)
    if routes:
        eng.set_forced_routes_pinned(routes)
    eng.run(24 * _H)
    hops = eng.hop_records()
    e2c = eng.get_lane_id_to_csr()
    c2l = {int(c): int(lid) for lid, c in enumerate(e2c)}
    unit_lanes: dict[int, set] = {}
    for sid, csr in zip(hops["flow_unit_id"], hops["csr_lane"]):
        unit_lanes.setdefault(int(sid), set()).add(c2l[int(csr)])
    ss = eng.slot_state()
    binding_lanes = {
        int(l) for l, u, c in zip(ss["lane_id"], ss["usage"], ss["capacity"])
        if c > 0 and u >= BINDING_UTIL * c
    }
    return eng.delivery_records(), eng.strand_records(), unit_lanes, binding_lanes


def run_seed_admission(seed: int) -> dict:
    t0 = time.perf_counter()
    hot, cut = _make_world(seed)
    cols = _cols(hot.demand_ipc)
    reqs = _reqs(cols)
    eng0 = _engine.PyEngine(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                            hot.demand_ipc, _H, "time_aware")
    ctx = build_plan_context(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                             eng0.get_lane_id_to_csr())
    e2c = ctx.lane_id_to_csr
    appear = dict(zip(cols["flow_unit_id"], cols["appear_atu"]))
    due = dict(zip(cols["flow_unit_id"], cols["due_atu"]))
    size = dict(zip(cols["flow_unit_id"], cols["size_units"]))

    # ── plans (ex-ante, full world) ─────────────────────────────────────
    planner = CapacityAwarePlanner(policy="value_weighted")
    planner.plan(reqs, ctx)
    raw_items = [(d.flow_unit_id, d.path_slots)
                 for d in planner.last_decision_trace
                 if d.status == "planned" and d.path_slots]
    raw_plan_v = sum(
        v_realized(*plan_times(ps, appear[sid], ctx), due[sid], size[sid],
                   THETA_HARD)
        for sid, ps in sorted(raw_items))

    commits = plan_greedy_wait(reqs, ctx, THETA_HARD)
    gw_items = [(c.flow_unit_id, c.path_slots) for c in commits]
    gw_plan_v = sum(c.marginal_v_realized
                    for c in sorted(commits, key=lambda c: c.flow_unit_id))

    sol = solve_wait_ilp(hot.node_ipc, hot.lane_ipc, hot.schedule_ipc,
                         hot.demand_ipc, horizon_hours=24, theta=THETA_HARD,
                         time_limit_sec=ILP_TIME_LIMIT)

    # ── admitted executions (ONE filter, each policy's own S) ──────────
    def leg(items_slots, routes_exact=None):
        S = {sid for sid, _ in items_slots} if routes_exact is None \
            else {sid for sid, _ in routes_exact}
        fdem = filter_demand(hot.demand_ipc, S)
        routes = (
            [(sid, [(int(e2c[eid]), int(ctx.schedule_departures[eid][sp][0]))
                    for eid, sp in ps]) for sid, ps in items_slots]
            if routes_exact is None else
            [(sid, [(int(e2c[eid]), int(dep)) for eid, dep in hops])
             for sid, hops in routes_exact]
        )
        dl, st, ul, bl = _exec_admitted(hot, fdem, routes)
        fcols = _cols(fdem)
        rep = value_report(fcols, dl, st, THETA_HARD)
        return S, rep, ul, bl

    S_raw, rep_raw, ul_raw, bl_raw = leg(raw_items)
    S_gw, rep_gw, ul_gw, bl_gw = leg(gw_items)
    S_o, rep_o, ul_o, bl_o = leg(None, routes_exact=sorted(sol.committed.items()))

    V_raw, V_gw, V_o = (rep_raw.v_realized_total, rep_gw.v_realized_total,
                        rep_o.v_realized_total)

    rt_oracle = V_o == sol.claimed_v_realized
    rt_gw = V_gw == gw_plan_v
    rt_raw = V_raw == raw_plan_v
    ordering_ok = V_raw <= V_gw + 1e-9 <= V_o + 2e-9

    common = S_raw & S_gw & S_o
    cut_lanes = set(cut.lane_ids)
    bad_lanes = bl_raw | bl_gw | bl_o | cut_lanes
    ctl, ctl_max = 0, 0.0
    for sid in sorted(common):
        lanes = (ul_raw.get(sid, set()) | ul_gw.get(sid, set())
                 | ul_o.get(sid, set()))
        if not lanes or lanes & bad_lanes:
            continue
        ctl += 1
        vals = [rep_raw.per_unit.get(sid), rep_gw.per_unit.get(sid),
                rep_o.per_unit.get(sid)]
        if None not in vals:
            ctl_max = max(ctl_max, max(vals) - min(vals))

    row = {
        "seed": seed,
        "V_raw": V_raw, "V_gw": V_gw, "V_oracle": V_o,
        "R_raw_S": V_o - V_raw, "R_wait_S": V_o - V_gw,
        "own_wait_S": V_gw - V_raw,
        "n_committed": {"raw": len(S_raw), "gw": len(S_gw), "o": len(S_o)},
        "roundtrips": {"oracle": rt_oracle, "greedy_wait": rt_gw,
                       "greedy_raw": rt_raw},
        "ordering_ok": ordering_ok,
        "n_control": ctl, "control_max_delta": ctl_max,
        "oracle_certified": sol.certified,
        "wall_sec": round(time.perf_counter() - t0, 1),
    }
    ok = rt_oracle and rt_gw and rt_raw and ordering_ok
    print(f"  [B seed {seed}] {'OK' if ok else 'GUARD-FAIL'} "
          f"R_raw_S={row['R_raw_S']:+.2f} R_wait_S={row['R_wait_S']:+.2f} "
          f"own={row['own_wait_S']:+.2f} | V raw/gw/o = "
          f"{V_raw:.1f}/{V_gw:.1f}/{V_o:.1f} n={row['n_committed']} "
          f"rt(o/gw/raw)={int(rt_oracle)}{int(rt_gw)}{int(rt_raw)} "
          f"ord={ordering_ok} ctl={ctl}/{ctl_max:.4f} "
          f"({row['wall_sec']}s)", flush=True)
    return row


def leg_admission() -> dict:
    rows = [run_seed_admission(seed) for seed in SEEDS]
    agg = {}
    for key in ("R_raw_S", "R_wait_S", "own_wait_S"):
        mean, half = _ci([r[key] for r in rows])
        agg[key] = {"mean": mean, "ci95_half": half,
                    "ci": [mean - half, mean + half],
                    "excludes_zero": (mean - half) > 0 or (mean + half) < 0}
        print(f"[B agg] {key}: {mean:+.3f} ± {half:.3f} "
              f"(CI [{mean - half:+.3f}, {mean + half:+.3f}])", flush=True)
    guards_ok = all(all(r["roundtrips"].values()) and r["ordering_ok"]
                    for r in rows)
    verdict = (
        "GUARD FAILURE — see rows" if not guards_ok else
        "INFLICTED-WAIT EXTERNALITY AT MAXIMUM STEEPNESS (admission) "
        "CONFIRMED" if agg["R_wait_S"]["excludes_zero"]
        and agg["R_wait_S"]["mean"] > 0 else
        "own-wait heuristic captures the gap at steep_hard under admission "
        "semantics")
    return {
        "scope": ("ILP-certifiable scale (grid n12/d120, HiGHS), controlled "
                  "bottleneck (knob rho=2.0); COMMITTED ADMISSION MODEL: "
                  "each policy plans ex-ante on the full world, then only "
                  "its committed S runs (one shared demand filter, asserted "
                  "exact per policy); theta = STEEP_SCARCE (hard tol)"),
        "seeds": list(SEEDS), "rows": rows, "aggregate": agg,
        "verdict": verdict,
    }


def compute() -> dict:
    return {
        "full_system_s075": leg_full_system(),
        "admission_steep_hard": leg_admission(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="inflicted-wait decomposition (P1c)")
    ap.add_argument("--write", action="store_true",
                    help="(re)generate repro/expected/inflicted_wait.json")
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
