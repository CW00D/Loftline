"""Adopting a project that exists already (ADR-036).

Discovery is names only, matching is by GitHub secret name, and nothing is
generated or changed. `gh` is a recorder answering canned JSON-ish lines.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from loftline.adopt import (
    AdoptError,
    Adoption,
    adopt,
    discover,
    load_project,
    match,
    project_descriptors,
    resolve_adopted,
)
from loftline.cli import app
from loftline.errors import SpecError
from loftline.models import Spec, load_descriptors, load_spec
from loftline.report import render_plan
from loftline.site import live_status, spec_summary, sync_project
from loftline.vault import VaultIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = REPO_ROOT / "credentials.yml"

RENDER_YAML = """\
services:
  - type: web
    name: beerreel-api
    envVars:
      - key: DATABASE_URL
        fromDatabase: {name: db, property: connectionString}
      - key: SMTP_PASSWORD
        sync: false
      - key: MAPBOX_TOKEN
        sync: false
"""

ENV_EXAMPLE = """\
# copy to .env
SMTP_USER=you@example.org
export SENTRY_DSN=
not_a_secret_lower=x
"""


def fake_gh(answers: dict[str, list[str]]) -> tuple[Any, list[list[str]]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        key = argv[2]  # the path after `gh api`
        if key not in answers:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="HTTP 404")
        return subprocess.CompletedProcess(
            argv, 0, stdout="\n".join(answers[key]) + "\n", stderr=""
        )

    return run, calls


GH = {
    "repos/acme/beerreel/environments": ["staging", "production"],
    "repos/acme/beerreel/environments/staging/secrets": ["JWT_SECRET", "SMTP_PASSWORD"],
    "repos/acme/beerreel/environments/production/secrets": [
        "JWT_SECRET",
        "SMTP_PASSWORD",
        "MAPBOX_TOKEN",
    ],
    "repos/acme/beerreel/actions/secrets": ["RENDER_API_KEY"],
}


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    directory = tmp_path / "beerreel"
    directory.mkdir()
    (directory / "render.yaml").write_text(RENDER_YAML, encoding="utf-8")
    (directory / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    return directory


# --- discovery -----------------------------------------------------------------


def test_discovery_collects_names_from_github_render_and_env_example(
    checkout: Path,
) -> None:
    run, calls = fake_gh(GH)

    found = discover(checkout, "acme/beerreel", run=run)

    assert found.environments == ("staging", "production")
    assert found.secrets["JWT_SECRET"] == ("production", "staging")
    assert found.secrets["MAPBOX_TOKEN"] == ("production", "staging")  # render.yaml too
    assert found.secrets["RENDER_API_KEY"] == ("production", "staging")  # repo-level
    assert found.secrets["SENTRY_DSN"] == ("production", "staging")
    assert "SMTP_USER" in found.secrets
    assert "not_a_secret_lower" not in found.secrets
    assert "DATABASE_URL" not in found.secrets  # Render fills it, nobody holds it
    assert set(found.sources["SMTP_PASSWORD"]) == {"github", "render.yaml"}
    assert all(argv[:2] == ["gh", "api"] for argv in calls)


def test_discovery_says_when_a_repository_has_no_environments(tmp_path: Path) -> None:
    run, _ = fake_gh(
        {
            "repos/x/y/environments": [],
            "repos/x/y/actions/secrets": ["API_KEY"],
        }
    )

    found = discover(tmp_path, "x/y", run=run)

    assert found.environments == ("production",)
    assert found.secrets == {"API_KEY": ("production",)}
    assert found.notes and "no GitHub environments" in found.notes[0]


def test_a_failing_gh_call_names_the_call(tmp_path: Path) -> None:
    run, _ = fake_gh({})

    with pytest.raises(AdoptError, match="repos/x/y/environments"):
        discover(tmp_path, "x/y", run=run)


# --- matching ------------------------------------------------------------------


def test_names_match_by_github_secret_and_the_rest_get_their_own() -> None:
    descriptors = load_descriptors(CREDENTIALS)

    matched = match(
        "beerreel",
        {"SMTP_PASSWORD": ("production",), "MAPBOX_TOKEN": ("production",)},
        descriptors,
    )

    assert matched.known["SMTP_PASSWORD"].name == "smtp_password"
    new = matched.new["MAPBOX_TOKEN"]
    assert new.name == "mapbox_token"
    assert new.state == "manual" and new.scope == "project"
    assert new.vault_path == "beerreel/adopted/mapbox_token"
    assert "MAPBOX_TOKEN was set by hand" in (new.acquire or "")


# --- the command ---------------------------------------------------------------


def test_adopt_writes_the_project_file_and_the_projects_descriptors(
    checkout: Path,
) -> None:
    run, _ = fake_gh(GH)

    report = adopt(
        checkout,
        "acme/beerreel",
        health={"production": "https://beerreel.example.org/"},
        descriptors=load_descriptors(CREDENTIALS),
        run=run,
    )

    assert report.adoption.project_name == "beerreel"
    assert report.known == (
        "JWT_SECRET",
        "RENDER_API_KEY",
        "SMTP_PASSWORD",
        "SMTP_USER",
    )
    assert report.new == ("MAPBOX_TOKEN", "SENTRY_DSN")
    project = load_project(checkout / "loftline.yml")
    assert isinstance(project, Adoption)
    assert project.repository == "acme/beerreel"
    assert project.health == {"production": "https://beerreel.example.org/"}
    merged = project_descriptors(load_descriptors(CREDENTIALS), checkout)
    assert merged["mapbox_token"].github_secret == "MAPBOX_TOKEN"
    assert "smtp_password" in merged
    text = (checkout / "loftline.credentials.yml").read_text(encoding="utf-8")
    assert not any(line.strip().startswith("value:") for line in text.splitlines())


def test_adopt_refuses_a_second_time_and_an_unknown_health_environment(
    checkout: Path,
) -> None:
    run, _ = fake_gh(GH)
    descriptors = load_descriptors(CREDENTIALS)

    with pytest.raises(AdoptError, match="not one of the repository's environments"):
        adopt(
            checkout,
            "acme/beerreel",
            health={"qa": "x"},
            descriptors=descriptors,
            run=run,
        )
    adopt(checkout, "acme/beerreel", descriptors=descriptors, run=run)
    with pytest.raises(AdoptError, match="exists already"):
        adopt(checkout, "acme/beerreel", descriptors=descriptors, run=run)


def test_a_project_descriptor_file_may_not_redefine_a_shared_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "loftline.credentials.yml").write_text(
        "smtp_password:\n  vendor: x\n  scope: project\n  state: manual\n"
        "  consumed_by: [api]\n  environments: [production]\n"
        "  github_secret: SMTP_PASSWORD_2\n  vault_path: p/x\n  acquire: y\n",
        encoding="utf-8",
    )

    with pytest.raises(AdoptError, match="redefines smtp_password"):
        project_descriptors(load_descriptors(CREDENTIALS), tmp_path)


def test_generated_project_commands_refuse_an_adoption(checkout: Path) -> None:
    run, _ = fake_gh(GH)
    adopt(checkout, "acme/beerreel", descriptors=load_descriptors(CREDENTIALS), run=run)

    with pytest.raises(SpecError, match="adopted project"):
        load_spec(checkout / "loftline.yml")
    outcome = CliRunner().invoke(
        app, ["new", str(checkout / "loftline.yml"), str(checkout / "out")]
    )
    assert outcome.exit_code == 1
    assert "adopted" in outcome.output


# --- resolution, plan, sync ------------------------------------------------------


def adoption() -> Adoption:
    return Adoption(
        project_name="beerreel",
        repository="acme/beerreel",
        environments=("staging", "production"),
        health={"production": "https://beerreel.example.org/"},
        credentials={
            "JWT_SECRET": ("production", "staging"),
            "SMTP_PASSWORD": ("production", "staging"),
            "MAPBOX_TOKEN": ("production",),
        },
    )


def descriptors_with_mapbox(tmp_path: Path) -> Any:
    run, _ = fake_gh(GH)
    checkout = tmp_path / "c"
    checkout.mkdir()
    adopt(checkout, "acme/beerreel", descriptors=load_descriptors(CREDENTIALS), run=run)
    return project_descriptors(load_descriptors(CREDENTIALS), checkout)


def test_resolution_partitions_like_a_generated_project(tmp_path: Path) -> None:
    descriptors = descriptors_with_mapbox(tmp_path)
    index = VaultIndex.from_paths([descriptors["smtp_password"].vault_path])

    resolution = resolve_adopted(adoption(), descriptors, index)

    assert [i.name for i in resolution.inject] == ["smtp_password"]
    assert [d.name for d in resolution.derive] == ["jwt_secret"]
    assert [r.name for r in resolution.request] == ["mapbox_token"]
    assert resolution.request[0].vault_path == "beerreel/adopted/mapbox_token"
    assert resolution.features == ("adopted",)


def test_the_plan_names_the_repository_instead_of_a_package(tmp_path: Path) -> None:
    descriptors = descriptors_with_mapbox(tmp_path)
    resolution = resolve_adopted(adoption(), descriptors, VaultIndex.from_paths([]))

    text = render_plan(adoption(), resolution, Path("v.yml"), Path("loftline.yml"), 0)

    assert "adopted from  acme/beerreel" in text
    assert "mapbox_token" in text and "package" not in text


def test_sync_checks_the_declared_url_and_leaves_the_rest_unchecked() -> None:
    asked: list[str] = []

    def plain(url: str) -> bool:
        asked.append(url)
        return True

    status = live_status(adoption(), plain=plain)

    assert asked == ["https://beerreel.example.org/"]
    by_name = {e["name"]: e for e in status["environments"]}
    assert by_name["production"]["healthy"] is True
    assert by_name["staging"]["healthy"] is None
    assert spec_summary(adoption()) == {
        "adopted": True,
        "repository": "acme/beerreel",
        "environments": ["staging", "production"],
        "health": {"production": "https://beerreel.example.org/"},
        "credentials": ["JWT_SECRET", "MAPBOX_TOKEN", "SMTP_PASSWORD"],
    }


class RecordingSite:
    def __init__(self) -> None:
        self.pushed: dict[str, Any] = {}

    def push_project(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.pushed = {"name": name, **kwargs}
        return {}

    def pull_project(self, name: str, *, org_slug: str | None = None) -> dict[str, Any]:
        return {"collaborators": []}

    def mark_applied(
        self, name: str, logins: list[str], *, org_slug: str | None = None
    ) -> None:
        pass


def test_sync_pushes_the_adoption_and_uses_its_repository() -> None:
    site = RecordingSite()

    report = sync_project(
        adoption(),
        site,
        repository=None,
        project_dir=None,
        health=lambda url: False,
        on_github=lambda repo: set(),
    )

    assert site.pushed["spec"]["adopted"] is True
    assert report.repository == "acme/beerreel"
    assert report.unchecked == ("staging",)
    assert site.pushed["status"]["environments"][0]["healthy"] is None


def test_spec_and_adoption_both_load_through_load_project(tmp_path: Path) -> None:
    (tmp_path / "gen.yml").write_text(
        "project_name: shop\npackage_name: shop\ndatabase: postgres\n", encoding="utf-8"
    )
    assert isinstance(load_project(tmp_path / "gen.yml"), Spec)
