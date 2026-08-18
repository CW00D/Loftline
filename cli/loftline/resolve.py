"""The resolver.

A pure function of the spec, the descriptor set and an index of vault paths.
No network, no vendor SDK, no vault value read, no clock unless one is passed.
Everything downstream consumes its output, so it is the component with real
test coverage (ADR-009).

The partition:

    inject   held or already-acquired, present in the vault and in date
    derive   obtainable from something already held
    request  absent from the vault, or elapsed, so a person must go and get it
    defer    cannot exist until provisioning creates it
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .errors import UnknownCredentialError
from .features import FeatureSet, load_default_features
from .models import Descriptor, DescriptorSet, Spec
from .vault import VaultIndex, as_index

ABSENT = "absent from vault"
EXPIRED = "expired"
UNDATED = "acquisition date unknown"


@dataclass(frozen=True)
class Inject:
    """Held already. Read from the vault at provisioning time, never before."""

    name: str
    vault_path: str
    github_secret: str
    environments: tuple[str, ...]
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Derive:
    """Generated from something already held."""

    name: str
    derivation: str
    github_secret: str
    environments: tuple[str, ...]


@dataclass(frozen=True)
class Request:
    """A person must go and get this one. The instructions travel with it."""

    name: str
    acquire: str | None
    vault_path: str
    reason: str
    vendor: str


@dataclass(frozen=True)
class Defer:
    """Created during provisioning and written straight to environment secrets."""

    name: str
    produced_by: str
    github_secret: str
    environments: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    """The partition, plus the features that produced it."""

    features: tuple[str, ...] = ()
    inject: tuple[Inject, ...] = ()
    derive: tuple[Derive, ...] = ()
    request: tuple[Request, ...] = ()
    defer: tuple[Defer, ...] = ()

    @property
    def total(self) -> int:
        return len(self.inject) + len(self.derive) + len(self.request) + len(self.defer)


def resolve(
    spec: Spec,
    descriptors: DescriptorSet,
    vault_path_index: VaultIndex | Mapping[str, datetime | None] | Iterable[str],
    *,
    features: FeatureSet | None = None,
    now: datetime | None = None,
) -> Resolution:
    """Partition every credential the spec implies into the four states."""
    features = features if features is not None else load_default_features()
    index = as_index(vault_path_index)
    now = now or datetime.now(UTC)

    enabled = features.enabled_for(spec)
    wanted: dict[str, str] = {}
    for feature in enabled:
        for name in (*feature.requires, *feature.produces):
            wanted.setdefault(name, feature.name)

    missing = tuple(
        (feature, name) for name, feature in wanted.items() if name not in descriptors
    )
    if missing:
        raise UnknownCredentialError(missing)

    inject: list[Inject] = []
    derive: list[Derive] = []
    request: list[Request] = []
    defer: list[Defer] = []

    for name in sorted(wanted):
        descriptor = descriptors[name]
        environments = _environments(spec, descriptor)

        if descriptor.state == "produced":
            assert descriptor.produced_by is not None
            defer.append(
                Defer(
                    name=name,
                    produced_by=descriptor.produced_by,
                    github_secret=descriptor.github_secret,
                    environments=environments,
                )
            )
            continue

        if descriptor.state == "derivable":
            assert descriptor.derivation is not None
            derive.append(
                Derive(
                    name=name,
                    derivation=descriptor.derivation,
                    github_secret=descriptor.github_secret,
                    environments=environments,
                )
            )
            continue

        # held or manual: the vault decides, not the descriptor.
        assert descriptor.vault_path is not None
        reason = _reason_to_request(descriptor, index, now)
        if reason is None:
            inject.append(
                Inject(
                    name=name,
                    vault_path=descriptor.vault_path,
                    github_secret=descriptor.github_secret,
                    environments=environments,
                    expires_at=_expires_at(descriptor, index),
                )
            )
        else:
            request.append(
                Request(
                    name=name,
                    acquire=descriptor.acquire,
                    vault_path=descriptor.vault_path,
                    reason=reason,
                    vendor=descriptor.vendor,
                )
            )

    return Resolution(
        features=tuple(feature.name for feature in enabled),
        inject=tuple(inject),
        derive=tuple(derive),
        request=tuple(request),
        defer=tuple(defer),
    )


def _environments(spec: Spec, descriptor: Descriptor) -> tuple[str, ...]:
    """The environments of this project that consume this credential."""
    return tuple(env for env in spec.environments if env in descriptor.environments)


def _expires_at(descriptor: Descriptor, index: VaultIndex) -> datetime | None:
    lifetime = descriptor.expires_after
    acquired = index.acquired_at(descriptor.vault_path or "")
    if lifetime is None or acquired is None:
        return None
    return acquired + lifetime


def _reason_to_request(
    descriptor: Descriptor, index: VaultIndex, now: datetime
) -> str | None:
    """Why this credential must be acquired, or None if it can be injected."""
    path = descriptor.vault_path or ""
    if path not in index:
        return ABSENT

    lifetime = descriptor.expires_after
    if lifetime is None:
        return None

    acquired = index.acquired_at(path)
    if acquired is None:
        # Freshness that cannot be proved is not assumed. The alternative is
        # injecting a credential that expired at some unknown point and finding
        # out during a deployment.
        return UNDATED
    if acquired + lifetime <= now:
        return EXPIRED
    return None
