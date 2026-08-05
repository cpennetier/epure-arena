#!/usr/bin/env python
"""Deterministic aggregation and validation of sharded gate-experiment output.

``repro/gate_experiment.py`` emits one JSONL row per experimental cell. Run
whole, it emits the complete record in a single stream; run as shards (one
process per world x scenario seed) it emits disjoint slices of that same
record. This module puts the slices back together and proves the result is
the record the sequential run would have produced:

  * every line parses, and every row carries a well-typed cell key;
  * no cell is claimed twice (a duplicated or double-uploaded shard);
  * no declared cell is absent (a dropped shard or a short-circuited run);
  * no undeclared cell is present (a shard run with the wrong arguments);
  * rows are ordered by the experiment's own nested-loop order, so the
    merged file does not depend on the order shards happen to arrive in.

The declared grid is NOT restated here. It is derived from
``gate_experiment.build_parser()`` defaults and the ``KINDS`` / ``SEVERITIES``
constants, so the aggregator cannot drift away from the experiment it
aggregates.

Main exports:
    Grid, MergedRecord, MergeError, build_grid, merge_shards, canonical_bytes

Complexity: time O(n log n) in the number of rows, dominated by the final
deterministic sort; space O(n) — the rows are held once, with their raw
source lines, to keep the output byte-identical to the shard output.

Usage (repo root):
    python repro/merge_shards.py partA --shard-dir shards/ \
        --out runs/gate_partA.jsonl
    python repro/merge_shards.py partB --shard-dir shards/ \
        --out runs/gate_partB.jsonl
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import itertools
import json
import pathlib
import sys
from collections.abc import Sequence

# repro/ is a directory of scripts, not a package; make the sibling
# experiment module importable no matter how this file is loaded.
_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import gate_experiment as _gate  # noqa: E402  (needs the path shim above)

#: Cell-key fields per part, in the experiment's own nesting order.
KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "partA": ("world", "scenario_seed", "kind", "severity", "disruption_seed"),
    "partB": ("world", "scenario_seed", "severity", "disruption_seed"),
}

#: Declared python type of each key field.
_FIELD_TYPE: dict[str, type] = {
    "world": str,
    "scenario_seed": int,
    "kind": str,
    "severity": str,
    "disruption_seed": int,
}

CellKey = tuple[object, ...]


class MergeError(Exception):
    """A shard set that cannot be merged into the declared record."""


@dataclasses.dataclass(frozen=True)
class Grid:
    """The declared set of experimental cells for one part.

    Attributes:
        part: Either ``"partA"`` or ``"partB"``.
        key_fields: The cell-key field names, in nesting order.
        keys: Every declared cell key, in the experiment's emission order.
        index: Cell key -> its position in ``keys``; the canonical sort key.
    """

    part: str
    key_fields: tuple[str, ...]
    keys: tuple[CellKey, ...]
    index: dict[CellKey, int]

    def __len__(self) -> int:
        return len(self.keys)


@dataclasses.dataclass(frozen=True)
class SourcedRow:
    """One parsed JSONL row together with where it came from.

    Attributes:
        key: The row's cell key.
        raw: The row's original source line, verbatim and newline-free.
        path: The shard file the row was read from.
        lineno: 1-based line number within that file.
    """

    key: CellKey
    raw: str
    path: pathlib.Path
    lineno: int


@dataclasses.dataclass(frozen=True)
class MergedRecord:
    """The result of a successful merge.

    Attributes:
        part: Either ``"partA"`` or ``"partB"``.
        rows: The merged rows, in canonical grid order.
        shard_paths: The shard files consumed, in the order they were read.
        digest: SHA-256 of the canonical output bytes.
    """

    part: str
    rows: tuple[SourcedRow, ...]
    shard_paths: tuple[pathlib.Path, ...]
    digest: str

    @property
    def manifest(self) -> dict[str, object]:
        """A compact, printable summary of what was merged."""
        return {
            "part": self.part,
            "shards": len(self.shard_paths),
            "rows": len(self.rows),
            "unique_keys": len({r.key for r in self.rows}),
            "digest_sha256": self.digest,
        }


def _split(csv: str) -> list[str]:
    """Split a comma-separated argument value, dropping empty fields."""
    return [part.strip() for part in csv.split(",") if part.strip()]


def build_grid(
    part: str,
    *,
    worlds: str | None = None,
    sseeds: str | None = None,
    dseeds: str | None = None,
    severities: str | None = None,
) -> Grid:
    """Build the declared cell grid for ``part``.

    Every axis defaults to the committed setting declared by
    ``gate_experiment.build_parser()``. Overrides exist so that reduced
    fixture runs (and this module's own tests) can describe the smaller grid
    they actually ran; the published reproduction passes no overrides.

    Args:
        part: ``"partA"`` or ``"partB"``.
        worlds: Comma-separated world names; defaults to the declared set.
        sseeds: Comma-separated scenario seeds; defaults to the declared set.
        dseeds: Comma-separated disruption seeds; defaults to the declared set.
        severities: Comma-separated severities. Part B only — Part A always
            sweeps the full ``gate_experiment.SEVERITIES`` constant, because
            that is what ``part_a`` iterates.

    Returns:
        The Grid for that part.

    Raises:
        MergeError: If ``part`` is unknown or an axis names something the
            experiment does not declare.
    """
    if part not in KEY_FIELDS:
        raise MergeError(f"unknown part {part!r}: expected partA or partB")

    defaults = _gate.build_parser().parse_args([part])
    world_list = _split(worlds if worlds is not None else defaults.worlds)
    sseed_list = [int(x) for x in
                  _split(sseeds if sseeds is not None else defaults.sseeds)]
    dseed_list = [int(x) for x in
                  _split(dseeds if dseeds is not None else defaults.dseeds)]

    unknown = [w for w in world_list if w not in _gate.WORLDS]
    if unknown:
        raise MergeError(
            f"undeclared world(s) {unknown}: the experiment declares "
            f"{sorted(_gate.WORLDS)}")

    if part == "partA":
        # part_a ignores --severities and sweeps the constant.
        sev_list = list(_gate.SEVERITIES)
    else:
        sev_list = _split(
            severities if severities is not None else defaults.severities)
    bad_sev = [s for s in sev_list if s not in _gate.SEVERITIES]
    if bad_sev:
        raise MergeError(
            f"undeclared severity/severities {bad_sev}: the experiment "
            f"declares {list(_gate.SEVERITIES)}")

    # The iteration order below mirrors the nested loops in part_a / part_b
    # exactly; it is what makes the merged file identical to a whole run.
    if part == "partA":
        product = itertools.product(
            world_list, sseed_list, _gate.KINDS, sev_list, dseed_list)
    else:
        product = itertools.product(
            world_list, sseed_list, sev_list, dseed_list)

    keys = tuple(tuple(cell) for cell in product)
    return Grid(
        part=part,
        key_fields=KEY_FIELDS[part],
        keys=keys,
        index={key: i for i, key in enumerate(keys)},
    )


def _key_of(row: object, grid: Grid, path: pathlib.Path, lineno: int) -> CellKey:
    """Extract and type-check one row's cell key.

    Raises:
        MergeError: If the row is not an object, lacks a key field, or a key
            field has the wrong type.
    """
    where = f"{path}:{lineno}"
    if not isinstance(row, dict):
        raise MergeError(
            f"{where}: expected a JSON object per line, got "
            f"{type(row).__name__}")
    key: list[object] = []
    for field in grid.key_fields:
        if field not in row:
            raise MergeError(
                f"{where}: row is missing the key field {field!r} "
                f"(required for {grid.part}: {list(grid.key_fields)})")
        value = row[field]
        want = _FIELD_TYPE[field]
        # bool is an int subclass; a boolean seed is a defect, not a seed.
        if isinstance(value, bool) or not isinstance(value, want):
            raise MergeError(
                f"{where}: key field {field!r} must be {want.__name__}, got "
                f"{type(value).__name__} ({value!r})")
        key.append(value)
    return tuple(key)


def read_shard(path: pathlib.Path, grid: Grid) -> list[SourcedRow]:
    """Parse one shard file into keyed rows.

    Blank lines are skipped (a trailing newline is not a defect). Every other
    line must be a well-formed JSON object carrying a valid cell key.

    Args:
        path: The shard file to read.
        grid: The grid whose key fields the rows must carry.

    Returns:
        The shard's rows, in file order.

    Raises:
        MergeError: On unreadable files, malformed JSON, or a bad cell key.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise MergeError(f"{path}: cannot read shard: {exc}") from exc

    rows: list[SourcedRow] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MergeError(
                f"{path}:{lineno}: malformed JSON: {exc.msg} "
                f"(at column {exc.colno})") from exc
        rows.append(SourcedRow(
            key=_key_of(parsed, grid, path, lineno),
            raw=line, path=path, lineno=lineno))
    return rows


def canonical_bytes(rows: Sequence[SourcedRow]) -> bytes:
    """Render merged rows as the canonical JSONL payload."""
    if not rows:
        return b""
    return ("\n".join(row.raw for row in rows) + "\n").encode()


def merge_shards(
    part: str, shard_paths: Sequence[pathlib.Path], grid: Grid | None = None,
) -> MergedRecord:
    """Merge shard files into the declared record, or explain why not.

    Args:
        part: ``"partA"`` or ``"partB"``.
        shard_paths: The shard files to merge. Order does not affect the
            result — only diagnostics name the file a row came from.
        grid: The declared grid; built from the committed defaults if omitted.

    Returns:
        The merged record, ordered by the experiment's own emission order.

    Raises:
        MergeError: On a malformed row, a duplicated cell, an undeclared
            cell, or any declared cell that no shard supplied.
    """
    grid = grid if grid is not None else build_grid(part)
    if grid.part != part:
        raise MergeError(
            f"grid is for {grid.part}, but merging {part}")

    seen: dict[CellKey, SourcedRow] = {}
    for path in shard_paths:
        for row in read_shard(path, grid):
            if row.key not in grid.index:
                raise MergeError(
                    f"{row.path}:{row.lineno}: cell "
                    f"{_render(grid, row.key)} is not in the declared "
                    f"{part} grid ({len(grid)} cells) — the shard was run "
                    f"with arguments outside the committed setting")
            prior = seen.get(row.key)
            if prior is not None:
                raise MergeError(
                    f"duplicate cell {_render(grid, row.key)}: first seen at "
                    f"{prior.path}:{prior.lineno}, again at "
                    f"{row.path}:{row.lineno}")
            seen[row.key] = row

    missing = [key for key in grid.keys if key not in seen]
    if missing:
        shown = ", ".join(_render(grid, key) for key in missing[:20])
        tail = (f" ... and {len(missing) - 20} more"
                if len(missing) > 20 else "")
        raise MergeError(
            f"{len(missing)} of {len(grid)} declared {part} cell(s) are "
            f"missing from the merged record — a shard was dropped or ended "
            f"early: {shown}{tail}")

    ordered = tuple(sorted(seen.values(), key=lambda r: grid.index[r.key]))
    digest = hashlib.sha256(canonical_bytes(ordered)).hexdigest()
    return MergedRecord(
        part=part, rows=ordered, shard_paths=tuple(shard_paths),
        digest=digest)


def _render(grid: Grid, key: CellKey) -> str:
    """Render a cell key as ``field=value`` pairs, for diagnostics."""
    return "(" + ", ".join(
        f"{field}={value!r}"
        for field, value in zip(grid.key_fields, key)) + ")"


def shard_matrix(grid: Grid) -> list[dict[str, object]]:
    """The shard axis: one entry per (world, scenario seed) in ``grid``.

    The sharded reproduction runs one process per entry, passing only
    ``--worlds`` and ``--sseeds`` so every other axis keeps its committed
    default. Deriving the axis here — rather than restating it in the
    workflow — is what keeps the job matrix and the experiment in step.

    Args:
        grid: The declared grid to shard.

    Returns:
        Shard descriptors in canonical order, each with ``world`` and
        ``sseed`` keys.
    """
    seen: dict[tuple[object, object], dict[str, object]] = {}
    for key in grid.keys:
        world, sseed = key[0], key[1]
        seen.setdefault((world, sseed), {"world": world, "sseed": sseed})
    return list(seen.values())


def discover_shards(
    shard_dir: pathlib.Path, pattern: str,
) -> list[pathlib.Path]:
    """List shard files under ``shard_dir``, sorted for stable diagnostics.

    The sort is for reproducible error messages only — the merged output is
    order-independent by construction.

    Raises:
        MergeError: If the directory is absent or matches nothing.
    """
    if not shard_dir.is_dir():
        raise MergeError(f"{shard_dir}: shard directory does not exist")
    found = sorted(p for p in shard_dir.rglob(pattern) if p.is_file())
    if not found:
        raise MergeError(
            f"{shard_dir}: no shard files matching {pattern!r} — every shard "
            f"upload is missing")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit status."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("part", choices=("partA", "partB"))
    ap.add_argument("--shard-dir", type=pathlib.Path,
                    help="directory searched recursively for shard files")
    ap.add_argument("--pattern", default="*.jsonl",
                    help="shard filename glob (default: %(default)s)")
    ap.add_argument("shards", nargs="*", type=pathlib.Path,
                    help="explicit shard files (alternative to --shard-dir)")
    ap.add_argument("--out", type=pathlib.Path,
                    help="path of the merged canonical JSONL record "
                         "(required unless --print-shard-matrix)")
    ap.add_argument("--print-shard-matrix", action="store_true",
                    help="print the (world, scenario seed) shard axis as "
                         "JSON and exit; used to build the job matrix")
    # Grid overrides mirror gate_experiment's own flags; the published
    # reproduction passes none of them and validates the full committed grid.
    ap.add_argument("--worlds")
    ap.add_argument("--sseeds")
    ap.add_argument("--dseeds")
    ap.add_argument("--severities")
    args = ap.parse_args(argv)

    try:
        grid = build_grid(
            args.part, worlds=args.worlds, sseeds=args.sseeds,
            dseeds=args.dseeds, severities=args.severities)
        if args.print_shard_matrix:
            print(json.dumps(shard_matrix(grid)))
            return 0
        if args.out is None:
            raise MergeError("--out is required when merging")
        paths = list(args.shards)
        if args.shard_dir is not None:
            paths += discover_shards(args.shard_dir, args.pattern)
        if not paths:
            raise MergeError(
                "no shard files given: pass --shard-dir or explicit paths")
        record = merge_shards(args.part, paths, grid)
    except MergeError as exc:
        print(f"merge-shards: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_bytes(record.rows))
    manifest = {**record.manifest, "declared_cells": len(grid),
                "out": str(args.out)}
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
