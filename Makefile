.PHONY: test test-python reproduce-smoke reproduce-paper reproduce-1m reproduce-benchmark reproduce-estimation reproduce-decision-gap reproduce-inflicted-wait verify-fingerprint

PY ?= python

# the compiled engine lives in ephemeris-kernel (installed as a dependency);
# this repo is pure Python.
test: test-python

test-python:
	$(PY) -m pytest tests -q

# ── reproduction tiers ────────────────────────────────────────────────────
# smoke: per-commit, < 10 min — anchor cells byte-asserted against
# repro/expected/. paper: scheduled/manual, ~4–6 h — the full tables.
# 1m: hardware-noted scale benchmark (counts asserted, timings reported).

reproduce-smoke: verify-fingerprint
	$(PY) -m pytest tests/golden_worlds tests/test_three_way_split.py tests/test_plan_fidelity.py -q
	$(PY) repro/smoke.py --verify

# paper: the ablation steps consume gate_experiment's rows as PIPED
# prior-step output through clone-relative runs/ files (gitignored) — never
# a machine-global /tmp path. Self-contained from a clean clone. The paper
# tables are RUN, not byte-asserted (the byte-assertions live in
# reproduce-smoke); this tier is the full, machine-dependent generation.
reproduce-paper:
	$(PY) repro/strand_matrix.py matrix
	$(PY) repro/regret_table.py
	@mkdir -p runs
	$(PY) repro/gate_experiment.py partA --no-emit > runs/gate_partA.jsonl
	$(PY) repro/gate_experiment.py partB --no-emit > runs/gate_partB.jsonl
	$(PY) repro/imagine_ablation.py --parta runs/gate_partA.jsonl --partb runs/gate_partB.jsonl
	$(PY) repro/propose_ablation.py --parta runs/gate_partA.jsonl

reproduce-1m:
	$(PY) repro/benchmark_1m.py

# performance characterization (manual/scheduled tier, ~4 min on an M2 Pro):
# throughput, latency-decomposed-certify, replay cost, memory, at 10k…1M.
# --verify byte-asserts the DETERMINISTIC counts/digests; wall-times and
# memory are reported (machine-dependent), printed as NeurIPS-style tables.
# Source for docs/benchmarks.md. Use --write to refresh the goldens.
reproduce-benchmark:
	$(PY) repro/benchmark.py --verify

# estimation-vs-decision: a byte-asserted regime CHARACTERIZATION (not a
# headline result) — reads the committed extract (its raw run folder is
# gitignored/absent). Runnable from a clean clone.
reproduce-estimation:
	$(PY) repro/estimation_vs_decision.py --verify

# decision gap (Gate P1b) — greedy-vs-oracle regret vs wait-cost steepness,
# ~25 min (30 HiGHS solves at grid n12/d120, controlled bottleneck rho=2.0).
# --verify byte-asserts the DETERMINISTIC fields of the committed golden
# (wall-times reported, not asserted). Raw run artifacts are not retained;
# this reproduces repro/expected/decision_gap.json from a clean clone.
reproduce-decision-gap:
	$(PY) repro/decision_gap.py --verify

# inflicted-wait decomposition (Gate P1c) — own-wait vs inflicted-wait at
# s=0.75 (full-system) and steep_hard (admission model), ~10 min (10 HiGHS
# solves). --verify byte-asserts the deterministic fields of the committed
# golden repro/expected/inflicted_wait.json (wall-times reported).
reproduce-inflicted-wait:
	$(PY) repro/inflicted_wait.py --verify

verify-fingerprint:  ## the engine's wire fingerprint must match docs/protocol.md
	$(PY) repro/verify_fingerprint.py
