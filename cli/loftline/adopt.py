"""Adopting a project that exists already (ADR-036).

Most of a team's projects predate Loftline. Adoption puts one on the
dashboard, with its collaborators, health and credential plan, without
generating anything or touching how it is deployed.

What adoption reads is the project's own account of itself: the GitHub
environments and the *names* of their secrets (GitHub never returns a value,
so this is names only by construction), the environment variables a
`render.yaml` marks as set by hand, and the keys of a `.env.example`. Each
name is matched against Loftline's descriptors by its GitHub secret name;
the rest get a descriptor of their own in a per-project credentials file.

Nothing switches over by itself. While any credential is missing, Loftline
only observes the project. Once every one is in the vault, `loftline secrets
write` can take over the GitHub environments, and from then on the vault is
the source. Until that command is run, the project keeps working exactly as
it does today.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .errors import LoftlineError
from .models import Descriptor, DescriptorSet, Spec, load_descriptors, load_spec
from .resolve import Defer, Derive, Inject, Request, Resolution, resolve
from .vault import VaultIndex, as_index

PROJECT_FILE = "loftline.yml"
CREDENTIALS_FILE = "loftline.credentials.yml"

SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
# GitHub sets these itself; they are not the project's to hold.
NOT_CREDENTIALS = frozenset({"GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"})

Runner = Callable[..., subprocess.CompletedProcess[str]]


class AdoptError(LoftlineError):
    pass


class Adoption(BaseModel):
    """`loftline.yml` for an adopted project: what it is and where it lives.

    Names and places only. `credentials` maps each GitHub secret name to the
    environments it was found in, so the plan is reproducible without asking
    GitHub again.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    adopted: Literal[True] = True
    project_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,38}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    environments: tuple[str, ...] = Field(min_length=1)
    # Environment name to the URL whose HTTP 200 means "up". Optional: an
    # adopted project has no /health contract of ours.
    health: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, tuple[str, ...]] = Field(default_factory=dict)


def is_adoption(data: Any) -> bool:  # noqa: ANN401 - parsed YAML
    return isinstance(data, dict) and data.get("adopted") is True


def load_project(path: Path) -> Spec | Adoption:
    """A `loftline.yml`, whichever kind it is."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AdoptError(f"{path} could not be read: {exc}") from exc
    return load_adoption(path) if is_adoption(data) else load_spec(path)


def resolve_project(
    project: Spec | Adoption,
    descriptors: DescriptorSet,
    vault_path_index: VaultIndex | Mapping[str, datetime | None] | Iterable[str],
) -> Resolution:
    if isinstance(project, Adoption):
        return resolve_adopted(project, descriptors, vault_path_index)
    return resolve(project, descriptors, vault_path_index)


def load_adoption(path: Path) -> Adoption:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AdoptError(f"{path} could not be read: {exc}") from exc
    try:
        return Adoption.model_validate(data)
    except ValueError as exc:
        raise AdoptError(f"{path} is not a valid adopted project:\n{exc}") from exc


# --- discovery -----------------------------------------------------------------


@dataclass(frozen=True)
class Discovery:
    """What the project says about itself. Names, never values."""

    environments: tuple[str, ...]
    # GitHub secret name to the environments it is set in. Repository-level
    # secrets count for every environment.
    secrets: dict[str, tuple[str, ...]]
    # Where each name was seen, for the report: github, render.yaml, .env.example
    sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def _gh(run: Runner, *args: str) -> list[str]:
    result = run(["gh", "api", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AdoptError(
            f"gh api {' '.join(args)} failed: {result.stderr.strip() or 'no detail'}. "
            "Is `gh auth status` signed in to an account that can see the repository?"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def discover(
    project_dir: Path,
    repository: str,
    *,
    run: Runner = subprocess.run,
) -> Discovery:
    """Read the project's environments and secret names from GitHub and from
    the files it carries."""
    environments = tuple(
        _gh(
            run,
            f"repos/{repository}/environments",
            "--paginate",
            "-q",
            ".environments[].name",
        )
    )
    secrets: dict[str, set[str]] = {}
    sources: dict[str, set[str]] = {}
    notes: list[str] = []

    def seen(name: str, envs: Iterable[str], source: str) -> None:
        if name in NOT_CREDENTIALS or not SECRET_NAME.match(name):
            return
        secrets.setdefault(name, set()).update(envs)
        sources.setdefault(name, set()).add(source)

    for environment in environments:
        for name in _gh(
            run,
            f"repos/{repository}/environments/{environment}/secrets",
            "--paginate",
            "-q",
            ".secrets[].name",
        ):
            seen(name, [environment], "github")
    for name in _gh(
        run,
        f"repos/{repository}/actions/secrets",
        "--paginate",
        "-q",
        ".secrets[].name",
    ):
        seen(name, environments or ("production",), "github")

    render = project_dir / "render.yaml"
    if render.is_file():
        for name in _render_manual_env_vars(render):
            seen(name, environments or ("production",), "render.yaml")
    for candidate in (".env.example", ".env.template", ".env.sample"):
        example = project_dir / candidate
        if example.is_file():
            for name in _env_file_keys(example):
                seen(name, environments or ("production",), candidate)

    if not environments:
        notes.append(
            "the repository has no GitHub environments; secrets are treated as "
            "production's until you add some"
        )
        environments = ("production",)

    return Discovery(
        environments=environments,
        secrets={n: tuple(sorted(e)) for n, e in sorted(secrets.items())},
        sources={n: tuple(sorted(s)) for n, s in sorted(sources.items())},
        notes=tuple(notes),
    )


def _render_manual_env_vars(path: Path) -> list[str]:
    """Environment variables a Render blueprint expects someone to set by hand
    (`sync: false`), which is Render's way of saying "a secret"."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    names: list[str] = []
    for service in document.get("services", []) if isinstance(document, dict) else []:
        for var in service.get("envVars", []) if isinstance(service, dict) else []:
            if isinstance(var, dict) and var.get("sync") is False and "key" in var:
                names.append(str(var["key"]))
    return names


