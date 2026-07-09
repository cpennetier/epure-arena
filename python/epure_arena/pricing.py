"""Wait-cost pricing (Gate 0): the value functional v_realized.

For a flow unit j with value v_j (its size in capacity units — the lattice
currency), held time w_j, and lateness L_j:

    C_wait_j(w) = v_j · (a·w + b·w²)          (w in hours)
    ℓ_j(L)      = hard:   v_j · 1[L > tol]
                  linear: v_j · min(1, L / tol)
    v_realized_j = v_j − C_wait_j(w_j) − ℓ_j(L_j)

implemented EXACTLY as stated — no cap, no floor. A negative v_realized_j
is meaningful (holding cost exceeded the unit's value) and keeps the
per-unit value-closure identity exact:

    v_j ≡ v_realized_j + wait_loss_j + late_loss_j            (delivered)
    v_j ≡ strand_loss_j                                        (stranded)
    v_j ≡ censored_loss_j                                      (in-flight)

HARD CONSTRAINT (Gate 0a, item 6): pricing consumes ``held_atu``
EXCLUSIVELY — never ``wait_atu``. Queue-only wait leaves schedule dwell
free, which reproduces the free-waiting Gate-1 null one level deeper.
``wait_atu`` appears here only as an ATTRIBUTION KEY (splitting the
held-priced wait loss into CAPACITY_QUEUE vs SCHEDULE_DWELL shares); it
is never a pricing input.

The convexity b > 0 is load-bearing: it creates the cross-curvature
∂²V/∂χ_i∂χ_j < 0 through shared slots (unit i's admission lengthens unit
j's held time, costing more per marginal hour the longer j already
waits). Linear-only pricing keeps V additive in expectation.

Domain profiles θ_D are ANCHORS, not tuning knobs: every swept θ must be
a defensible operating regime of a real domain (the agnostic-by-
parametrization criterion; the demo is the C_wait ≡ 0 point).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_H = 3_600_000.0  # ATU (i64 ms) per hour


@dataclass(frozen=True)
class WaitCostProfile:
    """One domain anchor θ_D.

    Attributes:
        name: Stable identifier (recorded in run manifests / reports).
        a_per_hour: Linear wait coefficient — fraction of unit value lost
            per held hour.
        b_per_hour2: Convex wait coefficient — fraction of unit value lost
            per held hour squared. b > 0 is what makes V(S) non-additive.
        late_mode: "hard" (any lateness beyond tol forfeits the unit) or
            "linear" (value ramps to zero over tol).
        late_tol_atu: Tolerance scale, i64 ATU. hard: L > tol ⇒ full loss;
            linear: loss = min(1, L/tol)·v.
        description: The domain claim this anchor encodes.
    """

    name: str
    a_per_hour: float
    b_per_hour2: float
    late_mode: str
    late_tol_atu: int
    description: str

    def __post_init__(self) -> None:
        if self.late_mode not in ("hard", "linear"):
            raise ValueError(f"late_mode must be hard|linear, got {self.late_mode}")
        if self.late_mode == "linear" and self.late_tol_atu <= 0:
            raise ValueError("linear late_mode requires late_tol_atu > 0")


#: Scarce/steep regime: no detour, small slack, hard tolerance, STEEP
#: convex wait — a held unit burns reservation value fast and a missed
#: window is a missed window. The regime predicted to SHOW a decision gap.
#: Domain reading lives in the project glossary, never in code.
STEEP_SCARCE = WaitCostProfile(
    name="steep_scarce", a_per_hour=0.02, b_per_hour2=0.15,
    late_mode="hard", late_tol_atu=0,
    description="steep convex holding cost; any lateness forfeits the unit",
)

#: Slack/elastic regime: high detour freedom, moderate slack, soft
#: tolerance, near-linear small wait. The regime predicted to REPRODUCE
#: the Gate-1 null — the control that confirms the framework.
#: Domain reading lives in the project glossary, never in code.
SLACK_ELASTIC = WaitCostProfile(
    name="slack_elastic", a_per_hour=0.01, b_per_hour2=0.002,
    late_mode="linear", late_tol_atu=int(12 * _H),
    description="mild near-linear holding cost; lateness ramps over 12h",
)

#: The demo's parameter point: C_wait ≡ 0 and hard on-time semantics, so
#: v_realized reduces IDENTICALLY to today's value (on-time units).
DEMO_ZERO = WaitCostProfile(
    name="demo_zero", a_per_hour=0.0, b_per_hour2=0.0,
    late_mode="hard", late_tol_atu=0,
    description="the zero point: v_realized ≡ v · 1[on-time]",
)

PROFILES = {p.name: p for p in (STEEP_SCARCE, SLACK_ELASTIC, DEMO_ZERO)}


def unit_value(size_units: int) -> float:
    """v_j — the unit's value in the lattice currency (capacity units)."""
    return float(size_units)


