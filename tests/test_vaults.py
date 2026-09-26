"""Which vault a project uses, and moving credentials between vaults (ADR-035).

A project's vault is its owner's. These tests pin the selection rule, the
registry that finds an organisation's vault, and `vault copy`, which must move
a value between two encrypted files without ever showing it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loftline.cli import app
from loftline.doctor import Status
from loftline.errors import VaultError
from loftline.models import load_descriptors, load_spec
from loftline.report import render_plan
from loftline.resolve import resolve
from loftline.site import credentials_plan
from loftline.vault import VaultIndex
from loftline.vault_sops import SopsAgeVault
from loftline.vaults import (
    copy_credentials,
    load_registry,
    org_vault_path,
    register_org_vault,
    vault_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = REPO_ROOT / "credentials.yml"

PERSONAL = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
    smtp:
        user:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
sops:
    version: 3.9.0
"""

ORG = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-09-01T10:04:00Z"
            rotate_from: personal
sops:
    version: 3.9.0
"""

SOPS_CONFIG = """\
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: age1primary,age1backup
"""


def make_vault(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".sops.yaml").write_text(SOPS_CONFIG, encoding="utf-8")
    vault = directory / "vault.yml"
    vault.write_text(text, encoding="utf-8")
    return vault


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "registry" / "vaults.yml"
    monkeypatch.setenv("LOFTLINE_VAULTS", str(path))
    return path


@pytest.fixture
def personal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = make_vault(tmp_path / "personal", PERSONAL)
    monkeypatch.setenv("LOFTLINE_VAULT", str(vault))
    return vault


@pytest.fixture
def acme(tmp_path: Path, registry: Path) -> Path:
    vault = make_vault(tmp_path / "acme", "loftline: {}\nsops:\n    version: 3.9.0\n")
    register_org_vault("acme", vault, registry)
    return vault


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Passes the decrypt and write gates; sops is a recorder that 'decrypts'
    every path to a fixed string."""
    key = tmp_path / "keys.txt"
    key.write_text("AGE-SECRET-KEY-PLACEHOLDER\n", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption", lambda: (Status.PASS, "on")
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        out = "rnd_secret_value\n" if argv[1:2] == ["-d"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# --- the registry --------------------------------------------------------------


def test_an_absent_registry_is_empty_and_registering_creates_it(registry: Path) -> None:
    assert load_registry(registry) == {}

    register_org_vault("acme", Path("some/where/vault.yml"), registry)

    assert registry.exists()
    assert list(load_registry(registry)) == ["acme"]
    assert load_registry(registry)["acme"].name == "vault.yml"


def test_an_unregistered_organisation_says_how_to_register(registry: Path) -> None:
    with pytest.raises(VaultError) as failure:
        org_vault_path("acme", registry)

    assert "loftline vault init" in str(failure.value)
    assert "--org acme" in str(failure.value)
    assert "loftline vault register" in str(failure.value)


# --- the selection rule ----------------------------------------------------------


def test_a_personal_project_uses_the_personal_vault(
    personal: Path, registry: Path
) -> None:
    assert vault_config(None, None).vault_path == personal


def test_an_organisations_project_uses_only_its_vault(
    personal: Path, acme: Path, registry: Path
) -> None:
    assert vault_config(None, "acme", registry=registry).vault_path == acme


def test_an_explicit_vault_path_wins(
    personal: Path, acme: Path, registry: Path
) -> None:
    other = personal.parent / "other.yml"
    assert vault_config(other, "acme", registry=registry).vault_path == other


# --- the rotation mark -------------------------------------------------------------


def test_a_copied_value_is_indexed_as_pending_rotation(tmp_path: Path) -> None:
    index = SopsAgeVault(make_vault(tmp_path / "v", ORG)).index()

    assert index.rotate_from("loftline/render/api_key") == "personal"
    assert (
        VaultIndex.from_paths(["loftline/render/api_key"]).rotate_from(
            "loftline/render/api_key"
        )
        is None
    )


def test_the_plan_and_the_dashboard_say_a_copy_needs_reissuing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path / "v", ORG)
    spec = load_spec(REPO_ROOT / "examples" / "shop.yml")
    index = SopsAgeVault(vault).index()
    resolution = resolve(spec, load_descriptors(CREDENTIALS), index)

    text = render_plan(spec, resolution, vault, Path("shop.yml"), len(index), index)
    plan = credentials_plan(resolution, index)

    assert "copied from personal; reissue" in text
    held = next(p for p in plan if p["name"] == "render_api_key")
    assert held["rotate_from"] == "personal"
    assert held["command"] == "loftline vault set render_api_key --replace"
    assert "rnd" not in text and not any("value" in p for p in plan)


def test_copying_marks_the_copy_and_refuses_to_overwrite(
    tmp_path: Path, machine: list[list[str]]
) -> None:
    source = SopsAgeVault(make_vault(tmp_path / "p", PERSONAL))
    target = SopsAgeVault(make_vault(tmp_path / "o", ORG))

    copied = copy_credentials(
        source, target, ["loftline/smtp/user"], mark_from="personal"
    )

    assert copied == ["loftline/smtp/user"]
    sops_set = next(c for c in machine if c[1:2] == ["set"])
    assert '"rotate_from": "personal"' in sops_set[4]
    assert '"value": "rnd_secret_value"' in sops_set[4]
    with pytest.raises(VaultError, match="--replace"):
        copy_credentials(
            source, target, ["loftline/render/api_key"], mark_from="personal"
        )


# --- the commands ------------------------------------------------------------------


def test_vault_copy_moves_what_a_spec_holds_and_shows_no_value(
    personal: Path, acme: Path, registry: Path, machine: list[list[str]]
) -> None:
    outcome = CliRunner().invoke(
        app,
        [
            "vault",
            "copy",
            "--to",
            "acme",
            "--spec",
            str(REPO_ROOT / "examples" / "shop.yml"),
            "--credentials",
            str(CREDENTIALS),
        ],
    )

    assert outcome.exit_code == 0, outcome.output
    assert "loftline/render/api_key" in outcome.output
    assert "loftline/smtp/user" not in outcome.output  # the shop spec has no email
    assert "rnd_secret_value" not in outcome.output
    assert "--org acme --replace" in outcome.output
    written = [c for c in machine if c[1:2] == ["set"]]
    assert all(c[2] == str(acme) for c in written)
    decrypted = [c for c in machine if c[1:2] == ["-d"]]
    assert all(c[-1] == str(personal) for c in decrypted)


def test_vault_copy_needs_a_name_or_a_spec(personal: Path, acme: Path) -> None:
    outcome = CliRunner().invoke(app, ["vault", "copy", "--to", "acme"])

    assert outcome.exit_code == 1
    assert "--spec" in outcome.output


def test_vault_set_with_org_writes_the_organisations_vault(
    personal: Path, acme: Path, registry: Path, machine: list[list[str]]
) -> None:
    outcome = CliRunner().invoke(
        app,
        [
            "vault",
            "set",
            "smtp_user",
            "--org",
            "acme",
            "--credentials",
            str(CREDENTIALS),
        ],
        input="someone@example.org\n",
    )

    assert outcome.exit_code == 0, outcome.output
    sops_set = next(c for c in machine if c[1:2] == ["set"])
    assert sops_set[2] == str(acme)
    assert "organisation vault for acme" in outcome.output
    assert "someone@example.org" not in outcome.output


def test_vault_init_with_org_registers_the_new_vault(
    tmp_path: Path, registry: Path, machine: list[list[str]]
) -> None:
    outcome = CliRunner().invoke(
        app,
        [
            "vault",
            "init",
            str(tmp_path / "acme-vault"),
            "--recipient",
            "age1one",
            "--recipient",
            "age1two",
            "--org",
            "acme",
        ],
    )

    assert outcome.exit_code == 0, outcome.output
    assert (
        load_registry(registry)["acme"]
        == (tmp_path / "acme-vault" / "vault.yml").resolve()
    )
    assert "--org acme" in outcome.output


def test_vault_register_records_an_existing_file(
    tmp_path: Path, registry: Path
) -> None:
    vault = make_vault(tmp_path / "cloned", PERSONAL)

    outcome = CliRunner().invoke(app, ["vault", "register", "acme", str(vault)])

    assert outcome.exit_code == 0, outcome.output
    assert load_registry(registry)["acme"] == vault.resolve()
    missing = CliRunner().invoke(
        app, ["vault", "register", "beta", str(tmp_path / "nowhere.yml")]
    )
    assert missing.exit_code == 1