def _env_file_keys(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].removeprefix("export ").strip()
        names.append(key)
    return names


# --- matching ------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    known: dict[str, Descriptor]  # secret name -> Loftline's descriptor
    new: dict[str, Descriptor]  # secret name -> descriptor written for this project


def match(
    project_name: str,
    secrets: Mapping[str, tuple[str, ...]],
    descriptors: DescriptorSet,
) -> Match:
    """Pair each secret name with Loftline's descriptor of the same GitHub
    secret, or write a project descriptor for it."""
    by_secret = {d.github_secret: d for d in descriptors.entries.values()}
    known: dict[str, Descriptor] = {}
    new: dict[str, Descriptor] = {}
    for name, environments in secrets.items():
        if name in by_secret:
            known[name] = by_secret[name]
            continue
        lowered = name.lower()
        new[name] = Descriptor(
            name=lowered,
            vendor="unknown",
            scope="project",
            state="manual",
            consumed_by=("api",),
            environments=environments,
            github_secret=name,
            vault_path=f"{project_name}/adopted/{lowered}",
            acquire=(
                f"{name} was set by hand before Loftline adopted this project. "
                "GitHub cannot show its value; copy it from the vendor's dashboard "
                "or wherever it was kept, then store it here. Edit vendor and "
                f"acquire in {CREDENTIALS_FILE} once you know them."
            ),
        )
    return Match(known=known, new=new)


def write_project_descriptors(path: Path, new: Mapping[str, Descriptor]) -> None:
    body = {
        d.name: {
            "vendor": d.vendor,
            "scope": d.scope,
            "state": d.state,
            "consumed_by": list(d.consumed_by),
            "environments": list(d.environments),
            "github_secret": d.github_secret,
            "vault_path": d.vault_path,
            "acquire": d.acquire,
        }
        for d in new.values()
    }
    path.write_text(
        "# Credentials this project needs that Loftline has no descriptor for.\n"
        "# Written by `loftline adopt`; edit vendor and acquire as you learn them.\n"
        "# Descriptors only: names, places and instructions. Never a value.\n"
        + yaml.safe_dump(body, sort_keys=True),
        encoding="utf-8",
    )


def project_descriptors(base: DescriptorSet, project_dir: Path | None) -> DescriptorSet:
    """Loftline's descriptors plus the project's own, if it has a file of them."""
    if project_dir is None:
        return base
    extra = project_dir / CREDENTIALS_FILE
    if not extra.is_file():
        return base
    local = load_descriptors(extra)
    clash = sorted(set(local.entries) & set(base.entries))
    if clash:
        raise AdoptError(
            f"{extra} redefines {', '.join(clash)}, which Loftline already describes. "
            "Remove them from the project file; the shared descriptor applies."
        )
    return DescriptorSet(entries={**base.entries, **local.entries})


# --- resolution ----------------------------------------------------------------


