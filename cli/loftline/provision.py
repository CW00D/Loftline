"""Provisioning: the deferred credentials become real, and the API goes live.

For each environment the spec names:

1. An Aura instance is created (or found), which produces NEO4J_URI,
   NEO4J_USER and NEO4J_PASSWORD.
2. Every credential the environment needs, held, derived and produced, is
   written to the Render service's environment and to the GitHub environment.
3. The service is deployed and its health endpoint is checked.

Values move from the vault or the vendor to the two sinks and are never
printed or returned. The Render service is the readable record of derived
and produced values, so a re-run recovers rather than regenerates.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import ProvisionError
from .models import Spec
from .providers.aura import Instance
from .providers.render import Service
from .resolve import Resolution
from .secrets import DERIVATIONS, SecretSink, derive
from .vault import VaultAdapter

# Render service names, as the blueprint names them (ADR-013).
SERVICE_SUFFIX = {"staging": "-api-staging", "production": "-api-prod"}

# What an Aura instance produces, in the order the descriptors name them.
INSTANCE_SECRETS = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")


class RenderLike(Protocol):
    """What the provisioner needs from Render. RenderClient satisfies it."""

    def find_service(self, name: str) -> Service | None: ...
    def env_vars(self, service_id: str) -> dict[str, str]: ...
    def set_env_var(self, service_id: str, key: str, value: str) -> None: ...
    def trigger_deploy(self, service_id: str) -> str: ...
    def wait_for_deploy(
        self, service_id: str, deploy_id: str, *, timeout: float = 900
    ) -> str: ...


class AuraLike(Protocol):
    """What the provisioner needs from Aura. AuraClient satisfies it."""

    def find_instance(self, name: str) -> Instance | None: ...
    def create_instance(
        self, name: str, *, instance_type: str = "free-db", region: str = "europe-west1"
    ) -> Instance: ...
    def wait_until_running(
        self, instance_id: str, *, timeout: float = 900
    ) -> Instance: ...


def service_name(project: str, environment: str) -> str:
    try:
        return project + SERVICE_SUFFIX[environment]
    except KeyError:
        raise ProvisionError(
            f"no Render service naming for environment {environment!r}; "
            f"known: {', '.join(SERVICE_SUFFIX)}"
        ) from None


def instance_name(project: str, environment: str) -> str:
    return f"{project}-{environment}"


class RenderSink(SecretSink):
    """A Render service per environment, as a secret sink."""

    def __init__(self, render: RenderLike, services: dict[str, Service]) -> None:
        self._render = render
        self._services = services

    def preflight(self) -> None:
        pass

    def ensure_environment(self, environment: str) -> None:
        if environment not in self._services:
            raise ProvisionError(f"no Render service for environment {environment}")

    def exists(self, name: str, environment: str) -> bool:
        return name in self._render.env_vars(self._services[environment].id)

    def write(self, name: str, environment: str, value: str) -> None:
        self._render.set_env_var(self._services[environment].id, name, value)


@dataclass(frozen=True)
class Provisioned:
    environment: str
    service: str
    url: str | None
    instance: str
    instance_status: str  # "created" | "reused"
    deploy_status: str | None
    healthy: bool | None


@dataclass(frozen=True)
class ProvisionReport:
    environments: tuple[Provisioned, ...] = ()
    outstanding: tuple[str, ...] = ()


def provision(
    spec: Spec,
    resolution: Resolution,
    vault: VaultAdapter,
    github: SecretSink,
    render: RenderLike,
    aura: AuraLike,
    *,
    environments: Sequence[str] | None = None,
    instance_type: str = "free-db",
    region: str = "europe-west1",
    deploy: bool = True,
    health: Callable[[str], bool] | None = None,
) -> ProvisionReport:
    """Make the spec's deferred credentials real and deploy each environment."""
    outstanding = tuple(entry.name for entry in resolution.request)
    if outstanding:
        raise ProvisionError(
            f"{len(outstanding)} credential(s) still to acquire: "
            f"{', '.join(outstanding)}. "
            "Provisioning needs every held credential present; run `loftline plan`."
        )
    targets = list(environments or spec.environments)
    for environment in targets:
        if environment not in spec.environments:
            raise ProvisionError(f"{environment} is not one of the spec's environments")

    # Every service must exist before anything is created or written, so a
    # missing blueprint connection fails here with nothing half done.
    services: dict[str, Service] = {}
    for environment in targets:
        name = service_name(spec.project_name, environment)
        found = render.find_service(name)
        if found is None:
            raise ProvisionError(
                f"no Render service named {name}. Connect the repository's render.yaml "
                "as a Blueprint once in the Render dashboard, then re-run."
            )
        services[environment] = found
    sink = RenderSink(render, services)

    check = health or _http_health
    done: list[Provisioned] = []
    for environment in targets:
        service = services[environment]
        current = render.env_vars(service.id)

        # --- the database -----------------------------------------------------
        name = instance_name(spec.project_name, environment)
        existing = aura.find_instance(name)
        if existing is not None:
            if not all(current.get(k) for k in INSTANCE_SECRETS):
                raise ProvisionError(
                    f"Aura instance {name} already exists but {service.name} does not "
                    "hold its password, which Aura only ever shows once. Delete the "
                    "instance in the Aura console and re-run, or set NEO4J_PASSWORD "
                    "on the service by hand."
                )
            instance = existing
            instance_status = "reused"
            produced = {k: current[k] for k in INSTANCE_SECRETS}
        else:
            created = aura.create_instance(
                name, instance_type=instance_type, region=region
            )
            if not created.password or not created.username:
                raise ProvisionError(
                    f"Aura created {name} but returned no credentials; "
                    "delete it in the console and re-run"
                )
            instance = aura.wait_until_running(created.id)
            instance_status = "created"
            produced = {
                "NEO4J_URI": instance.uri or "",
                "NEO4J_USER": created.username,
                "NEO4J_PASSWORD": created.password,
            }
            for key, value in produced.items():
                sink.write(key, environment, value)
        for key, value in produced.items():
            github.write(key, environment, value)

        # --- held and derived -------------------------------------------------
        for held in resolution.inject:
            if environment in held.environments:
                value = vault.get(held.vault_path)
                sink.write(held.github_secret, environment, value)
                github.write(held.github_secret, environment, value)
        for derived in resolution.derive:
            if environment not in derived.environments:
                continue
            value = current.get(derived.github_secret) or derive(
                derived.derivation, DERIVATIONS
            )
            if derived.github_secret not in current:
                sink.write(derived.github_secret, environment, value)
            github.write(derived.github_secret, environment, value)

        # --- the service id, which the blueprint produced ---------------------
        github.write("RENDER_SERVICE_ID", environment, service.id)

        # --- deploy -----------------------------------------------------------
        deploy_status: str | None = None
        healthy: bool | None = None
        if deploy:
            deploy_status = render.wait_for_deploy(
                service.id, render.trigger_deploy(service.id)
            )
            if deploy_status == "live" and service.url:
                healthy = check(f"{service.url}/health")

        done.append(
            Provisioned(
                environment=environment,
                service=service.name,
                url=service.url,
                instance=name,
                instance_status=instance_status,
                deploy_status=deploy_status,
                healthy=healthy,
            )
        )

    return ProvisionReport(environments=tuple(done), outstanding=())


def _http_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return bool(
                response.status == 200
                and b'"db":true' in response.read().replace(b" ", b"")
            )
    except (urllib.error.URLError, OSError):
        return False
