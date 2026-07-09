"""Typed reason-code taxonomy for non-ideal outcomes (Optimize MVP, Step 1).

Realizes the milestone sentence of the reference architecture (§22): for every
bad outcome the system says whether the WORLD made it impossible (R1), the
DESIGN ENVELOPE was exceeded (R2), or the ALGORITHM was not yet good enough
(R3). The attribution table is literally a GROUP BY over :class:`ReasonCode`.

The mapping is certified by the offline feasibility oracle
(``epure_arena.harness.feasibility_oracle``) and, where capacity feasibility is
the question, refined by the LP/flow oracle (Step 5). This module deliberately
imports nothing from node-engine: it consumes primitive verdict strings and
strand-reason ints, so the dependency direction stays planner-side-pure.

Main exports: :class:`ReasonCode`, :class:`RClass`, :func:`attribute_strand`,
:func:`attribution_table`.

Usage:
    from epure_arena.reasons import ReasonCode, attribute_strand
    code = attribute_strand(strand_reason=1, verdict="FEASIBLE_ON_TIME")
    assert code is ReasonCode.ALGO_DEADEND
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Iterable

# Oracle verdict strings (mirrors epure_arena.harness.feasibility_oracle —
# string constants, not an import, to keep this package node-engine-free).
FEASIBLE_ON_TIME = "FEASIBLE_ON_TIME"
FEASIBLE_LATE = "FEASIBLE_LATE"
INFEASIBLE = "INFEASIBLE"

# StrandReason as u8 (services/des-engine/src/types.rs).
TOPOLOGY_UNREACHABLE = 0
SCHEDULE_INFEASIBLE = 1
CAPACITY_EXHAUSTED = 2
DEADLINE_MISSED = 3
EDGE_OUTAGE = 4
HUB_OUTAGE = 5

# GENUINE sub-kinds (feasibility_oracle.FlowUnitVerdict.genuine_kind).
GENUINE_CLIFF = "CLIFF"
GENUINE_STRUCTURAL = "STRUCTURAL"


class RClass(enum.Enum):
    """The three top-level failure classes of reference-architecture §10."""

    R1_WORLD = "R1"  # mechanical infeasibility — the world made it impossible
    R2_ENVELOPE = "R2"  # outside the design envelope — demand/deadline/shock
    R3_ALGORITHM = "R3"  # a feasible/better action existed; the system missed it


class ReasonCode(enum.Enum):
    """Per-flow_unit typed reason for a non-ideal outcome.

    Every code names its certifying artifact in the docstring table of the
    plan report (§5, optimize-mvp-stranding-attribution-and-planner-plan.md).
    """

    # R1 — the world
    WORLD_STRUCTURAL = "WORLD_STRUCTURAL"  # no timetable path at ANY release
    WORLD_CAPACITY_SHORTFALL = "WORLD_CAPACITY_SHORTFALL"  # LP-certified cut
    # R2 — the design envelope
    ENVELOPE_CLIFF = "ENVELOPE_CLIFF"  # released after the last feasible wave
    ENVELOPE_DEADLINE = "ENVELOPE_DEADLINE"  # supply exists, arrives past due
    ENVELOPE_DEMAND_SURGE = "ENVELOPE_DEMAND_SURGE"  # realization > envelope
    ENVELOPE_SHOCK = "ENVELOPE_SHOCK"  # §E outage window strand
    # R3 — the algorithm
    ALGO_DEADEND = "ALGO_DEADEND"  # on-time path existed; executor stranded it
    ALGO_CAPACITY_MYOPIA = "ALGO_CAPACITY_MYOPIA"  # feasible assignment missed
    PLAN_FIDELITY_DIVERGENCE = "PLAN_FIDELITY_DIVERGENCE"  # plan ≠ execution

    @property
    def r_class(self) -> RClass:
        return _R_CLASS[self]


_R_CLASS: dict[ReasonCode, RClass] = {
    ReasonCode.WORLD_STRUCTURAL: RClass.R1_WORLD,
    ReasonCode.WORLD_CAPACITY_SHORTFALL: RClass.R1_WORLD,
    ReasonCode.ENVELOPE_CLIFF: RClass.R2_ENVELOPE,
    ReasonCode.ENVELOPE_DEADLINE: RClass.R2_ENVELOPE,
    ReasonCode.ENVELOPE_DEMAND_SURGE: RClass.R2_ENVELOPE,
    ReasonCode.ENVELOPE_SHOCK: RClass.R2_ENVELOPE,
    ReasonCode.ALGO_DEADEND: RClass.R3_ALGORITHM,
    ReasonCode.ALGO_CAPACITY_MYOPIA: RClass.R3_ALGORITHM,
    ReasonCode.PLAN_FIDELITY_DIVERGENCE: RClass.R3_ALGORITHM,
}


def attribute_strand(
    strand_reason: int,
    verdict: str | None,
    genuine_kind: str | None = None,
    *,
    lp_certified_infeasible: bool | None = None,
) -> ReasonCode:
    """Map one stranded flow_unit to its :class:`ReasonCode`.

    Args:
        strand_reason: ``StrandReason as u8`` from the DES strand log.
        verdict: capacity-free oracle verdict from ``(origin, release)`` —
            one of ``FEASIBLE_ON_TIME | FEASIBLE_LATE | INFEASIBLE`` — or
            ``None`` for reasons that need no oracle (topology, outages).
        genuine_kind: for ``INFEASIBLE`` verdicts, ``"CLIFF"`` (a path existed
            for earlier releases) or ``"STRUCTURAL"`` (no path at any release).
        lp_certified_infeasible: Step-5 refinement for capacity strands with
            on-time supply: ``True`` ⇒ the LP certifies no capacity-feasible
            assignment existed (R1); ``False`` ⇒ one existed (R3); ``None`` ⇒
            unrefined — conservatively attributed R3 (the capacity-free oracle
            already proved schedule supply, so the burden of proof for "the
            world did it" is on the capacity certificate, not the router).

    Returns:
        The typed reason code.

    Raises:
        ValueError: on an unknown strand_reason / verdict combination, so a
            new enum variant can never silently fall through ("every failure
            must compile" — Law 4).
    """
    if strand_reason == TOPOLOGY_UNREACHABLE:
        return ReasonCode.WORLD_STRUCTURAL
    if strand_reason in (EDGE_OUTAGE, HUB_OUTAGE):
        return ReasonCode.ENVELOPE_SHOCK

    if strand_reason in (SCHEDULE_INFEASIBLE, DEADLINE_MISSED, CAPACITY_EXHAUSTED):
        if verdict == FEASIBLE_ON_TIME:
            if strand_reason == CAPACITY_EXHAUSTED:
                if lp_certified_infeasible is True:
                    return ReasonCode.WORLD_CAPACITY_SHORTFALL
                return ReasonCode.ALGO_CAPACITY_MYOPIA
            return ReasonCode.ALGO_DEADEND
        if verdict == FEASIBLE_LATE:
            return ReasonCode.ENVELOPE_DEADLINE
        if verdict == INFEASIBLE:
            if genuine_kind == GENUINE_STRUCTURAL:
                return ReasonCode.WORLD_STRUCTURAL
            if genuine_kind == GENUINE_CLIFF:
                return ReasonCode.ENVELOPE_CLIFF
            raise ValueError(
                f"INFEASIBLE verdict requires genuine_kind CLIFF|STRUCTURAL, "
                f"got {genuine_kind!r}"
            )
        raise ValueError(
            f"strand_reason {strand_reason} requires an oracle verdict, "
            f"got {verdict!r}"
        )

    raise ValueError(f"unknown strand_reason {strand_reason}")


def attribution_table(
    rows: Iterable[tuple[int, str | None, str | None]],
) -> dict[str, int]:
    """Aggregate ``(strand_reason, verdict, genuine_kind)`` rows into the
    attribution table: one count per ReasonCode plus per-RClass totals.

    Returns a plain dict (JSON/markdown-friendly) with zero unexplained mass:
    every row maps to exactly one code or raises.
    """
    counts: Counter[ReasonCode] = Counter()
    for reason, verdict, kind in rows:
        counts[attribute_strand(reason, verdict, kind)] += 1
    out: dict[str, int] = {code.value: counts.get(code, 0) for code in ReasonCode}
    for rc in RClass:
        out[rc.value] = sum(
            n for code, n in counts.items() if code.r_class is rc
        )
    return out
