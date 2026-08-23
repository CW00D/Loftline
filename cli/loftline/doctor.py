"""Operational preconditions.

The preconditions listed in credentials.md, checked before anything is
attempted rather than discovered halfway through. The gate is per capability:

    read-index   the vault file resolves and parses
    decrypt      read-index, plus sops, age, a key file and an encrypted disk
    write        decrypt, plus a second recipient in .sops.yaml

`plan` needs only `read-index`, which is why it runs on a machine with no age
key at all. Nothing it does requires decrypting anything.

A check reports PASS, FAIL or UNKNOWN. UNKNOWN is not a pass and not a
blocker: it is reported loudly and lets the command proceed, because absence of
proof is not proof of absence.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from .errors import PreconditionError

VAULT_ENV = "LOFTLINE_VAULT"
KEY_ENV = "SOPS_AGE_KEY_FILE"


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Capability(Enum):
    READ_INDEX = "read-index"
    DECRYPT = "decrypt"
    WRITE = "write"

    @property
    def level(self) -> int:
        return {"read-index": 0, "decrypt": 1, "write": 2}[self.value]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    capability: Capability


def default_age_key_file(
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Where sops looks for the age key, per platform.

    sops uses Go's `os.UserConfigDir`, which is not `~/.config` everywhere.
    Getting this wrong reports a healthy key file that sops never consults,
    which is a false pass on the check that matters most.
    """
    system = system if system is not None else platform.system()
    environ = environ if environ is not None else os.environ
    home = home if home is not None else Path.home()

    if system == "Windows":
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        xdg = environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else home / ".config"

    return base / "sops" / "age" / "keys.txt"


@dataclass(frozen=True)
class VaultConfig:
    """Where the vault, its SOPS configuration and the age key live."""

    vault_path: Path | None
    sops_config: Path | None
    age_key_file: Path

    @classmethod
    def from_env(cls, vault_path: Path | None = None) -> VaultConfig:
        resolved = vault_path
        if resolved is None:
            from_env = os.environ.get(VAULT_ENV)
            resolved = Path(from_env) if from_env else None

        key = os.environ.get(KEY_ENV)
        age_key_file = Path(key) if key else default_age_key_file()

        sops_config: Path | None = None
        if resolved is not None:
            candidate = resolved.parent / ".sops.yaml"
            sops_config = candidate if candidate.exists() else None

        return cls(
            vault_path=resolved, sops_config=sops_config, age_key_file=age_key_file
        )


# --- the checks --------------------------------------------------------------


