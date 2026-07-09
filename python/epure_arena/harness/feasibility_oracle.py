"""Offline feasibility oracle — split ``schedule_infeasible`` strands into
GENUINE schedule-shortage vs ROUTER-INDUCED (recoverable) vs DEADLINE-bound.

This is a READ-ONLY analysis over a COMPLETED run. It does not touch routing or
strand behavior. For each flow_unit the DES stranded as ``ScheduleInfeasible`` it
asks the schedule-SUPPLY question on the world timetable:

    earliest = _engine.PyEngine.earliest_arrival(origin, release)   # capacity IGNORED
    arrival  = earliest[dest]

    arrival <= due        → FEASIBLE_ON_TIME → ROUTER-INDUCED (recoverable)
    due < arrival < ∞     → FEASIBLE_LATE    → DEADLINE-bound
    arrival == ∞ (i64MAX) → INFEASIBLE       → GENUINE schedule shortage

CAPACITY IS IGNORED BY DESIGN. Production runs carry ~0 capacity refusals, so
the only meaningful question is whether the SCHEDULE supplies a path — not
whether capacity admits it. The oracle reuses the production time-dependent
Dijkstra relaxation (``next_departure(lane, arrival).depart_atu +
lane.duration_atu``); see ``services/des-engine/src/feasibility_oracle.rs``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# StrandReason::ScheduleInfeasible as u8 (services/des-engine/src/types.rs).
SCHEDULE_INFEASIBLE: int = 1
# i64::MAX returned by earliest_arrival for an unreachable destination.
I64_MAX: int = (1 << 63) - 1

ROUTER_INDUCED = "FEASIBLE_ON_TIME"  # recoverable: a path arrives on time
DEADLINE_BOUND = "FEASIBLE_LATE"  # a path exists but arrives past the deadline
GENUINE = "INFEASIBLE"  # no schedule path at all — a real supply shortage

# GENUINE sub-kinds (Step-1 first-class split; mirrored as string constants in
# epure_arena.reasons to keep the dependency direction planner-side-pure):
GENUINE_CLIFF = "CLIFF"  # a path existed for earlier releases — timetable cliff
GENUINE_STRUCTURAL = "STRUCTURAL"  # no path at ANY release — build-supply gap


@dataclass
class FlowUnitVerdict:
    flow_unit_id: int
    origin: int
    dest: int
    appear_atu: int
    due_atu: int
    strand_node: int
    arrival_atu: int  # I64_MAX if unreachable
    verdict: str
    # For GENUINE verdicts only: CLIFF vs STRUCTURAL (None otherwise).
    genuine_kind: str | None = None
    # StrandReason as u8 of the classified record (classify_engine can widen
    # beyond ScheduleInfeasible via its ``reasons`` parameter).
    reason: int = SCHEDULE_INFEASIBLE
    # StrandSite as u8 (WHERE the engine decided the strand; Step-0 column).
    site: int = -1


@dataclass
class OracleReport:
    """Split of the ``schedule_infeasible`` strand bucket."""

    n_schedule_infeasible: int
    router_induced: int  # FEASIBLE_ON_TIME
    deadline_bound: int  # FEASIBLE_LATE
    genuine: int  # INFEASIBLE (== genuine_cliff + genuine_structural)
    genuine_cliff: int = 0  # release after the last feasible wave
    genuine_structural: int = 0  # no timetable path at any release
    verdicts: list[FlowUnitVerdict] = field(default_factory=list)
    capacity_ignored: bool = True

    def pct(self, n: int) -> float:
        return 100.0 * n / self.n_schedule_infeasible if self.n_schedule_infeasible else 0.0


def _verdict_for(arrival: int, due: int) -> str:
    if arrival >= I64_MAX:
        return GENUINE
    if arrival <= due:
        return ROUTER_INDUCED
    return DEADLINE_BOUND


# Bulk verdict codes (mirror services/des-engine/src/feasibility_oracle.rs).
_BULK_ON_TIME = 0
_BULK_LATE = 1
_BULK_CLIFF = 2
_BULK_STRUCTURAL = 3
_BULK_VERDICT = {
    _BULK_ON_TIME: (ROUTER_INDUCED, None),
    _BULK_LATE: (DEADLINE_BOUND, None),
    _BULK_CLIFF: (GENUINE, GENUINE_CLIFF),
    _BULK_STRUCTURAL: (GENUINE, GENUINE_STRUCTURAL),
}


def _report_from(verdicts: list[FlowUnitVerdict]) -> OracleReport:
    counts = Counter(v.verdict for v in verdicts)
    kinds = Counter(v.genuine_kind for v in verdicts if v.genuine_kind is not None)
    return OracleReport(
        n_schedule_infeasible=len(verdicts),
        router_induced=counts[ROUTER_INDUCED],
        deadline_bound=counts[DEADLINE_BOUND],
        genuine=counts[GENUINE],
        genuine_cliff=kinds[GENUINE_CLIFF],
        genuine_structural=kinds[GENUINE_STRUCTURAL],
        verdicts=verdicts,
    )


def classify_engine(
    engine: Any,
    *,
    reasons: tuple[int, ...] = (SCHEDULE_INFEASIBLE,),
    method: str = "profile",
) -> OracleReport:
    """Classify the strands of an ALREADY-RUN PyEngine (default: the
    ``ScheduleInfeasible`` bucket; pass ``reasons`` to widen, e.g. to include
    ``CapacityExhausted`` for the planner's attribution table).

    ``method="profile"`` (default) uses the bulk destination-rooted profile
    oracle (``PyEngine.oracle_verdicts``): one backward profile-CSA scan per
    distinct destination, one binary search per flow_unit — the 1M-scale path.
    ``method="dijkstra"`` keeps the original per-(origin, release)
    source-Dijkstra loop as the independent cross-check; both methods are
    pinned equal by tests (identical relaxation math).

    GENUINE verdicts are sub-classified CLIFF (a path existed for earlier
    releases — timetable cliff) vs STRUCTURAL (no path at any release).
    """
    if method not in ("profile", "dijkstra"):
        raise ValueError(f"unknown method {method!r}")
    sr = engine.strand_records()
    n = len(sr["flow_unit_id"])
    selected = [i for i in range(n) if sr["reason"][i] in reasons]

    if method == "profile":
        origins = [int(sr["origin_node"][i]) for i in selected]
        dests = [int(sr["dest_node"][i]) for i in selected]
        releases = [int(sr["appear_atu"][i]) for i in selected]
        dues = [int(sr["due_atu"][i]) for i in selected]
        bulk = engine.oracle_verdicts(origins, dests, releases, dues)
        verdicts = []
        for j, i in enumerate(selected):
            verdict, kind = _BULK_VERDICT[int(bulk["verdict"][j])]
            verdicts.append(
                FlowUnitVerdict(
                    flow_unit_id=int(sr["flow_unit_id"][i]),
                    origin=origins[j],
                    dest=dests[j],
                    appear_atu=releases[j],
                    due_atu=dues[j],
                    strand_node=int(sr["strand_node"][i]),
                    arrival_atu=int(bulk["arrival_atu"][j]),
                    verdict=verdict,
                    genuine_kind=kind,
                    reason=int(sr["reason"][i]),
                    site=int(sr["site"][i]),
                )
            )
        return _report_from(verdicts)

    cache: dict[tuple[int, int], list[int]] = {}
    t0_cache: dict[int, list[int]] = {}
    verdicts: list[FlowUnitVerdict] = []
    for i in selected:
        o = int(sr["origin_node"][i])
        t0 = int(sr["appear_atu"][i])
        d = int(sr["dest_node"][i])
        due = int(sr["due_atu"][i])
        key = (o, t0)
        arr = cache.get(key)
        if arr is None:
            arr = engine.earliest_arrival(o, t0)  # exact-time, all dests, capacity ignored
            cache[key] = arr
        arrival = int(arr[d]) if d < len(arr) else I64_MAX
        verdict = _verdict_for(arrival, due)
        genuine_kind: str | None = None
        if verdict == GENUINE:
            arr0 = t0_cache.get(o)
            if arr0 is None:
                arr0 = engine.earliest_arrival(o, 0)
                t0_cache[o] = arr0
            reachable_ever = d < len(arr0) and int(arr0[d]) < I64_MAX
            genuine_kind = GENUINE_CLIFF if reachable_ever else GENUINE_STRUCTURAL
        verdicts.append(
            FlowUnitVerdict(
                flow_unit_id=int(sr["flow_unit_id"][i]),
                origin=o,
                dest=d,
                appear_atu=t0,
                due_atu=due,
                strand_node=int(sr["strand_node"][i]),
                arrival_atu=arrival,
                verdict=verdict,
                genuine_kind=genuine_kind,
                reason=int(sr["reason"][i]),
                site=int(sr["site"][i]),
            )
        )
    return _report_from(verdicts)


def run_and_classify(
    bundle: Any,
    *,
    horizon_atu: int,
    bucket_atu: int = 3_600_000,
    enforce_capacity: bool = True,
    router: str = "time_aware",
) -> OracleReport:
    """Run a ScenarioBundle through the DES, then classify its strands."""
    from epure_arena import _engine

    eng = _engine.PyEngine(
        bundle.node_ipc, bundle.lane_ipc, bundle.schedule_ipc, bundle.demand_ipc,
        bucket_atu, router,
    )
    eng.set_enforce_capacity(enforce_capacity)
    eng.run(horizon_atu)
    return classify_engine(eng)


def feasible_on_time_set(engine: Any, demand_ipc: bytes) -> set[int]:
    """Validation helper: the set of flow_unit_ids the oracle finds
    FEASIBLE_ON_TIME over the FULL demand table (capacity ignored). Used to
    assert ``oracle FEASIBLE_ON_TIME ⊇ ILP-delivered``.
    """
    import pyarrow.ipc as ipc

    table = ipc.open_stream(demand_ipc).read_all()
    sids = table.column("flow_unit_id").to_pylist()
    origins = table.column("origin_node_id").to_pylist()
    dests = table.column("dest_node_id").to_pylist()
    appears = table.column("appear_atu").to_pylist()
    dues = table.column("due_atu").to_pylist()

    cache: dict[tuple[int, int], list[int]] = {}
    out: set[int] = set()
    for sid, o, d, t0, due in zip(sids, origins, dests, appears, dues):
        key = (int(o), int(t0))
        arr = cache.get(key)
        if arr is None:
            arr = engine.earliest_arrival(int(o), int(t0))
            cache[key] = arr
        arrival = int(arr[int(d)]) if int(d) < len(arr) else I64_MAX
        if arrival < I64_MAX and arrival <= int(due):
            out.add(int(sid))
    return out


def genuine_seeds(report: OracleReport, top_k: int = 20) -> dict[str, Any]:
    """Schedule-shortage seeds from the GENUINE bucket: rank the unmet supply by
    destination sink and by O-D pair.
    """
    g = [v for v in report.verdicts if v.verdict == GENUINE]
    by_dest = Counter(v.dest for v in g)
    by_od = Counter((v.origin, v.dest) for v in g)
    return {
        "by_dest": by_dest.most_common(top_k),
        "by_od": by_od.most_common(top_k),
    }


def router_induced_waste(report: OracleReport, top_k: int = 20) -> dict[str, Any]:
    """ROUTER-INDUCED count + the nodes where recoverable flow_units were wasted
    (the node each recoverable flow_unit was actually stranded at).
    """
    r = [v for v in report.verdicts if v.verdict == ROUTER_INDUCED]
    by_strand_node = Counter(v.strand_node for v in r)
    return {
        "count": len(r),
        "by_strand_node": by_strand_node.most_common(top_k),
    }
