"""The shard-aggregation invariant, enforced as a test.

The full reproduction runs ``repro/gate_experiment.py`` as disjoint shards and
reassembles them. That reassembly is only trustworthy if it is *provably* the
record a whole sequential run would have produced — so this suite pins:

  * the declared grid still has the published scope (a scope reduction, in
    either part, fails here before it can reach a table);
  * merging is byte-identical to the sequential emission order, and is
    independent of the order shards arrive in;
  * every corruption mode a sharded run can actually suffer — a duplicated
    upload, a dropped shard, a truncated shard, a malformed line, a shard run
    with the wrong arguments — is rejected with a diagnostic that names the
    exact defect rather than silently producing a short table.
"""

from __future__ import annotations

import json
import pathlib
import random
import sys

import pytest

# merge_shards is a repro-tier utility (repro/ is not a package).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "repro"))

import gate_experiment  # noqa: E402
import merge_shards  # noqa: E402
from merge_shards import (  # noqa: E402
    MergeError,
    build_grid,
    canonical_bytes,
    merge_shards as merge,
)

# A reduced grid: two scenario seeds so there is more than one shard, but
# small enough to enumerate by hand. The axis names are the declared ones.
REDUCED = {"worlds": "backbone", "sseeds": "42,43", "dseeds": "1,2"}


def rows_for(grid) -> list[dict]:
    """Synthesize one payload row per declared cell, in canonical order."""
    return [
        {**dict(zip(grid.key_fields, key)), "payload": i}
        for i, key in enumerate(grid.keys)
    ]


def shard_files(tmp_path: pathlib.Path, grid, rows: list[dict],
                ) -> list[pathlib.Path]:
    """Split rows into one file per (world, scenario seed), as the run does."""
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        buckets.setdefault((row["world"], row["scenario_seed"]), []).append(row)
    paths = []
    for (world, sseed), bucket in buckets.items():
        path = tmp_path / f"{grid.part}-{world}-{sseed}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in bucket))
        paths.append(path)
    return paths


@pytest.fixture()
def partA(tmp_path):
    grid = build_grid("partA", **REDUCED)
    rows = rows_for(grid)
    return grid, rows, shard_files(tmp_path, grid, rows)


# ── the published scope ──────────────────────────────────────────────────


def test_declared_partA_grid_is_the_published_scope():
    """2 worlds x 3 scenario seeds x 4 kinds x 3 severities x 15 seeds."""
    grid = build_grid("partA")
    assert len(grid) == 2 * 3 * 4 * 3 * 15 == 1080
    worlds = {k[0] for k in grid.keys}
    assert worlds == {"backbone", "mesh"}
    assert {k[1] for k in grid.keys} == {42, 43, 44}
    assert {k[2] for k in grid.keys} == set(gate_experiment.KINDS)
    assert {k[3] for k in grid.keys} == set(gate_experiment.SEVERITIES)
    assert {k[4] for k in grid.keys} == set(range(1, 16))


def test_declared_partB_grid_is_the_published_scope():
    """2 worlds x 3 scenario seeds x 2 severities x 15 disruption seeds."""
    grid = build_grid("partB")
    assert len(grid) == 2 * 3 * 2 * 15 == 180
    assert {k[0] for k in grid.keys} == {"backbone", "mesh"}
    assert {k[1] for k in grid.keys} == {42, 43, 44}
    assert {k[2] for k in grid.keys} == {"moderate", "severe"}
    assert {k[3] for k in grid.keys} == set(range(1, 16))


def test_grid_order_matches_the_experiment_nesting():
    """Canonical order is the emission order, not lexicographic order.

    The kinds are declared lane_outage, node_outage, capacity_shock,
    demand_surge — sorting them as strings would reorder the record.
    """
    grid = build_grid("partA", worlds="backbone", sseeds="42", dseeds="1")
    assert [k[2] for k in grid.keys][::3] == list(gate_experiment.KINDS)
    assert [k[3] for k in grid.keys][:3] == list(gate_experiment.SEVERITIES)


