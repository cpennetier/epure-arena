"""Navigate — selection over imagined candidate futures (the canonical role).

Fixed and declared in the reference harness: argmax over Imagine's
estimates, first-candidate tiebreak — deterministic. Navigate selects; it
never routes (that is Optimize) and never estimates (that is Imagine).
"""

from __future__ import annotations

from typing import Sequence


def navigate_argmax(candidates: Sequence, estimates: Sequence[float]) -> int:
    """Argmax estimate; first-candidate tiebreak (deterministic)."""
    best, best_i = None, 0
    for i, e in enumerate(estimates):
        if best is None or e > best:
            best, best_i = e, i
    return best_i
