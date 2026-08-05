#!/usr/bin/env python
"""reproduce-smoke: the per-commit byte-asserted reproduction tier.

Runs the smoke-sized slices of the paper emitters and byte-compares their
canonicalized rows against the committed expected tables:

  1. strand-attribution Regimes A and B (backbone @40k, seed 42) — the
     18-cell matrix's two anchor cells, via `repro/strand_matrix.py cell`.
  2. one regret-vs-lattice cell (backbone:hourly:tight, seed 42,
     slack_first) via `repro/regret_table.py`.
  3. gate tables on a reduced seed set (Part A: backbone, one scenario
     seed, three disruption seeds, moderate severity) via
     `repro/gate_experiment.py partA`.

Wall-clock-dependent fields (timings) are stripped before comparison; all
counts, attributions, verdicts, and the schema fingerprint byte-assert.

Usage:
    python repro/smoke.py --write    # regenerate repro/expected/*.json
    python repro/smoke.py --verify   # assert byte-identity (CI mode)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "repro" / "expected"

SLICES: dict[str, list[str]] = {
    "attribution_regime_a": [
        "repro/strand_matrix.py", "cell", "--topology", "backbone",
        "--cadence", "every_4h", "--capacity", "loose", "--no-emit",
    ],
    "attribution_regime_b": [
        "repro/strand_matrix.py", "cell", "--topology", "backbone",
        "--cadence", "hourly", "--capacity", "tight", "--no-emit",
    ],
    # 20k demand: the smoke-sized regret cell (the full 40k cell runs in
    # reproduce-paper); keeps the smoke tier inside its 10-minute budget.
    #
    # The LP time limit is raised well above regret_table's 600 s default ON
    # PURPOSE. That default is a wall-clock guard, and a solve it truncates
    # reports lp_status != "optimal" with lp_ub_units and regret_units_vs_lp
    # as null — so the byte-assertion below would depend on how fast the
    # machine is, not on what the code computes. This cell reaches optimality
    # in roughly 8 minutes on a hosted runner and well under that on
    # development hardware; 1800 s keeps a genuine runaway guard while
    # putting machine speed out of the assertion. Raising it cannot change
    # the asserted values: it only prevents truncation short of the optimum.
    "regret_one_cell": [
        "repro/regret_table.py", "--cells", "backbone:hourly:tight",
        "--seeds", "42", "--demand", "20000", "--policies", "slack_first",
        "--lp-time-limit", "1800", "--no-emit",
    ],
    "gate_partA_reduced": [
        "repro/gate_experiment.py", "partA", "--worlds", "backbone",
        "--sseeds", "42", "--dseeds", "1,2,3", "--severities", "moderate",
        "--events-per-kind", "2", "--no-emit",
    ],
}

# Timing fields are machine-dependent: reported, never asserted.
import re as _re

_VOLATILE = _re.compile(r"(^t_.*_(s|ns|ms)$)|(_wall_s$)|(_(ns|ms)$)|(^wall(_s)?$)")
_VOLATILE_KEYS = {"run_folder"}


def _canonical(value):
    if isinstance(value, dict):
        return {
            k: _canonical(v)
            for k, v in sorted(value.items())
            if k not in _VOLATILE_KEYS and not _VOLATILE.search(k)
        }
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _explain(expected: list, got: list, limit: int = 12) -> str:
    """Name the fields that drifted, so a mismatch diagnoses itself.

    A bare "MISMATCH" says a byte-assertion failed but not what moved, which
    is the difference between reading one line and re-running a ten-minute
    slice by hand. Reports at most ``limit`` differing fields.
    """
    if len(expected) != len(got):
        return (f"    row count: expected {len(expected)}, got {len(got)}")
    lines: list[str] = []
    for i, (exp, act) in enumerate(zip(expected, got)):
        for key in sorted(set(exp) | set(act)):
            e, a = exp.get(key, "<absent>"), act.get(key, "<absent>")
            if e != a:
                lines.append(f"    row {i} {key}: expected {e!r}, got {a!r}")
    if not lines:
        return "    (values equal; formatting or key order differs)"
    shown = lines[:limit]
    if len(lines) > limit:
        shown.append(f"    ... and {len(lines) - limit} more field(s)")
    return "\n".join(shown)


def run_slice(name: str, argv: list[str]) -> list[dict]:
    print(f"[smoke] {name}: {' '.join(argv)}", file=sys.stderr)
    proc = subprocess.run(
        [sys.executable, *argv], cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"[smoke] {name} FAILED (exit {proc.returncode})")
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return [_canonical(r) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    ap.add_argument("--only", help="run a single named slice")
    args = ap.parse_args()

    slices = SLICES if not args.only else {args.only: SLICES[args.only]}
    EXPECTED.mkdir(parents=True, exist_ok=True)
    failures = []
    for name, argv in slices.items():
        rows = run_slice(name, argv)
        blob = json.dumps(rows, indent=1, sort_keys=True) + "\n"
        path = EXPECTED / f"{name}.json"
        if args.write:
            path.write_text(blob)
            print(f"[smoke] wrote {path.relative_to(ROOT)} ({len(rows)} rows)",
                  file=sys.stderr)
        else:
            if not path.exists():
                failures.append(f"{name}: missing expected table {path}")
            elif path.read_text() != blob:
                failures.append(
                    f"{name}: MISMATCH vs {path.relative_to(ROOT)}\n"
                    + _explain(json.loads(path.read_text()), rows))
            else:
                print(f"[smoke] {name}: byte-identical ({len(rows)} rows)",
                      file=sys.stderr)
    if failures:
        raise SystemExit("[smoke] FAILED:\n  " + "\n  ".join(failures))
    if args.verify:
        print("[smoke] all slices byte-identical", file=sys.stderr)


if __name__ == "__main__":
    main()
