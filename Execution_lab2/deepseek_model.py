"""Compatibility alias for the frozen Execution V2 evidence suite."""

import sys

from Execution import deepseek_model as _production_deepseek_model

sys.modules[__name__] = _production_deepseek_model
