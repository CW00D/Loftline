"""Provisioning: the deferred credentials become real, and the API goes live.

For each environment the spec names:

1. The database exists. On Aura an instance is created (or found), which
   produces NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD. On Postgres the
   blueprint created the database beside the service and Render hands it
   DATABASE_URL directly; there is nothing to create here.
2. Every credential the environment needs, held, derived and produced, is
   written to the Render service's environment and to the GitHub environment.
   A held credential consumed by the web front-end is written to the static
   site as well.
3. Each payment module's Stripe webhook endpoint is registered against the
   service's URL, which produces that module's signing secret.
4. The services are deployed and the API's health endpoint is checked.

Values move from the vault or the vendor to the sinks and are never printed
or returned. The Render service is the readable record of derived and
produced values, so a re-run recovers rather than regenerates.
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
from .providers.stripe import WebhookEndpoint
from .resolve import Resolution
from .secrets import DERIVATIONS, SecretSink, derive
from .vault import VaultAdapter

# Render service names, as the blueprint names them (ADR-013, ADR-024).
SERVICE_SUFFIX = {"staging": "-api-staging", "production": "-api-prod"}
WEB_SUFFIX = {"staging": "-web-staging", "production": "-web-prod"}
DATABASE_SUFFIX = {"staging": "-db-staging", "production": "-db-prod"}

# What an Aura instance produces, in the order the descriptors name them.
INSTANCE_SECRETS = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")


@dataclass(frozen=True)
class WebhookModule:
    """A payment module's webhook endpoint (ADR-026): its path on the API,
    the secret it produces, and the events it needs."""

    path: str
    github_secret: str
    events: tuple[str, ...]


WEBHOOK_MODULES: dict[str, WebhookModule] = {
    "checkout": WebhookModule(
        "/checkout/webhook",
        "STRIPE_CHECKOUT_WEBHOOK_SECRET",
        ("payment_intent.succeeded", "payment_intent.payment_failed"),
    ),
    "subscriptions": WebhookModule(
        "/subscriptions/webhook",
        "STRIPE_SUBSCRIPTIONS_WEBHOOK_SECRET",
        (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.paid",
            "invoice.payment_failed",
        ),
    ),
}


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


class StripeLike(Protocol):
    """What the provisioner needs from Stripe. StripeClient satisfies it."""

    def find_webhook_endpoint(self, url: str) -> WebhookEndpoint | None: ...
    def create_webhook_endpoint(
        self, url: str, events: list[str]
    ) -> WebhookEndpoint: ...


def _named(project: str, environment: str, suffixes: dict[str, str]) -> str:
    try:
        return project + suffixes[environment]
    except KeyError:
        raise ProvisionError(
            f"no Render service naming for environment {environment!r}; "
            f"known: {', '.join(suffixes)}"
        ) from None


def service_name(project: str, environment: str) -> str:
    return _named(project, environment, SERVICE_SUFFIX)


def web_service_name(project: str, environment: str) -> str:
    return _named(project, environment, WEB_SUFFIX)


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
    instance_status: str  # "created" | "reused" | "blueprint"
    deploy_status: str | None
    healthy: bool | None
    webhooks: tuple[str, ...] = ()  # module names whose endpoint was created this run
    web_service: str | None = None
    web_deploy_status: str | None = None


@dataclass(frozen=True)
class ProvisionReport:
    environments: tuple[Provisioned, ...] = ()
    outstanding: tuple[str, ...] = ()


def _find_all(
    render: RenderLike, names: dict[str, str], what: str
) -> dict[str, Service]:
    """Every service must exist before anything is created or written, so a
    missing blueprint connection fails with nothing half done."""
    found: dict[str, Service] = {}
    for environment, name in names.items():
        service = render.find_service(name)
        if service is None:
            raise ProvisionError(
                f"no Render {what} named {name}. Connect the repository's render.yaml "
                "as a Blueprint once in the Render dashboard, then re-run."
            )
        found[environment] = service
    return found


def _database(
    spec: Spec,
    environment: str,
    service: Service,
    current: dict[str, str],
    aura: AuraLike | None,
    sink: RenderSink,
    github: SecretSink,
    *,
    instance_type: str,
    region: str,
) -> tuple[str, str]:
    """Make the environment's database exist. Returns (name, status)."""
    if spec.database == "postgres":
        # Created by the blueprint beside the service; Render sets DATABASE_URL
        # on the service itself. Nothing to create and nothing to write.
        return _named(spec.project_name, environment, DATABASE_SUFFIX), "blueprint"

    if aura is None:
        raise ProvisionError("database: aura needs an Aura client; none was given")
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
        status = "reused"
        produced = {k: current[k] for k in INSTANCE_SECRETS}
    else:
        created = aura.create_instance(name, instance_type=instance_type, region=region)
        if not created.password or not created.username:
            raise ProvisionError(
                f"Aura created {name} but returned no credentials; "
                "delete it in the console and re-run"
            )
        instance = aura.wait_until_running(created.id)
        status = "created"
        produced = {
            "NEO4J_URI": instance.uri or "",
            "NEO4J_USER": created.username,
            "NEO4J_PASSWORD": created.password,
        }
        for key, value in produced.items():
            sink.write(key, environment, value)
    for key, value in produced.items():
        github.write(key, environment, value)
    return name, status


