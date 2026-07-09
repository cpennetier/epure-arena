#!/usr/bin/env python
"""The gate experiment (pre-registered design + approved amendments).

PART A (unbudgeted, single-epoch suites). For each (world × scenario seed ×
kind × severity × disruption seed): exactly TWO rollouts — never (with the
scoring pass) and always — plus the dry-run scorer. Because the suite has
one epoch, every gated condition's outcome is EXACTLY the never- or
always-leg selected by its decision, so the five conditions × threshold
grids × forecast-quality sweep are evaluated by leg lookup (no
approximation). Noise channel: x̂ = x·(1+(1−q)·η), η ∈ U[−1,1] keyed
(scenario_seed, disruption_seed, kind, severity, salt) — deterministic.

Estimator arms, declared: value-A (pre-registered: Δ̂_A = n_recert −
degraded_UB; conservative bound) and value-B (declared post-smoke BEFORE any
experiment data: Δ̂_B = n_recert, degraded on-time ≈ 0, justified by the
Phase-1 measurement 7/586 ≈ 1%). Amendment-2 decomposition recorded per
epoch: noop_bound_slack = degraded_UB − degraded_actual_on_time;
externality = Δ_guaranteed_gain − Δ_realized contribution of non-affected
mass (computed from the paired typed flows).

PART B (budgeted, Amendment 1). Multi-epoch suites (4 kinds ×
events_per_kind=2 = 8 epochs). Scores from the never leg (offline scoring,
declared: ranking from no-op-baseline scores; outcomes measure the value of
the selected set). Oracle scores from per-epoch paired legs (intervene at i
only). Ranking arms: top-k by severity / Δ̂_B / oracle-Δ, k ∈ {1, 2, 4};
identical selected sets are cached.

Usage (node-engine venv, repo root):
    python repro/gate_experiment.py partA --worlds backbone,mesh
    python repro/gate_experiment.py partB --worlds backbone,mesh
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time

from epure_arena.harness.runlake import emit_run
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.harness.live_loop import run_live_loop

_H = 3_600_000
WORLDS = {
    "backbone": dict(topology="backbone", density="moderate"),
    "mesh": dict(topology="mesh", density="moderate"),
}
KINDS = ("lane_outage", "node_outage", "capacity_shock", "demand_surge")
SEVERITIES = ("mild", "moderate", "severe")


def eta(scenario_seed: int, disruption_seed: int, kind: str, severity: str,
        salt: str) -> float:
    """Deterministic pseudo-noise in [-1, 1]."""
    h = hashlib.sha256(
        f"{scenario_seed}|{disruption_seed}|{kind}|{severity}|{salt}".encode()
    ).digest()
    return (int.from_bytes(h[:8], "big") / 2**63) - 1.0


def realized_on_time(flows: dict[str, int]) -> int:
    return sum(v for k, v in flows.items() if k.endswith("_on_time"))


def bundle_for(world: str, scenario_seed: int, demand: int):
    w = WORLDS[world]
    return generate_scenario(ScenarioSpec(
        topology=w["topology"], n_nodes=200, density=w["density"],
        capacity="tight", demand_pattern="diurnal", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=demand, horizon_hours=24,
        seed=scenario_seed,
    ))


def part_a(args) -> None:
    rows = []
    dseeds = [int(x) for x in args.dseeds.split(",")]
    for world in args.worlds.split(","):
        for sseed in (int(x) for x in args.sseeds.split(",")):
            b = bundle_for(world, sseed, args.demand)
            for kind in KINDS:
                for severity in SEVERITIES:
                    for dseed in dseeds:
                        ds = make_disruptions(
                            b, kinds=(kind,), severity=severity,
                            horizon_hours=24, seed=dseed)
                        t0 = time.perf_counter()
                        never = run_live_loop(b, ds, intervene_epochs=set(),
                                              score_epochs=True,
                                              horizon_hours=24)
                        act = run_live_loop(b, ds, intervene_epochs={0},
                                            horizon_hours=24)
                        wall = time.perf_counter() - t0
                        assert never.mass_closed and act.mass_closed
                        tr = never.epoch_traces[0]
                        atr = act.epoch_traces[0]
                        v_never = realized_on_time(never.flows)
                        v_act = realized_on_time(act.flows)
                        # Amendment-2 decomposition pieces:
                        degraded_actual = sum(
                            v for k, v in never.flows.items()
                            if k.startswith("degraded_") and
                            k.endswith("_on_time"))
                        delta_guaranteed = (act.guaranteed_on_time
                                            - never.guaranteed_on_time)
                        row = {
                            "world": world, "scenario_seed": sseed,
                            "kind": kind, "severity": severity,
                            "disruption_seed": dseed,
                            "severity_score": tr.severity_score,
                            "n_recert_dry": tr.n_recertifiable,
                            "degraded_ub": tr.degraded_feasible_ub,
                            "dhat_A": tr.value_estimate,
                            "dhat_B": float(tr.n_recertifiable),
                            "v_never": v_never, "v_always": v_act,
                            "delta_true": v_act - v_never,
                            "delta_guaranteed": delta_guaranteed,
                            "n_recommitted_actual": atr.n_recommitted,
                            "degraded_actual_on_time": degraded_actual,
                            "noop_bound_slack": (tr.degraded_feasible_ub
                                                 - degraded_actual),
                            "externality": delta_guaranteed - (v_act - v_never),
                            "guaranteed_never": never.guaranteed_on_time,
                            "guaranteed_always": act.guaranteed_on_time,
                            "never_flows": never.flows,
                            "always_flows": act.flows,
                            "wall_s": round(wall, 1),
                        }
                        rows.append(row)
                        print(json.dumps(row))
                        sys.stdout.flush()
    if rows and not args.no_emit:
        folder = emit_run(
            name="gate-experiment-partA",
            config=vars(args), seed=int(args.sseeds.split(",")[0]),
            schema_fingerprint="a240a6e82d789398",
            metrics_rows=rows,
            repro_cmd="python repro/gate_experiment.py partA "
                      + " ".join(sys.argv[2:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


def part_b(args) -> None:
    rows = []
    dseeds = [int(x) for x in args.dseeds.split(",")]
    ks = [int(x) for x in args.budgets.split(",")]
    for world in args.worlds.split(","):
        for sseed in (int(x) for x in args.sseeds.split(",")):
            b = bundle_for(world, sseed, args.demand)
            for severity in args.severities.split(","):
                for dseed in dseeds:
                    ds = make_disruptions(
                        b, kinds=KINDS, severity=severity, horizon_hours=24,
                        seed=dseed, events_per_kind=args.events_per_kind)
                    n_ep = len(ds)
                    t0 = time.perf_counter()
                    never = run_live_loop(b, ds, intervene_epochs=set(),
                                          score_epochs=True, horizon_hours=24)
                    v_never = realized_on_time(never.flows)
                    # per-epoch oracle Δ via at-i-only legs
                    delta = []
                    for i in range(n_ep):
                        leg = run_live_loop(b, ds, intervene_epochs={i},
                                            horizon_hours=24)
                        delta.append(realized_on_time(leg.flows) - v_never)
                    scores = {
                        "severity": [t.severity_score
                                     for t in never.epoch_traces],
                        "value_B": [float(t.n_recertifiable)
                                    for t in never.epoch_traces],
                        "value_A": [t.value_estimate
                                    for t in never.epoch_traces],
                        "oracle": [float(d) for d in delta],
                    }
                    always = run_live_loop(
                        b, ds, intervene_epochs=set(range(n_ep)),
                        horizon_hours=24)
                    cache: dict[frozenset, int] = {
                        frozenset(): v_never,
                        frozenset(range(n_ep)): realized_on_time(always.flows),
                    }

                    def v_of(sel: frozenset) -> int:
                        if sel not in cache:
                            leg = run_live_loop(b, ds, intervene_epochs=set(sel),
                                                horizon_hours=24)
                            assert leg.mass_closed
                            cache[sel] = realized_on_time(leg.flows)
                        return cache[sel]

                    arms = {}
                    for fam, sc in scores.items():
                        for k in ks:
                            order = sorted(range(n_ep),
                                           key=lambda i: (-sc[i], i))
                            sel = frozenset(order[:k])
                            arms[f"{fam}_top{k}"] = v_of(sel)
                    wall = time.perf_counter() - t0
                    row = {
                        "world": world, "scenario_seed": sseed,
                        "severity": severity, "disruption_seed": dseed,
                        "n_epochs": n_ep,
                        "delta_per_epoch": delta,
                        "frac_negative_delta": (sum(1 for d in delta if d < 0)
                                                / n_ep),
                        "scores": scores,
                        "v_never": v_never,
                        "v_always": cache[frozenset(range(n_ep))],
                        **arms,
                        "wall_s": round(wall, 1),
                    }
                    rows.append(row)
                    print(json.dumps(row))
                    sys.stdout.flush()
    if rows and not args.no_emit:
        folder = emit_run(
            name="gate-experiment-partB",
            config=vars(args), seed=int(args.sseeds.split(",")[0]),
            schema_fingerprint="a240a6e82d789398",
            metrics_rows=rows,
            repro_cmd="python repro/gate_experiment.py partB "
                      + " ".join(sys.argv[2:]),
        )
        print(f"run folder: {folder}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("part", choices=("partA", "partB"))
    ap.add_argument("--worlds", default="backbone,mesh")
    ap.add_argument("--sseeds", default="42,43,44")
    ap.add_argument("--dseeds", default="1,2,3,4,5,6,7,8,9,10,11,12,13,14,15",
                    help="partA: first 5 = tuning, rest = evaluation")
    ap.add_argument("--demand", type=int, default=40_000)
    ap.add_argument("--severities", default="moderate,severe",
                    help="partB only")
    ap.add_argument("--events-per-kind", type=int, default=2)
    ap.add_argument("--budgets", default="1,2,4")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()
    (part_a if args.part == "partA" else part_b)(args)


if __name__ == "__main__":
    main()
