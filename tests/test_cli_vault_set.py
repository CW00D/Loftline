"""`loftline vault set` tests.

Storing a value is the one operation where a mistake leaks a secret, so these
assert the negative space: the value never appears on stdout, in an error, or
in the shell's argument vector for the command itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from loftline.cli import app
from loftline.doctor import Status

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = REPO_ROOT / "credentials.yml"

VAULT_WITH_RENDER = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
sops:
    version: 3.9.0
"""

EMPTY_VAULT = """\
loftline: {}
sops:
    version: 3.9.0
"""

SOPS_CONFIG = """\
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: age1primary,age1backup
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault.yml"
    path.write_text(EMPTY_VAULT, encoding="utf-8")
    (tmp_path / ".sops.yaml").write_text(SOPS_CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A machine that passes the write gate, with sops replaced by a recorder."""
    key = tmp_path / "keys.txt"
    key.write_text("AGE-SECRET-KEY-PLACEHOLDER\n", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    monkeypatch.delenv("LOFTLINE_VAULT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption", lambda: (Status.PASS, "on")
    )

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def vault_set(runner: CliRunner, vault: Path, *args: str, value: str | None) -> Result:
    return runner.invoke(
        app,
        [
            "vault",
            "set",
            *args,
            "--vault",
            str(vault),
            "--credentials",
            str(CREDENTIALS),
        ],
        input=None if value is None else f"{value}\n",
    )


# --- the happy path ----------------------------------------------------------


def test_set_stores_the_value_at_the_descriptor_path(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert outcome.exit_code == 0, outcome.output
    sops_set = next(c for c in writable if c[1:2] == ["set"])
    assert sops_set[2] == str(vault)
    assert sops_set[3] == '["loftline"]["render"]["api_key"]'
    assert '"value": "rnd_abc123"' in sops_set[4]
    assert '"acquired_at": "' in sops_set[4]


def test_set_confirms_the_name_and_path_and_nothing_else(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert "render_api_key" in outcome.output
    assert "loftline/render/api_key" in outcome.output
    assert "rnd_abc123" not in outcome.output


def test_set_reminds_you_to_push_the_vault(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert "push" in outcome.output.lower()


def test_the_value_is_prompted_for_with_input_hidden(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    """A hidden prompt echoes nothing, so the typed value must be absent."""
    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert "Value for render_api_key" in outcome.output
    assert "rnd_abc123" not in outcome.output


def test_the_value_can_be_piped_on_stdin(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_api_key", "--stdin", value="rnd_piped")

    assert outcome.exit_code == 0, outcome.output
    sops_set = next(c for c in writable if c[1:2] == ["set"])
    assert '"value": "rnd_piped"' in sops_set[4]
    assert "Value for" not in outcome.output


def test_a_trailing_newline_on_stdin_is_not_part_of_the_value(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    vault_set(runner, vault, "render_api_key", "--stdin", value="rnd_piped")

    sops_set = next(c for c in writable if c[1:2] == ["set"])
    assert '"value": "rnd_piped"' in sops_set[4]
    assert "\\n" not in sops_set[4]


# --- refusals ----------------------------------------------------------------


def test_an_unknown_credential_name_lists_the_known_ones(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_apikey", value="rnd_abc123")

    assert outcome.exit_code != 0
    assert "render_apikey" in outcome.output
    assert "render_api_key" in outcome.output
    assert not any(c[1:2] == ["set"] for c in writable)


def test_a_produced_credential_cannot_be_stored(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    """Invariant 4: produced credentials never live in the vault."""
    outcome = vault_set(runner, vault, "database_url", value="postgres://x")

    assert outcome.exit_code != 0
    assert "produced" in outcome.output
    assert "postgres://x" not in outcome.output
    assert not any(c[1:2] == ["set"] for c in writable)


def test_an_existing_value_is_not_overwritten_by_default(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    vault.write_text(VAULT_WITH_RENDER, encoding="utf-8")

    outcome = vault_set(runner, vault, "render_api_key", value="rnd_new")

    assert outcome.exit_code != 0
    assert "--replace" in outcome.output
    assert not any(c[1:2] == ["set"] for c in writable)


def test_replace_overwrites_an_existing_value(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    vault.write_text(VAULT_WITH_RENDER, encoding="utf-8")

    outcome = vault_set(runner, vault, "render_api_key", "--replace", value="rnd_new")

    assert outcome.exit_code == 0, outcome.output
    assert any(c[1:2] == ["set"] for c in writable)


def test_an_empty_value_is_refused(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    outcome = vault_set(runner, vault, "render_api_key", "--stdin", value="")

    assert outcome.exit_code != 0
    assert "empty" in outcome.output
    assert not any(c[1:2] == ["set"] for c in writable)


def test_the_write_gate_runs_before_the_prompt(
    runner: CliRunner,
    vault: Path,
    writable: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not ask for a secret you are then unable to store."""
    monkeypatch.setattr("shutil.which", lambda _name: None)

    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert outcome.exit_code != 0
    assert "sops" in outcome.output
    assert "Value for" not in outcome.output


def test_a_single_recipient_blocks_writing(
    runner: CliRunner, vault: Path, writable: list[list[str]]
) -> None:
    (vault.parent / ".sops.yaml").write_text(
        "creation_rules:\n  - path_regex: vault\\.ya?ml$\n    age: age1primary\n",
        encoding="utf-8",
    )

    outcome = vault_set(runner, vault, "render_api_key", value="rnd_abc123")

    assert outcome.exit_code != 0
    assert "recipients" in outcome.output
    assert "Value for" not in outcome.output


def test_a_failed_write_does_not_leak_the_value(
    runner: CliRunner,
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    writable: list[list[str]],
) -> None:
    def failing_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if argv[1:2] == ["set"]:
            return subprocess.CompletedProcess(
                argv, 128, stdout="", stderr=f"failed on {argv[4]}"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", failing_run)

    outcome = vault_set(runner, vault, "render_api_key", value="rnd_leaky")

    assert outcome.exit_code != 0
    assert "rnd_leaky" not in outcome.output
    assert "loftline/render/api_key" in outcome.output
