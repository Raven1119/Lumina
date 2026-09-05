"""Compatibility alias for the frozen Execution V2 evidence suite.

The supported implementation lives in :mod:`Execution.execution`. Keeping the
historical import path as the same module object lets the retained lab tests
validate production without maintaining a second active implementation.
"""

import sys

from Execution import execution as _production_execution

sys.modules[__name__] = _production_execution
