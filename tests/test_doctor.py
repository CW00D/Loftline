"""Precondition tests.

`loftline doctor` enforces the operational preconditions in docs/credentials.md.
The gate is per capability: reading the path index needs nothing but the file,
so `plan` runs on a machine with no age key at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loftline.doctor import (
    Capability,
    Status,
    VaultConfig,
    default_age_key_file,
    require,
    run_checks,
)
from loftline.errors import PreconditionError

VAULT = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v]
"""

SOPS_CONFIG_TWO = """\
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: age1primary,age1backup
"""

SOPS_CONFIG_ONE = """\
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: age1primary
"""


@pytest.fixture
def config(tmp_path: Path) -> VaultConfig:
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT, encoding="utf-8")
    sops_config = tmp_path / ".sops.yaml"
    sops_config.write_text(SOPS_CONFIG_TWO, encoding="utf-8")
    key = tmp_path / "keys.txt"
    key.write_text("AGE-SECRET-KEY-PLACEHOLDER\n", encoding="utf-8")
    if os.name == "posix":
        key.chmod(0o600)
    return VaultConfig(vault_path=vault, sops_config=sops_config, age_key_file=key)


def result(results: tuple[object, ...], name: str) -> object:
    return next(r for r in results if name in r.name)  # type: ignore[attr-defined]


@pytest.fixture
def tools_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def encrypted_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption",
        lambda: (Status.PASS, "BitLocker protection on"),
    )


# --- individual checks -------------------------------------------------------


def test_a_healthy_machine_passes_everything(
    config: VaultConfig, tools_present: None, encrypted_disk: None
) -> None:
    results = run_checks(config)

    assert all(r.status is Status.PASS for r in results), [
        (r.name, r.detail) for r in results if r.status is not Status.PASS
    ]


def test_a_missing_vault_file_fails_and_names_the_path(
    config: VaultConfig, tools_present: None
) -> None:
    assert config.vault_path is not None
    config.vault_path.unlink()

    check = result(run_checks(config), "vault file")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]
    assert "vault.yml" in check.detail  # type: ignore[attr-defined]


def test_an_unconfigured_vault_path_fails_with_the_env_var_named(
    tmp_path: Path, tools_present: None
) -> None:
    config = VaultConfig(vault_path=None, sops_config=None, age_key_file=tmp_path / "k")

    check = result(run_checks(config), "vault file")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]
    assert "LOFTLINE_VAULT" in check.detail  # type: ignore[attr-defined]


def test_missing_sops_binary_fails(
    config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: None if name == "sops" else "/usr/bin/age"
    )

    check = result(run_checks(config), "sops")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]


def test_missing_age_binary_fails(
    config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: None if name == "age" else "/usr/bin/sops"
    )

    check = result(run_checks(config), "age")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]


def test_a_missing_age_key_fails(config: VaultConfig, tools_present: None) -> None:
    config.age_key_file.unlink()

    check = result(run_checks(config), "age key")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_world_readable_age_key_fails(
    config: VaultConfig, tools_present: None
) -> None:
    config.age_key_file.chmod(0o644)

    check = result(run_checks(config), "age key")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]


def test_a_single_recipient_fails_because_key_loss_is_unrecoverable(
    config: VaultConfig, tools_present: None
) -> None:
    config.sops_config.write_text(SOPS_CONFIG_ONE, encoding="utf-8")  # type: ignore[union-attr]

    check = result(run_checks(config), "recipients")

    assert check.status is Status.FAIL  # type: ignore[attr-defined]
    assert "two" in check.detail.lower()  # type: ignore[attr-defined]


def test_recipients_may_be_written_as_a_list(
    config: VaultConfig, tools_present: None
) -> None:
    config.sops_config.write_text(  # type: ignore[union-attr]
        "creation_rules:\n"
        "  - path_regex: vault\\.ya?ml$\n"
        "    age:\n      - age1primary\n      - age1backup\n",
        encoding="utf-8",
    )

    check = result(run_checks(config), "recipients")

    assert check.status is Status.PASS  # type: ignore[attr-defined]


def test_full_disk_encryption_that_cannot_be_determined_is_reported_as_unknown(
    config: VaultConfig, tools_present: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption",
        lambda: (Status.UNKNOWN, "could not query BitLocker without elevation"),
    )

    check = result(run_checks(config), "full-disk encryption")

    assert check.status is Status.UNKNOWN  # type: ignore[attr-defined]


