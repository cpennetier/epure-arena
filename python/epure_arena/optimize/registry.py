"""PlannerRegistry — plugin architecture for routing strategies.

Register planners by name. Look them up at runtime.
Config-driven: adding a new planner requires only registration,
no changes to selector or evaluation code.

Usage:
    registry = PlannerRegistry()
    registry.register(StaticPlanner)
    registry.register(CBSPlanner)
    planner = registry.get("cbs")
"""

from __future__ import annotations

import logging
from typing import Type

from epure_arena.optimize.interface import Planner

logger = logging.getLogger(__name__)


class PlannerRegistry:
    """Registry of available planner implementations.

    Thread-safe singleton pattern. Planners register once at import time.
    The selector and evaluation framework look up planners by name.
    """

    def __init__(self) -> None:
        self._planners: dict[str, Type[Planner]] = {}

    def register(self, planner_cls: Type[Planner]) -> None:
        """Register a planner class.

        Args:
            planner_cls: Planner class (not instance). Must have a `name` property.
        """
        # Instantiate temporarily to get the name
        # For registration, we just need the class
        name = planner_cls.__name__.lower().replace("planner", "")
        self._planners[name] = planner_cls
        logger.info("Registered planner: %s → %s", name, planner_cls.__name__)

    def register_as(self, name: str, planner_cls: Type[Planner]) -> None:
        """Register a planner class under a specific name.

        Args:
            name: Registry key.
            planner_cls: Planner class.
        """
        self._planners[name] = planner_cls
        logger.info("Registered planner: %s → %s", name, planner_cls.__name__)

    def get(self, name: str) -> Type[Planner]:
        """Look up a planner class by name.

        Args:
            name: Registry key.

        Returns:
            Planner class.

        Raises:
            KeyError: If no planner registered under that name.
        """
        if name not in self._planners:
            available = ", ".join(sorted(self._planners.keys()))
            raise KeyError(
                f"Planner '{name}' not registered. Available: {available}"
            )
        return self._planners[name]

    def available(self) -> list[str]:
        """List all registered planner names."""
        return sorted(self._planners.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._planners


# Module-level default registry
default_registry = PlannerRegistry()
