from __future__ import annotations

import json
from pathlib import Path

import pytest
from flop_work_exchange.receipts import verify_receipt

from flop_capability_tournament.cli import main
from flop_capability_tournament.credentials import verify_credential
from flop_capability_tournament.models import load_challenge_file


def test_module_demo_writes_verifiable_receipts_and_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["demo", "--state-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["payment_mode"] == "paper"
    assert payload["settlement_status"] == "simulated"
    assert payload["urls_are_inert"] is True
    assert len(payload["kinds"]) >= 2
    assert "extract-structured-info@1.0.0" in payload["kinds"]
    assert "detect-malicious-instructions@1.0.0" in payload["kinds"]
    for run in payload["runs"]:
        assert run["bench_result"] == "PASS"
        assert run["status"] == "AWARDED"
        assert run["credential_verification"]["ok"] is True
        assert "prize" in run["award_receipt_roles"]
        assert "evaluation" in run["award_receipt_roles"]
        assert "entry" in run["entry_receipt_roles"]
        for item in run["receipt_verifications"]:
            assert item["verification"]["ok"] is True
        assert verify_credential(run["credential"])["ok"] is True
        bundle_path = Path(run["bundle_path"])
        assert bundle_path.exists()
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        for leg in bundle["legs"]:
            assert verify_receipt(leg["receipt"])["ok"] is True


def test_cli_list_challenges_after_demo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["demo", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["--state-dir", str(tmp_path), "list-challenges"]) == 0
    challenges = json.loads(capsys.readouterr().out)
    assert len(challenges) >= 2
    challenge_id = challenges[0]["challenge_id"]
    assert main(["--state-dir", str(tmp_path), "show", "--challenge-id", challenge_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["attempts"][0]["status"] == "AWARDED"
    attempt_id = shown["attempts"][0]["attempt_id"]
    assert (
        main(
            [
                "--state-dir",
                str(tmp_path),
                "show-credential",
                "--attempt-id",
                attempt_id,
            ]
        )
        == 0
    )
    cred = json.loads(capsys.readouterr().out)
    assert cred["verification"]["ok"] is True


def test_help_lists_required_commands() -> None:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    text = buffer.getvalue()
    for command in (
        "publish-challenge",
        "list-challenges",
        "enter",
        "submit-attempt",
        "evaluate",
        "award",
        "show-credential",
        "demo",
        "identity",
    ):
        assert command in text


def test_example_specs_load() -> None:
    from flop_capability_tournament.constants import CHALLENGE_KINDS

    root = Path(__file__).resolve().parents[1] / "examples" / "challenges"
    for kind in CHALLENGE_KINDS:
        spec = load_challenge_file(root / f"{kind}.json")
        assert spec.kind == kind
        assert spec.kind_id.endswith("@1.0.0")
        assert spec.entry_fee_micro > 0
        assert spec.prize_micro > 0