def _webhooks(
    spec: Spec,
    environment: str,
    service: Service,
    current: dict[str, str],
    stripe: StripeLike | None,
    sink: RenderSink,
    github: SecretSink,
) -> tuple[str, ...]:
    """Register each payment module's endpoint. Returns the modules created."""
    if not spec.payments:
        return ()
    if stripe is None:
        raise ProvisionError("payments modules need a Stripe client; none was given")
    if not service.url:
        raise ProvisionError(
            f"{service.name} has no URL yet, so its webhook endpoints cannot be "
            "registered. Let Render finish its first deploy and re-run."
        )
    created: list[str] = []
    for module in spec.payments:
        try:
            hook = WEBHOOK_MODULES[module]
        except KeyError:
            raise ProvisionError(
                f"no webhook definition for payments module {module}"
            ) from None
        url = service.url.rstrip("/") + hook.path
        if current.get(hook.github_secret):
            # The service is the record; re-runs recover rather than re-register.
            github.write(hook.github_secret, environment, current[hook.github_secret])
            continue
        if stripe.find_webhook_endpoint(url) is not None:
            raise ProvisionError(
                f"Stripe already has a webhook endpoint for {url} but {service.name} "
                "does not hold its signing secret, which Stripe only ever shows once. "
                "Delete the endpoint in the Stripe dashboard and re-run."
            )
        endpoint = stripe.create_webhook_endpoint(url, list(hook.events))
        if not endpoint.secret:
            raise ProvisionError(
                f"Stripe created the endpoint for {url} but returned no signing "
                "secret; delete it in the dashboard and re-run"
            )
        sink.write(hook.github_secret, environment, endpoint.secret)
        github.write(hook.github_secret, environment, endpoint.secret)
        created.append(module)
    return tuple(created)


def provision(
    spec: Spec,
    resolution: Resolution,
    vault: VaultAdapter,
    github: SecretSink,
    render: RenderLike,
    aura: AuraLike | None = None,
    *,
    stripe: StripeLike | None = None,
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

    services = _find_all(
        render, {e: service_name(spec.project_name, e) for e in targets}, "service"
    )
    web_on_render = spec.web and spec.hosting.web == "render"
    web_services = (
        _find_all(
            render,
            {e: web_service_name(spec.project_name, e) for e in targets},
            "static site",
        )
        if web_on_render
        else {}
    )
    sink = RenderSink(render, services)

    check = health or _http_health
    done: list[Provisioned] = []
    for environment in targets:
        service = services[environment]
        current = render.env_vars(service.id)

        name, instance_status = _database(
            spec,
            environment,
            service,
            current,
            aura,
            sink,
            github,
            instance_type=instance_type,
            region=region,
        )

        # --- held and derived -------------------------------------------------
        web_service = web_services.get(environment)
        for held in resolution.inject:
            if environment in held.environments:
                value = vault.get(held.vault_path)
                sink.write(held.github_secret, environment, value)
                github.write(held.github_secret, environment, value)
                if web_service is not None and "web" in held.consumed_by:
                    render.set_env_var(web_service.id, held.github_secret, value)
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

        # --- payment modules' webhook endpoints --------------------------------
        webhooks = _webhooks(spec, environment, service, current, stripe, sink, github)

        # --- deploy -----------------------------------------------------------
        deploy_status: str | None = None
        web_deploy_status: str | None = None
        healthy: bool | None = None
        if deploy:
            deploy_status = render.wait_for_deploy(
                service.id, render.trigger_deploy(service.id)
            )
            if deploy_status == "live" and service.url:
                healthy = check(f"{service.url}/health")
            if web_service is not None:
                # The site bakes its variables in at build time; a rebuild is
                # what makes a freshly written key reach the bundle.
                web_deploy_status = render.wait_for_deploy(
                    web_service.id, render.trigger_deploy(web_service.id)
                )

        done.append(
            Provisioned(
                environment=environment,
                service=service.name,
                url=service.url,
                instance=name,
                instance_status=instance_status,
                deploy_status=deploy_status,
                healthy=healthy,
                webhooks=webhooks,
                web_service=web_service.name if web_service is not None else None,
                web_deploy_status=web_deploy_status,
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
