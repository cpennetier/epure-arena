"""Plan paths → Rust inject_path format.

Converts planner output (PlanResult with lane_id paths) to the format
expected by Rust DES engine's set_forced_routes(). The engine expects
(flow_unit_id, Vec<CSR_lane_idx>) pairs.

Lane index translation: Python lane_ids are sequential 0..E-1.
Rust CSR indices differ because lanes are sorted by target within each
node's range. The lane_id_to_csr mapping bridges them.
"""

from __future__ import annotations

import logging
from typing import Any

from epure_arena.optimize.interface import PlanContext, PlanResult

logger = logging.getLogger(__name__)


def compile_for_engine(
    results: list[PlanResult],
    context: PlanContext,
) -> list[tuple[int, list[int]]]:
    """Convert PlanResults to Rust engine forced route format.

    Translates Python lane_ids to Rust CSR indices using lane_id_to_csr.

    Args:
        results: List of PlanResults with lane_id paths.
        context: PlanContext with lane_id_to_csr mapping.

    Returns:
        List of (flow_unit_id, [csr_lane_idx, ...]) tuples.
        Only includes results with non-empty paths and 'planned' or 'fallback' status.
    """
    compiled: list[tuple[int, list[int]]] = []
    e2c = context.lane_id_to_csr
    max_eid = len(e2c) - 1

    n_compiled = 0
    n_skipped = 0
    n_invalid = 0

    for result in results:
        if result.status == "failed" or not result.path:
            n_skipped += 1
            continue

        csr_path: list[int] = []
        valid = True
        for lane_id in result.path:
            if lane_id < 0 or lane_id > max_eid:
                logger.warning(
                    "FlowUnit %d: lane_id %d out of range [0, %d]",
                    result.flow_unit_id, lane_id, max_eid,
                )
                valid = False
                break
            csr_path.append(e2c[lane_id])

        if valid and csr_path:
            compiled.append((result.flow_unit_id, csr_path))
            n_compiled += 1
        else:
            n_invalid += 1

    logger.info(
        "Compiled %d forced routes (%d skipped, %d invalid)",
        n_compiled, n_skipped, n_invalid,
    )

    return compiled
