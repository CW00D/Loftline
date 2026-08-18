"""Credential descriptor and project spec schemas.

The descriptor schema is the one in credentials.md, with its conditional field
requirements enforced here rather than left to the resolver. `extra="forbid"`
is doing real work: it is what makes a stray `value:` key in `credentials.yml`
a validation failure rather than a committed secret.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import DescriptorError, SpecError

Scope = Literal["account", "project"]
State = Literal["held", "derivable", "manual", "produced"]

_DURATION = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[hdw])$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def _parse_duration(value: str) -> timedelta:
    match = _DURATION.match(value)
    if match is None:
        raise ValueError(f"expires must look like 365d, 12h or 2w, not {value!r}")
    return timedelta(**{_UNITS[match["unit"]]: int(match["count"])})


class Descriptor(BaseModel):
    """Everything about one credential except its value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    vendor: str = Field(min_length=1)
    scope: Scope
    state: State
    consumed_by: tuple[str, ...] = Field(min_length=1)
    environments: tuple[str, ...] = Field(min_length=1)
    github_secret: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    vault_path: str | None = None
    expires: str | None = None
    acquire: str | None = None
    produced_by: str | None = None
    derivation: str | None = None

    @model_validator(mode="after")
    def _check_state_requirements(self) -> Self:
        if self.expires is not None:
            _parse_duration(self.expires)

        if self.state == "manual" and not self.acquire:
            raise ValueError(
                f"{self.name}: a manual credential needs acquire instructions, "
                "because the whole point is not to research it twice"
            )
        if self.state == "derivable" and not self.derivation:
            raise ValueError(
                f"{self.name}: a derivable credential needs a derivation naming "
                "what it is generated from"
            )
        if self.state == "produced" and not self.produced_by:
            raise ValueError(
                f"{self.name}: a produced credential needs produced_by naming the "
                "provisioning step that creates it"
            )
        if self.state in ("held", "manual") and not self.vault_path:
            raise ValueError(
                f"{self.name}: a {self.state} credential needs a vault_path"
            )
        if self.state == "produced" and self.vault_path:
            raise ValueError(
                f"{self.name}: a produced credential must not carry a vault_path. "
                "It belongs to a project, not to the account, and is written "
                "straight to environment secrets"
            )
        return self

    @property
    def expires_after(self) -> timedelta | None:
        """The lifetime of the credential, or None if it does not elapse."""
        return None if self.expires is None else _parse_duration(self.expires)


class DescriptorSet(BaseModel):
    """The whole of `credentials.yml`, keyed by credential name."""

    model_config = ConfigDict(frozen=True)

    entries: dict[str, Descriptor]

    @model_validator(mode="after")
    def _check_no_collisions(self) -> Self:
        self._reject_duplicates(
            {name: entry.github_secret for name, entry in self.entries.items()},
            "github_secret",
            "one would silently overwrite the other at provisioning time",
        )
        self._reject_duplicates(
            {
                name: entry.vault_path
                for name, entry in self.entries.items()
                if entry.vault_path
            },
            "vault_path",
            "two credentials cannot share one vault entry",
        )
        return self

    @staticmethod
    def _reject_duplicates(values: dict[str, str | None], field: str, why: str) -> None:
        seen: dict[str, str] = {}
        for name, value in values.items():
            if value is None:
                continue
            if value in seen:
                raise DescriptorError(
                    f"{field} {value} is claimed by both "
                    f"{seen[value]} and {name}: {why}"
                )
            seen[value] = name

    def __getitem__(self, name: str) -> Descriptor:
        return self.entries[name]

    def __contains__(self, name: object) -> bool:
        return name in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, name: str) -> Descriptor | None:
        return self.entries.get(name)


class Spec(BaseModel):
    """`loftline.yml`.

    The question set is fixed at the five in the roadmap plus environments.
    `extra="forbid"` keeps it that way: a new question is a deliberate schema
    change, not something that appears because a spec file mentioned it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,38}$")
    package_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    database: Literal["postgres", "aura"]
    mobile: bool = False
    notifications: bool = False
    environments: tuple[str, ...] = ("staging", "production")

    @model_validator(mode="after")
    def _check_environments(self) -> Self:
        if not self.environments:
            raise ValueError("environments must name at least one deployment target")
        if len(set(self.environments)) != len(self.environments):
            raise ValueError(f"environments contains duplicates: {self.environments}")
        return self


def _read_yaml(path: Path, error: type[SpecError | DescriptorError]) -> Any:  # noqa: ANN401 - parsed YAML
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise error(f"{path} does not exist") from exc
    except OSError as exc:
        raise error(f"{path} could not be read: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise error(f"{path} is not valid YAML: {exc}") from exc


def load_spec(path: Path) -> Spec:
    """Load and validate a project spec."""
    data = _read_yaml(path, SpecError)
    if not isinstance(data, dict):
        raise SpecError(f"{path} must contain a mapping of spec fields")
    try:
        return Spec.model_validate(data)
    except ValueError as exc:
        raise SpecError(f"{path} is not a valid spec:\n{exc}") from exc


def load_descriptors(path: Path) -> DescriptorSet:
    """Load and validate `credentials.yml`."""
    data = _read_yaml(path, DescriptorError)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DescriptorError(f"{path} must contain a mapping of credential names")

    entries: dict[str, Descriptor] = {}
    for name, fields in data.items():
        if not isinstance(fields, dict):
            raise DescriptorError(f"{path}: descriptor {name} must be a mapping")
        try:
            entries[name] = Descriptor.model_validate({"name": name, **fields})
        except ValueError as exc:
            raise DescriptorError(
                f"{path}: descriptor {name} is invalid:\n{exc}"
            ) from exc

    return DescriptorSet(entries=entries)
