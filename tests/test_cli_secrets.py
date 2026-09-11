"""`loftline secrets write` tests: no gh, no sops, no network."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from loftline import cli
from loftline.cli import app
from loftline.doctor import Status
from loftline.vault import VaultIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "examples" / "skeleton.yml"
CREDENTIALS = REPO_ROOT / "credentials.yml"

Calls = list[tuple[list[str], str | None]]

# smtp_user and smtp_password held; render and aura credentials absent.
VAULT = """\
loftline:
    google:
        smtp_user:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-09-10T10:46:10Z"
        smtp_app_password:
            value: ENC[AES256_GCM,data:YmFy,type:str]
            acquired_at: "2026-09-10T10:46:40Z"
sops:
    version: 3.9.0
"""

SOPS_CONFIG = """\
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: age1primary,age1backup
"""


class FakeVault:
    """Stands in for SopsAgeVault: reads the fixture's structure, fakes decryption."""

    def __init__(self, path: Path) -> None:
        from loftline.vault_sops import SopsAgeVault

        self._real = SopsAgeVault(path)

    def index(self) -> VaultIndex:
        return self._real.index()

    def list_paths(self) -> list[str]:
        return self._real.list_paths()

    def get(self, path: str) -> str:
        return f"decrypted:{path}"

    def set(self, path: str, value: str) -> None:
        raise AssertionError("never")

    def acquired_at(self, path: str) -> datetime | None:
        return self._real.acquired_at(path)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault.yml"
    path.write_text(VAULT, encoding="utf-8")
    (tmp_path / ".sops.yaml").write_text(SOPS_CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Calls:
    """A healthy machine with a fake `gh` and a fake vault decrypt."""
    key = tmp_path / "keys.txt"
    key.write_text("AGE-SECRET-KEY-PLACEHOLDER\n", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    monkeypatch.delenv("LOFTLINE_VAULT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption", lambda: (Status.PASS, "on")
    )
    monkeypatch.setattr(cli, "SopsAgeVault", FakeVault)

    calls: Calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs.get("input")))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def write(runner: CliRunner, vault: Path, *extra: str) -> Result:
    return runner.invoke(
        app,
        [
            "secrets",
            "write",
            str(SPEC),
            "--repo",
            "CW00D/demo",
            "--vault",
            str(vault),
            "--credentials",
            str(CREDENTIALS),
            *extra,
        ],
    )


def test_outstanding_credentials_refuse_by_default(vault: Path, gh: Calls) -> None:
    result = write(CliRunner(), vault)

    assert result.exit_code != 0
    assert "render_api_key" in result.output
    assert "--partial" in result.output
    assert not any(argv[1:3] == ["secret", "set"] for argv, _ in gh)


def test_partial_writes_held_and_generated_and_lists_the_rest(
    vault: Path, gh: Calls
) -> None:
    result = write(CliRunner(), vault, "--partial")

    assert result.exit_code == 0, result.output
    sets = [
        (argv[3], argv[7], stdin)
        for argv, stdin in gh
        if argv[1:3] == ["secret", "set"]
    ]
    assert {name for name, _, _ in sets} == {"SMTP_USER", "SMTP_PASSWORD", "JWT_SECRET"}
    assert {env for _, env, _ in sets} == {"staging", "production"}
    # Held values came from the vault, generated ones did not.
    held = [v for n, _, v in sets if n.startswith("SMTP")]
    generated = [v for n, _, v in sets if n == "JWT_SECRET"]
    assert all(v is not None and v.startswith("decrypted:") for v in held)
    assert all(v is not None and not v.startswith("decrypted:") for v in generated)
    assert "render_api_key" in result.output


def test_no_value_reaches_the_output_or_an_argument(vault: Path, gh: Calls) -> None:
    result = write(CliRunner(), vault, "--partial")

    assert "decrypted:" not in result.output
    for argv, _ in gh:
        assert not any(a.startswith("decrypted:") for a in argv)


def test_environments_are_created_first(vault: Path, gh: Calls) -> None:
    write(CliRunner(), vault, "--partial")

    kinds = [argv[1:3] for argv, _ in gh]
    assert kinds.index(["api", "--method"]) < kinds.index(["secret", "set"])


def test_preflight_runs_before_any_decryption(
    vault: Path, gh: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        code = 1 if argv[1:3] == ["auth", "status"] else 0
        return subprocess.CompletedProcess(
            argv, code, stdout="", stderr="not logged in"
        )

    monkeypatch.setattr(subprocess, "run", failing)
    reads: list[str] = []

    def recording_get(self: FakeVault, path: str) -> str:
        reads.append(path)
        return "x"

    monkeypatch.setattr(FakeVault, "get", recording_get)

    result = write(CliRunner(), vault, "--partial")

    assert result.exit_code != 0
    assert "gh auth login" in result.output
    assert reads == []
