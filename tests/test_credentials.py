from __future__ import annotations

from pathlib import Path

import pytest
from flop_work_exchange.receipts import verify_receipt

from flop_capability_tournament.cli import main
from flop_capability_tournament.credentials import (
    EvidenceCredential,
    router_capability_claim,
    sign_credential,
    verify_credential,
)
from flop_capability_tournament.exceptions import ValidationError
from flop_capability_tournament.identity import create_ephemeral_party, generate_key, public_did
from tests.helpers import complete_to_evaluated

REQUIRED = (
    "job_id",
    "buyer_did",
    "seller_did",
    "operator_relationship",
    "service",
    "price_flop",
    "payment_mode",
    "tclk_deal_id",
    "result_hash",
    "bench_result",
    "completed_at",
    "settlement_status",
)


def test_sign_and_verify_credential_roundtrip() -> None:
    key = generate_key()
    did = public_did(key)
    _, subject = create_ephemeral_party("s")
    credential = EvidenceCredential(
        issuer_did=did,
        subject_did=subject,
        challenge_id="FLOP-CHAL-demo",
        challenge_kind="extract-structured-info@1.0.0",
        attempt_id="FLOP-ATTEMPT-demo",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        issued_at="2026-09-08T00:00:00Z",
        operator_relationship="independent",
        independent_reputation_eligible=True,
    )
    sign_credential(credential, key, did)
    payload = credential.to_dict()
    assert payload["signature"]
    assert verify_credential(payload)["ok"] is True
    claim = router_capability_claim(payload)
    assert claim["capability"] == "extract-structured-info"
    assert claim["independent_routing_evidence"] is True
    assert claim["payment_proof"] is False


def test_tampered_credential_fails_verify() -> None:
    key = generate_key()
    did = public_did(key)
    _, subject = create_ephemeral_party("s")
    credential = EvidenceCredential(
        issuer_did=did,
        subject_did=subject,
        challenge_id="FLOP-CHAL-demo",
        challenge_kind="extract-structured-info@1.0.0",
        attempt_id="FLOP-ATTEMPT-demo",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        issued_at="2026-09-08T00:00:00Z",
    )
    sign_credential(credential, key, did)
    payload = credential.to_dict()
    payload["challenge_kind"] = "forged-kind@9.9.9"
    with pytest.raises(ValidationError, match="signature verification failed"):
        verify_credential(payload)


def test_fail_result_cannot_be_credential() -> None:
    key = generate_key()
    did = public_did(key)
    _, subject = create_ephemeral_party("s")
    credential = EvidenceCredential(
        issuer_did=did,
        subject_did=subject,
        challenge_id="FLOP-CHAL-demo",
        challenge_kind="extract-structured-info@1.0.0",
        attempt_id="FLOP-ATTEMPT-demo",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="FAIL",
        issued_at="2026-09-08T00:00:00Z",
    )
    with pytest.raises(ValidationError, match="PASS"):
        sign_credential(credential, key, did)


def test_live_payment_mode_rejected_on_credential() -> None:
    key = generate_key()
    did = public_did(key)
    _, subject = create_ephemeral_party("s")
    credential = EvidenceCredential(
        issuer_did=did,
        subject_did=subject,
        challenge_id="FLOP-CHAL-demo",
        challenge_kind="extract-structured-info@1.0.0",
        attempt_id="FLOP-ATTEMPT-demo",
        result_hash="sha256:" + ("ab" * 32),
        bench_result="PASS",
        payment_mode="live",
        issued_at="2026-09-08T00:00:00Z",
    )
    with pytest.raises(ValidationError, match="payment_mode"):
        sign_credential(credential, key, did)


def test_awarded_receipts_and_credential_verify(tmp_path: Path) -> None:
    tournament, attempt_id, _publisher, _agent = complete_to_evaluated(tmp_path)
    _attempt, credential, bundle, path = tournament.award(attempt_id)
    assert path.exists()
    for leg in bundle.legs:
        for field in REQUIRED:
            assert field in leg.receipt
        assert leg.receipt["payment_mode"] == "paper"
        assert leg.receipt["settlement_status"] == "simulated"
        assert verify_receipt(leg.receipt)["ok"] is True
        assert leg.verification["ok"] is True
    roles = {leg.role for leg in bundle.legs}
    assert {"prize", "evaluation"} <= roles
    cred_path = tmp_path / "credentials" / f"{credential.credential_id}.json"
    assert cred_path.exists()
    assert verify_credential(credential.to_dict())["ok"] is True
    assert main(["verify-credential", str(cred_path)]) == 0
    prize_receipt = tmp_path / "receipts" / f"{attempt_id}.prize.json"
    assert prize_receipt.exists()
    assert main(["verify-receipt", str(prize_receipt)]) == 0
    shown = tournament.show_credential(attempt_id=attempt_id)
    assert shown["verification"]["ok"] is True
