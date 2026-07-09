#!/usr/bin/env python
"""Assert the engine's live wire fingerprint matches docs/protocol.md.

Run per-commit (`make verify-fingerprint`, part of reproduce-smoke): a
protocol drift that is not accompanied by a deliberate doc + golden-table
update fails CI here.
"""

from __future__ import annotations

import pathlib
import re

from epure_arena import _engine
from epure_arena.scenarios.library import ScenarioSpec, generate_scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    spec = ScenarioSpec(
        topology="star", n_nodes=8, density="sparse", capacity="loose",
        demand_pattern="uniform", schedule_cadence="hourly",
        priority_mix="mixed", n_demand_events=20, horizon_hours=24, seed=7,
    )
    b = generate_scenario(spec)
    eng = _engine.PyEngine(b.node_ipc, b.lane_ipc, b.schedule_ipc, b.demand_ipc)
    live = eng.get_schema_fingerprint()
    doc_text = (ROOT / "docs" / "protocol.md").read_text()
    m = re.search(r"fingerprint:\s*`([0-9a-f]{16})`", doc_text)
    if m is None:
        raise SystemExit("docs/protocol.md: no fingerprint declaration found")
    doc = m.group(1)
    if live != doc:
        raise SystemExit(f"fingerprint drift: engine={live} docs={doc}")
    print(f"fingerprint OK: {live}")


if __name__ == "__main__":
    main()
