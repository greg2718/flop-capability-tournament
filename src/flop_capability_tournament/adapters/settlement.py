"""Settlement adapters: paper ledger and disabled testnet rail.

Tournament does not reimplement the marketplace ledger. PaperSettlement and
TestnetSettlement come from flop-work-exchange. settlement_execution stays
DISABLED; TestnetSettlement always raises NotLiveError.
"""

from __future__ import annotations

from flop_work_exchange.adapters.settlement import PaperSettlement, TestnetSettlement

__all__ = ["PaperSettlement", "TestnetSettlement"]