def resolve_adopted(
    adoption: Adoption,
    descriptors: DescriptorSet,
    vault_path_index: VaultIndex | Mapping[str, datetime | None] | Iterable[str],
    *,
    now: datetime | None = None,
) -> Resolution:
    """The same partition `resolve` produces, for a project whose wanted
    credentials come from what it already has rather than from features."""
    index = as_index(vault_path_index)
    now = now or datetime.now(UTC)
    by_secret = {d.github_secret: d for d in descriptors.entries.values()}
    inject: list[Inject] = []
    derive: list[Derive] = []
    request: list[Request] = []
    defer: list[Defer] = []
    for name, environments in sorted(adoption.credentials.items()):
        descriptor = by_secret.get(name)
        if descriptor is None:
            raise AdoptError(
                f"{adoption.project_name} needs {name} but no descriptor describes it. "
                f"Run `loftline adopt` again, or add one to {CREDENTIALS_FILE}."
            )
        if descriptor.state == "derivable":
            derive.append(
                Derive(descriptor.name, descriptor.derivation or "", name, environments)
            )
        elif descriptor.state == "produced":
            defer.append(
                Defer(descriptor.name, descriptor.produced_by or "", name, environments)
            )
        elif descriptor.vault_path in index:
            acquired = index.acquired_at(descriptor.vault_path or "")
            expires_at = (
                acquired + descriptor.expires_after
                if acquired is not None and descriptor.expires_after is not None
                else None
            )
            inject.append(
                Inject(
                    descriptor.name,
                    descriptor.vault_path or "",
                    name,
                    environments,
                    expires_at,
                    descriptor.consumed_by,
                )
            )
        else:
            request.append(
                Request(
                    descriptor.name,
                    descriptor.acquire,
                    descriptor.vault_path or "",
                    f"{adoption.project_name} sets {name}; the vault does not hold it",
                    descriptor.vendor,
                )
            )
    return Resolution(
        features=("adopted",),
        inject=tuple(inject),
        derive=tuple(derive),
        request=tuple(request),
        defer=tuple(defer),
    )


# --- the command ---------------------------------------------------------------


@dataclass(frozen=True)
class AdoptReport:
    adoption: Adoption
    known: tuple[str, ...]
    new: tuple[str, ...]
    sources: dict[str, tuple[str, ...]]
    notes: tuple[str, ...]
    project_file: Path
    credentials_file: Path | None


def adopt(
    project_dir: Path,
    repository: str,
    *,
    name: str | None = None,
    health: Mapping[str, str] | None = None,
    descriptors: DescriptorSet,
    run: Runner = subprocess.run,
) -> AdoptReport:
    """Discover, match, and write `loftline.yml` and the project's descriptors.

    Refuses a directory that already has a Loftline project file: adopting
    twice would overwrite what someone edited by hand.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise AdoptError(f"{project_dir} is not a directory")
    project_file = project_dir / PROJECT_FILE
    if project_file.exists():
        raise AdoptError(
            f"{project_file} exists already. Remove it to adopt again, or edit it."
        )
    project_name = name or repository.split("/", 1)[1].lower()
    project_name = re.sub(r"[^a-z0-9-]", "-", project_name).strip("-")[:39]

    found = discover(project_dir, repository, run=run)
    matched = match(project_name, found.secrets, descriptors)
    for environment in health or {}:
        if environment not in found.environments:
            raise AdoptError(
                f"--health names {environment}, which is not one of the repository's "
                f"environments ({', '.join(found.environments)})"
            )

    adoption = Adoption(
        project_name=project_name,
        repository=repository,
        environments=found.environments,
        health=dict(health or {}),
        credentials=found.secrets,
    )
    project_file.write_text(
        "# An adopted project (ADR-036): what it is and where it lives, so the\n"
        "# dashboard can track it. Names and places only. Written by\n"
        "# `loftline adopt`; the credentials list is what the repository sets.\n"
        + yaml.safe_dump(json.loads(adoption.model_dump_json()), sort_keys=False),
        encoding="utf-8",
    )
    credentials_file: Path | None = None
    if matched.new:
        credentials_file = project_dir / CREDENTIALS_FILE
        write_project_descriptors(credentials_file, matched.new)

    return AdoptReport(
        adoption=adoption,
        known=tuple(sorted(matched.known)),
        new=tuple(sorted(matched.new)),
        sources=found.sources,
        notes=found.notes,
        project_file=project_file,
        credentials_file=credentials_file,
    )


def adoption_summary(adoption: Adoption) -> dict[str, Any]:
    """The adopted project as the dashboard shows it. Names only."""
    return {
        "adopted": True,
        "repository": adoption.repository,
        "environments": list(adoption.environments),
        "health": dict(adoption.health),
        "credentials": sorted(adoption.credentials),
    }
