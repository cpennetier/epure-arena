// coordination-frontier.typ — "When Is Intelligence Worth Spending?"
// Typst source for the coordination-frontier research note, v0.2. Self-
// contained: the style block below mirrors the program's note template
// (warm paper, single ink-blue accent, honesty-label badges). Compiles with
// typst 0.14.x:
//
//     python3 make_figures.py          # regenerate figures from ledgers
//     typst compile coordination-frontier.typ coordination-frontier.pdf
//
// Every number in this document is quoted from a committed ledger in
// ./ledgers/ — verify with `python3 verify_note_numbers.py`.

#let accent = rgb("#1b3a5c")
#let ash = rgb("#6f6a5e")
#let ink = rgb("#1c1a16")
#let paper = rgb("#f4f2ec")

// Inline honesty marker — paper-native: small caps with a thin left rule,
// no brackets, no outlined box. PROVEN bold accent · CHARACTERIZATION
// medium accent · FRAMING muted ash.
#let claimtag(status) = {
  let weak = status == "framing" or status == "frame"
  let fill = if weak { ash } else { accent }
  box(inset: (left: 5pt, right: 1pt, y: 0.5pt), outset: (y: 1pt),
    stroke: (left: 1.3pt + fill),
    text(size: 7.5pt, fill: fill, tracking: 0.7pt,
      weight: if status == "proven" { "bold" } else { "medium" },
      upper(status)))
}

// Blockquote — left accent rule.
#let bq(body) = block(above: 0.7em, below: 0.7em, inset: (left: 10pt), width: 100%,
  stroke: (left: 1.5pt + accent), text(fill: ink, body))

// Figure caption helper: short caption + one-line provenance (reproduce ID).
#let fcap(short, repro) = [
  #short
  #text(fill: ash, size: 8.5pt)[ · Reproduce: #repro (appendix).]
]

#let note-doc(title: [], subtitle: [], overline: none, body) = {
  set page(paper: "a4", margin: (x: 2.2cm, top: 2.1cm, bottom: 2cm), numbering: "1")
  // hyphenate: false — no discretionary/soft hyphens, so text extraction,
  // search, and copy/paste stay clean (ordinary hyphens only).
  set text(font: ("Libertinus Serif", "New Computer Modern"), size: 10.5pt,
    fill: ink, hyphenate: false)
  set par(justify: true, leading: 0.62em, spacing: 0.95em)
  show heading: set text(fill: ink)
  show heading: set block(sticky: true)
  show heading.where(level: 1): set text(size: 12.5pt)
  show heading.where(level: 1): set block(above: 1.1em, below: 0.55em, sticky: true)
  show raw: set text(font: "DejaVu Sans Mono", size: 0.9em)
  show link: set text(fill: accent)
  if overline != none {
    text(size: 7.5pt, fill: ash, tracking: 1.2pt)[#upper(overline)]
    linebreak()
    v(0.25em)
  }
  text(size: 19pt, weight: "bold")[#title]
  parbreak()
  if subtitle != [] {
    text(size: 11pt, style: "italic", fill: ash)[#subtitle]
    v(0.15em)
  }
  line(length: 100%, stroke: 0.5pt + ash)
  v(0.35em)
  body
}

#let tablestyle = (
  stroke: (x, y) => if y == 0 { (bottom: 0.7pt + ink) } else { (bottom: 0.3pt + ash.lighten(50%)) },
  inset: (x: 6pt, y: 4.5pt),
)

#show: note-doc.with(
  title: [When Is Intelligence Worth Spending?],
  subtitle: [Epure Arena and the Coordination Frontier],
  overline: "research note · epure arena · v0.2 · 2026",
)

#text(size: 9.5pt, fill: ash)[Christophe Pennetier · 2026 · note version 0.2]

