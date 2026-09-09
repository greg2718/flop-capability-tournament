"""Adapter interfaces for Scout, Bench, Router, Sentinel, TCLK, and settlement.

These are adapters only. They do not reimplement the sibling agents. Stubs
work fully offline and return the sibling decision/verdict shapes. Local*
adapters subprocess or import the real packages when selected via config/env.

Challenge evaluation stays on ChallengeBenchAdapter (kind checks against local
fixtures). Work Exchange paid-leg verify uses StubBenchAdapter or LocalBenchAdapter.
"""

from __future__ import annotations

from typing import Any, Protocol

from flop_work_exchange.models import (
    BenchVerdict,
    ExecutionPlan,
    Job,
    Offer,
    SentinelVerdict,
    TclkPaperDeal,
    WorkerCandidate,
)

from flop_capability_tournament.adapters.bench import (
    ChallengeBenchAdapter,
    LocalBenchAdapter,
    StubBenchAdapter,
)
from flop_capability_tournament.adapters.router import LocalRouterAdapter, StubRouterAdapter
from flop_capability_tournament.adapters.scout import LocalScoutAdapter, StubScoutAdapter
from flop_capability_tournament.adapters.sentinel import LocalSentinelAdapter, StubSentinelAdapter
from flop_capability_tournament.adapters.settlement import PaperSettlement, TestnetSettlement
from flop_capability_tournament.adapters.tclk import StubTclkAdapter


class ScoutAdapter(Protocol):
    kind: str

    def probe(self) -> dict[str, Any]: ...

    def find_candidates(self, job: Job) -> list[WorkerCandidate]: ...


class RouterAdapter(Protocol):
    kind: str

    def probe(self) -> dict[str, Any]: ...

    def plan(self, job: Job, offers: list[Offer]) -> ExecutionPlan: ...


class SentinelAdapter(Protocol):
    kind: str

    def probe(self) -> dict[str, Any]: ...

    def screen(self, artifact_type: str, artifact: dict[str, Any]) -> SentinelVerdict: ...


class BenchAdapter(Protocol):
    kind: str

    def probe(self) -> dict[str, Any]: ...

    def verify_delivery(self, job: Job) -> BenchVerdict: ...


class TclkAdapter(Protocol):
    def record_paper_deal(self, job: Job, offer: Offer) -> TclkPaperDeal: ...


class SettlementAdapter(Protocol):
    backend: str

    def credit(
        self, account: str, amount_micro: int, reason: str, job_id: str | None = None
    ) -> None: ...

    def transfer(
        self,
        *,
        debit_account: str,
        credit_account: str,
        amount_micro: int,
        reason: str,
        job_id: str | None = None,
    ) -> None: ...


__all__ = [
    "BenchAdapter",
    "ChallengeBenchAdapter",
    "LocalBenchAdapter",
    "LocalRouterAdapter",
    "LocalScoutAdapter",
    "LocalSentinelAdapter",
    "PaperSettlement",
    "RouterAdapter",
    "ScoutAdapter",
    "SentinelAdapter",
    "SettlementAdapter",
    "StubBenchAdapter",
    "StubRouterAdapter",
    "StubScoutAdapter",
    "StubSentinelAdapter",
    "StubTclkAdapter",
    "TclkAdapter",
    "TestnetSettlement",
]
