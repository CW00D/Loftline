"""The dashboard client and `loftline sync`, with a fake site and no network."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from loftline.site import (
    SiteClient,
    SiteError,
    environment_urls,
    live_status,
    spec_summary,
    sync_project,
    write_collaborators,
)

from .conftest import spec

Call = tuple[str, str, dict[str, str], bytes | None]


class FakeTransport:
    def __init__(self, answers: dict[tuple[str, str], tuple[int, object]]) -> None:
        self.answers = answers
        self.calls: list[Call] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, dict(headers), body))
        status, answer = self.answers.get(
            (method, path), (404, {"detail": "no answer"})
        )
        return status, json.dumps(answer).encode()


class FakeSite:
    def __init__(self, collaborators: list[dict[str, Any]] | None = None) -> None:
        self.collaborators = collaborators or []
        self.pushed: list[dict[str, Any]] = []
        self.applied: list[list[str]] = []

    def push_project(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.pushed.append({"name": name, **kwargs})
        return {"id": "p1", "name": name}

    def pull_project(self, name: str, *, org_slug: str | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "repository": "acme/shop",
            "collaborators": self.collaborators,
        }

    def mark_applied(
        self, name: str, logins: list[str], *, org_slug: str | None = None
    ) -> None:
        self.applied.append(logins)


NOW = datetime(2026, 9, 13, tzinfo=UTC)


def shop() -> Any:
    return spec(
        project_name="shop", database="postgres", web=True, environments=["staging"]
    )


# --- the client --------------------------------------------------------------


def test_the_client_sends_the_token_and_the_documented_shapes() -> None:
    transport = FakeTransport(
        {
            ("PUT", "/sync/projects/shop"): (200, {"id": "p1"}),
            ("GET", "/sync/projects/shop?org_slug=acme"): (200, {"collaborators": []}),
            ("POST", "/sync/projects/shop/applied"): (200, {"ok": True}),
        }
    )
    client = SiteClient("llt_x", site="https://api.example", transport=transport)

    client.push_project("shop", repository="acme/shop", spec={"web": True}, status={})
    client.pull_project("shop", org_slug="acme")
    client.mark_applied("shop", ["octocat"])

    put = transport.calls[0]
    assert put[2]["Authorization"] == "Bearer llt_x"
    assert json.loads(put[3] or b"{}")["repository"] == "acme/shop"
    assert transport.calls[2][3] == b'{"github_logins": ["octocat"]}'


def test_a_refused_token_says_what_to_do() -> None:
    transport = FakeTransport(
        {("GET", "/me"): (401, {"detail": "Invalid or revoked token"})}
    )
    client = SiteClient("llt_old", transport=transport)

    with pytest.raises(SiteError, match="loftline login"):
        client.me()


# --- what goes up ------------------------------------------------------------


def test_the_spec_summary_carries_names_and_choices_only() -> None:
    summary = spec_summary(shop())

    assert summary["database"] == "postgres"
    assert summary["environments"] == ["staging"]
    assert not any("secret" in key for key in summary)


def test_urls_follow_the_domain_when_there_is_one() -> None:
    plain = environment_urls(shop(), "staging")
    branded = environment_urls(
        spec(project_name="shop", web=True, domain="brand.dev"), "staging"
    )
    production = environment_urls(
        spec(project_name="shop", web=True, domain="brand.dev"), "production"
    )

    assert plain == {
        "api_url": "https://shop-api-staging.onrender.com",
        "web_url": "https://shop-web-staging.onrender.com",
    }
    assert branded == {
        "api_url": "https://api.staging.brand.dev",
        "web_url": "https://staging.brand.dev",
    }
    assert production == {
        "api_url": "https://api.brand.dev",
        "web_url": "https://brand.dev",
    }


def test_live_status_checks_each_environment_once() -> None:
    asked: list[str] = []

    def health(url: str) -> bool:
        asked.append(url)
        return url.startswith("https://shop-api-staging")

    status = live_status(shop(), health=health, now=NOW)

    assert asked == ["https://shop-api-staging.onrender.com/health"]
    assert status["environments"][0]["healthy"] is True
    assert status["environments"][0]["checked_at"].startswith("2026-09-13")


# --- what comes down ---------------------------------------------------------


def test_collaborators_are_written_into_tfvars_and_replaced_on_the_next_run(
    tmp_path: Path,
) -> None:
    tfvars = tmp_path / "terraform.tfvars"
    tfvars.write_text(
        'repository   = "shop"\nenvironments = ["staging"]\n', encoding="utf-8"
    )

    write_collaborators(tfvars, {"octocat": "push", "hubot": "admin"})
    first = tfvars.read_text(encoding="utf-8")
    write_collaborators(tfvars, {"octocat": "push"})
    second = tfvars.read_text(encoding="utf-8")

    assert 'repository   = "shop"' in first
    assert '  "hubot" = "admin"\n  "octocat" = "push"' in first
    assert "hubot" not in second
    assert second.count("collaborators = {") == 1

    write_collaborators(tfvars, {})
    assert "collaborators" not in tfvars.read_text(encoding="utf-8")


def test_sync_pushes_pulls_writes_and_reports_what_is_pending(tmp_path: Path) -> None:
    (tmp_path / "infra").mkdir()
    site = FakeSite(
        [
            {"github_login": "octocat", "permission": "push"},
            {"github_login": "hubot", "permission": "pull"},
        ]
    )

    report = sync_project(
        shop(),
        site,
        repository="acme/shop",
        project_dir=tmp_path,
        health=lambda url: True,
        on_github=lambda repo: {"octocat"},
    )

    assert site.pushed[0]["status"]["environments"][0]["healthy"] is True
    assert site.applied == [["octocat"]]
    assert report.applied == ("octocat",)
    assert report.pending == ("hubot",)
    assert report.tfvars == tmp_path / "infra" / "terraform.tfvars"
    assert '"hubot" = "pull"' in report.tfvars.read_text(encoding="utf-8")
    assert any("terraform apply" in note for note in report.notes)


def test_sync_without_a_project_directory_still_reports(tmp_path: Path) -> None:
    site = FakeSite([{"github_login": "octocat", "permission": "push"}])

    report = sync_project(
        shop(),
        site,
        repository=None,
        project_dir=None,
        health=lambda url: False,
        on_github=lambda repo: set(),
    )

    assert report.repository == "acme/shop"
    assert report.environments == (("staging", False),)
    assert report.tfvars is None
    assert report.pending == ("octocat",)
