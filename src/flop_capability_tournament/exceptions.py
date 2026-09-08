from __future__ import annotations

from flop_work_exchange.exceptions import (
    AdapterError,
    InsufficientFundsError,
    IsolationError,
    NotLiveError,
    PolicyError,
    SafetyError,
    StateError,
    ValidationError,
    WorkExchangeError,
)


class TournamentError(WorkExchangeError):
    """Base error for FLOP Capability Tournament."""


class ChallengeStateError(StateError, TournamentError):
    """Illegal challenge/attempt lifecycle transition or missing local state."""


class InertUrlError(SafetyError, TournamentError):
    """Attempted to fetch a URL recorded as inert challenge or fixture metadata."""


__all__ = [
    "AdapterError",
    "ChallengeStateError",
    "InertUrlError",
    "InsufficientFundsError",
    "IsolationError",
    "NotLiveError",
    "PolicyError",
    "SafetyError",
    "StateError",
    "TournamentError",
    "ValidationError",
    "WorkExchangeError",
]