def wait_cost(held_atu: int, size_units: int, p: WaitCostProfile) -> float:
    """C_wait_j(w) = v_j · (a·w + b·w²), w = held_atu in hours.

    Consumes held_atu EXCLUSIVELY (never wait_atu). Deterministic: a pure
    float function of i64 inputs.
    """
    w = max(0, held_atu) / _H
    return unit_value(size_units) * (p.a_per_hour * w + p.b_per_hour2 * w * w)


def late_loss(late_atu: int, size_units: int, p: WaitCostProfile) -> float:
    """ℓ_j(L) under the profile's tolerance semantics."""
    v = unit_value(size_units)
    if late_atu <= 0:
        return 0.0
    if p.late_mode == "hard":
        return v if late_atu > p.late_tol_atu else 0.0
    return v * min(1.0, late_atu / p.late_tol_atu)


def v_realized(held_atu: int, delivered_atu: int, due_atu: int,
               size_units: int, p: WaitCostProfile) -> float:
    """v_j − C_wait_j(held) − ℓ_j(max(0, delivered − due)). No floor."""
    v = unit_value(size_units)
    return (v
            - wait_cost(held_atu, size_units, p)
            - late_loss(delivered_atu - due_atu, size_units, p))


# ── plan-side (substrate) times ─────────────────────────────────────────
def plan_times(path_slots: list[tuple[int, int]], appear_atu: int,
               ctx: Any) -> tuple[int, int]:
    """(planned_held_atu, planned_delivered_atu) for one committed unit.

    Reconstructs the ready chain from the plan's exact slots: ready₀ =
    appear; per hop, held += depart − ready; ready = depart + duration.
    Under Option-A pinned execution these equal the engine's realized
    held/delivered EXACTLY — that equality is the Gate-0c round trip.

    Args:
        path_slots: The decision trace's [(lane_id, slot_pos)] chain.
        appear_atu: The unit's appearance time.
        ctx: PlanContext (schedule_departures + adjacency for durations).
    """
    dur_of: dict[int, int] = {}
    for adj in ctx.adjacency:
        for _v, eid, dur, _c, _co, _m in adj:
            dur_of[eid] = dur
    ready = appear_atu
    held = 0
    for eid, slot_pos in path_slots:
        depart = ctx.schedule_departures[eid][slot_pos][0]
        held += max(0, depart - ready)
        ready = depart + dur_of[eid]
    return held, ready


# ── record-side (engine / agent) value report ───────────────────────────
@dataclass
class ValueReport:
    """Typed value decomposition over one execution. All floats are exact
    sums of deterministic per-unit terms (sorted by flow_unit_id).

    Zero-unexplained-mass identity (asserted by ``closes``):
        total_value == v_realized_total + wait_loss + late_loss
                       + strand_loss + censored_loss
    """

    profile: str
    n_units: int
    total_value: float
    v_realized_total: float
    wait_loss: float
    late_loss: float
    strand_loss: float          # typed by reason in strand_loss_by_reason
    censored_loss: float        # in-flight at cutoff (neither log)
    wait_loss_capacity_queue: float   # attribution label: wait_atu share
    wait_loss_schedule_dwell: float   # attribution label: 1 − share
    strand_loss_by_reason: dict[str, float]
    held_on_lost_units_atu: int  # diagnostic: held time on strands (not priced)
    per_unit: dict[int, float]   # flow_unit_id → v_realized_j (delivered only)

    def closes(self, tol: float = 1e-6) -> bool:
        return abs(
            self.total_value
            - (self.v_realized_total + self.wait_loss + self.late_loss
               + self.strand_loss + self.censored_loss)
        ) <= tol


