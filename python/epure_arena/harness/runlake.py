"""Run-folder emission for experiment artifacts ("every run is paper data").

Each experiment run emits one folder under a results root (default ``runs/``,
gitignored) following the run_manifest pattern (services/des-engine/src/
run_manifest.rs): config + seed + schema_fingerprint + config hash, metrics as
parquet AND as a paper-ready markdown table, and the exact repro command.

Main exports: :func:`emit_run`, :func:`config_hash`, :func:`markdown_table`.

Usage:
    from epure_arena.harness.runlake import emit_run
    folder = emit_run(
        name="strand-matrix",
        config={"topology": "backbone", "seed": 42},
        seed=42,
        schema_fingerprint=engine.get_schema_fingerprint(),
        metrics_rows=[{"cell": "B", "stranded": 181638}],
        repro_cmd="python tools/analysis/strand_matrix.py matrix",
    )
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

_RESULTS_ROOT = Path("runs")


def config_hash(config: dict[str, Any]) -> str:
    """SHA-256 over the canonical-JSON config (sorted keys, no whitespace)."""
    canon = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def markdown_table(rows: list[dict[str, Any]]) -> str:
    """Render rows (uniform-key dicts) as a GitHub-markdown table."""
    if not rows:
        return "(no rows)\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out) + "\n"


def emit_run(
    name: str,
    config: dict[str, Any],
    *,
    seed: int,
    schema_fingerprint: str,
    metrics_rows: list[dict[str, Any]],
    repro_cmd: str,
    root: Path | str = _RESULTS_ROOT,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write one run_id folder and return its path.

    The run_id is ``<name>-<UTC stamp>-<config_hash[:8]>`` — unique per launch,
    reproducible in content from (config, seed) via the recorded repro command.

    Args:
        name: experiment name (kebab-case).
        config: the full experiment configuration (JSON-serializable).
        seed: the RNG seed the run used.
        schema_fingerprint: DES Arrow schema fingerprint of the engine build.
        metrics_rows: list of uniform-key dicts; written as ``metrics.parquet``
            AND ``table.md`` (the paper-ready view).
        repro_cmd: the exact command that reproduces this run.
        root: results root (default ``runs/``, gitignored).
        extra_files: optional ``{filename: text}`` additions (e.g. per-flow_unit
            attribution CSVs, plots manifests).

    Returns:
        Path of the created run folder.
    """
    chash = config_hash(config)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_id = f"{name}-{stamp}-{chash[:8]}"
    folder = Path(root) / run_id
    folder.mkdir(parents=True, exist_ok=False)

    manifest = {
        "run_id": run_id,
        "name": name,
        "created_utc": stamp,
        "seed": seed,
        "schema_fingerprint": schema_fingerprint,
        "config_hash": chash,
        "repro_cmd": repro_cmd,
        "config": config,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (folder / "table.md").write_text(markdown_table(metrics_rows))

    import pyarrow as pa
    import pyarrow.parquet as pq

    if metrics_rows:
        cols = {k: [r.get(k) for r in metrics_rows] for k in metrics_rows[0]}
        # Stringify nested values (dicts/lists) so the parquet schema stays flat.
        for k, vals in cols.items():
            if any(isinstance(v, (dict, list)) for v in vals):
                cols[k] = [json.dumps(v, sort_keys=True) for v in vals]
        pq.write_table(pa.table(cols), folder / "metrics.parquet")

    for fname, text in (extra_files or {}).items():
        (folder / fname).write_text(text)
    return folder
