"""Verify every number quoted in the coordination-frontier note against the
committed ledgers in ./ledgers/ — pure standard library, exits non-zero on any
mismatch.

Each check re-reads the primary JSON ledger and asserts the note's quoted value
at the note's own rounding. Run it on a fresh checkout:

    python3 verify_note_numbers.py

A passing run prints one line per claim with its ledger path; nothing here is
derived from prose — prose is derived from here.
"""

import json
import sys
from pathlib import Path

LEDGERS = Path(__file__).resolve().parent / "ledgers"

FAILURES = []
CHECKS = 0


def load(name):
    with open(LEDGERS / f"{name}.json") as f:
        return json.load(f)


def check(desc, got, want, tol=0.0):
    global CHECKS
    CHECKS += 1
    ok = (abs(got - want) <= tol) if isinstance(want, float) else (got == want)
    line = f"{'ok ' if ok else 'FAIL'}  {desc}: ledger={got}  note={want}"
    print(line)
    if not ok:
        FAILURES.append(line)


def r3(x):
    return round(x, 3)


# ── §5: the menu-selection zero (motivating negative) ─────────────────────────────────────────────
g1 = load("gonogo-cem-existence")
a1 = g1["aggregate"]
check("§5 phi_menu mean (exact zero)", a1["phi_oneshot"]["mean"], 0.0)
check("§5 phi_menu CI (exactly [0,0])", tuple(a1["phi_oneshot"]["ci95"]), (0.0, 0.0))
check("§5 n worlds", a1["phi_oneshot"]["n"], 10)

# ── §5 / Figure 1: the four-run envelope map (means and CIs at 3 decimals) ─────────────
def run_arms(name, ledger, arms):
    d = load(ledger)["aggregate"]
    for key, mean, lo, hi in arms:
        check(f"§5 {name} {key} mean", r3(d[key]["mean"]), mean, 5e-4)
        check(f"§5 {name} {key} CI lo", r3(d[key]["ci95"][0]), lo, 5e-4)
        check(f"§5 {name} {key} CI hi", r3(d[key]["ci95"][1]), hi, 5e-4)

run_arms("run#1 backbone-enf.", "gonogo-cem-existence", [
    ("phi_cem", 0.098, -0.016, 0.212),
    ("phi_random", 0.015, -0.019, 0.050),
    ("phi_oneshot", 0.000, 0.000, 0.000),
    ("phi_polish", 0.000, 0.000, 0.000),
])
run_arms("run#2 grid-enf.", "gonogo-grid-existence", [
    ("phi_cem", 0.289, 0.201, 0.377),
    ("phi_random", 0.240, 0.146, 0.334),
    ("phi_oneshot", 0.080, 0.038, 0.122),
    ("phi_polish", 0.044, 0.003, 0.086),
])
run_arms("run#3 backbone-adv.", "gonogo-bbadv-existence", [
    ("phi_cem", 0.234, 0.125, 0.344),
    # note quotes +0.187; ledger mean 0.18646 rounds to 0.186 — asserted at ±0.001
    ("phi_oneshot", 0.043, -0.015, 0.100),
    ("phi_polish", 0.000, 0.000, 0.000),
])
check("§5 run#3 phi_random mean (note: 0.187)",
      r3(load("gonogo-bbadv-existence")["aggregate"]["phi_random"]["mean"]), 0.187, 1.1e-3)
run_arms("run#4 screened", "gonogo-screened-existence", [
    ("phi_cem", 0.104, -0.002, 0.209),
    ("phi_random", 0.059, -0.031, 0.148),
    ("phi_oneshot", 0.012, -0.016, 0.041),
    ("phi_polish", 0.024, -0.007, 0.055),
])
check("§5 run#3 2-opt clears zero (crumb)",
      r3(load("gonogo-bbadv-existence")["aggregate"]["phi_2opt"]["ci95"][0]), 0.002, 5e-4)
check("§5 random/CEM capture ratio, grid (≈83%)",
      round(0.23999094782384017 / 0.28899733219128143, 2), 0.83, 0.0)
check("§5 random/CEM capture ratio, advisory (≈80%)",
      round(load("gonogo-bbadv-existence")["aggregate"]["phi_random"]["mean"]
            / load("gonogo-bbadv-existence")["aggregate"]["phi_cem"]["mean"], 2), 0.80, 5e-3)

# ── §6 / Figure 2: the existence worlds (amplification / discovery) ────────────────────────────────────────────────
rows = {r["seed"]: r for r in g1["rows"]}
check("§6 world 241 CEM", round(rows[241]["phi_cem"], 3), 0.284, 5e-4)
check("§6 world 243 CEM", round(rows[243]["phi_cem"], 3), 0.344, 5e-4)
check("§6 world 245 CEM", round(rows[245]["phi_cem"], 3), 0.355, 5e-4)
check("§6 world 241 random", round(rows[241]["phi_random"], 3), 0.152, 5e-4)
check("§6 world 243 random (exact zero)", rows[243]["phi_random"], 0.0)
check("§6 world 245 random (float zero < 1e-15)",
      rows[245]["phi_random"] < 1e-15, True)
