# coordination-frontier — reproduce every number and figure

This directory is the evidence base for the note *"When Is Intelligence Worth
Spending? Epure Arena and the Coordination Frontier"*
(v0.2; `coordination-frontier.pdf`, rendered from `coordination-frontier.typ`).

The committed primary artifacts are the **ledgers** — nine JSON experiment
records in [`ledgers/`](ledgers/). Every number quoted in the note, and every
mark on every figure, is read directly out of these files. Nothing in the note
is derived from prose; prose is derived from the ledgers.

## Verify every quoted number (stdlib only, no install)

```
python3 verify_note_numbers.py
```

Re-reads the ledgers and asserts each of the note's quoted values at the
note's own rounding — the menu-selection exact zero and all four runs'
per-arm means and 95% CIs (§5 / Figure 1), the three existence worlds
(amplification / discovery) and the 0.016 reliability-gate miss (§6 /
Figure 2), all four budget-curve sweeps and the 93-world count (§6 /
Figure 3), the 40/40 construction proofs, the P(find) refutation table,
the per-k relative-advantage ratios and the absolute-gap peak (§7 /
Figure 4), and the 133-world scope fence (§8). Exits non-zero on any
mismatch; 97 checks.

## Regenerate the figures (stdlib only)

```
python3 make_figures.py
```

Writes the four figures embedded in the PDF to `figures/` (SVG, generated —
not committed; the ledgers are the artifact, the figures are a view of them).

## Rebuild the PDF

```
python3 make_figures.py
typst compile coordination-frontier.typ coordination-frontier.pdf
```

Requires [typst](https://typst.app) ≥ 0.14.

## The ledgers

| file | experiment |
|---|---|
| `gonogo-cem-existence.json` | run #1 — backbone-enforced (hard, sparse); the menu-selection zero and the 3/10 existence worlds |
| `gonogo-grid-existence.json` | run #2 — grid-enforced (abundant); REJECT-TOO-EASY |
| `gonogo-bbadv-existence.json` | run #3 — backbone-advisory (mid); REJECT-TOO-EASY |
| `gonogo-screened-existence.json` | run #4 — screened, prospective; the screen's two measured flaws |
| `curves-{existence,bbenforced,grid,bbadvisory}.json` | verifier-budget sweeps, K ∈ {25…800}, P(φ > 0.02) with bootstrap bands |
| `planted-kcycle.json` | the planted alternating k-cycle anchor — exhaustive construction checks and the refutation |

Each ledger carries its own `scope` (regime, world sizes, seeds, budgets),
`label` (honesty label), and an `attestations` block (non-circularity attested
at the level of imports and symbols — the proposer structurally cannot read
the oracle).

**Provenance note.** The `reproduce_target` field inside each ledger names the
experiment-harness script that *generated* it, in the research workspace where
the runs were executed; those harness scripts are part of the pre-release
research apparatus and are not shipped here. What this directory guarantees is
the stronger, checkable half of the chain: the committed ledgers are the
primary records, and every number and figure in the note regenerates from them
with the two stdlib scripts above, on a fresh checkout, with no dependencies.