#block(inset: (x: 12pt, y: 8pt), width: 100%, fill: paper, radius: 3pt)[
  #text(size: 9.5pt)[*Abstract.* Modern systems must decide not only _what_
  action to take, but how much intelligence to spend producing it: a rule,
  random search plus verification, a learned proposer, a solver, or a human.
  Yet most benchmarks score only the final action; they cannot tell whether
  the extra intelligence was necessary or whether it paid for itself. We
  introduce Epure Arena, an eval-blind decision environment built on
  Ephemeris Kernel, with exact counterfactual pricing, deterministic replay,
  and small-scale oracle regret. The arena separates proposal generation from
  value estimation: a policy cannot see the answer key, while every candidate
  is scored exactly. Across four controlled regimes and a planted
  coordination family, we find that learned proposal generation has a narrow
  operating envelope. It adds value only when improvements require
  coordinated moves, random discovery is inefficient but feasible, and
  partial proposals provide informative graded feedback. When good moves are
  abundant, random sampling plus verification is sufficient; when they are
  too rare, all cheap methods starve; and on all-or-nothing constructions,
  adaptive refitting underperforms random exploration. We call the useful
  band the coordination frontier. The contribution is not a new optimizer,
  but a certified apparatus for measuring when additional intelligence earns
  its cost.]
]

#bq[*I built a test track for decision intelligence.* Modern systems can
spend a cheap rule, a learned model, search, simulation, a solver, a
verifier, or a human on the same decision — but they rarely know whether the
extra intelligence was necessary. Epure Arena makes that question
measurable: it hides the answer from the policy, prices proposed decisions
exactly, and measures regret against a certified optimum at small scale. Its
first result is the *coordination frontier* — a narrow regime where learned
proposal generation earns its cost, and outside of which cheaper methods
win. The result motivates *Cenacle*: an agentic decision architecture that
diagnoses the regime first, then spends the cheapest sufficient
intelligence.]

#bq[*Reading note.* A research note, not a full paper. Every number is
quoted from a committed experiment ledger and carries its scope. The central
positive result is an _existence_ result at stated scope; the central
_contribution_ is the environment and the map it produces, not any single
learned method. Claims carry labels — #claimtag("proven")
#claimtag("characterization") #claimtag("framing") — where the distinction
matters.]

= 1. The decision above the decision

Most work on decision-making asks _what is the best action?_ A growing class
of systems must first answer a harder question: _which kind of intelligence
should produce the action at all?_ A cheap rule, a random proposal filtered
by a verifier, a learned proposer, a bounded solver, a human — each has a
cost, and the right choice is not fixed. This is already visible wherever
compute is routed selectively: mixture-of-experts gates activate only some
experts per input (Shazeer et al., 2017); LLM-cascade systems such as
FrugalGPT (Chen et al., 2023) and learned routers such as RouteLLM (Ong et
al., 2024) decide when a stronger, costlier model is worth invoking. In all of these
the scarce resource is no longer only capacity — it is _intelligence
itself_, and knowing when to spend it is the lever.

The principle we want to make measurable is simple to state and hard to
verify: *use the cheapest sufficient intelligence, and prove that any
escalation paid for itself.* Most benchmarks cannot check the second
clause — they score the final action, not the return on the intelligence
that produced it. We study the question in a setting where it _can_ be
checked exactly: hard constrained-decision problems where global
optimization is intractable, a better value _estimate_ is often irrelevant
(the missing value is not a scoring error but an action the cheap policy
cannot express), and the natural role for learning is not as a scorer but as
a _proposer_ of coordinated moves that a verifier certifies.

= 2. Epure Arena

Epure Arena is a decision environment built on a neutral discrete-event
runtime — *Ephemeris Kernel*: an integer-time event queue with a total event
order, deterministic replay, append-only event and state ledgers with
byte-identical snapshots, and cryptographic run manifests. The kernel knows
nothing of decisions; the arena adds four things that make the _return on
intelligence_ measurable rather than assumed.