_STRAND_REASONS = {
    0: "topology_unreachable", 1: "schedule_infeasible",
    2: "capacity_exhausted", 3: "deadline_missed",
    4: "lane_outage", 5: "node_outage",
}


def value_report(demand_cols: dict[str, list], delivery: dict[str, list],
                 strands: dict[str, list], p: WaitCostProfile,
                 restrict_ids: set[int] | None = None) -> ValueReport:
    """Price one execution's records into the typed value decomposition.

    Args:
        demand_cols: dict with flow_unit_id / due_atu / size_units lists
            (the demand table, plus any injected surge units).
        delivery: ``PyEngine.delivery_records()`` (must carry held_atu).
        strands: ``PyEngine.strand_records()`` (must carry held_atu).
        p: The wait-cost profile θ.
        restrict_ids: If given, price only these units (the admitted-set
            view V(S)); censored = restricted units in neither log.
    """
    due_of = dict(zip(demand_cols["flow_unit_id"], demand_cols["due_atu"]))
    size_of = dict(zip(demand_cols["flow_unit_id"], demand_cols["size_units"]))
    ids = set(size_of) if restrict_ids is None else set(restrict_ids)

    dl = sorted(
        (int(s), int(d), int(h), int(w))
        for s, d, h, w in zip(delivery["flow_unit_id"],
                              delivery["delivered_atu"],
                              delivery["held_atu"],
                              delivery["wait_atu"])
        if int(s) in ids
    )
    st = sorted(
        (int(s), int(r), int(h))
        for s, r, h in zip(strands["flow_unit_id"], strands["reason"],
                           strands["held_atu"])
        if int(s) in ids
    )

    total_value = float(sum(size_of[i] for i in sorted(ids)))
    per_unit: dict[int, float] = {}
    _cw: list[float] = []
    _lo: list[float] = []
    _cw_q: list[float] = []
    _cw_d: list[float] = []
    seen: set[int] = set()
    for sid, delivered, held, wait in dl:
        seen.add(sid)
        size = size_of[sid]
        cw = wait_cost(held, size, p)
        lo = late_loss(delivered - due_of[sid], size, p)
        per_unit[sid] = unit_value(size) - cw - lo
        _cw.append(cw)
        _lo.append(lo)
        # Attribution labels only — the PRICE came from held_atu.
        share = (wait / held) if held > 0 else 0.0
        _cw_q.append(cw * share)
        _cw_d.append(cw * (1.0 - share))
    # ONE accumulation convention across every plane: builtin sum() over
    # ascending-sid terms (Neumaier-compensated for floats on CPython
    # ≥ 3.12). The oracle (ilp_wait.claimed_v_realized) uses the same, so
    # cross-plane totals are bit-identical when per-unit terms are.
    vr_total = sum(per_unit[s] for s in sorted(per_unit))
    wl = sum(_cw)
    ll = sum(_lo)
    wl_queue = sum(_cw_q)
    wl_dwell = sum(_cw_d)

    sl = 0.0
    sl_by: dict[str, float] = {}
    held_lost = 0
    for sid, reason, held in st:
        seen.add(sid)
        v = unit_value(size_of[sid])
        sl += v
        key = _STRAND_REASONS.get(reason, f"reason_{reason}")
        sl_by[key] = sl_by.get(key, 0.0) + v
        held_lost += held

    censored = float(sum(size_of[i] for i in sorted(ids - seen)))

    return ValueReport(
        profile=p.name, n_units=len(ids), total_value=total_value,
        v_realized_total=vr_total, wait_loss=wl, late_loss=ll,
        strand_loss=sl, censored_loss=censored,
        wait_loss_capacity_queue=wl_queue, wait_loss_schedule_dwell=wl_dwell,
        strand_loss_by_reason=dict(sorted(sl_by.items())),
        held_on_lost_units_atu=held_lost, per_unit=per_unit,
    )