def test_full_disk_encryption_check_does_not_raise_when_the_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loftline import doctor

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("no such tool")

    monkeypatch.setattr(subprocess, "run", boom)

    status, detail = doctor._full_disk_encryption()

    assert status in (Status.UNKNOWN, Status.FAIL)
    assert detail


# --- the gate ----------------------------------------------------------------


def test_reading_the_index_needs_no_key_and_no_binaries(
    config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why `plan` works on a machine that cannot decrypt anything."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    config.age_key_file.unlink()

    require(config, Capability.READ_INDEX)


def test_decrypting_without_sops_is_refused_before_anything_is_attempted(
    config: VaultConfig, monkeypatch: pytest.MonkeyPatch, encrypted_disk: None
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(PreconditionError) as excinfo:
        require(config, Capability.DECRYPT)

    assert "sops" in str(excinfo.value)
    assert "loftline doctor" in str(excinfo.value)


def test_writing_requires_a_backup_recipient(
    config: VaultConfig, tools_present: None, encrypted_disk: None
) -> None:
    config.sops_config.write_text(SOPS_CONFIG_ONE, encoding="utf-8")  # type: ignore[union-attr]

    require(config, Capability.DECRYPT)

    with pytest.raises(PreconditionError, match="recipients"):
        require(config, Capability.WRITE)


def test_an_unknown_result_does_not_block(
    config: VaultConfig, tools_present: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence of proof is reported loudly, but it is not proof of absence."""
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption",
        lambda: (Status.UNKNOWN, "unknown"),
    )

    require(config, Capability.DECRYPT)


def test_a_failing_gate_lists_every_failure_not_just_the_first(
    config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    config.age_key_file.unlink()

    with pytest.raises(PreconditionError) as excinfo:
        require(config, Capability.DECRYPT)

    message = str(excinfo.value)
    assert "sops" in message and "age" in message and "age key" in message


# --- configuration -----------------------------------------------------------


def test_vault_path_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOFTLINE_VAULT", str(tmp_path / "vault.yml"))

    assert VaultConfig.from_env().vault_path == tmp_path / "vault.yml"


def test_an_explicit_vault_path_beats_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOFTLINE_VAULT", str(tmp_path / "from-env.yml"))

    config = VaultConfig.from_env(vault_path=tmp_path / "explicit.yml")

    assert config.vault_path == tmp_path / "explicit.yml"


def test_the_age_key_file_defaults_to_the_sops_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)

    assert VaultConfig.from_env().age_key_file.parts[-3:] == ("sops", "age", "keys.txt")


def test_sops_age_key_file_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(tmp_path / "keys.txt"))

    assert VaultConfig.from_env().age_key_file == tmp_path / "keys.txt"


def test_the_sops_config_is_looked_for_beside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT, encoding="utf-8")
    (tmp_path / ".sops.yaml").write_text(SOPS_CONFIG_TWO, encoding="utf-8")

    assert VaultConfig.from_env(vault_path=vault).sops_config == tmp_path / ".sops.yaml"


# --- where sops actually looks for the age key -------------------------------


def test_the_default_key_location_follows_sops_on_windows() -> None:
    """sops uses Go's os.UserConfigDir, which is %AppData% on Windows.

    Defaulting to the Linux path here reported a healthy key file that sops
    would never consult, which is a false pass on the one check that matters.
    """
    path = default_age_key_file(
        system="Windows",
        environ={"APPDATA": r"C:\Users\chris\AppData\Roaming"},
        home=Path(r"C:\Users\chris"),
    )

    assert path == Path(r"C:\Users\chris\AppData\Roaming\sops\age\keys.txt")


def test_the_default_key_location_follows_sops_on_macos() -> None:
    path = default_age_key_file(system="Darwin", environ={}, home=Path("/Users/chris"))

    assert path == Path("/Users/chris/Library/Application Support/sops/age/keys.txt")


def test_the_default_key_location_follows_sops_on_linux() -> None:
    path = default_age_key_file(system="Linux", environ={}, home=Path("/home/chris"))

    assert path == Path("/home/chris/.config/sops/age/keys.txt")


def test_xdg_config_home_is_honoured_on_linux() -> None:
    path = default_age_key_file(
        system="Linux",
        environ={"XDG_CONFIG_HOME": "/home/chris/elsewhere"},
        home=Path("/home/chris"),
    )

    assert path == Path("/home/chris/elsewhere/sops/age/keys.txt")


def test_windows_without_appdata_falls_back_to_the_profile() -> None:
    path = default_age_key_file(
        system="Windows", environ={}, home=Path(r"C:\Users\chris")
    )

    assert path == Path(r"C:\Users\chris\AppData\Roaming\sops\age\keys.txt")