def test_undeclared_axis_values_are_rejected():
    with pytest.raises(MergeError, match="undeclared world"):
        build_grid("partA", worlds="atlantis")
    with pytest.raises(MergeError, match="undeclared severity"):
        build_grid("partB", severities="apocalyptic")
    with pytest.raises(MergeError, match="unknown part"):
        build_grid("partC")


# ── the happy path ───────────────────────────────────────────────────────


def test_merge_reproduces_the_sequential_record(partA):
    grid, rows, paths = partA
    record = merge("partA", paths, grid)
    assert len(record.rows) == len(grid) == 48
    sequential = "".join(json.dumps(r) + "\n" for r in rows).encode()
    assert canonical_bytes(record.rows) == sequential


def test_merge_manifest_reports_the_shape(partA):
    grid, rows, paths = partA
    record = merge("partA", paths, grid)
    manifest = record.manifest
    assert manifest["part"] == "partA"
    assert manifest["shards"] == 2
    assert manifest["rows"] == 48
    assert manifest["unique_keys"] == 48
    assert len(manifest["digest_sha256"]) == 64


def test_merge_is_independent_of_shard_arrival_order(partA):
    """Artifacts download in arbitrary order; the record must not care."""
    grid, _rows, paths = partA
    reference = merge("partA", paths, grid)
    rng = random.Random(0)
    for _ in range(8):
        shuffled = list(paths)
        rng.shuffle(shuffled)
        other = merge("partA", shuffled, grid)
        assert other.digest == reference.digest
        assert canonical_bytes(other.rows) == canonical_bytes(reference.rows)


def test_row_order_inside_a_shard_does_not_matter(tmp_path):
    grid = build_grid("partA", **REDUCED)
    rows = rows_for(grid)
    forward = shard_files(tmp_path, grid, rows)
    reference = merge("partA", forward, grid)

    reversed_dir = tmp_path / "reversed"
    reversed_dir.mkdir()
    backward = shard_files(reversed_dir, grid, list(reversed(rows)))
    assert merge("partA", backward, grid).digest == reference.digest


def test_partB_merges_on_its_own_key(tmp_path):
    grid = build_grid("partB", **REDUCED)
    rows = rows_for(grid)
    record = merge("partB", shard_files(tmp_path, grid, rows), grid)
    assert len(record.rows) == len(grid) == 1 * 2 * 2 * 2


# ── the corruption modes ─────────────────────────────────────────────────


def test_duplicate_cell_is_rejected(partA, tmp_path):
    grid, rows, paths = partA
    dupe = tmp_path / "dupe.jsonl"
    dupe.write_text(json.dumps(rows[7]) + "\n")
    with pytest.raises(MergeError) as exc:
        merge("partA", [*paths, dupe], grid)
    message = str(exc.value)
    assert "duplicate cell" in message
    assert f"kind={rows[7]['kind']!r}" in message
    assert "dupe.jsonl:1" in message


def test_a_shard_uploaded_twice_is_rejected(partA):
    grid, _rows, paths = partA
    with pytest.raises(MergeError, match="duplicate cell"):
        merge("partA", [*paths, paths[0]], grid)


def test_omitted_shard_is_rejected(partA):
    grid, _rows, paths = partA
    with pytest.raises(MergeError) as exc:
        merge("partA", paths[:1], grid)
    message = str(exc.value)
    assert "missing from the merged record" in message
    assert "24 of 48" in message
    assert "scenario_seed=43" in message


def test_single_missing_cell_is_rejected(partA, tmp_path):
    """A shard that ended early loses cells, not whole files."""
    grid, rows, paths = partA
    truncated = tmp_path / "short.jsonl"
    keep = [r for r in rows if (r["world"], r["scenario_seed"]) == ("backbone", 42)]
    dropped = keep.pop(5)
    truncated.write_text("".join(json.dumps(r) + "\n" for r in keep))
    with pytest.raises(MergeError) as exc:
        merge("partA", [truncated, paths[1]], grid)
    message = str(exc.value)
    assert "1 of 48" in message
    assert f"kind={dropped['kind']!r}" in message
    assert f"severity={dropped['severity']!r}" in message


