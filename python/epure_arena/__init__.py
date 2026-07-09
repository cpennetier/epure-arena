"""epure-arena: a deterministic, certificate-driven decision-quality engine
for constrained dynamic systems.

A world is a capacitated time-graph: flow units traverse nodes and lanes by
boarding scheduled connections. The engine is deterministic and replayable;
commitments are certified before they are pinned; every non-ideal outcome
carries a typed reason backed by a certificate; counterfactual value is
measured by paired same-seed rollouts.

The canonical roles are first-class seams: World, Gate, Propose, Imagine,
Navigate, Optimize — with the ledger/state-machine/commit substrate beneath
them. See each subpackage's docstring.
"""

from epure_arena.optimize.capacity_aware import CapacityAwarePlanner
from epure_arena.optimize.interface import (
    PlanContext,
    Planner,
    PlanRequest,
    PlanResult,
)
from epure_arena.optimize.registry import PlannerRegistry, default_registry
from epure_arena.optimize.static import StaticPlanner

for _cls in (StaticPlanner,):
    _name = _cls.__name__.lower().replace("planner", "")
    if _name not in default_registry:
        default_registry.register(_cls)
if "capacity_aware" not in default_registry:
    default_registry.register_as("capacity_aware", CapacityAwarePlanner)

__all__ = [
    "CapacityAwarePlanner",
    "PlanContext",
    "PlanRequest",
    "PlanResult",
    "Planner",
    "PlannerRegistry",
    "StaticPlanner",
    "default_registry",
]
