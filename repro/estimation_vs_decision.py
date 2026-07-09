#!/usr/bin/env python
"""reproduce-estimation: a regime CHARACTERIZATION (not a headline result).

This is NOT "estimation ≠ decision as a law." It is an honest characterization
of an **over-determined** regime, byte-asserted from committed data.

The finding, stated plainly:

  * The budgeted decision here is dominated by ~2 high-value interventions per
    suite (the rest carry ≈0 realized value). So a simple top-k ranking — by
    almost any score — already reaches the optimum, and a better estimator
    cannot move the committed set:
      - **top-2 is over-determined**: budgeted ranking regret is identically 0
        for *every* estimator, including the non-value ``severity`` baseline.
        The #2→#3 value gap is too wide for any estimate error to reorder it.
      - **top-1 is within noise**: the estimators do **not** separate beyond
        ~1 standard error (k1 regret ≈ 82.6 ± 47 vs 103.5 ± 49 over 72 suites).
        The best-MAE estimator (gbt) is not the best decider, but the
        difference is not significant.

  * Meanwhile estimator MAE varies a lot — up to ~3.5× (3.48× on
    ``demand_surge`` where slack=0; 3.13× averaged). That accuracy lands
    exactly where it cannot change a decision: ``demand_surge`` has slack=0
    (the decision is forced) while the easy kinds carry huge slack.

So: in this regime, prediction accuracy and decision quality are decoupled —
not because of a deep law, but because the decision is **easy / over-determined
here**. A regime that actually stresses the decision (many comparable competing
interventions) is not produced by *these* generators; the purpose-built
controlled-bottleneck world that does is a separate experiment (``RESULTS.md``
sections 4-5, ``reproduce-decision-gap`` / ``reproduce-inflicted-wait``).

PROVENANCE. The raw gate Part-A run folder
(``gate-experiment-partA-20260611T142140Z-89759584``) is **gitignored and
absent**. This recomputes the characterization from the COMMITTED neutral
extract (``data/estimation_vs_decision.json``) and byte-asserts it — not a
from-zero re-derivation. No sklearn needed (the reason the extract is committed:
the gbt arm is not byte-stable across sklearn versions).

Usage:
    python repro/estimation_vs_decision.py --write    # regenerate golden
    python repro/estimation_vs_decision.py --verify   # assert byte-identity
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "estimation_vs_decision.json"
EXPECTED = ROOT / "repro" / "expected"

_KS = ("k1", "k2", "k4")


def headline(data: dict) -> dict:
    """Derive the (demoted) characterization from the committed extract."""
    em = data["estimator_mae"]
    mae = em["per_estimator_mean_mae"]
    rr = data["ranking_regret"]
    n = rr["n_suites"]
    estimators = [e for e in rr if e != "n_suites"]
    sqrt_n = math.sqrt(n)

    # ── decision axis WITH noise (the honest part) ──────────────────────
    regret = {}
    for e in estimators:
        regret[e] = {
            k: {"mean": rr[e][f"{k}_mean"],
                "ci95": round(1.96 * rr[e][f"{k}_sd"] / sqrt_n, 1)}
            for k in _KS
        }
    # do any two estimators separate beyond noise at any k? (non-overlapping CI)
    separations = []
    for k in _KS:
        for i in range(len(estimators)):
            for j in range(i + 1, len(estimators)):
                a, b = estimators[i], estimators[j]
                ma, mb = regret[a][k]["mean"], regret[b][k]["mean"]
                ca, cb = regret[a][k]["ci95"], regret[b][k]["ci95"]
                if abs(ma - mb) > ca + cb:
                    separations.append({"k": k, "a": a, "b": b,
                                        "delta": round(abs(ma - mb), 1)})
    top2_over_determined = all(rr[e]["k2_mean"] == 0 for e in estimators)

    # ── estimation axis, per kind (the mechanism) ───────────────────────
    per_kind = []
    for row in em["per_kind"]:
        arms = {"analytic": row["analytic_mae"], "gbt": row["gbt_mae"],
                "ridge": row["ridge_mae"]}
        best = min(arms, key=arms.__getitem__)
        per_kind.append({
            "kind": row["kind"],
            "analytic_mae": row["analytic_mae"],
            "gbt_mae": row["gbt_mae"],
            "ridge_mae": row["ridge_mae"],
            "best_estimator": best,
            "mae_cut_analytic_over_best": round(row["analytic_mae"] / arms[best], 2),
            "mean_slack": row["mean_slack"],
            "mean_externality": row["mean_externality"],
        })
    max_cut = max(per_kind, key=lambda r: r["mae_cut_analytic_over_best"])

    return {
        "kind": "characterization (not a headline result)",
        "n_suites": n,
        "decision": {
            "regret_with_ci95": regret,
            "top2_over_determined_regret_zero": top2_over_determined,
            "pairs_separating_beyond_noise": separations,
            "estimators_separate_on_decision": bool(separations),
        },
        "estimation": {
            "mean_per_kind_mae": mae,
            "mae_cut_averaged_analytic_over_gbt": round(mae["analytic"] / mae["gbt"], 2),
            "mae_cut_max": {
                "kind": max_cut["kind"],
                "ratio": max_cut["mae_cut_analytic_over_best"],
                "best_estimator": max_cut["best_estimator"],
                "mean_slack": max_cut["mean_slack"],
            },
            "per_kind": per_kind,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if not DATA.exists():
        raise SystemExit(f"[estimation] missing committed extract {DATA}")
    data = json.loads(DATA.read_text())
    h = headline(data)
    blob = json.dumps(h, indent=1, sort_keys=True) + "\n"
    path = EXPECTED / "estimation_vs_decision.json"
    EXPECTED.mkdir(parents=True, exist_ok=True)

    print("[estimation] NOTE: raw gate Part-A run folder "
          f"({data['provenance']['source_run_id']}) is absent/gitignored; "
          "reproducing the characterization from the committed extract.",
          file=sys.stderr)

    if args.write:
        path.write_text(blob)
        print(f"[estimation] wrote {path.relative_to(ROOT)}", file=sys.stderr)
    else:
        if not path.exists():
            raise SystemExit(f"[estimation] missing golden {path}")
        if path.read_text() != blob:
            raise SystemExit(f"[estimation] MISMATCH vs {path.relative_to(ROOT)}")
        print("[estimation] byte-identical", file=sys.stderr)

    d, e = h["decision"], h["estimation"]
    print(f"\nestimation vs decision — gate Part-A, {h['n_suites']} suites "
          "(committed extract)")
    print("CHARACTERIZATION, not a headline result: the budgeted decision is "
          "over-determined here.\n")

    print("DECISION axis — budgeted ranking regret vs oracle (mean ± 95% CI):")
    print(f"  {'estimator':<12}{'k=1':>16}{'k=2':>10}{'k=4':>14}")
    for est, r in d["regret_with_ci95"].items():
        cells = "  ".join(f"{r[k]['mean']:>6} ± {r[k]['ci95']:<5}" for k in _KS)
        print(f"  {est:<12}{cells}")
    sep = d["pairs_separating_beyond_noise"]
    print(f"  → top-2 regret 0 for every estimator (over-determined): "
          f"{d['top2_over_determined_regret_zero']}")
    print(f"  → any estimator pair separating beyond noise: "
          f"{d['estimators_separate_on_decision']}"
          + (f" {sep}" if sep else " (none — accuracy does not move the decision)"))

    mx = e["mae_cut_max"]
    print(f"\nESTIMATION axis — MAE varies up to {mx['ratio']}× "
          f"({mx['kind']}, analytic→{mx['best_estimator']}, slack={mx['mean_slack']}); "
          f"{e['mae_cut_averaged_analytic_over_gbt']}× averaged (analytic→gbt)")
    print(f"\n  {'kind':<16}{'analytic':>9}{'gbt':>8}{'ridge':>8}{'cut→best':>10}"
          f"{'slack':>9}{'extern.':>9}")
    print("  " + "-" * 67)
    for r in e["per_kind"]:
        print(f"  {r['kind']:<16}{r['analytic_mae']:>9}{r['gbt_mae']:>8}"
              f"{r['ridge_mae']:>8}{r['mae_cut_analytic_over_best']:>9}×"
              f"{r['mean_slack']:>9}{r['mean_externality']:>9}")
    print("\n  mechanism: the big MAE cut lands on demand_surge (slack 0 → "
          "decision forced); the easy kinds carry the slack. Accuracy improves "
          "where it cannot change a decision.\n")


if __name__ == "__main__":
    main()
