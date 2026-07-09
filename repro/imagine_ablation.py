#!/usr/bin/env python
"""C2 Phase 3 — Imagine ablation: Δ-estimator variants graded by DECISION
quality (budgeted ranking regret, PAIRED per suite), not RMSE.

Estimator arms:
- analytic: Δ̂_B = n_recert_dry (the dry-run planner; the standing estimator).
- ridge / hist_gbt: trained on Part-A TUNING rows (dseeds 1-5), features
  declared = kind one-hot, severity ordinal, world one-hot, severity_score,
  n_recert_dry, degraded_ub; label = exact paired Δ (realized on-time).
  n_recert_dry is openly a feature: the learned arms test whether learning
  the EXTERNALITY RESIDUAL on top of the dry-run improves decisions.
- rollout: truncated paired rollouts (onset + 6h cut, certified-pending
  accounting) on the DECLARED subset (backbone, sseed 42, severe, dseeds
  1-6) — truth at cost.

HYPOTHESIS (rider 2, stated in advance): per the Amendment-2 decomposition,
the learned arms improve over the analytic estimator iff they capture the
SURGE externality term (surge cells: slack 0, externality 250-530); on
slack-dominated outage epochs they should MATCH, not beat, the dry-run.

GRADING (rider 1): per-suite PAIRED regret vs the oracle ranking at
k ∈ {1, 2, 4} on the Part-B suites (identical suites and dseeds across
estimators); seed-level CIs; achieved power reported (minimal detectable
paired difference at α = 0.05). If estimators do not separate beyond noise,
that is the result. No suite expansion without a gate.

Usage (node-engine venv, repo root):
    python repro/imagine_ablation.py
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
import time

from epure_arena.harness.runlake import emit_run, markdown_table
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.harness.live_loop import run_live_loop

# Prior-step inputs are clone-relative (under gitignored runs/), never a
# machine-global /tmp path: a stranger's stale /tmp file must not silently
# feed a reproduce run. reproduce-paper pipes gate_experiment into these.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_PARTA = str(_ROOT / "runs" / "gate_partA.jsonl")
_DEFAULT_PARTB = str(_ROOT / "runs" / "gate_partB.jsonl")


def _require_jsonl(path: str, part: str) -> list:
    """Load a JSONL prior-step file; if absent, say exactly how to make it."""
    if not pathlib.Path(path).exists():
        sys.exit(f"missing {part} input: {path}\n"
                 f"produce it first (clone-relative, no /tmp):\n"
                 f"  python repro/gate_experiment.py {part} --no-emit > {path}")
    return [json.loads(line) for line in open(path)]


_H = 3_600_000
# MUST match gate_experiment.py exactly: make_disruptions consumes one
# rng stream per kind IN ORDER — a different order is a different world.
KINDS = ("lane_outage", "node_outage", "capacity_shock", "demand_surge")
TUNE = {1, 2, 3, 4, 5}


def realized_on_time(flows):
    return sum(v for k, v in flows.items() if k.endswith("_on_time"))


def feats(kind: str, severity: str, world: str, sev_score: float,
          n_recert: float, degraded_ub: float) -> list[float]:
    return ([1.0 if kind == k else 0.0 for k in KINDS]
            + [{"mild": 1, "moderate": 2, "severe": 3}[severity],
               1.0 if world == "mesh" else 0.0,
               sev_score, n_recert, degraded_ub])


def train_models(parta_rows):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    tr = [r for r in parta_rows if r["disruption_seed"] in TUNE]
    X = [feats(r["kind"], r["severity"], r["world"], r["severity_score"],
               r["n_recert_dry"], r["degraded_ub"]) for r in tr]
    y = [r["delta_true"] for r in tr]
    t0 = time.perf_counter()
    ridge = Ridge(alpha=1.0).fit(X, y)
    t_ridge = time.perf_counter() - t0
    t0 = time.perf_counter()
    gbt = HistGradientBoostingRegressor(random_state=0).fit(X, y)
    t_gbt = time.perf_counter() - t0
    return ridge, gbt, len(tr), t_ridge, t_gbt


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parta", default=_DEFAULT_PARTA)
    ap.add_argument("--partb", default=_DEFAULT_PARTB)
    ap.add_argument("--budgets", default="1,2,4")
    ap.add_argument("--rollout-dseeds", default="1,2,3,4,5,6")
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    parta = _require_jsonl(args.parta, "partA")
    partb = _require_jsonl(args.partb, "partB")
    ks = [int(x) for x in args.budgets.split(",")]

    ridge, gbt, n_train, t_ridge, t_gbt = train_models(parta)
    print(json.dumps({"trained_on": n_train, "t_ridge_s": round(t_ridge, 3),
                      "t_gbt_s": round(t_gbt, 3)}))

    # ── rider-2 residual diagnostics on Part-A EVAL rows, per kind ───────
    evl = [r for r in parta if r["disruption_seed"] not in TUNE]
    resid_tbl = []
    for kind in KINDS:
        cell = [r for r in evl if r["kind"] == kind]
        Xc = [feats(r["kind"], r["severity"], r["world"], r["severity_score"],
                    r["n_recert_dry"], r["degraded_ub"]) for r in cell]
        rid = ridge.predict(Xc)
        gb = gbt.predict(Xc)
        resid_tbl.append({
            "kind": kind, "n": len(cell),
            "analytic_mae": round(statistics.mean(
                abs(r["n_recert_dry"] - r["delta_true"]) for r in cell), 1),
            "ridge_mae": round(statistics.mean(
                abs(p - r["delta_true"]) for p, r in zip(rid, cell)), 1),
            "gbt_mae": round(statistics.mean(
                abs(p - r["delta_true"]) for p, r in zip(gb, cell)), 1),
            "mean_externality": round(statistics.mean(
                r["externality"] for r in cell), 1),
            "mean_slack": round(statistics.mean(
                r["noop_bound_slack"] for r in cell), 1),
        })

    # ── budgeted ranking on Part-B suites (paired) ────────────────────────
    # Existing arm outcomes are in the rows; learned arms may select new
    # sets → run those legs (deterministic regeneration).
    suite_cache: dict[tuple, dict[frozenset, int]] = {}

    def suite_setup(r):
        b = generate_scenario(ScenarioSpec(
            topology=r["world"], n_nodes=200, density="moderate",
            capacity="tight", demand_pattern="diurnal",
            schedule_cadence="hourly", priority_mix="mixed",
            n_demand_events=40_000, horizon_hours=24,
            seed=r["scenario_seed"]))
        ds = make_disruptions(b, kinds=KINDS, severity=r["severity"],
                              horizon_hours=24, seed=r["disruption_seed"],
                              events_per_kind=2)
        kinds_by_epoch = [d.kind for d in
                          sorted(ds, key=lambda x: (x.onset_atu, x.kind))]
        return b, ds, kinds_by_epoch

    def v_of(r, sel: frozenset) -> int:
        key = (r["world"], r["scenario_seed"], r["severity"],
               r["disruption_seed"])
        cache = suite_cache.setdefault(key, {})
        if sel in cache:
            return cache[sel]
        b, ds, _ = suite_setup(r)
        leg = run_live_loop(b, ds, intervene_epochs=set(sel),
                            horizon_hours=24)
        assert leg.mass_closed
        cache[sel] = realized_on_time(leg.flows)
        return cache[sel]

    rollout_dseeds = {int(x) for x in args.rollout_dseeds.split(",")}
    suite_rows = []
    for r in partb:
        n_ep = r["n_epochs"]
        sev_s = r["scores"]["severity"]
        vb = r["scores"]["value_B"]
        va = r["scores"]["value_A"]
        ub = [b - a for b, a in zip(vb, va)]  # degraded_ub per epoch
        _, _, kinds_by_epoch = suite_setup(r)
        X = [feats(kinds_by_epoch[i], r["severity"], r["world"], sev_s[i],
                   vb[i], ub[i]) for i in range(n_ep)]
        scores = {
            "analytic": vb,
            "ridge": list(map(float, ridge.predict(X))),
            "hist_gbt": list(map(float, gbt.predict(X))),
            "severity": sev_s,
            "oracle": r["scores"]["oracle"],
        }
        # rollout arm on the declared subset only
        is_rollout_suite = (r["world"] == "backbone"
                            and r["scenario_seed"] == 42
                            and r["severity"] == "severe"
                            and r["disruption_seed"] in rollout_dseeds)
        t_roll = None
        if is_rollout_suite:
            b, ds, _ = suite_setup(r)
            ds_sorted = sorted(ds, key=lambda x: (x.onset_atu, x.kind))
            t0 = time.perf_counter()
            roll = []
            for i, d in enumerate(ds_sorted):
                cut = d.onset_atu + 6 * _H
                nv = run_live_loop(b, ds, intervene_epochs=set(),
                                   horizon_hours=24, truncate_at=cut)
                ac = run_live_loop(b, ds, intervene_epochs={i},
                                   horizon_hours=24, truncate_at=cut)
                roll.append(realized_on_time(ac.flows)
                            + ac.guaranteed_on_time
                            - realized_on_time(nv.flows)
                            - nv.guaranteed_on_time)
            t_roll = round(time.perf_counter() - t0, 1)
            scores["rollout"] = [float(x) for x in roll]

        srow = {"world": r["world"], "scenario_seed": r["scenario_seed"],
                "severity": r["severity"],
                "disruption_seed": r["disruption_seed"],
                "rollout_wall_s": t_roll}
        for fam, sc in scores.items():
            for k in ks:
                order = sorted(range(n_ep), key=lambda i: (-sc[i], i))
                sel = frozenset(order[:k])
                key = f"{fam}_top{k}"
                if key in r:  # reuse the Part-B outcomes (paired identity)
                    suite_cache.setdefault(
                        (r["world"], r["scenario_seed"], r["severity"],
                         r["disruption_seed"]), {})[sel] = r[key]
                srow[key] = v_of(r, sel)
        suite_rows.append(srow)
        print(json.dumps(srow))
        sys.stdout.flush()

    # ── rider-1 paired analysis ───────────────────────────────────────────
    paired = []
    for fam in ("analytic", "ridge", "hist_gbt", "severity"):
        for k in ks:
            diffs = [s[f"oracle_top{k}"] - s[f"{fam}_top{k}"]
                     for s in suite_rows if f"{fam}_top{k}" in s]
            n = len(diffs)
            m = statistics.mean(diffs)
            sd = statistics.stdev(diffs) if n > 1 else 0.0
            se = sd / math.sqrt(n) if n else 0.0
            # seed-level (scenario-seed) means
            by_seed = {}
            for s, d in zip(suite_rows, diffs):
                by_seed.setdefault(s["scenario_seed"], []).append(d)
            seed_means = [statistics.mean(v) for v in by_seed.values()]
            paired.append({
                "estimator": fam, "k": k, "n_suites": n,
                "paired_regret_vs_oracle": f"{m:.1f} ± {1.96 * se:.1f}",
                "seed_level_means": [round(x, 1) for x in seed_means],
                "mdd_alpha05": round(1.96 * se, 1),  # minimal detectable diff
            })
    roll_paired = []
    for k in ks:
        diffs = [s[f"oracle_top{k}"] - s[f"rollout_top{k}"]
                 for s in suite_rows if f"rollout_top{k}" in s]
        if diffs:
            roll_paired.append({
                "estimator": "rollout(subset)", "k": k,
                "n_suites": len(diffs),
                "paired_regret_vs_oracle":
                    f"{statistics.mean(diffs):.1f}",
                "subset": "backbone/s42/severe/d1-6",
            })

    out = {"residuals_per_kind": resid_tbl, "paired_ranking": paired,
           "rollout_paired": roll_paired}
    print(json.dumps(out, indent=1))

    if not args.no_emit:
        folder = emit_run(
            name="imagine-ablation",
            config=vars(args), seed=42,
            schema_fingerprint="a240a6e82d789398",
            metrics_rows=suite_rows,
            repro_cmd="python repro/imagine_ablation.py "
                      + " ".join(sys.argv[1:]),
            extra_files={
                "residuals_per_kind.md": markdown_table(resid_tbl),
                "paired_ranking.md": markdown_table(paired + roll_paired),
            },
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
