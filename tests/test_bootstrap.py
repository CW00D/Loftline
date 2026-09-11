"""First-run helper tests: vault init with a fake sops, and MCP config output."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from loftline.bootstrap import init_vault, mcp_config
from loftline.doctor import VaultConfig
from loftline.errors import VaultError

KEYS = ["age1primary", "age1backup"]


@pytest.fixture
def sops(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A fake sops that 'encrypts' by rewriting the file with a sops block."""
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        if "-e" in argv:
            # sops resolves its config from the working directory; the real
            # binary fails without both of these, so the fake insists on them.
            assert argv[1:3] == ["--config", ".sops.yaml"], argv
            assert kwargs.get("cwd"), "sops must run from the vault directory"
            path = Path(str(kwargs["cwd"])) / argv[-1]
            path.write_text(
                path.read_text(encoding="utf-8") + "sops:\n    version: 3.9.0\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}" if name in ("sops", "git") else None,
    )
    monkeypatch.setattr(subprocess, "run", run)
    return calls


# --- vault init --------------------------------------------------------------


def test_init_writes_config_vault_and_gitignore(
    tmp_path: Path, sops: list[list[str]]
) -> None:
    vault = init_vault(tmp_path / "my-vault", KEYS, run=subprocess.run)

    assert vault == tmp_path / "my-vault" / "vault.yml"
    config = (tmp_path / "my-vault" / ".sops.yaml").read_text(encoding="utf-8")
    assert "age: age1primary,age1backup" in config
    assert "encrypted_regex: '^value$'" in config
    assert "keys.txt" in (tmp_path / "my-vault" / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "sops:" in vault.read_text(encoding="utf-8")


def test_init_encrypts_in_place_and_initialises_git(
    tmp_path: Path, sops: list[list[str]]
) -> None:
    init_vault(tmp_path / "v", KEYS, run=subprocess.run)

    kinds = [c[1:3] for c in sops]
    assert ["--config", ".sops.yaml"] in kinds
    assert ["init", "-q"] in kinds
    assert any(c[1] == "commit" for c in sops)


def test_init_needs_two_recipients(tmp_path: Path, sops: list[list[str]]) -> None:
    with pytest.raises(VaultError, match="two recipients"):
        init_vault(tmp_path / "v", ["age1only"], run=subprocess.run)

    assert not (tmp_path / "v").exists()


def test_init_rejects_a_non_age_key(tmp_path: Path, sops: list[list[str]]) -> None:
    with pytest.raises(VaultError, match="not an age public key"):
        init_vault(tmp_path / "v", ["age1ok", "ssh-rsa AAAA"], run=subprocess.run)


def test_init_refuses_to_overwrite_a_vault(
    tmp_path: Path, sops: list[list[str]]
) -> None:
    init_vault(tmp_path / "v", KEYS, run=subprocess.run)

    with pytest.raises(VaultError, match="already exists"):
        init_vault(tmp_path / "v", KEYS, run=subprocess.run)


def test_init_without_sops_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(VaultError, match="sops is not on PATH"):
        init_vault(tmp_path / "v", KEYS)


def test_a_failed_encrypt_leaves_no_plaintext_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    def failing(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="no such recipient"
        )

    with pytest.raises(VaultError, match="no such recipient"):
        init_vault(tmp_path / "v", KEYS, run=failing)

    assert not (tmp_path / "v" / "vault.yml").exists()


# --- mcp config --------------------------------------------------------------


def test_desktop_config_states_every_path_absolutely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: str(tmp_path / "bin" / name))
    config = VaultConfig(
        vault_path=tmp_path / "vault.yml",
        sops_config=None,
        age_key_file=tmp_path / "keys.txt",
    )

    out = json.loads(mcp_config("desktop", config=config, venv=tmp_path / "venv"))

    entry = out["mcpServers"]["loftline"]
    assert Path(entry["command"]).is_absolute()
    assert entry["env"]["LOFTLINE_VAULT"] == str((tmp_path / "vault.yml").resolve())
    assert entry["env"]["SOPS_AGE_KEY_FILE"] == str(tmp_path / "keys.txt")
    assert str(tmp_path / "bin") in entry["env"]["PATH"]


def test_desktop_config_needs_a_vault(tmp_path: Path) -> None:
    config = VaultConfig(vault_path=None, sops_config=None, age_key_file=tmp_path / "k")

    with pytest.raises(VaultError, match="LOFTLINE_VAULT"):
        mcp_config("desktop", config=config, venv=tmp_path)


def test_code_config_references_the_variable(tmp_path: Path) -> None:
    config = VaultConfig(
        vault_path=tmp_path / "vault.yml", sops_config=None, age_key_file=tmp_path / "k"
    )

    out = json.loads(mcp_config("code", config=config, venv=tmp_path / "venv"))

    assert out["mcpServers"]["loftline"]["env"] == {
        "LOFTLINE_VAULT": "${LOFTLINE_VAULT}"
    }
    assert out["mcpServers"]["loftline"]["command"].endswith(
        "loftline-mcp.exe" if os.name == "nt" else "loftline-mcp"
    )


def test_an_unknown_client_is_refused(tmp_path: Path) -> None:
    config = VaultConfig(
        vault_path=tmp_path / "v", sops_config=None, age_key_file=tmp_path / "k"
    )

    with pytest.raises(VaultError, match="desktop or code"):
        mcp_config("cursor", config=config, venv=tmp_path)
