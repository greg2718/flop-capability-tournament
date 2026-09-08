from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from flop_capability_tournament.adapters.bench import (
    InertUrlError,
    looks_like_url,
    reject_url_fetch,
)
from flop_capability_tournament.exceptions import SafetyError
from tests.helpers import extract_spec, fresh_dids, make_tournament


def test_source_url_is_recorded_not_fetched(tmp_path: Path) -> None:
    tournament = make_tournament(tmp_path)
    publisher = fresh_dids(1)[0]
    spec = extract_spec()
    spec.source_url = "https://github.com/greg2718/flop-work-exchange"
    with patch("urllib.request.urlopen") as urlopen:
        challenge = tournament.publish_challenge(publisher_did=publisher, spec=spec)
        urlopen.assert_not_called()
    assert challenge.spec.source_url == spec.source_url
    assert challenge.sentinel_status in {"ALLOW", "REVIEW"}
    assert any("inert" in note for note in challenge.notes)


def test_bench_rejects_url_fetch() -> None:
    with pytest.raises(InertUrlError):
        reject_url_fetch({"adapter": "file_exists", "path": "https://evil.example/README.md"})
    assert looks_like_url("https://github.com/greg2718/flop-bench") is True


def test_local_command_not_self_authorized(tmp_path: Path) -> None:
    from flop_capability_tournament.adapters.bench import evaluate_attempt
    from flop_capability_tournament.models import Attempt

    tournament = make_tournament(tmp_path)
    publisher, agent = fresh_dids(2)
    tournament.credit_paper(agent, "10")
    challenge = tournament.publish_challenge(publisher_did=publisher, spec=extract_spec())
    attempt = Attempt(
        attempt_id="FLOP-ATTEMPT-x",
        challenge_id=challenge.challenge_id,
        entry_id="FLOP-ENTRY-x",
        agent_did=agent,
        payload={"extracted": {}},
    )
    with pytest.raises(SafetyError, match="allow_local_exec"):
        evaluate_attempt(challenge, attempt, allow_local_exec=True)
