from __future__ import annotations

import getpass
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from flop_capability_tournament.cli import main
from flop_capability_tournament.exceptions import SafetyError, ValidationError
from flop_capability_tournament.identity import (
    IDENTITY_CONFIRMATION,
    PRODUCTION_IDENTITY_JSON,
    PRODUCTION_IDENTITY_PEM,
    TEST_IDENTITY_JSON,
    TEST_IDENTITY_PEM,
    create_production_identity,
    create_test_identity,
    ensure_identity,
    is_valid_ed25519_did,
    load_identity_meta,
    load_tournament_key,
    public_did,
)
from flop_capability_tournament.config import TournamentConfig
from flop_capability_tournament.tournament import CapabilityTournament

TEST_PASSPHRASE = "capability-tournament-test-passphrase"  # noqa: S105


@pytest.fixture(autouse=True)
def _never_write_real_production_identity() -> Iterator[None]:
    real = Path.home() / ".flop_agents" / "capability-tournament"
    before_pem = (real / PRODUCTION_IDENTITY_PEM).exists()
    before_json = (real / PRODUCTION_IDENTITY_JSON).exists()
    yield
    if not before_pem:
        assert not (real / PRODUCTION_IDENTITY_PEM).exists()
    if not before_json:
        assert not (real / PRODUCTION_IDENTITY_JSON).exists()


def _production_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "flop_capability_tournament.identity.DEFAULT_PRODUCTION_STATE",
        tmp_path,
    )
    return tmp_path


def test_create_test_identity_refuses_production_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "flop_capability_tournament.constants.DEFAULT_PRODUCTION_STATE",
        tmp_path,
    )
    with pytest.raises(SafetyError, match="refusing to auto-create identity"):
        create_test_identity(tmp_path)


def test_create_production_identity_requires_confirmation_and_temp_dirs_fail(
    tmp_path: Path,
) -> None:
    with pytest.raises(SafetyError, match="explicit identity creation confirmation"):
        create_production_identity(
            state_dir=tmp_path,
            confirm="nope",
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )
    with pytest.raises(SafetyError, match="must resolve exactly"):
        create_production_identity(
            state_dir=tmp_path,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )


def test_create_production_identity_encrypted_pem_and_persistent_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _production_state(tmp_path, monkeypatch)
    meta = create_production_identity(
        state_dir=state_dir,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    pem_path = state_dir / PRODUCTION_IDENTITY_PEM
    json_path = state_dir / PRODUCTION_IDENTITY_JSON
    assert pem_path.exists()
    assert json_path.exists()
    assert "BEGIN ENCRYPTED PRIVATE KEY" in pem_path.read_text(encoding="utf-8")
    assert meta["persistent"] is True
    assert meta["purpose"] == "flop-capability-tournament-production"
    assert is_valid_ed25519_did(str(meta["did"]))
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["did"] == meta["did"]
    assert loaded["persistent"] is True
    key, did = load_tournament_key(state_dir, passphrase=TEST_PASSPHRASE)
    assert did == loaded["did"]
    assert public_did(key) == did
    shown = load_identity_meta(state_dir)
    assert shown["did"] == loaded["did"]
    with pytest.raises(SafetyError, match="already exists"):
        create_production_identity(
            state_dir=state_dir,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE,
        )


def test_production_passphrase_mismatch_and_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _production_state(tmp_path, monkeypatch)
    with pytest.raises(SafetyError, match="passphrase confirmation does not match"):
        create_production_identity(
            state_dir=state_dir,
            confirm=IDENTITY_CONFIRMATION,
            passphrase=TEST_PASSPHRASE,
            passphrase_confirmation=TEST_PASSPHRASE + "-other",
        )
    with pytest.raises(SafetyError, match="at least 16 characters"):
        create_production_identity(
            state_dir=state_dir,
            confirm=IDENTITY_CONFIRMATION,
            passphrase="short-pass",
            passphrase_confirmation="short-pass",
        )


def test_load_tournament_key_requires_passphrase_for_encrypted_pem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _production_state(tmp_path, monkeypatch)
    create_production_identity(
        state_dir=state_dir,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    with pytest.raises(ValidationError, match="passphrase"):
        load_tournament_key(state_dir)
    with pytest.raises(ValidationError, match="decrypt"):
        load_tournament_key(state_dir, passphrase="wrong-passphrase-16")


def test_identity_show_prefers_production_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_meta = create_test_identity(tmp_path)
    state_dir = _production_state(tmp_path, monkeypatch)
    prod_meta = create_production_identity(
        state_dir=state_dir,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    assert (tmp_path / TEST_IDENTITY_JSON).exists()
    assert (tmp_path / PRODUCTION_IDENTITY_JSON).exists()
    shown = load_identity_meta(tmp_path)
    assert shown["did"] == prod_meta["did"]
    assert shown["did"] != test_meta["did"]
    assert shown["persistent"] is True
    key, did = load_tournament_key(tmp_path, passphrase=TEST_PASSPHRASE)
    assert did == prod_meta["did"]
    assert public_did(key) == prod_meta["did"]


def test_ensure_identity_loads_production_without_creating_test_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _production_state(tmp_path, monkeypatch)
    meta = create_production_identity(
        state_dir=state_dir,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    loaded = ensure_identity(state_dir)
    assert loaded["did"] == meta["did"]
    assert not (state_dir / TEST_IDENTITY_JSON).exists()
    assert not (state_dir / TEST_IDENTITY_PEM).exists()


def test_load_identity_json_did_is_not_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _production_state(tmp_path / "one", monkeypatch)
    meta_one = create_production_identity(
        state_dir=first,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    second = _production_state(tmp_path / "two", monkeypatch)
    meta_two = create_production_identity(
        state_dir=second,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    assert meta_one["did"] != meta_two["did"]
    assert load_identity_meta(first)["did"] == meta_one["did"]
    assert load_identity_meta(second)["did"] == meta_two["did"]
    assert is_valid_ed25519_did(str(meta_one["did"]))
    assert is_valid_ed25519_did(str(meta_two["did"]))


def test_cli_identity_init_and_show_in_temp_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--state-dir", str(tmp_path), "identity", "init"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["persistent"] is False
    assert created["purpose"] == "test-only"
    assert main(["--state-dir", str(tmp_path), "identity", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["did"] == created["did"]


def test_cli_init_production_uses_getpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _production_state(tmp_path, monkeypatch)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": TEST_PASSPHRASE)
    code = main(
        [
            "--state-dir",
            str(tmp_path),
            "identity",
            "init-production",
            "--confirm",
            IDENTITY_CONFIRMATION,
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["persistent"] is True
    assert payload["purpose"] == "flop-capability-tournament-production"
    assert is_valid_ed25519_did(str(payload["did"]))
    assert main(["--state-dir", str(tmp_path), "identity", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["did"] == payload["did"]


def test_cli_init_production_wrong_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_state(tmp_path, monkeypatch)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": TEST_PASSPHRASE)
    code = main(
        [
            "--state-dir",
            str(tmp_path),
            "identity",
            "init-production",
            "--confirm",
            "CREATE-FLOP-WORK-EXCHANGE-IDENTITY",
        ]
    )
    assert code == 1


def test_tournament_uses_production_did_with_passphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _production_state(tmp_path, monkeypatch)
    meta = create_production_identity(
        state_dir=state_dir,
        confirm=IDENTITY_CONFIRMATION,
        passphrase=TEST_PASSPHRASE,
        passphrase_confirmation=TEST_PASSPHRASE,
    )
    tournament = CapabilityTournament(
        TournamentConfig(state_dir=state_dir),
        identity_passphrase=TEST_PASSPHRASE,
    )
    assert tournament.tournament_did() == meta["did"]
    assert not (state_dir / TEST_IDENTITY_JSON).exists()
