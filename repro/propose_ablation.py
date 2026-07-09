#!/usr/bin/env python
"""C2 Phase 3 — Propose ablation: candidate diversity under perfect selection.

Gate = always-intervene (≡ oracle in this regime — Δ ≥ 0 measured); Imagine
= TRUTH (the per-epoch argmax over candidate outcomes, exact on single-epoch
cells: best-of over the candidates' paired legs). Candidates are
cascade-flavored SCOPE selections over the affected+surge set:

    recommit_all   — the trivial single candidate (the C2 standing Propose)
    vip_express    — re-certify priority ≤ 1 only (the cascade's PROMOTE bias)
    tight_half     — re-certify the earliest-due half (deadline triage)
    committed_only — exclude surge demand (the cascade's HOLD-new bias)

Estimand: V(best-of-candidates) − V(recommit_all) per cell — the value of
candidate diversity when selection is perfect. Suite (declared): the Part-A
severe cells, backbone, scenario seed 42, all 4 kinds × evaluation dseeds
6-15; never/recommit_all legs reused from the Part-A rows (paired identity).

Usage (node-engine venv, repo root):
    python repro/propose_ablation.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

import pyarrow.ipc as ipc

from epure_arena.harness.runlake import emit_run, markdown_table
from epure_arena.scenarios.disruptions import make_disruptions
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario
from epure_arena.harness.live_loop import Candidate, run_live_loop

# Prior-step input is clone-relative (under gitignored runs/), never /tmp.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_PARTA = str(_ROOT / "runs" / "gate_partA.jsonl")


def _require_jsonl(path: str, part: str) -> list:
    """Load a JSONL prior-step file; if absent, say exactly how to make it."""
    if not pathlib.Path(path).exists():
        sys.exit(f"missing {part} input: {path}\n"
                 f"produce it first (clone-relative, no /tmp):\n"
                 f"  python repro/gate_experiment.py {part} --no-emit > {path}")
    return [json.loads(line) for line in open(path)]


KINDS = ("lane_outage", "node_outage", "capacity_shock", "demand_surge")
EVAL_DSEEDS = tuple(range(6, 16))


def realized_on_time(flows):
    return sum(v for k, v in flows.items() if k.endswith("_on_time"))


class ScopedPropose:
    """Single scoped candidate; scope filters close over demand metadata."""

    def __init__(self, name: str, prio_of: dict[int, int],
                 due_of: dict[int, int]):
        self.name = name
        self.prio_of = prio_of
        self.due_of = due_of

    def propose(self, features, affected, surge):
        pool = list(affected) + list(surge)
        if self.name == "recommit_all":
            keep = pool
        elif self.name == "vip_express":
            keep = [s for s in pool if self.prio_of.get(s, 3) <= 1]
        elif self.name == "tight_half":
            ranked = sorted(pool, key=lambda s: (self.due_of.get(s, 0), s))
            keep = ranked[: max(1, len(ranked) // 2)] if ranked else []
        elif self.name == "committed_only":
            keep = list(affected)
        else:
            raise ValueError(self.name)
        return [Candidate(name=self.name, sids=tuple(keep))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parta", default=_DEFAULT_PARTA)
    ap.add_argument("--no-emit", action="store_true")
    args = ap.parse_args()

    parta = _require_jsonl(args.parta, "partA")
    base = {(r["kind"], r["disruption_seed"]): r for r in parta
            if r["world"] == "backbone" and r["scenario_seed"] == 42
            and r["severity"] == "severe"
            and r["disruption_seed"] in EVAL_DSEEDS}

    b = generate_scenario(ScenarioSpec(
        topology="backbone", n_nodes=200, density="moderate",
        capacity="tight", demand_pattern="diurnal",
        schedule_cadence="hourly", priority_mix="mixed",
        n_demand_events=40_000, horizon_hours=24, seed=42))
    dem = ipc.open_stream(b.demand_ipc).read_all()
    prio_of = dict(zip((int(x) for x in dem["flow_unit_id"].to_pylist()),
                       (int(x) for x in dem["priority_class"].to_pylist())))
    due_of = dict(zip((int(x) for x in dem["flow_unit_id"].to_pylist()),
                      (int(x) for x in dem["due_atu"].to_pylist())))

    rows = []
    for kind in KINDS:
        for dseed in EVAL_DSEEDS:
            r0 = base[(kind, dseed)]
            ds = make_disruptions(b, kinds=(kind,), severity="severe",
                                  horizon_hours=24, seed=dseed)
            # surge demand metadata enters via the disruption — extend maps.
            for d in ds:
                if d.surge_ipc is not None:
                    sd = ipc.open_stream(d.surge_ipc).read_all()
                    for s, p, due in zip(sd["flow_unit_id"].to_pylist(),
                                         sd["priority_class"].to_pylist(),
                                         sd["due_atu"].to_pylist()):
                        prio_of[int(s)] = int(p)
                        due_of[int(s)] = int(due)
            legs = {"never": r0["v_never"], "recommit_all": r0["v_always"]}
            for scope in ("vip_express", "tight_half", "committed_only"):
                # outage kinds have no surge → committed_only ≡ recommit_all;
                # surge kind has no affected → committed_only ≡ never.
                if scope == "committed_only":
                    legs[scope] = (r0["v_never"] if kind == "demand_surge"
                                   else r0["v_always"])
                    continue
                from epure_arena.gate import (
                    AlwaysOnGate,
                )
                res = run_live_loop(
                    b, ds, gate=AlwaysOnGate(),
                    propose=ScopedPropose(scope, prio_of, due_of),
                    horizon_hours=24)
                assert res.mass_closed
                legs[scope] = realized_on_time(res.flows)
            cands = ("recommit_all", "vip_express", "tight_half",
                     "committed_only")
            best = max(cands, key=lambda c: legs[c])
            row = {"kind": kind, "dseed": dseed, **legs,
                   "best_candidate": best,
                   "v_best": legs[best],
                   "diversity_gain": legs[best] - legs["recommit_all"]}
            rows.append(row)
            print(json.dumps(row))
            sys.stdout.flush()

    summary = []
    for kind in KINDS:
        cell = [r for r in rows if r["kind"] == kind]
        summary.append({
            "kind": kind,
            "recommit_all": f"{statistics.mean(r['recommit_all'] for r in cell):.0f}",
            "vip_express": f"{statistics.mean(r['vip_express'] for r in cell):.0f}",
            "tight_half": f"{statistics.mean(r['tight_half'] for r in cell):.0f}",
            "committed_only": f"{statistics.mean(r['committed_only'] for r in cell):.0f}",
            "diversity_gain_mean": round(statistics.mean(
                r["diversity_gain"] for r in cell), 1),
            "best_counts": json.dumps({c: sum(1 for r in cell
                                              if r["best_candidate"] == c)
                                       for c in ("recommit_all", "vip_express",
                                                 "tight_half",
                                                 "committed_only")}),
        })
    print(json.dumps({"summary": summary}, indent=1))

    if not args.no_emit:
        folder = emit_run(
            name="propose-ablation",
            config=vars(args), seed=42,
            schema_fingerprint="a240a6e82d789398",
            metrics_rows=rows,
            repro_cmd="python repro/propose_ablation.py",
            extra_files={"summary.md": markdown_table(summary)},
        )
        print(f"run folder: {folder}", file=sys.stderr)


if __name__ == "__main__":
    main()
