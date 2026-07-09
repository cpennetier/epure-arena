# epure-arena — Performance Benchmarks

> The numbers that justify presenting epure-arena as a **certified
> environment / simulator / prover** rather than asserting it. Every figure
> here is produced by `repro/benchmark.py` (`make reproduce-benchmark`), at
> increasing scale (10k → 1M flow-units), in one regime (backbone / hourly /
> **loose** capacity, seed 42).
>
> **What is asserted vs reported.** *Counts and digests* are deterministic
> (integer ATU, seed-derived, the same compiled engine) and are **byte-asserted**
> against [`../repro/expected/benchmark.json`](../repro/expected/benchmark.json).
> *Wall-times, throughput, and memory* are **machine-dependent** — they are
> **reported** numbers (full record in [`../data/benchmark.json`](../data/benchmark.json)),
> never byte-asserted. The hardware is stated in every caption.
>
> **Scope of these claims.** These are **absolute, single-machine** measurements
> on **one scenario family** (backbone topology / hourly cadence / loose
> capacity, seed 42). They are **not** a comparison against any other system or
> library, and **not** a topology/cadence/capacity sweep. Every "faster" or
> "×" below is strictly *epure-arena's certified planner vs epure-arena's own
> Dijkstra baseline, on this configuration, on this machine*. Whether the same
> ratios hold on other topologies, saturation regimes, or hardware is **not**
> claimed here — it would need the sweep, which this benchmark does not run.

**Hardware for the reported numbers:** Apple M2 Pro (10 cores), macOS 26.5
(Darwin 25.5.0), Python 3.12.12, single-threaded Python planner over the
PyO3-compiled Rust engine (`epure_arena._engine`).

---

## Why these four metrics

A *certified environment* makes three promises, and each costs something
measurable:

1. **It is a fast world to step.** An RL/decision environment is only useful
   if a rollout is cheap. → **Throughput** (Table 1).
2. **Its certificate is affordable.** Producing a *proven* plan (not just a
   route) must not cost orders of magnitude more than a naïve router, or no one
   will run it in the loop. → **Latency**, decomposed so you can see *where* the
   certificate's cost lives (Tables 2–3).
3. **Its replay is exact and the exactness is free.** The reward signal is
   "certified by construction" only if the run is byte-identical on replay; the
   *cost of that guarantee* must be stated. → **Replay / determinism + memory**
   (Table 4).

---

## Table 1 — Throughput: the DES world run (greedy router, no plan)

The world replayed at speed under the engine's own greedy router, capacity
enforced. This is the "what physically happened" step that sits behind every
reward.

| flow-units | events processed | run (s) | flow-units/s | events/s |
|-----------:|-----------------:|--------:|-------------:|---------:|
|     10,000 |           89,298 |   0.082 |      121,862 | 1,088,202 |
|     30,000 |          266,066 |   0.199 |      150,652 | 1,336,114 |
|    100,000 |          885,595 |   0.534 |      187,376 | 1,659,389 |
|    300,000 |        2,656,789 |   1.665 |      180,198 | 1,595,827 |
|  1,000,000 |        8,855,911 |   3.070 |      325,728 | 2,884,621 |

**What it means.** `events_processed` is the count of discrete simulation
events (injections, boardings, arrivals, deliveries, strands) the kernel
retired — the true unit of work for a DES. **events/s** *rises* with scale
(1.1M → 2.9M) because per-run fixed costs (graph load, schedule index)
amortize: a 1M-flow-unit world — 8.9M events — replays in ~3 s. **Why it
matters for an environment:** the paired same-seed rollout behind every
certified reward is two such runs; at ~3 s each, a full counterfactual at
1M scale is ~6 s of wall, which is what makes the certified-reward loop
practical rather than theoretical.

---

## Table 2 — Decision latency: static (Dijkstra) vs certified (A\*)

Two decision policies graded on the *same* requests and context. **Static**
is `StaticPlanner` — Dijkstra on `duration_atu`, capacity-/schedule-blind (the
ARCHITECTURE.md "what does a capacity-blind router do" control). **Certified**
is `CapacityAwarePlanner` — time-dependent A\* over per-(lane, slot) residual
capacity, guided by the capacity-free destination oracle as an admissible
lower bound.

| flow-units | static (s) | µs/fu | certify (s) | µs/fu | speedup (static/certify) |
|-----------:|-----------:|------:|------------:|------:|-------------------------:|
|     10,000 |      0.608 |  60.8 |       0.603 |  60.3 | 1.01× |
|     30,000 |      1.819 |  60.6 |       1.097 |  36.6 | 1.66× |
|    100,000 |      6.075 |  60.8 |       2.830 |  28.3 | 2.15× |
|    300,000 |     19.286 |  64.3 |       7.840 |  26.1 | 2.46× |
|  1,000,000 |     62.050 |  62.0 |      26.804 |  26.8 | 2.31× |

**The headline.** On this configuration, certification is **cheaper than
epure-arena's own capacity-blind baseline** — the certified planner runs 2.3×
faster than this Dijkstra baseline at 1M (it is *not* a claim against any
external router), and the gap *widens* with scale. The reason is
structural, not an implementation trick: Dijkstra expands the network outward
until it pops the destination (cost ~flat at ~60 µs/fu regardless of scale),
while the certified A\* is guided by the capacity-free destination profile —
an *admissible* heuristic that prunes every state that cannot make the
deadline even with infinite capacity, so the search pops a handful of labels
per flow-unit (~27 µs/fu). **Why it matters for a prover:** the thing that
makes the plan *certified* (the destination oracle / lower bound) is the same
thing that makes it *fast*. You do not trade speed for the proof; the proof
pays for itself.

