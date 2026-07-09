#!/usr/bin/env python
"""C2 Phase-2 analysis: the pre-registered tables from the raw experiment rows.

Inputs: the Part-A / Part-B jsonl row dumps (or their run folders). Produces:
1. The five-condition table (never / always / severity / value-A / value-B /
   oracle), realized on-time mean ± std over EVALUATION disruption seeds at
   q = 1.0, thresholds tuned ONLY on the TUNING seeds (first 5 of --dseeds,
   per the pre-registration) and frozen.
2. The forecast-quality sweep data (per family at frozen τ, per q).
3. The Δ-distribution (Amendment 1): per-cell stats + fraction of
   negative-Δ epochs (Part A cells; Part B per-epoch).
4. The estimator error decomposition (Amendment 2): no-op-bound slack vs
   omitted externality, per kind × severity.
5. The budgeted ranking table (Part B): per family × k, with regret vs the
   oracle ranking.

Threshold grids (frozen; the tuning step may only PICK): severity
{0, 50, 150, 400, 1000}; value-A {−50, 0, 25, 100, 300}; value-B
{0, 25, 100, 300}. Noise: x̂ = x·(1+(1−q)·η), η keyed
(scenario_seed, disruption_seed, kind, severity, salt).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

from epure_arena.harness.runlake import emit_run, markdown_table

# Prior-step inputs are clone-relative (under gitignored runs/), never /tmp.
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


TUNE_SEEDS = {1, 2, 3, 4, 5}
QS = (0.25, 0.5, 0.75, 1.0)
GRIDS = {
    "severity": [0, 50, 150, 400, 1000],
    "value_A": [-50, 0, 25, 100, 300],
    "value_B": [0, 25, 100, 300],
}
SCORE_KEY = {"severity": "severity_score", "value_A": "dhat_A",
             "value_B": "dhat_B"}


def eta(r: dict, salt: str) -> float:
    import hashlib
    h = hashlib.sha256(
        f"{r['scenario_seed']}|{r['disruption_seed']}|{r['kind']}|"
        f"{r['severity']}|{salt}".encode()).digest()
    return (int.from_bytes(h[:8], "big") / 2**63) - 1.0


def decide(r: dict, family: str, tau: float, q: float) -> bool:
    if family == "never":
        return False
    if family == "always":
        return True
    if family == "oracle":
        return r["delta_true"] > 0
    x = r[SCORE_KEY[family]]
    salt = "sev" if family == "severity" else "val"
    return x * (1 + (1 - q) * eta(r, salt)) > tau


def value(r: dict, act: bool) -> int:
    return r["v_always"] if act else r["v_never"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parta", default=_DEFAULT_PARTA)
    ap.add_argument("--partb", default=_DEFAULT_PARTB)
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    rows = _require_jsonl(args.parta, "partA")
    tune = [r for r in rows if r["disruption_seed"] in TUNE_SEEDS]
    evl = [r for r in rows if r["disruption_seed"] not in TUNE_SEEDS]

    # ── tune thresholds at q=1.0 on tuning seeds, freeze ────────────────
    frozen: dict[str, float] = {}
    for fam, grid in GRIDS.items():
        best_tau, best_v = None, None
        for tau in grid:
            v = sum(value(r, decide(r, fam, tau, 1.0)) for r in tune)
            if best_v is None or v > best_v:
                best_tau, best_v = tau, v
        frozen[fam] = best_tau

    # ── five-condition table (q = 1.0, evaluation seeds) ────────────────
    def cond_stats(fam: str, tau: float | None, q: float):
        per_seed: dict[tuple, int] = {}
        acts = 0
        for r in evl:
            act = decide(r, fam, tau if tau is not None else 0.0, q)
            acts += int(act)
            key = (r["world"], r["scenario_seed"], r["disruption_seed"])
            per_seed[key] = per_seed.get(key, 0) + value(r, act)
        vals = list(per_seed.values())
        return (statistics.mean(vals), statistics.stdev(vals), acts)

    five = []
    for fam, tau in (("never", None), ("always", None),
                     ("severity", frozen["severity"]),
                     ("value_A", frozen["value_A"]),
                     ("value_B", frozen["value_B"]), ("oracle", None)):
        m, sd, acts = cond_stats(fam, tau, 1.0)
        five.append({"condition": fam,
                     "tau": tau if tau is not None else "—",
                     "realized_on_time": f"{m:.0f} ± {sd:.0f}",
                     "interventions": acts,
                     "_mean": m})
    oracle_mean = next(x["_mean"] for x in five if x["condition"] == "oracle")
    for x in five:
        x["regret_vs_oracle"] = f"{oracle_mean - x.pop('_mean'):.0f}"

    # ── q-sweep data ─────────────────────────────────────────────────────
    sweep = []
    for q in QS:
        row = {"q": q}
        for fam in ("severity", "value_A", "value_B"):
            m, sd, _ = cond_stats(fam, frozen[fam], q)
            row[fam] = f"{m:.0f} ± {sd:.0f}"
            row[f"_{fam}"] = m
        sweep.append(row)
    # monotonicity (per family: deltas between adjacent q means)
    mono = {fam: [round(sweep[i + 1][f"_{fam}"] - sweep[i][f"_{fam}"], 1)
                  for i in range(len(QS) - 1)]
            for fam in ("severity", "value_A", "value_B")}
    for row in sweep:
        for fam in ("severity", "value_A", "value_B"):
            row.pop(f"_{fam}", None)

    # ── Δ distribution + decomposition per kind × severity ──────────────
    dist = []
    for kind in sorted({r["kind"] for r in rows}):
        for sev in ("mild", "moderate", "severe"):
            cell = [r for r in rows if r["kind"] == kind
                    and r["severity"] == sev]
            if not cell:
                continue
            deltas = [r["delta_true"] for r in cell]
            dist.append({
                "kind": kind, "severity": sev, "n": len(cell),
                "delta_mean": round(statistics.mean(deltas), 1),
                "delta_min": min(deltas), "delta_max": max(deltas),
                "frac_negative": round(
                    sum(1 for d in deltas if d < 0) / len(deltas), 3),
                "noop_bound_slack_mean": round(statistics.mean(
                    r["noop_bound_slack"] for r in cell), 1),
                "externality_mean": round(statistics.mean(
                    r["externality"] for r in cell), 1),
                "severity_score_mean": round(statistics.mean(
                    r["severity_score"] for r in cell), 1),
            })

    # ── Part B: budgeted ranking table ───────────────────────────────────
    brows = _require_jsonl(args.partb, "partB")
    budget_tbl = []
    fams = ("severity", "value_B", "value_A", "oracle")
    ks = sorted({int(k.split("top")[1]) for r in brows for k in r
                 if "_top" in k})
    for fam in fams:
        for k in ks:
            key = f"{fam}_top{k}"
            vals = [r[key] for r in brows if key in r]
            ovals = [r[f"oracle_top{k}"] for r in brows]
            budget_tbl.append({
                "family": fam, "k": k,
                "realized_on_time": f"{statistics.mean(vals):.0f} ± "
                                    f"{statistics.stdev(vals):.0f}",
                "regret_vs_oracle_topk": f"{statistics.mean(o - v for v, o in zip(vals, ovals)):.0f}",
            })
    neg_fracs = [r["frac_negative_delta"] for r in brows]
    budget_meta = {
        "n_suites": len(brows),
        "frac_negative_delta_mean": round(statistics.mean(neg_fracs), 3),
        "v_never_mean": round(statistics.mean(r["v_never"] for r in brows), 0),
        "v_always_mean": round(statistics.mean(r["v_always"] for r in brows), 0),
    }

    out = {
        "frozen_thresholds": frozen,
        "five_condition": five,
        "q_sweep": sweep,
        "monotonicity_deltas": mono,
        "delta_distribution": dist,
        "budgeted": budget_tbl,
        "budget_meta": budget_meta,
    }
    print(json.dumps(out, indent=1))

    if not args.no_emit:
        folder = emit_run(
            name="gate-experiment-analysis",
            config={"parta": args.parta, "partb": args.partb,
                    "frozen_thresholds": frozen,
                    "monotonicity_deltas": mono, "budget_meta": budget_meta},
            seed=42, schema_fingerprint="a240a6e82d789398",
            metrics_rows=dist,
            repro_cmd="python repro/gate_analysis.py",
            extra_files={
                "five_condition.md": markdown_table(five),
                "q_sweep.md": markdown_table(sweep),
                "budgeted_ranking.md": markdown_table(budget_tbl),
            },
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
