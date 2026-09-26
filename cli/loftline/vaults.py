"""Which vault a project's credentials live in (ADR-035).

The personal vault is `LOFTLINE_VAULT`, made by `loftline setup`. An
organisation's vault is a second SOPS file in its own repository; a personal
project may also opt into a vault of its own. Both kinds are registered on
this machine by name, so `--org <slug>` finds the organisation's and a
project's name finds its own.

The rule: an organisation's project uses the organisation's vault and nothing
else. A personal project uses its own vault if this machine has one
registered for it, otherwise the personal vault. The choice is made when the
project is defined, defaults to the personal vault, and is recorded on the
dashboard; `realise` checks the machine agrees with it.

The registry is a small YAML file mapping names to vault paths. It holds
locations, never values, and it is not the vault: losing it means
re-registering, nothing more.

Moving a project between owners switches which vault it uses. The credentials
it needs can be stored afresh, or copied across with `loftline vault copy`,
which marks each copy as pending rotation: a value two vaults hold should
become a value one vault holds alone.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from .doctor import VaultConfig
from .errors import VaultError
from .vault_sops import SopsAgeVault

REGISTRY_ENV = "LOFTLINE_VAULTS"
KINDS = ("org", "project")
_SECTION = {"org": "orgs", "project": "projects"}


def registry_path(environ: Mapping[str, str] | None = None) -> Path:
    """Where this machine records its organisation and project vaults."""
    environ = os.environ if environ is None else environ
    override = environ.get(REGISTRY_ENV)
    if override:
        return Path(override)
    return Path.home() / ".loftline" / "vaults.yml"


def load_registry(path: Path | None = None) -> dict[str, dict[str, Path]]:
    """`{"org": {slug: path}, "project": {name: path}}`. Absent file: empty."""
    path = path or registry_path()
    registry: dict[str, dict[str, Path]] = {kind: {} for kind in KINDS}
    if not path.exists():
        return registry
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise VaultError(f"the vault registry {path} could not be read: {exc}") from exc
    if not isinstance(document, dict):
        raise VaultError(f"the vault registry {path} must be a mapping")
    for kind, section in _SECTION.items():
        entries = document.get(section, {}) or {}
        if not isinstance(entries, dict):
            raise VaultError(
                f"the vault registry {path} must hold a mapping under '{section}'"
            )
        registry[kind] = {str(k): Path(str(v)) for k, v in entries.items()}
    return registry


def register_vault(kind: str, name: str, vault: Path, path: Path | None = None) -> Path:
    """Record that the `kind` vault called `name` is the file at `vault`."""
    if kind not in KINDS:
        raise VaultError(f"a vault is registered as one of {', '.join(KINDS)}")
    path = path or registry_path()
    registry = load_registry(path)
    registry[kind][name] = Path(vault).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        section: {n: str(p) for n, p in sorted(registry[kind].items())}
        for kind, section in _SECTION.items()
        if registry[kind]
    }
    path.write_text(
        "# Where each organisation's and project's vault lives on this machine. "
        "Locations only.\n" + yaml.safe_dump(body, sort_keys=False),
        encoding="utf-8",
    )
    return path


def register_org_vault(slug: str, vault: Path, path: Path | None = None) -> Path:
    return register_vault("org", slug, vault, path)


def org_vault_path(slug: str, path: Path | None = None) -> Path:
    registry = load_registry(path)["org"]
    try:
        return registry[slug]
    except KeyError:
        known = ", ".join(sorted(registry)) or "none"
        raise VaultError(
            f"no vault is registered for the organisation {slug} on this machine "
            f"(registered: {known}). Create one with "
            f"`loftline vault init <directory> --org {slug} --recipient ... "
            "--recipient ...`, or register an existing one with "
            f"`loftline vault register <path to its vault.yml> --org {slug}`."
        ) from None


def project_vault_path(name: str, path: Path | None = None) -> Path | None:
    """The project's own vault, if this machine has one registered."""
    return load_registry(path)["project"].get(name)


def require_project_vault(name: str, path: Path | None = None) -> Path:
    found = project_vault_path(name, path)
    if found is None:
        raise VaultError(
            f"the dashboard says {name} has a vault of its own, but none is "
            f"registered on this machine. Create it with `loftline vault init "
            f"<directory> --project {name} --recipient ... --recipient ...`, or "
            f"register a clone with `loftline vault register <path to its "
            f"vault.yml> --project {name}`."
        )
    return found


@dataclass(frozen=True)
class Choice:
    """Which vault applies, and why, for a message and for the dashboard."""

    config: VaultConfig
    kind: str  # personal | organisation | project | explicit
    name: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "organisation":
            return f"the organisation vault for {self.name}"
        if self.kind == "project":
            return f"the project vault for {self.name}"
        if self.kind == "explicit":
            return f"the vault at {self.config.vault_path}"
        return "your personal vault"

    @property
    def mark(self) -> str:
        """How a copy from this vault is labelled in the receiving one."""
        if self.kind == "organisation":
            return str(self.name)
        if self.kind == "project":
            return f"project:{self.name}"
        return "personal"


def choose(
    vault: Path | None,
    org: str | None,
    project: str | None = None,
    *,
    registry: Path | None = None,
) -> Choice:
    """The vault a command should use.

    An explicit `--vault` wins. An organisation's project uses the
    organisation's registered vault. A personal project uses its own vault if
    one is registered for it here, otherwise the personal vault.
    """
    if vault is not None:
        return Choice(VaultConfig.from_env(vault), "explicit")
    if org:
        return Choice(
            VaultConfig.from_env(org_vault_path(org, registry)), "organisation", org
        )
    if project:
        own = project_vault_path(project, registry)
        if own is not None:
            return Choice(VaultConfig.from_env(own), "project", project)
    return Choice(VaultConfig.from_env(), "personal")


def vault_config(
    vault: Path | None,
    org: str | None,
    project: str | None = None,
    *,
    registry: Path | None = None,
) -> VaultConfig:
    return choose(vault, org, project, registry=registry).config


def vault_label(org: str | None, project: str | None = None) -> str:
    return (
        choose(None, org, project).label if (org or project) else "your personal vault"
    )


def parse_target(text: str) -> tuple[str | None, str | None]:
    """`acme` is an organisation; `project:shop` is a project's own vault;
    `personal` is the personal vault. Returns (org, project)."""
    if text == "personal":
        return None, None
    if text.startswith("project:"):
        name = text[len("project:") :]
        if not name:
            raise VaultError("project: needs a project name, as in project:shop")
        return None, name
    if text.startswith("org:"):
        return text[len("org:") :], None
    return text, None


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
