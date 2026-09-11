"""CLI tests.

`loftline plan` performs no side effects and needs no age key, no `sops` binary
and no network. These tests assert all four.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from loftline.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "examples" / "loftline.yml"
CREDENTIALS = REPO_ROOT / "credentials.yml"

VAULT = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
sops:
    age:
        - recipient: age1qqqq
    version: 3.9.0
"""

EMPTY_VAULT = """\
loftline: {}
sops:
    version: 3.9.0
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault.yml"
    path.write_text(VAULT, encoding="utf-8")
    return path


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No sops, no age, no key, and shelling out at all is a failure."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("plan must not shell out")

    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)
    monkeypatch.delenv("LOFTLINE_VAULT", raising=False)


def plan(runner: CliRunner, vault: Path, *extra: str) -> Result:
    return runner.invoke(
        app,
        [
            "plan",
            str(SPEC),
            "--vault",
            str(vault),
            "--credentials",
            str(CREDENTIALS),
            *extra,
        ],
    )


# --- plan --------------------------------------------------------------------


def test_plan_runs_with_no_key_no_sops_and_no_network(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    outcome = plan(runner, vault)

    assert outcome.exit_code == 0, outcome.output


def test_plan_reports_all_four_sections(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    output = plan(runner, vault).output

    assert "Inject" in output
    assert "Generate" in output
    assert "Acquire" in output
    assert "Create during provisioning" in output


def test_plan_lists_a_held_credential_under_inject(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    output = plan(runner, vault).output
    inject_section = output.split("Generate")[0]

    assert "render_api_key" in inject_section
    assert "RENDER_API_KEY" in inject_section


def test_plan_lists_produced_credentials_under_provisioning(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    output = plan(runner, vault).output
    provisioning = output.split("Create during provisioning")[1]

    assert "database_url" in provisioning
    assert "render_service_id" in provisioning


def test_plan_prints_acquire_instructions_inline(
    runner: CliRunner, tmp_path: Path, offline: None
) -> None:
    """The point of the acquire field: never look the steps up twice."""
    empty = tmp_path / "vault.yml"
    empty.write_text(EMPTY_VAULT, encoding="utf-8")

    output = plan(runner, empty).output

    assert "render_api_key" in output
    assert "dashboard.render.com" in output


def test_plan_names_the_vault_path_it_read(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    output = plan(runner, vault).output

    assert str(vault) in output
    assert "no decryption" in output


def test_plan_prints_no_ciphertext_and_no_values(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    output = plan(runner, vault).output

    assert "ENC[" not in output
    assert "AES256_GCM" not in output


def test_plan_writes_nothing(
    runner: CliRunner, vault: Path, tmp_path: Path, offline: None
) -> None:
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    before = {p: digest(p) for p in tmp_path.iterdir() if p.is_file()}
    repo_before = {p.name for p in REPO_ROOT.iterdir()}

    plan(runner, vault)

    assert {p: digest(p) for p in tmp_path.iterdir() if p.is_file()} == before
    assert {p.name for p in REPO_ROOT.iterdir()} == repo_before


def test_plan_is_deterministic(runner: CliRunner, vault: Path, offline: None) -> None:
    assert plan(runner, vault).output == plan(runner, vault).output


def test_plan_takes_the_vault_from_the_environment(
    runner: CliRunner, vault: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    monkeypatch.setenv("LOFTLINE_VAULT", str(vault))

    outcome = runner.invoke(app, ["plan", str(SPEC), "--credentials", str(CREDENTIALS)])

    assert outcome.exit_code == 0, outcome.output


def test_plan_without_a_vault_configured_fails_with_advice(
    runner: CliRunner, offline: None
) -> None:
    outcome = runner.invoke(app, ["plan", str(SPEC), "--credentials", str(CREDENTIALS)])

    assert outcome.exit_code != 0
    assert "LOFTLINE_VAULT" in outcome.output


def test_plan_fails_loudly_on_a_credential_with_no_descriptor(
    runner: CliRunner, vault: Path, tmp_path: Path, offline: None
) -> None:
    """A descriptor file that has never heard of what the base needs."""
    thin = tmp_path / "credentials.yml"
    thin.write_text(
        "render_api_key:\n"
        "  vendor: render\n"
        "  scope: account\n"
        "  state: held\n"
        "  consumed_by: [ci]\n"
        "  environments: [staging, production]\n"
        "  github_secret: RENDER_API_KEY\n"
        "  vault_path: loftline/render/api_key\n",
        encoding="utf-8",
    )

    outcome = runner.invoke(
        app, ["plan", str(SPEC), "--vault", str(vault), "--credentials", str(thin)]
    )

    assert outcome.exit_code != 0
    assert "smtp_user" in outcome.output
    assert "base" in outcome.output
    assert "credentials.yml" in outcome.output


def test_plan_on_a_malformed_spec_names_the_problem(
    runner: CliRunner, vault: Path, tmp_path: Path, offline: None
) -> None:
    spec = tmp_path / "loftline.yml"
    spec.write_text("project_name: beerreel\n", encoding="utf-8")

    outcome = runner.invoke(
        app,
        ["plan", str(spec), "--vault", str(vault), "--credentials", str(CREDENTIALS)],
    )

    assert outcome.exit_code != 0
    assert "package_name" in outcome.output


# --- vault list --------------------------------------------------------------


def test_vault_list_prints_paths(runner: CliRunner, vault: Path, offline: None) -> None:
    """The Step 1 gate."""
    outcome = runner.invoke(app, ["vault", "list", "--vault", str(vault)])

    assert outcome.exit_code == 0
    assert "loftline/render/api_key" in outcome.output


def test_vault_list_prints_no_values(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    outcome = runner.invoke(app, ["vault", "list", "--vault", str(vault)])

    assert "ENC[" not in outcome.output


def test_there_is_no_command_that_prints_a_secret(runner: CliRunner) -> None:
    """Invariant 1 forbids it, so `vault get` deliberately does not exist."""
    outcome = runner.invoke(app, ["vault", "get", "loftline/render/api_key"])

    assert outcome.exit_code != 0


# --- doctor ------------------------------------------------------------------


def test_doctor_reports_each_precondition(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    outcome = runner.invoke(app, ["doctor", "--vault", str(vault)])

    assert "sops" in outcome.output
    assert "age key" in outcome.output
    assert "full-disk encryption" in outcome.output


def test_doctor_exits_non_zero_when_a_precondition_fails(
    runner: CliRunner, vault: Path, offline: None
) -> None:
    outcome = runner.invoke(app, ["doctor", "--vault", str(vault)])

    assert outcome.exit_code != 0