def test_malformed_row_is_rejected_with_its_location(partA, tmp_path):
    grid, rows, _paths = partA
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(rows[0]) + "\n"
        + '{"world": "backbone", "scenario_seed": 42,\n')
    with pytest.raises(MergeError) as exc:
        merge("partA", [bad], grid)
    message = str(exc.value)
    assert "bad.jsonl:2" in message
    assert "malformed JSON" in message


def test_non_object_row_is_rejected(tmp_path):
    grid = build_grid("partA", **REDUCED)
    bad = tmp_path / "list.jsonl"
    bad.write_text("[1, 2, 3]\n")
    with pytest.raises(MergeError, match="expected a JSON object"):
        merge("partA", [bad], grid)


def test_missing_key_field_is_rejected(tmp_path):
    grid = build_grid("partA", **REDUCED)
    bad = tmp_path / "nokey.jsonl"
    row = dict(zip(grid.key_fields, grid.keys[0]))
    row.pop("kind")
    bad.write_text(json.dumps(row) + "\n")
    with pytest.raises(MergeError, match="missing the key field 'kind'"):
        merge("partA", [bad], grid)


def test_wrongly_typed_key_field_is_rejected(tmp_path):
    grid = build_grid("partA", **REDUCED)
    bad = tmp_path / "badtype.jsonl"
    row = dict(zip(grid.key_fields, grid.keys[0]))
    row["scenario_seed"] = "42"
    bad.write_text(json.dumps(row) + "\n")
    with pytest.raises(MergeError, match="must be int, got str"):
        merge("partA", [bad], grid)


def test_cell_outside_the_declared_grid_is_rejected(tmp_path):
    """A shard run with the wrong arguments must not enlarge the record."""
    grid = build_grid("partA", **REDUCED)
    stray = tmp_path / "stray.jsonl"
    row = dict(zip(grid.key_fields, grid.keys[0]))
    row["scenario_seed"] = 44  # declared by the experiment, not by this grid
    stray.write_text(json.dumps(row) + "\n")
    with pytest.raises(MergeError) as exc:
        merge("partA", [stray], grid)
    assert "not in the declared partA grid" in str(exc.value)


def test_blank_lines_are_not_a_defect(partA, tmp_path):
    grid, rows, _paths = partA
    padded = tmp_path / "padded.jsonl"
    padded.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n\n")
    record = merge("partA", [padded], grid)
    assert len(record.rows) == 48


def test_missing_shard_directory_is_rejected(tmp_path):
    with pytest.raises(MergeError, match="does not exist"):
        merge_shards.discover_shards(tmp_path / "absent", "*.jsonl")


def test_empty_shard_directory_is_rejected(tmp_path):
    with pytest.raises(MergeError, match="no shard files matching"):
        merge_shards.discover_shards(tmp_path, "*.jsonl")


# ── the CLI ──────────────────────────────────────────────────────────────


def test_cli_writes_the_record_and_prints_a_manifest(partA, tmp_path, capsys):
    grid, rows, paths = partA
    out = tmp_path / "merged" / "gate_partA.jsonl"
    status = merge_shards.main([
        "partA", "--shard-dir", str(paths[0].parent), "--out", str(out),
        "--worlds", REDUCED["worlds"], "--sseeds", REDUCED["sseeds"],
        "--dseeds", REDUCED["dseeds"],
    ])
    assert status == 0
    assert out.read_bytes() == "".join(
        json.dumps(r) + "\n" for r in rows).encode()
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["rows"] == manifest["unique_keys"] == 48
    assert manifest["declared_cells"] == 48
    assert manifest["shards"] == 2


def test_cli_reports_a_defect_and_exits_nonzero(partA, tmp_path, capsys):
    grid, _rows, paths = partA
    out = tmp_path / "gate_partA.jsonl"
    status = merge_shards.main([
        "partA", str(paths[0]), "--out", str(out),
        "--worlds", REDUCED["worlds"], "--sseeds", REDUCED["sseeds"],
        "--dseeds", REDUCED["dseeds"],
    ])
    assert status == 1
    assert "missing from the merged record" in capsys.readouterr().err
    assert not out.exists()
