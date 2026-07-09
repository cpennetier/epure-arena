"""Compiled-engine re-export.

The discrete-event runtime lives in ephemeris-kernel (the time-and-truth
substrate); this module aliases it so every historical import path
(`from epure_arena import _engine`, `import epure_arena._engine`,
`epure_arena._engine.PyEngine`) keeps resolving unchanged.
"""

import sys

from ephemeris import _engine as _kernel_engine

sys.modules[__name__] = _kernel_engine
