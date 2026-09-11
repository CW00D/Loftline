"""Provisioner tests with fake vendors. What matters: values reach both sinks
and nowhere else, existing instances are reused only when their password is
recoverable, and nothing is created when a service is missing."""

from __future__ import annotations

from datetime import datetime

import pytest

from loftline.errors import ProvisionError
from loftline.providers.aura import Instance
from loftline.providers.render import Service
from loftline.provision import provision, service_name
from loftline.resolve import Defer, Derive, Inject, Request, Resolution

from .conftest import spec

ENVS = ("staging", "production")


class FakeVault:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def list_paths(self) -> list[str]:
        return sorted(self.values)

    def get(self, path: str) -> str:
        return self.values[path]

    def set(self, path: str, value: str) -> None:
        raise AssertionError("never")

    def acquired_at(self, path: str) -> datetime | None:
        return None


class FakeGitHub:
    def __init__(self) -> None:
        self.writes: dict[tuple[str, str], str] = {}

    def preflight(self) -> None:
        pass

    def ensure_environment(self, environment: str) -> None:
        pass

    def exists(self, name: str, environment: str) -> bool:
        return (name, environment) in self.writes

    def write(self, name: str, environment: str, value: str) -> None:
        self.writes[(name, environment)] = value


class FakeRender:
    def __init__(
        self, services: dict[str, Service], env: dict[str, dict[str, str]] | None = None
    ) -> None:
        self.services = services
        self.env: dict[str, dict[str, str]] = env or {
            s.id: {} for s in services.values()
        }
        self.deploys: list[str] = []

    def find_service(self, name: str) -> Service | None:
        return self.services.get(name)

    def env_vars(self, service_id: str) -> dict[str, str]:
        return dict(self.env[service_id])

    def set_env_var(self, service_id: str, key: str, value: str) -> None:
        self.env[service_id][key] = value

    def trigger_deploy(self, service_id: str) -> str:
        self.deploys.append(service_id)
        return f"dep-{service_id}"

    def wait_for_deploy(
        self, service_id: str, deploy_id: str, *, timeout: float = 900
    ) -> str:
        return "live"


class FakeAura:
    def __init__(
        self, existing: dict[str, Instance] | None = None, refuse: str | None = None
    ) -> None:
        self.existing = existing or {}
        self.refuse = refuse
        self.created: list[str] = []

    def find_instance(self, name: str) -> Instance | None:
        return self.existing.get(name)

    def create_instance(
        self, name: str, *, instance_type: str = "free-db", region: str = "europe-west1"
    ) -> Instance:
        if self.refuse:
            raise ProvisionError(self.refuse)
        self.created.append(name)
        return Instance(
            id=f"id-{name}",
            name=name,
            status="creating",
            uri=None,
            username="neo4j",
            password=f"pw-{name}",
        )

    def wait_until_running(self, instance_id: str, *, timeout: float = 900) -> Instance:
        name = instance_id.removeprefix("id-")
        return Instance(
            id=instance_id,
            name=name,
            status="running",
            uri=f"neo4j+s://{name}",
            username=None,
            password=None,
        )


def services(project: str = "demo") -> dict[str, Service]:
    return {
        f"{project}-api-staging": Service(
            "srv-s", f"{project}-api-staging", "https://s.onrender.com", "staging"
        ),
        f"{project}-api-prod": Service(
            "srv-p", f"{project}-api-prod", "https://p.onrender.com", "prod"
        ),
    }


def resolution() -> Resolution:
    return Resolution(
        features=("base",),
        inject=(Inject("smtp_user", "loftline/google/smtp_user", "SMTP_USER", ENVS),),
        derive=(Derive("jwt_secret", "random_bytes_32_base64", "JWT_SECRET", ENVS),),
        defer=(
            Defer("neo4j_uri", "aura_instance_create", "NEO4J_URI", ENVS),
            Defer("neo4j_password", "aura_instance_create", "NEO4J_PASSWORD", ENVS),
            Defer(
                "render_service_id", "render_service_create", "RENDER_SERVICE_ID", ENVS
            ),
        ),
    )


def test_provision_creates_an_instance_per_environment_and_writes_everything() -> None:
    render, aura, github = FakeRender(services()), FakeAura(), FakeGitHub()

    report = provision(
        spec(project_name="demo", database="aura"),
        resolution(),
        FakeVault({"loftline/google/smtp_user": "me@gmail.com"}),
        github,
        render,
        aura,
        health=lambda url: True,
    )

    assert aura.created == ["demo-staging", "demo-production"]
    staging = render.env["srv-s"]
    assert staging["NEO4J_URI"] == "neo4j+s://demo-staging"
    assert staging["NEO4J_USER"] == "neo4j"
    assert staging["NEO4J_PASSWORD"] == "pw-demo-staging"
    assert staging["SMTP_USER"] == "me@gmail.com"
    assert len(staging["JWT_SECRET"].encode()) >= 32
    # GitHub gets the same values, so CI and the service agree.
    assert github.writes[("JWT_SECRET", "staging")] == staging["JWT_SECRET"]
    assert github.writes[("NEO4J_PASSWORD", "production")] == "pw-demo-production"
    assert github.writes[("RENDER_SERVICE_ID", "staging")] == "srv-s"
    assert render.deploys == ["srv-s", "srv-p"]
    assert [e.instance_status for e in report.environments] == ["created", "created"]
    assert all(e.deploy_status == "live" and e.healthy for e in report.environments)