Both planners scale **linearly** in demand (µs/fu is flat across two orders of
magnitude). Certified at ~27 µs/fu ≈ 37k certified commitments/s.

---

## Table 3 — Where the certify cost goes (decomposition, seconds)

The certified pipeline, stage by stage. `setup` = residual + connection index;
`oracle` = capacity-free backward destination profiles (the admissible bound);
`a*` = the time-dependent A\* search itself (Σ per-flow-unit `search_ns` from
the decision trace); `ledger` = commit-order sort + per-slot residual mutation
(`t_commit − a*`); `apply` = commit/pin onto the engine; `des` = pinned
execution; `attrib` = three-way split + plan-fidelity join (the proof over the
realized outcome).

| flow-units | setup | oracle |    a\* | ledger | apply |   des | attrib |  total |
|-----------:|------:|-------:|-------:|-------:|------:|------:|-------:|-------:|
|     10,000 | 0.004 |  0.280 |  0.284 |  0.032 | 0.014 | 0.009 |  0.004 |  0.628 |
|     30,000 | 0.010 |  0.274 |  0.672 |  0.137 | 0.124 | 0.029 |  0.017 |  1.263 |
|    100,000 | 0.014 |  0.278 |  2.055 |  0.478 | 0.531 | 0.108 |  0.125 |  3.589 |
|    300,000 | 0.006 |  0.347 |  6.076 |  1.401 | 0.593 | 0.444 |  0.265 |  9.132 |
|  1,000,000 | 0.005 |  0.319 | 21.346 |  5.105 | 6.609 | 2.123 |  1.053 | 36.560 |

**What it means.** At 1M the cost splits ≈ **A\* 58% · apply 18% · ledger 14% ·
des 6% · attribution 3% · oracle <1%**. Two facts carry the story:

- **The oracle is ~constant** (~0.3 s at *every* scale). It is built
  per *destination*, not per flow-unit — O(nodes · connections), independent of
  demand — so the admissible lower bound behind every certificate is a fixed,
  amortized cost. The "proof" part of the pipeline does **not** grow with the
  problem; the *search* it guides does.
- **A\* dominates and scales linearly** (0.28 → 21.3 s, ~75× for 100× demand).
  The certify cost is, to first order, "run a tiny guided search per
  flow-unit." `ledger` (residual bookkeeping) and `apply` (pinning the plan
  into the engine) are the next terms; `attribution` — turning the realized run
  into a typed, proven three-way split — is **~3%**, i.e. proving *whose fault*
  every non-ideal outcome was is nearly free once the run exists.

**Why it matters for a prover.** "Certify is ~N× greedy" decomposes into a
*constant* proof-bound (oracle) plus a *linear* guided search (A\*); it is not a
super-linear blow-up. The certificate scales.

---

## Table 4 — Replay (determinism) cost & memory

A *bare* run (execute, discard) vs a *replay* run (execute, capture
delivery/strand records, compute the byte-identity digest). A second replay's
digest is asserted equal to the first — byte-identical replay, verified at
every scale.

| flow-units | bare run (s) | replay run (s) | overhead | byte-identical | peak RSS (MB) |
|-----------:|-------------:|---------------:|---------:|:--------------:|--------------:|
|     10,000 |        0.081 |          0.090 |   10.6 % |      ✅ True    |         115.0 |
|     30,000 |        0.214 |          0.215 |    0.3 % |      ✅ True    |         185.0 |
|    100,000 |        0.555 |          0.597 |    7.4 % |      ✅ True    |         371.0 |
|    300,000 |        1.529 |          1.699 |   11.1 % |      ✅ True    |         854.9 |
|  1,000,000 |        3.056 |          4.386 |   43.5 % |      ✅ True    |       2,158.0 |

**What it means.** The engine is deterministic by construction (integer time,
fixed event ordering), so **determinism itself is free** — replay is just
re-execution and yields a byte-identical digest every time. The "overhead"
column is the price of a *verifiable* artifact: pulling the records out of the
engine and hashing them. It is small at moderate scale and grows to ~44% at
1M, where materializing and sorting ~1M delivery records to digest them is a
real (but one-time, off the hot loop) cost. **Memory** is ~linear in demand
(~2.2 GB at 1M), dominated by the Arrow record buffers and the planner's
per-node residual/profile state.

**Why it matters for an environment.** The claim "the reward is exact, not
estimated" rests entirely on byte-identical replay. Table 4 is the evidence: a
paired same-seed rollout (act vs. no-op) differs *only* by the action, because
everything else replays to the same bytes — `replay_identical = True` at every
scale is that property, measured, not assumed.

---

## Scaling summary (growth trends, 10k → 1M = 100× demand)

| quantity | 10k → 1M factor | trend |
|---|---:|---|
| events/s (throughput) | ×2.65 *faster* | improves — fixed costs amortize |
| static (Dijkstra) wall | ×102 | **linear** in demand (~62 µs/fu, flat) |
| certified (A\*) wall | ×44 | **linear** search (~27 µs/fu, flat); sub-100× total only because the oracle is fixed |
| oracle (proof bound) | ×1.1 | **constant** — per-destination, demand-independent |
| peak RSS | ×18.8 | ~linear in demand |

---

## Reproducing

```bash
make develop                       # build the engine into the venv
make reproduce-benchmark           # re-run; byte-assert counts/digests; print tables
python repro/benchmark.py --write  # refresh the goldens (counts) + data/benchmark.json
python repro/benchmark.py --scales 10000,100000   # a smaller curve
```

`--verify` re-runs the full curve (~4 min on the reference machine) and fails
iff any **deterministic** count or digest diverges from
`repro/expected/benchmark.json`. Wall-times and memory in this document are the
reported run on the hardware above; they will differ on yours, the counts and
digests will not.
