"""Arrow IPC serializers for the testkit.

Defines the canonical wire schemas, byte-compatible
with the WebSocket pipeline. The Rust `services/des-engine/src/io.rs`
reader uses a strict subset of these columns; we emit the full schema so
downstream consumers (UI, planner) work without adapters.
"""

from __future__ import annotations

from typing import Any

import h3
import pyarrow as pa

# These are the canonical protocol v1 schemas (see docs/protocol.md).
# Duplicated here so the testkit has zero runtime dependency on serializer
# internals (which deal in ORM-ish NodeRecord objects).

NODES_SCHEMA = pa.schema(
    [
        pa.field("node_id", pa.uint32()),
        pa.field("h3_index", pa.uint64()),
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        pa.field("tier", pa.utf8()),
        pa.field("territory_id", pa.uint32()),
        pa.field("demand_gravity", pa.float64()),
        pa.field("has_airport", pa.bool_()),
        pa.field("has_port", pa.bool_()),
        pa.field("has_rail", pa.bool_()),
        pa.field("landmass_id", pa.int32()),
        # v2 atlas-pinned node→cell rollup columns (protocol v1).
        pa.field("cell_id_r3", pa.uint64()),
        pa.field("cell_centroid_lat", pa.float64()),
        pa.field("cell_centroid_lon", pa.float64()),
    ],
    metadata={b"node_schema_version": b"2"},
)

LANES_SCHEMA = pa.schema([
    pa.field("lane_id", pa.uint32()),
    pa.field("src_node_id", pa.uint32()),
    pa.field("dst_node_id", pa.uint32()),
    pa.field("src_lat", pa.float64()),
    pa.field("src_lon", pa.float64()),
    pa.field("dst_lat", pa.float64()),
    pa.field("dst_lon", pa.float64()),
    pa.field("lane_mode", pa.uint8()),
    pa.field("duration_sec", pa.float32()),
    pa.field("duration_atu", pa.int64()),
    pa.field("cost_usd", pa.float32()),
    pa.field("cost_usd_micros", pa.int64()),
    pa.field("co2_g", pa.int64()),
    pa.field("phase", pa.utf8()),
])

SCHEDULES_SCHEMA = pa.schema([
    pa.field("lane_id", pa.uint32()),
    pa.field("depart_atu", pa.int64()),
    pa.field("capacity", pa.uint32()),
])

DEMAND_SCHEMA = pa.schema([
    pa.field("flow_unit_id", pa.uint64()),
    pa.field("appear_atu", pa.int64()),
    pa.field("due_atu", pa.int64()),
    pa.field("origin_node_id", pa.uint32()),
    pa.field("dest_node_id", pa.uint32()),
    pa.field("origin_cell_idx", pa.uint32()),
    pa.field("dest_cell_idx", pa.uint32()),
    pa.field("size_units", pa.uint32()),
    pa.field("priority_class", pa.uint8()),
    pa.field("handling_flags", pa.uint8()),
])


def _write_batch(batch: pa.RecordBatch) -> bytes:
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, batch.schema)
    writer.write_batch(batch)
    writer.close()
    return sink.getvalue().to_pybytes()


def to_node_ipc(nodes: list[dict[str, Any]]) -> bytes:
    """Serialize node dicts to Arrow IPC stream bytes."""
    if not nodes:
        batch = pa.record_batch(
            [pa.array([], type=t) for t in NODES_SCHEMA.types],
            schema=NODES_SCHEMA,
        )
    else:
        # Atlas-pinned res-3 rollup, derived from each node's coordinate. By H3
        # containment this equals the res-3 parent of the node's own cell, and is
        # robust to synthetic h3_index values. Canonical protocol v1 derivation.
        rollups = [_cell_rollup(h["lat"], h["lon"]) for h in nodes]
        batch = pa.record_batch(
            [
                pa.array([h["node_id"] for h in nodes], type=pa.uint32()),
                pa.array([h["h3_index"] for h in nodes], type=pa.uint64()),
                pa.array([h["lat"] for h in nodes], type=pa.float64()),
                pa.array([h["lon"] for h in nodes], type=pa.float64()),
                pa.array([h["tier"] for h in nodes], type=pa.utf8()),
                pa.array([h["territory_id"] for h in nodes], type=pa.uint32()),
                pa.array([h["demand_gravity"] for h in nodes], type=pa.float64()),
                pa.array([h["has_airport"] for h in nodes], type=pa.bool_()),
                pa.array([h["has_port"] for h in nodes], type=pa.bool_()),
                pa.array([h["has_rail"] for h in nodes], type=pa.bool_()),
                pa.array([h["landmass_id"] for h in nodes], type=pa.int32()),
                pa.array([r[0] for r in rollups], type=pa.uint64()),
                pa.array([r[1] for r in rollups], type=pa.float64()),
                pa.array([r[2] for r in rollups], type=pa.float64()),
            ],
            schema=NODES_SCHEMA,
        )
    return _write_batch(batch)