- *An exact counterfactual verifier.* Given any proposed coordinated move —
  a bundle of admissions, drops, and re-routings — the verifier returns its
  exact certified value delta by separable pricing, in time linear in the
  size of the move, with no simulator rollout. The reward is ground truth,
  not an estimate. The pricing identity is _algebraically exact_;
  implementations agree to relative error below $10^(-13)$.
  #claimtag("proven")

- *A small-scale optimal oracle.* At $n = 12$, an ILP computes the exact
  optimum _over the arena's declared action space_, so any policy's regret
  is measured against ground truth, not a heuristic reference.

- *An eval-blind wall.* The policy structurally cannot read the answer key;
  the oracle is used only to compute the regret denominator, never inside
  candidate generation. Non-circularity is attested at the level of imports
  and symbols, not comments — a proposer that reached the answer through the
  oracle would be worthless, and the wall makes that structurally
  impossible.

- *A separation of proposal from valuation.* Candidate _generation_ and
  value _estimation_ are distinct layers. This is what lets us ask whether
  the missing value is a scoring problem (fix the estimate) or a vocabulary
  problem (generate a move the cheap policy cannot express).

#block(breakable: false)[
The central metric is *coordination capture*,

$ phi = (V_"policy" - V_"greedy") / (V_"oracle" - V_"greedy"), $

the fraction of the greedy-to-optimal gap a policy recovers: $phi = 0$ is no
improvement over the cheap baseline, $phi = 1$ is the certified optimum.
Every result below is a $phi$ measured against a certified ceiling.
]

The probe we use to test whether certified feedback contains _learnable_
signal is deliberately minimal — a cross-entropy method (CEM) that samples
coordinated bundles, prices them exactly, keeps the elites, and refits its
sampling distribution toward them, round over round (Rubinstein & Kroese,
2004). It is a distribution-shifter, not a solver: it emits hints, and an
exact non-learned layer owns all feasibility and pricing. Because the
verifier is exact, there is no learned value critic and no bootstrapping —
the only learned object is the proposal distribution. We use _learned_ in
the narrow operational sense that the proposal distribution is _updated from
verifier feedback_; CEM is adaptive stochastic search, not a gradient-trained
neural model. *CEM is a thermometer for learnable structure, not the
product.* The product is the arena and the map.

= 3. Three terms, defined once

The map below turns on three properties of a decision problem. We define
them precisely here so the rest of the note can use them without
qualification.

- *Coordination-bound.* Value exists above the baseline, but no allowed
  local move (single-item, pairwise) or fixed-menu choice reaches it;
  improvement requires a _jointly constructed multi-item move_.

- *Scarce-but-reachable.* Positive proposals are _rare_ under random
  sampling, but _discoverable_ within the measured verifier budget.
  (Extremely scarce value — undiscoverable at any affordable budget — is a
  distinct, _starved_ regime.)

- *Graded.* Incomplete proposals produce _ordered intermediate certified
  values_, so adaptive sampling can distinguish warmer candidates from
  colder ones. Its opposite is an _all-or-nothing_ landscape, where any
  incomplete attempt scores like no attempt.

= 4. The coordination frontier: the map

The central result is a decision map. Two structural axes —
coordination-boundness and informative partial credit — determine which
_kind_ of intelligence is the correct spend; a third, scarcity, governs
whether any affordable method can find value at all.

#let mapcell(title, sub, fill: white, tcol: ink, scol: ash, tw: "semibold") = box(
  width: 100%, height: 6.2em, fill: fill, stroke: 0.7pt + ash, inset: 9pt,
  align(center + horizon)[
    #text(size: 10pt, weight: tw, fill: tcol, title)
    #v(2pt)
    #text(size: 8.5pt, fill: scol, sub)
  ])
