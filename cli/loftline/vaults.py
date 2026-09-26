"""Which vault a project's credentials live in (ADR-035).

Two kinds of vault. The personal one is `LOFTLINE_VAULT`, made by
`loftline setup`. An organisation's vault is a second SOPS file in its own
repository, registered on this machine by slug. A project's vault is its
owner's: an organisation's project reads and writes the organisation's vault
and nothing else; a personal project uses the personal vault. There is no
question to ask and no fallback between the two.

The registry is a small YAML file mapping organisation slugs to vault paths.
It holds locations, never values, and it is not the vault: losing it means
re-registering, nothing more.

Moving a project into an organisation switches which vault it uses. The
credentials it needs can be stored afresh in the organisation's vault, or
copied across from the personal one with `loftline vault copy`, which marks
each copy as pending rotation: a value two vaults hold should become a value
the organisation holds alone.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

from .doctor import VaultConfig
from .errors import VaultError
from .vault_sops import SopsAgeVault

REGISTRY_ENV = "LOFTLINE_VAULTS"
ORGS_KEY = "orgs"


def registry_path(environ: Mapping[str, str] | None = None) -> Path:
    """Where this machine records its organisation vaults."""
    environ = os.environ if environ is None else environ
    override = environ.get(REGISTRY_ENV)
    if override:
        return Path(override)
    return Path.home() / ".loftline" / "vaults.yml"


def load_registry(path: Path | None = None) -> dict[str, Path]:
    """Organisation slug to vault path. An absent file is an empty registry."""
    path = path or registry_path()
    if not path.exists():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise VaultError(f"the vault registry {path} could not be read: {exc}") from exc
    orgs = document.get(ORGS_KEY, {}) if isinstance(document, dict) else None
    if not isinstance(orgs, dict):
        raise VaultError(
            f"the vault registry {path} must hold a mapping under '{ORGS_KEY}'"
        )
    return {str(slug): Path(str(vault)) for slug, vault in orgs.items()}


def register_org_vault(slug: str, vault: Path, path: Path | None = None) -> Path:
    """Record that `slug`'s vault is the file at `vault`. Returns the registry."""
    path = path or registry_path()
    registry = load_registry(path)
    registry[slug] = Path(vault).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Where each organisation's vault lives on this machine. Locations only.\n"
        + yaml.safe_dump(
            {ORGS_KEY: {s: str(p) for s, p in sorted(registry.items())}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def org_vault_path(slug: str, path: Path | None = None) -> Path:
    registry = load_registry(path)
    try:
        return registry[slug]
    except KeyError:
        known = ", ".join(sorted(registry)) or "none"
        raise VaultError(
            f"no vault is registered for the organisation {slug} on this machine "
            f"(registered: {known}). Create one with "
            f"`loftline vault init <directory> --org {slug} --recipient ... "
            "--recipient ...`, or register an existing one with "
            f"`loftline vault register {slug} <path to its vault.yml>`."
        ) from None


def vault_config(
    vault: Path | None,
    org: str | None,
    *,
    registry: Path | None = None,
) -> VaultConfig:
    """The vault a command should use.

    An explicit `--vault` wins. Otherwise an organisation's project uses the
    organisation's registered vault, and anything else uses the personal one.
    """
    if vault is not None:
        return VaultConfig.from_env(vault)
    if org:
        return VaultConfig.from_env(org_vault_path(org, registry))
    return VaultConfig.from_env()


def vault_label(org: str | None) -> str:
    return f"the organisation vault for {org}" if org else "your personal vault"


def copy_credentials(
    source: SopsAgeVault,
    target: SopsAgeVault,
    paths: Iterable[str],
    *,
    mark_from: str,
    replace: bool = False,
) -> list[str]:
    """Copy each path's value from one vault to another, marking the copy as
    pending rotation.

    Each value is decrypted once and handed straight to the target's `set`; it
    is never printed or written in the clear. Refuses to overwrite a value the
    target already holds unless `replace` is set. Returns the paths copied.
    """
    held = set(target.list_paths())
    copied: list[str] = []
    for path in paths:
        if path in held and not replace:
            raise VaultError(
                f"{path} is already in the target vault. Pass --replace to overwrite "
                "it, or leave it and copy the others by name."
            )
        target.set(path, source.get(path), rotate_from=mark_from)
        copied.append(path)
    return copied
