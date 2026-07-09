"""World — the environment (the canonical role).

The world is physics, not intelligence: a deterministic discrete-event
engine over a capacitated time-graph, exposed through the compiled
extension (`epure_arena._engine.PyEngine`) plus the Arrow-to-context
adapter. Agents never mutate the world directly; they propose, the executor
certifies and pins, the engine applies.
"""

from epure_arena import _engine
from epure_arena.world.graph_adapter import build_plan_context

PyEngine = _engine.PyEngine

__all__ = ["PyEngine", "_engine", "build_plan_context"]
