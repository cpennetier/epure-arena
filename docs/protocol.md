# Wire protocol v1 (arrow-bridge v1)

The engine's data plane is Arrow IPC. Four input tables define a world; the
engine emits KPI/strand/delivery/surface tables back. The protocol is
versioned independently of the Python API: **arrow-bridge v1**,
fingerprint: `0d0082854fc4a5ad`.

The fingerprint is the first 16 hex chars of the SHA-256 over the sorted
column names of the surface schema (see
`ephemeris-kernel crates/ephemeris-des/src/surface_writer.rs::schema_fingerprint`). It is
asserted per-commit by `make verify-fingerprint` and recorded in every run
manifest — a run produced under a different protocol is mechanically
distinguishable.

## Input tables

All time fields are ATU (`i64` milliseconds). Costs are integer
microdollars. Capacities are `u32` units. Spatial indexing uses
[H3](https://h3geo.org) (resolution-3 rollups for cells); H3 is a
scenarios-level dependency only — the engine consumes the derived columns.

### nodes

| column | type | notes |
|---|---|---|
| `node_id` | u32 | dense, unique |
| `h3_index` | u64 | precise location index |
| `lat`, `lon` | f64 | |
| `tier` | utf8 | `gateway` \| `regional` \| `leaf` — routing targets are `leaf` |
| `territory_id` | u32 | |
| `demand_gravity` | f64 | |
| `has_airport`, `has_port`, `has_rail` | bool | |
| `landmass_id` | i32 | |
| `cell_id_r3` | u64 | resolution-3 rollup cell |
| `cell_centroid_lat`, `cell_centroid_lon` | f64 | |

### lanes

| column | type | notes |
|---|---|---|
| `lane_id` | u32 | |
| `src_node_id`, `dst_node_id` | u32 | directed |
| `src_lat`, `src_lon`, `dst_lat`, `dst_lon` | f64 | |
| `lane_mode` | u8 | |
| `duration_sec` | f32 | display only |
| `duration_atu` | i64 | authoritative transit time |
| `cost_usd` | f32 | display only |
| `cost_usd_micros` | i64 | authoritative cost |
| `co2_g` | i64 | |
| `phase` | utf8 | |

### connections (schedule)

| column | type | notes |
|---|---|---|
| `lane_id` | u32 | |
| `depart_atu` | i64 | sorted per lane |
| `capacity` | u32 | per-(lane, connection) residual |

### demand

| column | type | notes |
|---|---|---|
| `flow_unit_id` | u64 | surge ids live at/above 10,000,000 |
| `appear_atu`, `due_atu` | i64 | |
| `origin_node_id`, `dest_node_id` | u32 | |
| `origin_cell_idx`, `dest_cell_idx` | u32 | |
| `size_units` | u32 | |
| `priority_class` | u8 | |

## Output surfaces

KPIs (JSON): `total_flow_units`, `total_injected`, `total_delivered`,
`stranded_flow_units`, `max_node_load`, `node_loads`, … Strand records carry
a typed reason (`topo` / `sched` / `cap` / `deadline` / `lane_outage` /
`node_outage`) and a decide-site. Delivery and hop logs are opt-in. Surface
tables embed the protocol version and fingerprint as schema metadata.

## Versioning

- Column renames or type changes ⇒ protocol v2 (new fingerprint, declared).
- Additive nullable columns ⇒ minor revision; fingerprint changes and is
  re-pinned in the same commit, with golden bundles regenerated
  deliberately — never silently.
- The Python API (SemVer, pre-1.0) and the wire protocol version move
  independently.