#figure(
  kind: image,
  placement: none,
  block(breakable: false, width: 100%)[
    #align(center)[#text(size: 8.5pt, fill: ash, tracking: 1.1pt)[INFORMATIVE PARTIAL CREDIT #sym.arrow.r]]
    #v(3pt)
    #grid(
      columns: (7.2em, 1fr, 1fr),
      column-gutter: 6pt,
      row-gutter: 6pt,
      [],
      align(center)[#text(size: 9pt, fill: ink, tracking: 0.8pt)[LOW]],
      align(center)[#text(size: 9pt, fill: ink, tracking: 0.8pt)[HIGH]],
      align(end + horizon)[#text(size: 8.5pt, fill: ink, tracking: 0.6pt)[COORDINATION-#linebreak()BOUND]],
      mapcell([Zero-credit needle], [structured exploration — adaptive refitting can lose to random]),
      mapcell([THE COORDINATION FRONTIER], [learned proposer + verifier],
        fill: accent, tcol: white, scol: rgb("#c9d4e2"), tw: "bold"),
      align(end + horizon)[#text(size: 8.5pt, fill: ink, tracking: 0.6pt)[NOT#linebreak()COORDINATION-#linebreak()BOUND]],
      mapcell([Local method sufficient], [single-item / pairwise moves reach the value]),
      mapcell([Random proposal + verifier sufficient], [coordinated grammar + exact pricing, no learning]),
    )
    #v(6pt)
    #grid(columns: (7.2em, 1fr), column-gutter: 6pt,
      [],
      box(width: 100%, fill: paper, stroke: (top: 0.7pt + ash), inset: 7pt,
        align(center)[#text(size: 8.5pt, fill: ash)[#text(tracking: 0.8pt)[SCARCITY GATE] — too scarce to reach #sym.arrow.r all affordable modes starve, regardless of cell]]))
  ],
  caption: [The decision map: two structural axes select the correct
    intelligence to spend; scarcity gates whether any affordable method
    reaches value at all. The frontier cell is the note's result; the other
    three cells are its controls.],
)

Stated as a rule: *spend a learned proposer only where the problem is
coordination-bound, scarce-but-reachable, and graded — the frontier cell.
Among the tested decision modes, a cheaper method is sufficient outside the
frontier cell.* The verifier supplies truth; the learner supplies taste; and
taste is worth paying for only where value is scarce and graded. The rest of this note is the evidence that places each
regime in a cell — and one controlled construction that shows
coordination-boundness alone is _not_ enough to justify learning.

The bridge from this cell to the operating decisions that motivate it —
which model to call, when to escalate, when to invoke a solver — is stated
as a hypothesis, not a claim, in §8.

= 5. Evidence I — the operating-envelope map across four regimes

We tested, across four pre-registered regimes, whether a learned proposer
recovers coordination value that simpler methods cannot. Three conditions
must all hold for learning to be worth spending: *reachable* (some
non-oracle-seeded method crosses $phi > 0$), *coordination-bound*
(single-move, pairwise, menu-selection, and polish stay at zero), and
*learning-separates* (the learned proposer beats random under the identical
grammar and budget). Reading the results into the map:

#block(breakable: false)[
#table(
  columns: (1.35fr, 0.95fr, 0.75fr, 1.05fr, 1.15fr),
  ..tablestyle,
  table.header([*regime*], [*reachability*], [*local*], [*learning gain*],
    [*reading*]),
  [sparse enforced], [rare], [dead], [yes, on subset], [*frontier candidate*],
  [grid], [high], [nonzero], [none], [random + verify],
  [advisory], [moderate], [small], [none material], [grammar sufficient],
  [prospective screen], [unstable], [nonzero], [inconclusive], [selector failed],
)
]

Per-arm coordination capture — every arm's mean and 95% CI — is reported in
Figure 2; the ledger filenames live in the reproduce map (appendix).

The shape is legible. Where the residual is _hard to reach_
(backbone-enforced), only a minority of worlds are reachable at all, but on
those worlds nothing simpler than learning crosses zero. Where the residual
is _easy to reach_ (grid, advisory), a coordinated grammar with an exact
verifier — even random sampling — already captures most of the value (random
reaches 80–83% of CEM's capture), so learning is unnecessary. *Reachability
and learning-necessity are anti-correlated.* #claimtag("characterization")

_A note on the backbone-enforced verdict._ Its pre-registered reliability
gate — an aggregate confidence interval excluding zero — was _not met_
(missed by 0.016). The same run nevertheless contains a positive per-world
existence signal (§6). These are not in tension: the aggregate reliability
claim is not made; the per-world existence claim is. Figure 2 reports each
regime by its _decision_ (rarely reachable; learning unnecessary; grammar
sufficient; prospective selector failed), with the pre-registered GO/NO-GO
capsules kept in the reproduce appendix.

_The motivating negative, in one line._ An earlier proposer restricted to
one-shot _menu-selection_ — choose, per unit, from a fixed candidate list —
recovered exactly zero ($phi = +0.000$, 10/10 worlds), because the useful
value lives in route _combinations absent from any per-unit menu_. This
isolated the bottleneck as candidate _generation_, not value _estimation_,
and motivated the shift from selection to distribution-shift.

#figure(
  image("figures/fig_envelope_arms.svg", width: 94%),
  caption: fcap([Certified capture by arm across the four pre-registered
    regimes. Bars = means, whiskers = 95% CIs; the learned arm is solid, the
    random arm hatched; capsules give each regime's decision reading.],
    [F2-A–F2-D]),
)

= 6. Evidence II — existence, and reachability as a curve

*Existence.* On 3 of the 10 pre-registered worlds in the hard regime, the
learned proposer is certified-positive. The three worlds show _two distinct
mechanisms_, and the distinction is more informative than a single headline
(Figure 3): on world 241, *amplification* — learning improves on value
random already reaches (+0.284 vs +0.152); on worlds 243 and 245,
*discovery* — learning finds value random does not (+0.344 and +0.355
against exact and floating-point zeros).

On two of the three worlds, random sampling with the _identical_ grammar,
verifier, and budget found nothing — only the arm that refits toward
certified elites crossed zero. On the third, learning _amplified_ value
random already reached. This is a clean mechanism isolation: same grammar,
same verifier, same compute, and distribution-shift is what separates.

*Scope, stated plainly.* This is 3 of 10 worlds in one regime, at one
budget — a _retrospective_ subset, selected after the run because it was
existence-positive, not a prospective population. It is evidence that the
mechanism is _real_, not that it is _reliable_. #claimtag("characterization")
#text(fill: ash, style: "italic", size: 9.5pt)[— existence at stated scope.]

#figure(
  image("figures/fig_existence_worlds.svg", width: 88%),
  caption: fcap([The three certified-positive worlds of run \#1, grouped by
    mechanism: one amplification, two learned-only discoveries. Bars =
    per-world certified $phi$ (single-run point values).], [F3]),
)

*Reachability as a curve.* Fixing a single budget hides the mechanism.
Sweeping the verifier budget $K in {25, 50, 100, 200, 400, 800}$ (a doubling
scale) and measuring $P(phi > 0.02)$ across 93 worlds makes the envelope
quantitative:

- *existence subset (retrospective, $n = 3$).* The CEM curve shifts _left_
  of the random curve under the same grammar and budget — CEM reaches a
  given probability at no more budget than random, and less at most budgets
  tested (at $K = 800$, 0.30 vs 0.20). The sweep _shows_ a consistent
  directional shift within this selected subset; it does not establish a
  population-level effect, and "certified" is reserved for the verifier's
  exact value claims, not for this descriptive curve. This panel is
  descriptive, not population-level.
- *grid (abundant, 30 worlds).* CEM and random curves are effectively
  identical at every budget — learning buys nothing where value is this
  reachable.
- *backbone-advisory (mid, 30).* Near-identical — the grammar plus exact
  verifier does the work, not the learning.
- *backbone-enforced, unscreened sparse (30).* Both curves flat-low across
  the whole sweep — sparsity starves every arm; the small CEM edge never
  clears materiality.

The left-shift on the existence subset is the entire positive learning
signal, made into a curve. It appears in exactly one of four regimes.

#figure(
  placement: auto,
  image("figures/fig_budget_curves.svg", width: 96%),
  caption: fcap([$P("certified" phi > 0.02)$ vs verifier budget $K$
    (doubling scale). Lines = point estimates, whiskers = bootstrap 95%
    bands; the retrospective run-\#1 subset is segregated (tinted, dashed
    border) and carries a $Delta P(K)$ inset showing the left-shift
    directly.], [F4-A–F4-D]),
)

