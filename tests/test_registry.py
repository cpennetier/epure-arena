"""Tests for PlannerRegistry plugin architecture.

Covers: register, register_as, get, available, __contains__, missing key,
and the downstream-extension seam (an application package registers its own
planner classes into a registry without core knowing about them).
"""

from __future__ import annotations

import pytest

from epure_arena.optimize.capacity_aware import CapacityAwarePlanner
from epure_arena.optimize.registry import PlannerRegistry
from epure_arena.optimize.static import StaticPlanner


class DownstreamPlanner(StaticPlanner):
    """Stand-in for an application-defined planner registered from outside."""


class TestRegistryRegister:
    """Registry.register() auto-derives name from class."""

    def test_register_static(self) -> None:
        reg = PlannerRegistry()
        reg.register(StaticPlanner)
        assert "static" in reg

    def test_register_downstream(self) -> None:
        reg = PlannerRegistry()
        reg.register(DownstreamPlanner)
        assert "downstream" in reg


class TestRegistryRegisterAs:
    """Registry.register_as() uses explicit name."""

    def test_custom_name(self) -> None:
        reg = PlannerRegistry()
        reg.register_as("dijkstra", StaticPlanner)
        assert "dijkstra" in reg
        assert reg.get("dijkstra") is StaticPlanner

    def test_override_name(self) -> None:
        reg = PlannerRegistry()
        reg.register_as("capacity_aware", CapacityAwarePlanner)
        cls = reg.get("capacity_aware")
        assert cls is CapacityAwarePlanner


class TestRegistryGet:
    """Registry.get() retrieves planner class by name."""

    def test_get_registered(self) -> None:
        reg = PlannerRegistry()
        reg.register(StaticPlanner)
        cls = reg.get("static")
        assert cls is StaticPlanner

    def test_get_missing_raises(self) -> None:
        reg = PlannerRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("nonexistent")


class TestRegistryAvailable:
    """Registry.available() lists all registered names."""

    def test_empty_registry(self) -> None:
        reg = PlannerRegistry()
        assert reg.available() == []

    def test_multiple_registered(self) -> None:
        reg = PlannerRegistry()
        reg.register(StaticPlanner)
        reg.register(DownstreamPlanner)
        reg.register_as("capacity_aware", CapacityAwarePlanner)
        names = reg.available()
        assert "static" in names
        assert "downstream" in names
        assert "capacity_aware" in names
        assert names == sorted(names)  # Sorted alphabetically


class TestRegistryContains:
    """Registry.__contains__() for 'in' operator."""

    def test_contains_registered(self) -> None:
        reg = PlannerRegistry()
        reg.register(StaticPlanner)
        assert "static" in reg

    def test_not_contains_unregistered(self) -> None:
        reg = PlannerRegistry()
        assert "nonexistent" not in reg


class TestDefaultRegistry:
    """The package-level default registry ships the core planners."""

    def test_core_planners_present(self) -> None:
        from epure_arena import default_registry

        assert "static" in default_registry
        assert "capacity_aware" in default_registry
