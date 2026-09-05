"""Compatibility alias for the frozen Execution V2 evidence suite."""

import sys

from Execution import ipython_control as _production_ipython_control

sys.modules[__name__] = _production_ipython_control
