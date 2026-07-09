"""The dependency-direction invariant, enforced as a test (not a comment).

Core never imports an adapter: every import reachable from ``epure_arena``
must resolve to the package itself, the standard library, or the declared
dependency list. Anything else — and in particular any downstream
application package — fails CI.
"""

from __future__ import annotations

import ast
import pathlib
import sys

DECLARED = {
    # runtime dependencies (pyproject [project.dependencies])
    "numpy", "pyarrow", "h3",
    # optional extras
    "pulp", "scipy", "sklearn",
    # the compiled engine lives in the ephemeris-kernel dependency and is
    # re-exported as epure_arena._engine
    "ephemeris",
    "epure_arena",
}


def stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names


def test_core_never_imports_an_adapter():
    pkg = pathlib.Path(__file__).resolve().parents[1] / "python" / "epure_arena"
    violations = []
    for path in pkg.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module] if node.module else []
            for name in names:
                top = name.split(".")[0]
                if not (stdlib(top) or top in DECLARED):
                    violations.append(f"{path.relative_to(pkg)}: {name}")
    assert not violations, (
        "core imports outside the declared dependency set:\n"
        + "\n".join(violations)
    )
