"""The self-containment invariant, enforced as a test (not a review habit).

epure-arena is a public, self-contained artifact. It must not point into the
private monorepo it was extracted from, nor cite that repo's internal process
taxonomy — such references are dangling for a public reader and leak internal
structure. This guard is the internal-reference complement to the domain-
vocabulary guard: the latter polices *what* the code talks about, this one
polices *what it points at*.

Any banned pattern appearing in a tracked, scanned file (outside this file)
fails CI — the same way domain vocabulary does. No exemptions.

Two families:
  * private-monorepo paths — no pointer into the private tree may ship.
  * internal-process taxonomy — keep the technical CONTENT, drop the CITATION
    (an ADR / sprint / tier-codename a public reader cannot resolve).
"""

from __future__ import annotations

import pathlib
import re
import subprocess

BANNED = [
    # ── private-monorepo paths ──────────────────────────────────────────
    r"docs/engineering",       # the private engineering doc tree
    r"\bapps/",                # private application packages (e.g. the UI)
    r"/Users/",                # absolute developer paths
    r"/home/",
    r"C:\\",
    # ── internal-process taxonomy (dangling citations) ──────────────────
    r"ADR-\d",                 # architecture decision records
    r"Sprint \d",              # sprint numbering
    r"Tier[- ]?β",        # "Tier β" / "Tier-β"
    r"Tier[- ]?beta",
    r"Change β",          # "Change β"
    r"Change beta",
    r"Tier-?1\.5",             # internal tier-1.5 codename
]

ALLOWED_FILES = {
    "tests/test_no_internal_references.py",
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


def test_no_internal_references_anywhere():
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
        "internal / private-monorepo references found:\n"
        + "\n".join(violations[:60])
        + (f"\n... and {len(violations) - 60} more" if len(violations) > 60 else "")
    )