= 7. Evidence III — the controlled anchor, and its refutation

To isolate the mechanism from sampling luck, we _construct_ 40 worlds with a
planted alternating $k$-cycle (per-step value $Delta$, coordination order
$k in {3, 4, 5, 6}$) so that coordination-boundness is built in. Both
construction properties are checked exhaustively: 1-opt and 2-opt are
exactly neutral on *40/40* worlds, and the cycle lift equals $k dot Delta$
to numerical tolerance on *40/40* worlds. #claimtag("proven")

The expected half of the result holds: reachability falls sharply as $k$
rises in _both_ arms (abundant at $k = 3$, essentially unreachable at
$k = 6$) — coordination difficulty scales with order, as designed.

*The pre-registered expectation — that learning would separate at medium
$k$ — is refuted. Random beats CEM at every $k$* ($P("find")$ at $K = 800$:
$k = 3$, 1.00 vs 0.89; $k = 4$, 0.80 vs 0.47; $k = 5$, 0.28 vs 0.16;
$k = 6$, 0.09 vs 0.04), with a _growing relative advantage_ for random
(1.1× at $k = 3$ to 2.3× at $k = 6$; the absolute gap peaks at $k = 4$). We
report it as refuted.


The mechanism is clean and it _sharpens_ the whole result. A pure
alternating cycle is an all-or-nothing needle: any bundle completing fewer
than $k$ of the $k$ required swaps scores at or below zero — *no partial
credit*. So the elites CEM selects carry no informative ranking signal (an
incomplete attempt scores like no attempt), and the refit concentrates the
sampler's mass _away_ from the answer. Random, which never refits, keeps
exploring and accumulates independent draws at the needle as budget grows.