def _check_vault_file(config: VaultConfig) -> CheckResult:
    name = "vault file resolves and parses"
    if config.vault_path is None:
        return CheckResult(
            name,
            Status.FAIL,
            f"no vault configured. Set {VAULT_ENV} or pass --vault.",
            Capability.READ_INDEX,
        )
    if not config.vault_path.exists():
        return CheckResult(
            name,
            Status.FAIL,
            f"{config.vault_path} does not exist",
            Capability.READ_INDEX,
        )
    try:
        yaml.safe_load(config.vault_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(
            name,
            Status.FAIL,
            f"{config.vault_path} could not be parsed: {exc}",
            Capability.READ_INDEX,
        )
    return CheckResult(name, Status.PASS, str(config.vault_path), Capability.READ_INDEX)


def _check_binary(binary: str) -> Callable[[VaultConfig], CheckResult]:
    def check(_config: VaultConfig) -> CheckResult:
        found = shutil.which(binary)
        if found is None:
            return CheckResult(
                f"{binary} on PATH",
                Status.FAIL,
                f"{binary} is not installed or not on PATH",
                Capability.DECRYPT,
            )
        return CheckResult(f"{binary} on PATH", Status.PASS, found, Capability.DECRYPT)

    return check


def _check_age_key(config: VaultConfig) -> CheckResult:
    name = "age key file present and private"
    path = config.age_key_file
    if not path.exists():
        return CheckResult(
            name,
            Status.FAIL,
            f"{path} does not exist. Set {KEY_ENV} if the key lives elsewhere.",
            Capability.DECRYPT,
        )
    if os.name != "posix":
        # Windows has no equivalent of mode 600. The protections that remain
        # are the user profile ACL and full-disk encryption, which is the check
        # below and the reason ADR-011 treats it as a prerequisite.
        return CheckResult(
            name,
            Status.PASS,
            f"{path} (file modes not applicable on {platform.system()})",
            Capability.DECRYPT,
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return CheckResult(
            name,
            Status.FAIL,
            f"{path} is mode {mode:o}, readable beyond you. Run chmod 600 on it.",
            Capability.DECRYPT,
        )
    return CheckResult(name, Status.PASS, str(path), Capability.DECRYPT)


def _check_disk_encryption(_config: VaultConfig) -> CheckResult:
    status, detail = _full_disk_encryption()
    return CheckResult(
        "full-disk encryption enabled", status, detail, Capability.DECRYPT
    )


def _check_recipients(config: VaultConfig) -> CheckResult:
    name = ".sops.yaml lists at least two recipients"
    if config.sops_config is None:
        return CheckResult(
            name,
            Status.FAIL,
            "no .sops.yaml found beside the vault file",
            Capability.WRITE,
        )
    try:
        document = yaml.safe_load(config.sops_config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(
            name, Status.FAIL, f"{config.sops_config}: {exc}", Capability.WRITE
        )

    rules = document.get("creation_rules") or []
    if not rules:
        return CheckResult(
            name,
            Status.FAIL,
            f"{config.sops_config} declares no creation_rules",
            Capability.WRITE,
        )
    for rule in rules:
        recipients = _recipients(rule.get("age"))
        if len(recipients) < 2:
            return CheckResult(
                name,
                Status.FAIL,
                f"{config.sops_config} encrypts to {len(recipients)} recipient(s). "
                "Encrypt to two: losing the sole key destroys the vault with no "
                "recovery path.",
                Capability.WRITE,
            )
    return CheckResult(name, Status.PASS, str(config.sops_config), Capability.WRITE)


def _recipients(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


# Each check is paired with the capability it gates, so a command asking for
# one capability never runs the checks belonging to a higher one. `plan` asks
# for read-index and therefore never probes for disk encryption or binaries.
CHECKS: tuple[tuple[Capability, Callable[[VaultConfig], CheckResult]], ...] = (
    (Capability.READ_INDEX, _check_vault_file),
    (Capability.DECRYPT, _check_binary("sops")),
    (Capability.DECRYPT, _check_binary("age")),
    (Capability.DECRYPT, _check_age_key),
    (Capability.DECRYPT, _check_disk_encryption),
    (Capability.WRITE, _check_recipients),
)


def _full_disk_encryption() -> tuple[Status, str]:
    """Best effort, per platform. Undeterminable is UNKNOWN, never a silent pass."""
    system = platform.system()
    try:
        if system == "Windows":
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-BitLockerVolume -MountPoint $env:SystemDrive)."
                    "ProtectionStatus",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            answer = completed.stdout.strip()
            if answer == "On":
                return Status.PASS, "BitLocker protection is on for the system drive"
            if answer == "Off":
                return Status.FAIL, "BitLocker is off for the system drive"
            return Status.UNKNOWN, (
                "could not determine BitLocker status; querying it needs an "
                "elevated shell. Run `manage-bde -status C:` as Administrator. "
                "Windows 11 Home reports this as Device encryption, in Settings, "
                "Privacy and security."
            )

        if system == "Darwin":
            completed = subprocess.run(
                ["fdesetup", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if "FileVault is On" in completed.stdout:
                return Status.PASS, "FileVault is on"
            if "FileVault is Off" in completed.stdout:
                return Status.FAIL, "FileVault is off"
            return Status.UNKNOWN, "could not determine FileVault status"

        completed = subprocess.run(
            ["lsblk", "-o", "TYPE"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if "crypt" in completed.stdout:
            return Status.PASS, "an encrypted block device is present"
        return Status.UNKNOWN, "no encrypted block device found by lsblk; check by hand"
    except Exception as exc:
        return Status.UNKNOWN, f"could not probe disk encryption: {exc}"


# --- running and gating ------------------------------------------------------


def run_checks(
    config: VaultConfig, capability: Capability | None = None
) -> tuple[CheckResult, ...]:
    """Run every check, or only those needed for one capability."""
    return tuple(
        check(config)
        for gates, check in CHECKS
        if capability is None or gates.level <= capability.level
    )


def require(config: VaultConfig, capability: Capability) -> tuple[CheckResult, ...]:
    """Refuse to continue unless every precondition for `capability` holds."""
    results = run_checks(config, capability)
    failures = [r for r in results if r.status is Status.FAIL]
    if failures:
        lines = "\n".join(f"  {r.name}: {r.detail}" for r in failures)
        raise PreconditionError(
            f"{len(failures)} precondition(s) not met for {capability.value}:\n"
            f"{lines}\n\nRun `loftline doctor` for the full picture."
        )
    return results