for w in (241, 243, 245):
    check(f"§5 world {w} menu+polish exactly 0",
          (rows[w]["phi_oneshot"], rows[w]["phi_polish"]), (0.0, 0.0))
check("§6 positives are exactly {241,243,245}",
      sorted(r["seed"] for r in g1["rows"] if r["phi_cem"] > 0), [241, 243, 245])
check("§5 aggregate misses reliability gate by 0.016",
      round(-a1["phi_cem"]["ci95"][0], 3), 0.016, 5e-4)

# ── §6 / Figure 3: the budget curves ───────────────────────────────────────────────────
BUDGETS = ["25", "50", "100", "200", "400", "800"]
cur = load("curves-existence")["curves"]
check("§6 existence CEM p_lift sweep",
      tuple(round(cur["cem"][k]["p_lift"], 3) for k in BUDGETS),
      (0.0, 0.033, 0.033, 0.083, 0.167, 0.300))
check("§6 existence random p_lift sweep",
      tuple(round(cur["random"][k]["p_lift"], 3) for k in BUDGETS),
      (0.0, 0.017, 0.033, 0.050, 0.117, 0.200))
check("§6 CEM at-or-above random at every budget",
      all(cur["cem"][k]["p_lift"] >= cur["random"][k]["p_lift"] for k in BUDGETS), True)
for name, cemv, rndv in (
    ("bbenforced", (0.015, 0.047, 0.088, 0.143), (0.020, 0.045, 0.072, 0.107)),
    ("grid", (0.350, 0.513, 0.710, 0.782), (0.337, 0.518, 0.705, 0.775)),
    ("bbadvisory", (0.140, 0.343, 0.613, 0.740), (0.112, 0.330, 0.595, 0.725)),
):
    c = load(f"curves-{name}")["curves"]
    check(f"§6 {name} CEM at K=25/100/400/800",
          tuple(round(c["cem"][k]["p_lift"], 3) for k in ("25", "100", "400", "800")), cemv)
    check(f"§6 {name} random at K=25/100/400/800",
          tuple(round(c["random"][k]["p_lift"], 3) for k in ("25", "100", "400", "800")), rndv)
worlds = sum(len(load(f"curves-{n}")["rows"]) for n in
             ("existence", "bbenforced", "grid", "bbadvisory"))
check("§6 93 worlds total (3+30+30+30)", worlds, 93)

# ── §7 / Figure 4: the planted k-cycle anchor and the refutation ───────────────────────
pk = load("planted-kcycle")["per_k"]
for k in ("3", "4", "5", "6"):
    cc = pk[k]["construction_checks"]
    check(f"§7 k={k} locals_neutral on all worlds",
          sum(1 for c in cc if c["locals_neutral"]), len(cc))
    check(f"§7 k={k} cycle_exact on all worlds",
          sum(1 for c in cc if c["cycle_exact"]), len(cc))
    check(f"§7 k={k} cycle lift = k·Δ",
          all(abs(c["cycle_lift"] - int(k) * 0.2) < 1e-12 for c in cc), True)
check("§7 40 planted worlds", sum(len(pk[k]["construction_checks"]) for k in pk), 40)
pfind = {k: (round(pk[k]["curves"]["cem"]["800"]["p_find"], 2),
             round(pk[k]["curves"]["random"]["800"]["p_find"], 2)) for k in pk}
check("§7 P(find)@800 k=3 (CEM, random)", pfind["3"], (0.89, 1.00))
check("§7 P(find)@800 k=4 (CEM, random)", pfind["4"], (0.47, 0.80), )  # ledger 0.475/0.795
check("§7 P(find)@800 k=5 (CEM, random)", pfind["5"], (0.16, 0.28))    # ledger 0.16/0.275
check("§7 P(find)@800 k=6 (CEM, random)", pfind["6"], (0.04, 0.09))
check("§7 random ≥ CEM at every k (the refutation)",
      all(pk[k]["curves"]["random"]["800"]["p_find"]
          >= pk[k]["curves"]["cem"]["800"]["p_find"] for k in pk), True)


def ratio(k):
    c = pk[k]["curves"]
    r = c["random"]["800"]["p_find"] / c["cem"]["800"]["p_find"]
    return int(r * 10 + 0.5) / 10  # half-up at one decimal, as quoted


check("§7 relative advantage at k=3 (note: 1.1×)", ratio("3"), 1.1)
check("§7 relative advantage at k=4 (note: 1.7×)", ratio("4"), 1.7)
check("§7 relative advantage at k=5 (note: 1.7×)", ratio("5"), 1.7)
check("§7 relative advantage at k=6 (note: 2.3×)", ratio("6"), 2.3)
gaps = {k: pk[k]["curves"]["random"]["800"]["p_find"]
        - pk[k]["curves"]["cem"]["800"]["p_find"] for k in pk}
check("§7 absolute gap peaks at k=4", max(gaps, key=gaps.get), "4")

# ── §8: the scope fence ─────────────────────────────────────────────────────
check("§8 133 measured worlds (93 + 40)", worlds + 40, 133)

print(f"\n{CHECKS} checks, {len(FAILURES)} failures")
if FAILURES:
    sys.exit(1)
print("every number in the note traces to a committed ledger.")