*CEM is a partial-credit exploiter, not a general coordination learner.*
Where the certified landscape between "nothing" and "the full bundle" is
graded — as in the real sparse worlds of §6, where partial drops and
re-routes carry intermediate certified value — distribution-shift has an
informative elite signal to climb and separates from random. Where the
landscape is a gradient-free needle, _adaptive elite refitting underperforms
random exploration on this construction._ Coordination-boundness alone does
not determine whether adaptive refitting helps; partial credit is the
discriminating axis isolated by the controlled construction.

#figure(
  placement: auto,
  image("figures/fig_planted_ktransition.svg", width: 100%),
  caption: fcap([The planted $k$-cycle at $K = 800$: random wins at every
    $k$ with a growing relative advantage (1.1× → 2.3×); the absolute gap
    peaks at $k = 4$. Bars = $P("find")$, whiskers = bootstrap 95% bands.],
    [F5]),
)

= 8. What is established, and what is hypothesis

*Across the tested regimes*, learned proposal generation helped only where
three conditions aligned — coordination-bound, scarce-but-reachable, and
graded. We state this as an observed frontier, not a universal law: the
three-axis alignment is demonstrated on a three-world retrospective
subset — the only tested region in which the full alignment appeared. Move
any axis and the recommendation flips: abundant → learning unnecessary;
coordination-bound but gradient-free → refitting can lose to exploration;
coordination-bound and graded but too starved → nobody finds it reliably at
cheap budgets.