def _cell_rollup(lat: float, lon: float) -> tuple[int, float, float]:
    """(cell_id_r3_u64, centroid_lat, centroid_lon) for a node coordinate."""
    parent = h3.latlng_to_cell(float(lat), float(lon), 3)
    plat, plon = h3.cell_to_latlng(parent)
    return h3.str_to_int(parent), float(plat), float(plon)


def to_lane_ipc(lanes: list[dict[str, Any]]) -> bytes:
    """Serialize lane dicts to Arrow IPC stream bytes."""
    if not lanes:
        batch = pa.record_batch(
            [pa.array([], type=t) for t in LANES_SCHEMA.types],
            schema=LANES_SCHEMA,
        )
    else:
        batch = pa.record_batch({
            "lane_id": pa.array([e["lane_id"] for e in lanes], type=pa.uint32()),
            "src_node_id": pa.array([e["src_node_id"] for e in lanes], type=pa.uint32()),
            "dst_node_id": pa.array([e["dst_node_id"] for e in lanes], type=pa.uint32()),
            "src_lat": pa.array([e["src_lat"] for e in lanes], type=pa.float64()),
            "src_lon": pa.array([e["src_lon"] for e in lanes], type=pa.float64()),
            "dst_lat": pa.array([e["dst_lat"] for e in lanes], type=pa.float64()),
            "dst_lon": pa.array([e["dst_lon"] for e in lanes], type=pa.float64()),
            "lane_mode": pa.array([e["lane_mode"] for e in lanes], type=pa.uint8()),
            "duration_sec": pa.array([e["duration_sec"] for e in lanes], type=pa.float32()),
            "duration_atu": pa.array([e["duration_atu"] for e in lanes], type=pa.int64()),
            "cost_usd": pa.array([e["cost_usd"] for e in lanes], type=pa.float32()),
            "cost_usd_micros": pa.array([e["cost_usd_micros"] for e in lanes], type=pa.int64()),
            "co2_g": pa.array([e["co2_g"] for e in lanes], type=pa.int64()),
            "phase": pa.array([e["phase"] for e in lanes], type=pa.utf8()),
        })
    return _write_batch(batch)


def to_schedule_ipc(schedules: list[dict[str, Any]]) -> bytes:
    """Serialize schedule slot dicts to Arrow IPC stream bytes."""
    if not schedules:
        batch = pa.record_batch(
            [pa.array([], type=t) for t in SCHEDULES_SCHEMA.types],
            schema=SCHEDULES_SCHEMA,
        )
    else:
        batch = pa.record_batch({
            "lane_id": pa.array([s["lane_id"] for s in schedules], type=pa.uint32()),
            "depart_atu": pa.array([s["depart_atu"] for s in schedules], type=pa.int64()),
            "capacity": pa.array([s["capacity"] for s in schedules], type=pa.uint32()),
        })
    return _write_batch(batch)


def to_demand_ipc(events: list[dict[str, Any]]) -> bytes:
    """Serialize demand event dicts to Arrow IPC stream bytes.

    Caller must ensure events are sorted ascending by `appear_atu` —
    the engine's `DemandQueue::from_arrays` debug-asserts this invariant.
    """
    if not events:
        batch = pa.record_batch(
            [pa.array([], type=t) for t in DEMAND_SCHEMA.types],
            schema=DEMAND_SCHEMA,
        )
    else:
        batch = pa.record_batch({
            "flow_unit_id": pa.array([e["flow_unit_id"] for e in events], type=pa.uint64()),
            "appear_atu": pa.array([e["appear_atu"] for e in events], type=pa.int64()),
            "due_atu": pa.array([e["due_atu"] for e in events], type=pa.int64()),
            "origin_node_id": pa.array([e["origin_node_id"] for e in events], type=pa.uint32()),
            "dest_node_id": pa.array([e["dest_node_id"] for e in events], type=pa.uint32()),
            "origin_cell_idx": pa.array([e["origin_cell_idx"] for e in events], type=pa.uint32()),
            "dest_cell_idx": pa.array([e["dest_cell_idx"] for e in events], type=pa.uint32()),
            "size_units": pa.array([e["size_units"] for e in events], type=pa.uint32()),
            "priority_class": pa.array([e["priority_class"] for e in events], type=pa.uint8()),
            "handling_flags": pa.array([e["handling_flags"] for e in events], type=pa.uint8()),
        })
    return _write_batch(batch)
