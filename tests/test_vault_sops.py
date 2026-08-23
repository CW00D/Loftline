"""SOPS with age adapter tests.

`sops` and `age` are not installed in this test run and no age key exists.
Everything the resolver depends on must work regardless, because SOPS leaves
keys, structure and `acquired_at` in plaintext (ADR-011).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from loftline.errors import VaultError
from loftline.vault_sops import SopsAgeVault

VAULT = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,iv:aXY=,tag:dGFn,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
    expo:
        push_token:
            value: ENC[AES256_GCM,data:YmFy,iv:aXY=,tag:dGFn,type:str]
            acquired_at: "2025-01-02T09:00:00Z"
        account_id:
            value: ENC[AES256_GCM,data:YmF6,iv:aXY=,tag:dGFn,type:str]
sops:
    age:
        - recipient: age1qqqq
          enc: |
            -----BEGIN AGE ENCRYPTED FILE-----
            -----END AGE ENCRYPTED FILE-----
    lastmodified: "2026-08-01T10:04:05Z"
    version: 3.9.0
"""


@pytest.fixture
def vault_file(tmp_path: Path) -> Path:
    path = tmp_path / "vault.yml"
    path.write_text(VAULT, encoding="utf-8")
    return path


@pytest.fixture
def vault(vault_file: Path) -> SopsAgeVault:
    return SopsAgeVault(vault_file)


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any shelling out at all fails this fixture's tests."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("this operation must not shell out")

    monkeypatch.setattr(subprocess, "run", forbidden)


# --- list_paths: ciphertext only ---------------------------------------------


def test_list_paths_walks_to_every_node_holding_a_value(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    assert vault.list_paths() == [
        "loftline/expo/account_id",
        "loftline/expo/push_token",
        "loftline/render/api_key",
    ]


def test_list_paths_ignores_the_sops_metadata_block(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    assert not any(path.startswith("sops") for path in vault.list_paths())


def test_list_paths_needs_no_key_and_no_sops_binary(
    vault: SopsAgeVault, monkeypatch: pytest.MonkeyPatch, no_subprocess: None
) -> None:
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert vault.list_paths()


def test_an_empty_vault_lists_nothing(tmp_path: Path, no_subprocess: None) -> None:
    path = tmp_path / "vault.yml"
    path.write_text("{}\n", encoding="utf-8")

    assert SopsAgeVault(path).list_paths() == []


def test_a_missing_vault_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(VaultError, match=r"absent\.yml"):
        SopsAgeVault(tmp_path / "absent.yml").list_paths()


def test_a_malformed_vault_file_names_the_path(tmp_path: Path) -> None:
    path = tmp_path / "vault.yml"
    path.write_text("loftline: [unclosed\n", encoding="utf-8")

    with pytest.raises(VaultError, match=r"vault\.yml"):
        SopsAgeVault(path).list_paths()


# --- acquired_at: also ciphertext only ---------------------------------------


def test_acquired_at_is_read_without_decrypting(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    assert vault.acquired_at("loftline/render/api_key") == datetime(
        2026, 8, 1, 10, 4, tzinfo=UTC
    )


def test_acquired_at_is_none_when_the_entry_has_no_timestamp(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    assert vault.acquired_at("loftline/expo/account_id") is None


def test_acquired_at_on_an_unknown_path_names_the_path(vault: SopsAgeVault) -> None:
    with pytest.raises(VaultError, match="loftline/nope/key"):
        vault.acquired_at("loftline/nope/key")


def test_the_index_is_built_from_ciphertext_alone(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    index = vault.index()

    assert "loftline/render/api_key" in index
    assert index.acquired_at("loftline/expo/push_token") == datetime(
        2025, 1, 2, 9, 0, tzinfo=UTC
    )
    assert index.acquired_at("loftline/expo/account_id") is None


# --- get: the only operation that decrypts -----------------------------------


def test_get_extracts_a_single_value(
    vault: SopsAgeVault, vault_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="rnd_secret\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert vault.get("loftline/render/api_key") == "rnd_secret"
    assert calls == [
        [
            "/usr/bin/sops",
            "-d",
            "--extract",
            '["loftline"]["render"]["api_key"]["value"]',
            str(vault_file),
        ]
    ]


def test_get_refuses_when_sops_is_absent(
    vault: SopsAgeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(VaultError, match="sops"):
        vault.get("loftline/render/api_key")


def test_get_on_an_unknown_path_does_not_shell_out(
    vault: SopsAgeVault, no_subprocess: None
) -> None:
    with pytest.raises(VaultError, match="loftline/nope/key"):
        vault.get("loftline/nope/key")


def test_a_failing_get_reports_stderr_and_names_the_path(
    vault: SopsAgeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="no age key found"
        )

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VaultError) as excinfo:
        vault.get("loftline/render/api_key")

    assert "loftline/render/api_key" in str(excinfo.value)
    assert "no age key found" in str(excinfo.value)


# --- set: in place, never through a plaintext temporary file -----------------


def test_set_writes_value_and_timestamp_in_place(
    vault: SopsAgeVault, vault_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    vault.set(
        "loftline/render/api_key", "rnd_new", now=datetime(2026, 8, 18, 12, tzinfo=UTC)
    )

    assert len(calls) == 1
    argv = calls[0]
    assert argv[:2] == ["/usr/bin/sops", "set"]
    assert str(vault_file) in argv
    assert '["loftline"]["render"]["api_key"]' in argv
    assert '{"value": "rnd_new", "acquired_at": "2026-08-18T12:00:00Z"}' in argv


def test_set_leaves_no_plaintext_behind(
    vault: SopsAgeVault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plaintext must never touch disk, so no decrypt-edit-encrypt round trip."""
    before = {p.name for p in tmp_path.iterdir()}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    vault.set("loftline/render/api_key", "rnd_new")

    assert {p.name for p in tmp_path.iterdir()} == before
    assert "rnd_new" not in (tmp_path / "vault.yml").read_text(encoding="utf-8")


def test_a_failing_set_does_not_leak_the_value_into_the_error(
    vault: SopsAgeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="failed on rnd_supersecret"
        )

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VaultError) as excinfo:
        vault.set("loftline/render/api_key", "rnd_supersecret")

    assert "rnd_supersecret" not in str(excinfo.value)
    assert "loftline/render/api_key" in str(excinfo.value)
    assert "[value redacted]" in str(excinfo.value)


def test_a_failing_set_keeps_the_diagnostic_around_the_redaction(
    vault: SopsAgeVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redacting the value must not cost the reason it failed."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 128, stdout="", stderr="identity did not match any of the recipients"
        )

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VaultError, match="did not match any of the recipients"):
        vault.set("loftline/render/api_key", "rnd_supersecret")


def test_set_rejects_an_empty_value(vault: SopsAgeVault, no_subprocess: None) -> None:
    with pytest.raises(VaultError, match="empty"):
        vault.set("loftline/render/api_key", "")


@pytest.mark.parametrize("path", ["", "loftline", "loftline//api_key", "/loftline/x"])
def test_a_malformed_path_is_rejected(
    vault: SopsAgeVault, path: str, no_subprocess: None
) -> None:
    with pytest.raises(VaultError):
        vault.get(path)