*Transfer hypothesis.* #claimtag("framing") Epure Arena demonstrates the
mechanism in a controlled capacitated decision universe. We _hypothesize_
that the same operating-envelope question appears in LLM orchestration,
compute scheduling, grid control, and other constrained systems — that
"which intelligence should this decision receive?" has the same four-cell
structure there. These are motivations and future instantiations, *not
claims established by the present experiments.* The bridge is a research
direction, not a result.

= 9. What is open

- *A prospective selector is unsolved.* A cheap screen to select
  coordination-bound, graded, reachable worlds _ahead of time_ failed on two
  axes: its hits were partly luck (4/10 did not re-fire under fresh
  randomness) and it selected for the wrong kind of value (admit-fill, not
  coordination). A refined screen is specified but not yet run. Building a
  _working_ prospective selector — cheap observable features that predict
  the correct decision mode before spending — is the natural next
  experiment, and the first working version of a regime-diagnosing manager.
- *Small-$n$.* The existence result is 3/10; the prospective aggregate
  misses the zero-exclusion bar by 0.002 — margins small-$n$ intervals are
  too wide to resolve without more worlds or a materiality-aware guard.
- *How much partial credit is enough?* We show real worlds have graded
  structure and pure cycles do not; we do not yet characterize the threshold
  of partial credit a world needs before learning separates, nor whether
  that threshold is measurable prospectively.

= 10. What comes next

The map reframes the architecture it motivates. The right system is not one
optimizer nor one learned solver, but a council that _diagnoses the regime
first_ and then spends the cheapest sufficient intelligence: shape the
situation, propose coordinated candidates, imagine their consequences,
allocate a verifier budget, certify the result. This note is the foundation
beneath that architecture — it establishes _why_ such a system needs a
regime-diagnosing manager at all, and _where_ a learned proposer (or an
energy-based proposal prior — a verifier-budget efficiency tool for the
frontier, not a universal solver) earns its cost. The next experiment
follows directly from §9: turn the retrospective map into a _prospective
selector_ — the first working version of that manager.

The whole evidence base was produced in roughly one workstation-hour of
sub-millisecond verifier calls, every claim tied to a committed, regenerable
ledger, and the one refuted prediction reported as refuted. That is the
point of a certified environment: it does not only produce wins — it
produces a map, and it tells you, _before you spend_, whether intelligence
is worth it.

= Reproduce & artifacts

Epure Arena and Ephemeris Kernel are open. The public artifact
independently rederives every reported number and figure from committed
ledgers; the experiment harness that _generated_ those ledgers (world
generation and the run driver) is presently private but named by each
ledger's reproduce target. So the checkable public chain is
_ledger #sym.arrow.r verified numbers #sym.arrow.r figures_; end-to-end
regeneration from raw world generation is not yet public.

#block(breakable: false)[
- Note version 0.2 · 2026
- Epure Arena — #link("https://github.com/cpennetier/epure-arena")[`github.com/cpennetier/epure-arena`]
- Ephemeris Kernel — #link("https://github.com/cpennetier/ephemeris-kernel")[`github.com/cpennetier/ephemeris-kernel`]
- Reproduce — #link("https://github.com/cpennetier/epure-arena/blob/main/experiments/coordination-frontier/REPRODUCE.md")[`experiments/coordination-frontier/REPRODUCE.md`]
- Evidence commit — `e372cc3` (ledgers + verification + figure scripts)
]

On a fresh checkout of `experiments/coordination-frontier/`:
`python3 verify_note_numbers.py` re-derives every number quoted above from
the committed ledgers and exits non-zero on any mismatch;
`python3 make_figures.py` regenerates all four data figures from the same
ledgers. Both are standard-library-only.

= References

#set par(justify: false)
- Rubinstein, R. Y., and Kroese, D. P. (2004). _The Cross-Entropy Method: A
  Unified Approach to Combinatorial Optimization, Monte-Carlo Simulation,
  and Machine Learning._ Springer.
