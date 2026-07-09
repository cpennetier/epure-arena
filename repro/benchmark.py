#!/usr/bin/env python
"""reproduce-benchmark: the certified-environment performance characterization.

Measures, at increasing scale (flow-units), the cost of every stage that lets
epure-arena call itself a *certified simulator / prover*, so the claim is
backed by numbers rather than adjectives:

  THROUGHPUT  — the DES world run under the engine's own greedy router
                (no plan): flow-units/sec and events-processed/sec. This is
                "what physically happens" replayed at speed.

  LATENCY     — decision-policy wall time, two policies graded apples-to-apples
                on the SAME requests/context:
                  * static (Dijkstra)  — the capacity-blind baseline router
                    (ARCHITECTURE.md: the "what does a capacity-blind router
                    do" control / explore baseline).
                  * certified (A*)     — the certified planner, and WHERE its
                    cost goes, decomposed into:
                      setup        residual + connection index
                      oracle       capacity-free destination profiles
                                   (backward connection-scan, the admissible
                                   lower bound)
                      a*           the time-dependent A* search itself
                                   (Σ per-flow-unit search_ns from the trace)
                      ledger       commit-order sort + per-(lane,slot) residual
                                   mutation (t_commit − a*)
                      apply        commit/pin onto the engine
                                   (set_forced_routes_pinned)
                      des          pinned execution of the committed plan
                      attribution  three-way split + plan-fidelity join
                                   (the proof-over-outcome step)

  REPLAY      — determinism cost: a bare run vs a run that captures records and
                computes the byte-identity digest (what you pay to obtain a
                *verifiable* artifact). A second replay's digest is asserted
                equal to the first — byte-identical replay, proven per scale.

  MEMORY      — peak RSS at each scale (each scale runs in its OWN subprocess,
                so the high-water mark is isolated, not cumulative).

What is byte-asserted vs reported
─────────────────────────────────
Counts and digests are DETERMINISTIC (integer ATU, seed-derived, same compiled
engine) and are byte-asserted against repro/expected/benchmark.json from a
clean run:  events_processed, greedy delivered/on-time/stranded, the world
record digest, certified planned/guaranteed-on-time, the certify digest, and
the replay-identity flag.  Wall-times, throughput, and memory are
MACHINE-DEPENDENT and are written to data/benchmark.json as REPORTED numbers
(hardware noted in the caption) — never byte-asserted.

Usage (repo root, the engine venv):
    python repro/benchmark.py --write          # run + (re)write both goldens
    python repro/benchmark.py --verify          # run + byte-assert the counts
    python repro/benchmark.py --scales 10000,100000
    python repro/benchmark.py scale --demand 100000 --seed 42   # one worker row
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import platform
import resource
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "repro" / "expected" / "benchmark.json"
DATA = ROOT / "data" / "benchmark.json"

_H = 3_600_000
DEFAULT_SCALES = (10_000, 30_000, 100_000, 300_000, 1_000_000)

# Headline regime: backbone, hourly cadence, LOOSE (unsaturated) capacity — the
# regime in which the certified planner is meant to commit nearly everything,
# so the cost decomposition is not dominated by failed searches.
REGIME = dict(
    topology="backbone", n_nodes=200, density="moderate", capacity="loose",
    demand_pattern="diurnal", schedule_cadence="hourly", priority_mix="mixed",
    horizon_hours=24,
)

# Fields that are deterministic and therefore byte-asserted (the rest are
# machine-dependent and only REPORTED). Kept in one place so the orchestrator
# and the doc agree on the contract.
CANONICAL_FIELDS = (
    "demand", "seed", "events_processed",
    "greedy_delivered", "greedy_on_time", "greedy_stranded", "world_digest",
    "certified_planned", "guaranteed_on_time", "certify_digest",
    "deterministic", "replay_identical",
)


# ── digests (deterministic identity keys) ───────────────────────────────────

def _world_digest(engine) -> str:
    """sha256 over the realized world outcome: every (flow_unit, delivered_atu)
    in id order, then every stranded id. Integer, seed-derived ⇒ byte-stable
    across machines."""
    dl = engine.delivery_records()
    rows = sorted(zip((int(s) for s in dl["flow_unit_id"]),
                      (int(t) for t in dl["delivered_atu"])))
    h = hashlib.sha256()
    for sid, t in rows:
        h.update(f"{sid}|{t}".encode())
    h.update(b"#strands#")
    for sid in sorted(int(s) for s in engine.strand_records()["flow_unit_id"]):
        h.update(f"{sid}".encode())
    return h.hexdigest()


def _certify_digest(trace) -> str:
    """sha256 over the planner decision trace (the plan identity)."""
    h = hashlib.sha256()
    for d in trace:
        h.update(
            f"{d.flow_unit_id}|{d.status}|{d.reason}|{d.planned_arrival_atu}|"
            f"{d.path_slots}".encode()
        )
    return h.hexdigest()


def _peak_rss_mb() -> float:
    # macOS ru_maxrss is bytes; Linux is kibibytes. Normalize to MiB.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    div = (1 << 20) if sys.platform == "darwin" else (1 << 10)
    return round(raw / div, 1)


# ── the worker: every measurement for ONE scale, in its own process ─────────

def run_scale(demand: int, seed: int) -> dict:
    from epure_arena import _engine
    import pyarrow.ipc as ipc
    from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
    from epure_arena import CapacityAwarePlanner
    from epure_arena.optimize.static import StaticPlanner
    from epure_arena.optimize.fidelity import (
        apply_plan, measure_fidelity, three_way_split,
    )
    from epure_arena.world.graph_adapter import build_plan_context
    from epure_arena.optimize.interface import PlanRequest

    spec = ScenarioSpec(n_demand_events=demand, seed=seed, **REGIME)
    t = time.perf_counter()
    b = generate_scenario(spec)
    t_gen = time.perf_counter() - t

    eng0 = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware")
    ctx = build_plan_context(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, eng0.get_lane_id_to_csr())
    dem = ipc.open_stream(b.demand_ipc).read_all()
    cols = {k: dem[k].to_pylist() for k in (
        "flow_unit_id", "origin_node_id", "dest_node_id", "appear_atu",
        "due_atu", "size_units", "priority_class")}
    reqs = [
        PlanRequest(cols["flow_unit_id"][i], cols["origin_node_id"][i],
                    cols["dest_node_id"][i], cols["appear_atu"][i],
                    cols["due_atu"][i], cols["size_units"][i],
                    cols["priority_class"][i])
        for i in range(dem.num_rows)
    ]
    sizes = {r.flow_unit_id: r.size_units for r in reqs}

    # ── THROUGHPUT: greedy DES world run (no plan) ──────────────────────────
    eg = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware")
    eg.set_enforce_capacity(True)
    t = time.perf_counter()
    eg.run(24 * _H)
    t_greedy = time.perf_counter() - t
    kg = json.loads(eg.get_kpis())
    events = kg["events_processed"]
    world_digest = _world_digest(eg)

    # ── REPLAY: bare run vs run+capture+digest; second replay → identity ────
    eb = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware")
    eb.set_enforce_capacity(True)
    t = time.perf_counter()
    eb.run(24 * _H)
    t_run_bare = time.perf_counter() - t

    er = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware")
    er.set_enforce_capacity(True)
    t = time.perf_counter()
    er.run(24 * _H)
    replay_digest = _world_digest(er)
    t_run_replay = time.perf_counter() - t
    replay_identical = (replay_digest == world_digest)
    replay_overhead_pct = round(
        100.0 * (t_run_replay - t_run_bare) / max(t_run_bare, 1e-9), 1)

    # ── LATENCY: static (Dijkstra) baseline ─────────────────────────────────
    t = time.perf_counter()
    StaticPlanner().plan(reqs, ctx)
    t_static = time.perf_counter() - t

    # ── LATENCY: certified (A*) + decomposition ─────────────────────────────
    planner = CapacityAwarePlanner()
    t = time.perf_counter()
    cres = planner.plan(reqs, ctx)
    t_certify = time.perf_counter() - t
    tim = dict(planner.last_timings)
    t_astar = sum(d.search_ns for d in planner.last_decision_trace
                  if d.search_ns) / 1e9
    t_ledger = max(0.0, tim["t_commit_s"] - t_astar)  # order + residual mutate
    certify_digest = _certify_digest(planner.last_decision_trace)

    # determinism: re-plan, assert identical plan digest.
    planner2 = CapacityAwarePlanner()
    planner2.plan(reqs, ctx)
    deterministic = (_certify_digest(planner2.last_decision_trace)
                     == certify_digest)

    # ── apply / pin → execute → attribute ───────────────────────────────────
    eng = _engine.PyEngine(
        b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc, _H, "time_aware")
    t = time.perf_counter()
    apply_plan(eng, cres, ctx, decision_trace=planner.last_decision_trace,
               sizes=sizes, pinned=True)
    t_apply = time.perf_counter() - t
    eng.set_enforce_capacity(True)
    t = time.perf_counter()
    eng.run(24 * _H)
    t_des = time.perf_counter() - t
    t = time.perf_counter()
    split = three_way_split(planner.last_decision_trace, eng)
    fid = measure_fidelity(planner.last_decision_trace, eng)
    t_attr = time.perf_counter() - t

    planned = sum(1 for r in cres if r.status == "planned")
    certify_total = (tim["t_setup_s"] + tim["t_profiles_s"]
                     + tim["t_commit_s"] + t_apply + t_des + t_attr)

    return {
        # identity / counts (BYTE-ASSERTED)
        "demand": demand, "seed": seed,
        "events_processed": events,
        "greedy_delivered": kg["total_delivered"],
        "greedy_on_time": kg["total_on_time"],
        "greedy_stranded": kg["stranded_flow_units"],
        "world_digest": world_digest[:16],
        "certified_planned": planned,
        "guaranteed_on_time": split["guaranteed_on_time"],
        "certify_digest": certify_digest[:16],
        "deterministic": deterministic,
        "replay_identical": replay_identical,
        # throughput (REPORTED)
        "t_greedy_run_s": round(t_greedy, 4),
        "flow_units_per_s": round(demand / max(t_greedy, 1e-9), 0),
        "events_per_s": round(events / max(t_greedy, 1e-9), 0),
        # latency (REPORTED)
        "t_gen_s": round(t_gen, 4),
        "t_static_s": round(t_static, 4),
        "static_us_per_fu": round(1e6 * t_static / demand, 2),
        "t_certify_s": round(t_certify, 4),
        "certify_us_per_fu": round(1e6 * t_certify / demand, 2),
        "t_setup_s": tim["t_setup_s"],
        "t_oracle_s": tim["t_profiles_s"],
        "t_astar_s": round(t_astar, 4),
        "t_ledger_s": round(t_ledger, 4),
        "t_apply_s": round(t_apply, 4),
        "t_des_s": round(t_des, 4),
        "t_attribution_s": round(t_attr, 4),
        "t_certify_pipeline_s": round(certify_total, 4),
        "certify_over_greedy_x": round(certify_total / max(t_greedy, 1e-9), 2),
        # replay (REPORTED)
        "t_run_bare_s": round(t_run_bare, 4),
        "t_run_replay_s": round(t_run_replay, 4),
        "replay_overhead_pct": replay_overhead_pct,
        # memory (REPORTED) — isolated: this worker owns one scale
        "peak_rss_mb": _peak_rss_mb(),
        "fidelity_material": fid.material,
    }


# ── orchestrator: spawn one worker per scale, tabulate, golden-compare ───────

def _hardware_caption() -> dict:
    cpu = platform.processor() or platform.machine()
    try:  # a friendlier CPU string on macOS
        cpu = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, check=True).stdout.strip() or cpu
    except Exception:  # noqa: BLE001 — caption only; any failure ⇒ fall back
        pass
    return {
        "cpu": cpu,
        "machine": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "note": "wall-times and memory are REPORTED (machine-dependent); "
                "counts and digests are byte-asserted in repro/expected/benchmark.json",
    }


def _canonical(rows: list[dict]) -> list[dict]:
    return [{k: r[k] for k in CANONICAL_FIELDS} for r in rows]


def _spawn(demand: int, seed: int) -> dict:
    proc = subprocess.run(
        [sys.executable, __file__, "scale", "--demand", str(demand),
         "--seed", str(seed)],
        cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"[benchmark] scale {demand} FAILED ({proc.returncode})")
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


def _fmt_int(n) -> str:
    return f"{int(n):,}"


def _print_tables(rows: list[dict], caption: dict) -> None:
    p = print
    cap = (f"Hardware: {caption['cpu']} · {caption['os']} · "
           f"Python {caption['python']}. Regime: backbone / hourly / loose, "
           f"seed {rows[0]['seed']}. Wall-times & memory REPORTED "
           f"(machine-dependent); counts & digests byte-asserted.")

    p("\n" + "=" * 78)
    p("Table 1 — THROUGHPUT: greedy DES world run (no plan)")
    p("=" * 78)
    p(f"{'flow-units':>12} {'events':>14} {'run (s)':>10} "
      f"{'fu/s':>14} {'events/s':>14}")
    p("-" * 78)
    for r in rows:
        p(f"{_fmt_int(r['demand']):>12} {_fmt_int(r['events_processed']):>14} "
          f"{r['t_greedy_run_s']:>10.3f} {_fmt_int(r['flow_units_per_s']):>14} "
          f"{_fmt_int(r['events_per_s']):>14}")

    p("\n" + "=" * 78)
    p("Table 2 — DECISION LATENCY: static (Dijkstra) vs certified (A*)")
    p("=" * 78)
    p(f"{'flow-units':>12} {'static (s)':>11} {'µs/fu':>8} "
      f"{'certify (s)':>12} {'µs/fu':>8}  {'speedup':>8}")
    p("-" * 78)
    for r in rows:
        sp = r["t_static_s"] / max(r["t_certify_s"], 1e-9)
        p(f"{_fmt_int(r['demand']):>12} {r['t_static_s']:>11.3f} "
          f"{r['static_us_per_fu']:>8.1f} {r['t_certify_s']:>12.3f} "
          f"{r['certify_us_per_fu']:>8.1f}  {sp:>7.2f}x")
    p("  speedup = static/certify wall (>1 ⇒ certified A* is FASTER than the "
      "capacity-blind\n  Dijkstra baseline — the destination-profile heuristic "
      "keeps the search tiny).")

    p("\n" + "=" * 78)
    p("Table 3 — CERTIFY COST DECOMPOSITION (seconds; where the certify cost goes)")
    p("=" * 78)
    p(f"{'flow-units':>11} {'setup':>7} {'oracle':>7} {'a*':>7} {'ledger':>7} "
      f"{'apply':>7} {'des':>7} {'attrib':>7} {'total':>8}")
    p("-" * 78)
    for r in rows:
        p(f"{_fmt_int(r['demand']):>11} {r['t_setup_s']:>7.3f} "
          f"{r['t_oracle_s']:>7.3f} {r['t_astar_s']:>7.3f} "
          f"{r['t_ledger_s']:>7.3f} {r['t_apply_s']:>7.3f} {r['t_des_s']:>7.3f} "
          f"{r['t_attribution_s']:>7.3f} {r['t_certify_pipeline_s']:>8.3f}")

    p("\n" + "=" * 78)
    p("Table 4 — REPLAY (determinism) & MEMORY")
    p("=" * 78)
    p(f"{'flow-units':>12} {'bare (s)':>10} {'replay (s)':>11} "
      f"{'overhead':>9} {'identical':>10} {'peak RSS (MB)':>14}")
    p("-" * 78)
    for r in rows:
        p(f"{_fmt_int(r['demand']):>12} {r['t_run_bare_s']:>10.3f} "
          f"{r['t_run_replay_s']:>11.3f} {r['replay_overhead_pct']:>8.1f}% "
          f"{str(r['replay_identical']):>10} {r['peak_rss_mb']:>14.1f}")
    p("  overhead = cost of capturing records + the byte-identity digest "
      "(what a\n  VERIFIABLE/replayable run costs over a discard run); engine "
      "determinism itself is free.")
    p("\n" + cap + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("scale", help="worker: one scale, prints a JSON row")
    w.add_argument("--demand", type=int, required=True)
    w.add_argument("--seed", type=int, default=42)

    ap.add_argument("--scales", default=",".join(str(s) for s in DEFAULT_SCALES))
    ap.add_argument("--seed", type=int, default=42)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="(re)write repro/expected/benchmark.json + data/benchmark.json")
    mode.add_argument("--verify", action="store_true",
                      help="byte-assert canonical counts vs the golden")
    args = ap.parse_args()

    if args.cmd == "scale":
        print(json.dumps(run_scale(args.demand, args.seed)))
        return

    scales = [int(s) for s in args.scales.split(",")]
    rows = []
    for s in scales:
        print(f"[benchmark] scale={_fmt_int(s)} …", file=sys.stderr)
        rows.append(_spawn(s, args.seed))
    caption = _hardware_caption()
    _print_tables(rows, caption)

    canonical = _canonical(rows)
    golden_blob = json.dumps(canonical, indent=1, sort_keys=True) + "\n"

    if args.write:
        EXPECTED.parent.mkdir(parents=True, exist_ok=True)
        DATA.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED.write_text(golden_blob)
        DATA.write_text(json.dumps(
            {"caption": caption, "rows": rows}, indent=1, sort_keys=True) + "\n")
        print(f"[benchmark] wrote {EXPECTED.relative_to(ROOT)} "
              f"({len(canonical)} rows) and {DATA.relative_to(ROOT)}",
              file=sys.stderr)
    elif args.verify:
        if not EXPECTED.exists():
            raise SystemExit(f"[benchmark] missing golden {EXPECTED}")
        want = json.loads(EXPECTED.read_text())
        got = json.loads(golden_blob)
        if want != got:
            # surface the first differing scale for a readable failure
            for a, b in zip(got, want):
                if a != b:
                    diffs = {k: (a.get(k), b.get(k)) for k in CANONICAL_FIELDS
                             if a.get(k) != b.get(k)}
                    raise SystemExit(
                        f"[benchmark] MISMATCH at demand={a.get('demand')}: "
                        f"{diffs}")
            raise SystemExit("[benchmark] MISMATCH (row count differs)")
        print(f"[benchmark] byte-identical: {len(got)} scales verified",
              file=sys.stderr)


if __name__ == "__main__":
    main()
