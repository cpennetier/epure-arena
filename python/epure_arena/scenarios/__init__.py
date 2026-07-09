"""The canonical synthetic scenario library: seeded, deterministic,
self-contained world generation (topologies, demand patterns, connection
schedules), the golden worlds with known optima, and the disruption suite.
"""

from epure_arena.scenarios.library import ScenarioBundle, ScenarioSpec, generate_scenario

__all__ = ["ScenarioBundle", "ScenarioSpec", "generate_scenario"]