- Shazeer, N., Mirhoseini, A., Maziarz, K., Davis, A., Le, Q., Hinton, G.,
  and Dean, J. (2017). Outrageously large neural networks: the
  sparsely-gated mixture-of-experts layer. _ICLR 2017._
  #link("https://arxiv.org/abs/1701.06538")[arXiv:1701.06538]
- Chen, L., Zaharia, M., and Zou, J. (2023). FrugalGPT: how to use large
  language models while reducing cost and improving performance.
  #link("https://arxiv.org/abs/2305.05176")[arXiv:2305.05176]
- Ong, I., Almahairi, A., Wu, V., Chiang, W.-L., Wu, T., Gonzalez, J. E.,
  Kadous, M. W., and Stoica, I. (2024). RouteLLM: learning to route LLMs
  with preference data. #link("https://arxiv.org/abs/2406.18665")[arXiv:2406.18665]
#set par(justify: true)

#block(breakable: false)[
= Appendix — reproduce map

Every data figure and number regenerates from a committed ledger (Figure 1
is the conceptual map; its cells are instantiated by F2–F5). The
pre-registered GO/NO-GO gate outcomes are recorded here (the figures carry
the scientific reading; the gates are process labels).

#block(breakable: false)[
#table(
  columns: (0.5fr, 1.5fr, 1.35fr, 1.15fr),
  ..tablestyle,
  table.header([*ID*], [*ledger (`ledgers/`)*], [*regenerates via*],
    [*pre-registered gate*]),
  [F2-A], [`gonogo-cem-existence.json`], [`make_figures.py` /
    `verify_note_numbers.py`], [NO-GO (reliability CI, missed by 0.016)],
  [F2-B], [`gonogo-grid-existence.json`], [same], [REJECT-TOO-EASY],
  [F2-C], [`gonogo-bbadv-existence.json`], [same], [REJECT-TOO-EASY],
  [F2-D], [`gonogo-screened-existence.json`], [same], [REJECT],
  [F3], [`gonogo-cem-existence.json` (rows 241/243/245)], [same], [— (per-world detail of F2-A)],
  [F4-A], [`curves-existence.json`], [same], [— (descriptive subset)],
  [F4-B], [`curves-bbenforced.json`], [same], [—],
  [F4-C], [`curves-grid.json`], [same], [—],
  [F4-D], [`curves-bbadvisory.json`], [same], [—],
  [F5], [`planted-kcycle.json`], [same], [construction checks proven 40/40],
)
#text(size: 8.5pt, fill: ash)[All ledgers at evidence commit `e372cc3`;
commands run from `experiments/coordination-frontier/` with stock Python 3 —
no dependencies. The `reproduce_target` field inside each ledger names the
private experiment harness that generated it; the public, checkable half of
the chain is the ledgers themselves and the regeneration scripts beside
them.]
]
]

#v(1.2em)
#block(breakable: false)[
#text(size: 8.5pt, fill: ash, tracking: 1.1pt)[THE ARTIFACT STACK]
#v(4pt)
#let stackbox(title, sub, fill: white, tcol: ink) = box(width: 100%,
  fill: fill, stroke: 0.7pt + ash, inset: 8pt,
  align(center)[
    #text(size: 9.5pt, weight: "semibold", fill: tcol, title)
    #linebreak()
    #text(size: 8pt, fill: if tcol == white { rgb("#c9d4e2") } else { ash }, sub)
  ])
#grid(
  columns: (1fr, auto, 1fr, auto, 1fr),
  column-gutter: 6pt,
  align: horizon,
  stackbox([Ephemeris Kernel], [deterministic discrete-event runtime — replay, ledgers, manifests]),
  text(fill: ash, size: 11pt, sym.arrow.r),
  stackbox([Epure Arena], [certified decision environment — exact pricing, oracle regret, eval-blind wall],
    fill: accent, tcol: white),
  text(fill: ash, size: 11pt, sym.arrow.r),
  stackbox([Cenacle], [regime-diagnosing decision council — future work, motivated by the map]),
)
]
