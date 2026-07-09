"""The neutrality invariant, enforced as a test (not a review habit).

epure-arena models constrained dynamic systems generically: flow units move
through a capacitated network of nodes, lanes, and connections. No named
application domain receives special status — in code identifiers, wire
columns, scenario names, test names, or docs.

Any banned term appearing anywhere in the repository (outside this file and
the explicitly allowed mapping table in the protocol doc) fails CI.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

BANNED = [
    "parcel", "shipment", "freight", "logistics",
    "depot", "warehouse", "courier", "consignment",
    # 'hub' is banned as a word; \bhub\b avoids false hits inside e.g.
    # 'github' — repository URLs remain expressible.
    r"\bhubs?\b",
]

ALLOWED_FILES = {
    "tests/test_no_domain_vocabulary.py",
}

SCAN_SUFFIXES = {".py", ".rs", ".toml", ".md", ".yml", ".yaml", ".json"}


def repo_root() -> pathlib.Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return pathlib.Path(out.stdout.strip())


def tracked_files(root: pathlib.Path) -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files"], cwd=root,
                         capture_output=True, text=True, check=True)
    return [root / line for line in out.stdout.splitlines()
            if pathlib.Path(line).suffix in SCAN_SUFFIXES
            and line not in ALLOWED_FILES]


def test_no_banned_vocabulary_anywhere():
    root = repo_root()
    pattern = re.compile("|".join(BANNED), re.IGNORECASE)
    violations: list[str] = []
    for path in tracked_files(root):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                violations.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
    assert not violations, (
        "banned domain vocabulary found:\n" + "\n".join(violations[:40])
        + (f"\n... and {len(violations) - 40} more" if len(violations) > 40 else "")
    )
