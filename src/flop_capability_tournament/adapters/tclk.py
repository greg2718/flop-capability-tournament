"""Paper TCLK adapter re-export. Simulation only; settlement execution DISABLED.

Tournament does not reimplement TCLK. Work Exchange owns the paper deal id.
"""

from __future__ import annotations

from flop_work_exchange.adapters.tclk import StubTclkAdapter

__all__ = ["StubTclkAdapter"]