def test_a_missing_service_stops_everything_before_anything_is_created() -> None:
    render, aura = FakeRender({}), FakeAura()

    with pytest.raises(ProvisionError, match="Blueprint"):
        provision(
            spec(project_name="demo", database="aura"),
            resolution(),
            FakeVault({}),
            FakeGitHub(),
            render,
            aura,
        )

    assert aura.created == []


def test_outstanding_credentials_block_provisioning() -> None:
    blocked = Resolution(
        request=(
            Request("render_api_key", "steps", "p", "absent from vault", "render"),
        )
    )

    with pytest.raises(ProvisionError, match="render_api_key"):
        provision(
            spec(database="aura"),
            blocked,
            FakeVault({}),
            FakeGitHub(),
            FakeRender(services()),
            FakeAura(),
        )


def test_an_aura_refusal_surfaces_verbatim_with_nothing_deployed() -> None:
    render = FakeRender(services())
    aura = FakeAura(refuse="free-tier instance limit reached")

    with pytest.raises(ProvisionError, match="free-tier instance limit"):
        provision(
            spec(project_name="demo", database="aura"),
            resolution(),
            FakeVault({"loftline/google/smtp_user": "x"}),
            FakeGitHub(),
            render,
            aura,
        )

    assert render.deploys == []


def test_an_existing_instance_is_reused_when_the_service_holds_its_password() -> None:
    svc = services()
    render = FakeRender(
        svc,
        env={
            "srv-s": {
                "NEO4J_URI": "neo4j+s://old",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "kept",
            },
            "srv-p": {},
        },
    )
    aura = FakeAura(
        existing={
            "demo-staging": Instance(
                "id-demo-staging",
                "demo-staging",
                "running",
                "neo4j+s://old",
                None,
                None,
            )
        }
    )
    github = FakeGitHub()

    report = provision(
        spec(project_name="demo", database="aura"),
        resolution(),
        FakeVault({"loftline/google/smtp_user": "x"}),
        github,
        render,
        aura,
        environments=["staging"],
        health=lambda url: True,
    )

    assert aura.created == []
    assert report.environments[0].instance_status == "reused"
    assert github.writes[("NEO4J_PASSWORD", "staging")] == "kept"


def test_an_existing_instance_with_no_recoverable_password_is_refused() -> None:
    aura = FakeAura(
        existing={
            "demo-staging": Instance(
                "id-demo-staging",
                "demo-staging",
                "running",
                "neo4j+s://old",
                None,
                None,
            )
        }
    )

    with pytest.raises(ProvisionError, match="only ever shows once"):
        provision(
            spec(project_name="demo", database="aura"),
            resolution(),
            FakeVault({"loftline/google/smtp_user": "x"}),
            FakeGitHub(),
            FakeRender(services()),
            aura,
            environments=["staging"],
        )


def test_a_derived_value_already_on_the_service_is_reused_not_regenerated() -> None:
    svc = services()
    render = FakeRender(
        svc,
        env={
            "srv-s": {"JWT_SECRET": "existing-secret-existing-secret-xx"},
            "srv-p": {},
        },
    )
    github = FakeGitHub()

    provision(
        spec(project_name="demo", database="aura"),
        resolution(),
        FakeVault({"loftline/google/smtp_user": "x"}),
        github,
        render,
        FakeAura(),
        environments=["staging"],
        deploy=False,
    )

    assert render.env["srv-s"]["JWT_SECRET"] == "existing-secret-existing-secret-xx"
    assert (
        github.writes[("JWT_SECRET", "staging")] == "existing-secret-existing-secret-xx"
    )


def test_only_the_requested_environments_are_touched() -> None:
    render, aura = FakeRender(services()), FakeAura()

    provision(
        spec(project_name="demo", database="aura"),
        resolution(),
        FakeVault({"loftline/google/smtp_user": "x"}),
        FakeGitHub(),
        render,
        aura,
        environments=["production"],
        deploy=False,
    )

    assert aura.created == ["demo-production"]
    assert render.env["srv-s"] == {}


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ProvisionError, match="not one of the spec's environments"):
        provision(
            spec(database="aura"),
            resolution(),
            FakeVault({}),
            FakeGitHub(),
            FakeRender(services("beerreel")),
            FakeAura(),
            environments=["qa"],
        )


def test_service_names_follow_the_blueprint() -> None:
    assert service_name("demo", "staging") == "demo-api-staging"
    assert service_name("demo", "production") == "demo-api-prod"
    with pytest.raises(ProvisionError):
        service_name("demo", "qa")


def test_the_report_carries_no_values() -> None:
    render = FakeRender(services())
    report = provision(
        spec(project_name="demo", database="aura"),
        resolution(),
        FakeVault({"loftline/google/smtp_user": "hunter2-mail"}),
        FakeGitHub(),
        render,
        FakeAura(),
        deploy=False,
    )

    assert "hunter2" not in repr(report)
    assert "pw-demo" not in repr(report)
