"""Generation tests.

These render the working tree of `template/` through Copier into temporary
directories. No network, no credentials: rendering is a pure function of the
spec and the template.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from loftline.errors import GenerateError
from loftline.generate import TEMPLATE_ROOT, answers_for, generate
from loftline.models import Hosting, Spec

from .conftest import spec

WORKFLOWS = TEMPLATE_ROOT / "template" / ".github" / "workflows"


def rendered_text_files(root: Path) -> dict[Path, str]:
    """Every rendered file that is text, keyed by path relative to the root."""
    out: dict[Path, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix != ".png":
            out[path.relative_to(root)] = path.read_text(encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def minimal(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project with no overlays."""
    dest = tmp_path_factory.mktemp("minimal") / "plainapi"
    return generate(
        spec(project_name="plainapi", package_name="plainapi", database="aura"), dest
    )


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A project with every overlay."""
    dest = tmp_path_factory.mktemp("full") / "demo-app"
    return generate(
        spec(
            project_name="demo-app",
            package_name="demo_app",
            database="aura",
            mobile=True,
            web=True,
            notifications=True,
        ),
        dest,
    )


@pytest.fixture(scope="module")
def postgres(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The relational database branch, with the notifications overlay."""
    dest = tmp_path_factory.mktemp("postgres") / "relapi"
    return generate(
        spec(
            project_name="relapi",
            package_name="relapi",
            database="postgres",
            notifications=True,
        ),
        dest,
    )


# --- the question set --------------------------------------------------------


def test_copier_asks_exactly_the_spec_questions() -> None:
    """The questions in copier.yml are the Spec's fields, minus environments.

    `hosting` is a nested model in the spec and one flat question per slot in
    Copier, since Copier questions are scalars and lists (ADR-022).
    """
    questions = {
        key
        for key in yaml.safe_load(
            (TEMPLATE_ROOT / "copier.yml").read_text(encoding="utf-8")
        )
        if not key.startswith("_")
    }
    hosting_questions = {f"hosting_{slot}" for slot in Hosting.model_fields}

    spec_questions = set(Spec.model_fields) - {"environments", "hosting"}

    assert questions == spec_questions | hosting_questions


def test_answers_are_exactly_the_spec() -> None:
    answers = answers_for(spec(project_name="beerreel", mobile=True, database="aura"))

    assert answers == {
        "project_name": "beerreel",
        "package_name": "beerreel",
        "database": "aura",
        "mobile": True,
        "web": False,
        "notifications": False,
        "payments": [],
        "hosting_api": "render",
        "hosting_web": "render",
    }


# --- the base ----------------------------------------------------------------


def test_the_base_renders_the_skeleton(minimal: Path) -> None:
    for expected in [
        "README.md",
        "docker-compose.yml",
        "render.yaml",
        ".gitignore",
        ".gitattributes",
        ".copier-answers.yml",
        ".github/workflows/ci.yml",
        "api/main.py",
        "api/routers/__init__.py",
        "api/routers/auth.py",
        "api/tests/test_api.py",
    ]:
        assert (minimal / expected).is_file(), expected


def test_the_placeholder_name_is_gone(minimal: Path) -> None:
    """ADR-014: every `skeleton` was the project name and must be substituted."""
    leftovers = {
        path
        for path, text in rendered_text_files(minimal).items()
        if "skeleton" in text.lower()
    }

    assert leftovers == set()


def test_nothing_is_left_unrendered(minimal: Path, full: Path, postgres: Path) -> None:
    """No Jinja that Copier should have consumed survives.

    A bare `{{` is not evidence: JSX writes `style={{ flex: 1 }}` and GitHub
    writes `${{ secrets.X }}`. What must be gone is every template variable
    and every statement tag.
    """
    for root in (minimal, full, postgres):
        for path, text in rendered_text_files(root).items():
            assert "{%" not in text, path
            for variable in ("project_name", "package_name", "_copier"):
                assert "{{ " + variable not in text, (path, variable)


def test_the_project_name_reaches_the_hosting_blueprint(minimal: Path) -> None:
    blueprint = (minimal / "render.yaml").read_text(encoding="utf-8")

    assert "name: plainapi-api-staging" in blueprint
    assert "name: plainapi-api-prod" in blueprint


def test_no_jinja_suffix_survives(minimal: Path, full: Path, postgres: Path) -> None:
    for root in (minimal, full, postgres):
        assert not list(root.rglob("*.jinja"))


# --- invariants 4 and 5 ------------------------------------------------------


def test_the_answers_file_is_emitted_and_not_gitignored(minimal: Path) -> None:
    """Invariant 4: without it, `copier update` has no state to reconcile."""
    answers = yaml.safe_load(
        (minimal / ".copier-answers.yml").read_text(encoding="utf-8")
    )
    gitignore = (minimal / ".gitignore").read_text(encoding="utf-8")

    assert answers["project_name"] == "plainapi"
    assert answers["mobile"] is False
    assert "_commit" in answers
    assert "copier-answers" not in gitignore


def test_workflows_are_copied_byte_for_byte(
    minimal: Path, full: Path, postgres: Path
) -> None:
    """Invariant 5: `${{ secrets.X }}` must never meet a template engine."""
    aura_ci = (WORKFLOWS / "{% if database == 'aura' %}ci.yml{% endif %}").read_bytes()
    assert (minimal / ".github/workflows/ci.yml").read_bytes() == aura_ci
    assert (full / ".github/workflows/ci.yml").read_bytes() == aura_ci

    postgres_ci = (
        WORKFLOWS / "{% if database == 'postgres' %}ci.yml{% endif %}"
    ).read_bytes()
    assert (postgres / ".github/workflows/ci.yml").read_bytes() == postgres_ci

    eas = next(WORKFLOWS.glob("*eas-build-staging.yml*")).read_bytes()
    assert (full / ".github/workflows/eas-build-staging.yml").read_bytes() == eas
    web = next(WORKFLOWS.glob("*web.yml*")).read_bytes()
    assert (full / ".github/workflows/web.yml").read_bytes() == web


# --- overlays are conditional paths (invariant 6) ----------------------------


def test_overlays_are_absent_when_off(minimal: Path) -> None:
    assert not (minimal / "app").exists()
    assert not (minimal / "web").exists()
    assert not (minimal / ".github/workflows/web.yml").exists()
    assert not (minimal / "api/push.py").exists()
    assert not (minimal / "api/routers/push_tokens.py").exists()
    assert not (minimal / "api/tests/test_push.py").exists()
    assert not (minimal / ".github/workflows/eas-build-staging.yml").exists()


def test_overlays_are_present_when_on(full: Path) -> None:
    for expected in [
        "app/App.js",
        "app/app.config.js",
        "app/eas.json",
        "app/package.json",
        "app/src/screens/LoginScreen.js",
        "app/assets/icon.png",
        "api/push.py",
        "api/routers/push_tokens.py",
        "api/tests/test_push.py",
        ".github/workflows/eas-build-staging.yml",
        "web/package.json",
        "web/index.html",
        "web/vite.config.js",
        "web/src/App.jsx",
        "web/src/pages/Login.jsx",
        "web/.env.example",
        ".github/workflows/web.yml",
    ]:
        assert (full / expected).is_file(), expected


def test_shared_files_are_identical_across_feature_choices(
    minimal: Path, full: Path
) -> None:
    """The point of invariant 6: turning a feature on changes nothing shared.

    Only files carrying the project name may differ, and only in the name.
    """
    shared = [
        "api/routers/auth.py",
        "api/routers/__init__.py",
        "api/schema.py",
        "api/db.py",
        "api/security.py",
        "api/models.py",
        "api/tests/conftest.py",
        "api/tests/test_api.py",
        "api/Dockerfile",
        "api/requirements.txt",
        ".gitignore",
    ]
    for path in shared:
        assert (minimal / path).read_bytes() == (full / path).read_bytes(), path

    # main.py carries the project name in the API title and nothing else.
    minimal_main = (minimal / "api/main.py").read_text(encoding="utf-8")
    full_main = (full / "api/main.py").read_text(encoding="utf-8")
    assert minimal_main.replace("plainapi", "X") == full_main.replace("demo-app", "X")


def test_the_package_name_reaches_the_app_identifiers(full: Path) -> None:
    config = (full / "app/app.config.js").read_text(encoding="utf-8")
    package = (full / "app/package.json").read_text(encoding="utf-8")

    assert 'const BUNDLE_ID = "com.demo_app"' in config
    assert 'slug: "demo-app"' in config
    assert '"name": "demo-app"' in package


# --- infrastructure ----------------------------------------------------------


def test_the_infra_root_module_is_rendered(minimal: Path) -> None:
    for expected in [
        "infra/main.tf",
        "infra/variables.tf",
        "infra/terraform.tfvars",
        "infra/backend.tf.example",
        "infra/.gitignore",
    ]:
        assert (minimal / expected).is_file(), expected


def test_tfvars_carry_the_project_name_and_no_owner(minimal: Path) -> None:
    """The owner comes from the token; the spec has no owner question."""
    tfvars = (minimal / "infra/terraform.tfvars").read_text(encoding="utf-8")

    assert 'repository   = "plainapi"' in tfvars
    assert "owner" not in tfvars


def test_the_root_module_references_loftline_by_source_not_by_copy(
    minimal: Path,
) -> None:
    main = (minimal / "infra/main.tf").read_text(encoding="utf-8")

    assert "//infra/github-project" in main
    assert not (minimal / "infra/github-project").exists()


def test_prod_waits_for_the_ci_job_names_that_exist(minimal: Path) -> None:
    """The protection rule names CI jobs; they must be the jobs ci.yml defines."""
    import yaml

    workflow = yaml.safe_load(
        (minimal / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    variables = (minimal / "infra/variables.tf").read_text(encoding="utf-8")

    for job in ("api", "secrets"):
        assert job in workflow["jobs"]
        assert f'"{job}"' in variables


def test_terraform_state_is_never_tracked(minimal: Path) -> None:
    ignored = (minimal / "infra/.gitignore").read_text(encoding="utf-8")

    for pattern in ("*.tfstate", ".terraform/", "backend.tf"):
        assert pattern in ignored


# --- the database branch is a directory (ADR-023) -----------------------------


def test_the_postgres_branch_renders_its_own_data_layer(postgres: Path) -> None:
    for expected in [
        "api/db.py",
        "api/tables.py",
        "api/alembic.ini",
        "api/migrations/env.py",
        "api/migrations/script.py.mako",
        "api/migrations/versions/0001_users.py",
        "api/migrations/versions/0002_push_token.py",
        "api/tables_push.py",
        "api/routers/auth.py",
        "api/routers/push_tokens.py",
        "api/tests/conftest.py",
        "api/tests/test_push.py",
        "api/.env.example",
        "api/requirements.txt",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
    ]:
        assert (postgres / expected).is_file(), expected
    # Nothing of the graph branch leaks across.
    assert not (postgres / "api/schema.py").exists()
    requirements = (postgres / "api/requirements.txt").read_text(encoding="utf-8")
    assert "neo4j" not in requirements
    assert "sqlalchemy" in requirements
    compose = (postgres / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:16" in compose


def test_the_aura_branch_has_no_relational_files(minimal: Path) -> None:
    assert not (minimal / "api/tables.py").exists()
    assert not (minimal / "api/migrations").exists()
    assert not (minimal / "api/alembic.ini").exists()


def test_the_push_token_migration_is_an_overlay(tmp_path: Path) -> None:
    project = generate(
        spec(project_name="quiet", database="postgres", notifications=False),
        tmp_path / "quiet",
    )

    assert (project / "api/migrations/versions/0001_users.py").is_file()
    assert not (project / "api/migrations/versions/0002_push_token.py").exists()
    assert not (project / "api/tables_push.py").exists()
    assert not (project / "api/routers/push_tokens.py").exists()


def test_shared_files_are_identical_across_databases(
    minimal: Path, postgres: Path
) -> None:
    """The database is a directory; what is outside it does not change."""
    for path in [
        "api/security.py",
        "api/models.py",
        "api/config.py",
        "api/Dockerfile",
        "api/pyproject.toml",
        "api/requirements-dev.txt",
        "api/routers/__init__.py",
        "api/tests/test_api.py",
        ".gitignore",
    ]:
        assert (minimal / path).read_bytes() == (postgres / path).read_bytes(), path

    minimal_main = (minimal / "api/main.py").read_text(encoding="utf-8")
    postgres_main = (postgres / "api/main.py").read_text(encoding="utf-8")
    assert minimal_main.replace("plainapi", "X") == postgres_main.replace("relapi", "X")


def test_the_blueprint_creates_a_render_postgres_per_environment(
    postgres: Path,
) -> None:
    blueprint = yaml.safe_load((postgres / "render.yaml").read_text(encoding="utf-8"))

    assert [d["name"] for d in blueprint["databases"]] == [
        "relapi-db-staging",
        "relapi-db-prod",
    ]
    staging = next(
        s for s in blueprint["services"] if s["name"] == "relapi-api-staging"
    )
    url = next(v for v in staging["envVars"] if v["key"] == "DATABASE_URL")
    assert url["fromDatabase"] == {
        "name": "relapi-db-staging",
        "property": "connectionString",
    }
    assert not any(v["key"].startswith("NEO4J") for v in staging["envVars"])


def test_the_web_overlay_adds_static_sites_and_tells_the_api(full: Path) -> None:
    """ADR-024: a static site per environment, the API's CORS pointed at it."""
    blueprint = yaml.safe_load((full / "render.yaml").read_text(encoding="utf-8"))
    by_name = {s["name"]: s for s in blueprint["services"]}

    assert set(by_name) == {
        "demo-app-api-staging",
        "demo-app-api-prod",
        "demo-app-web-staging",
        "demo-app-web-prod",
    }
    web = by_name["demo-app-web-prod"]
    assert web["runtime"] == "static"
    assert web["rootDir"] == "web"
    assert web["routes"] == [
        {"type": "rewrite", "source": "/*", "destination": "/index.html"}
    ]
    api_url = next(v for v in web["envVars"] if v["key"] == "VITE_API_URL")
    assert api_url["value"] == "https://demo-app-api-prod.onrender.com"
    cors = next(
        v for v in by_name["demo-app-api-prod"]["envVars"] if v["key"] == "CORS_ORIGINS"
    )
    assert cors["value"] == "https://demo-app-web-prod.onrender.com"


def test_without_web_the_blueprint_has_no_site_and_no_cors(minimal: Path) -> None:
    blueprint = yaml.safe_load((minimal / "render.yaml").read_text(encoding="utf-8"))

    assert [s["name"] for s in blueprint["services"]] == [
        "plainapi-api-staging",
        "plainapi-api-prod",
    ]
    keys = {v["key"] for v in blueprint["services"][0]["envVars"]}
    assert "CORS_ORIGINS" not in keys


def test_the_project_name_reaches_the_web_manifest_and_title(full: Path) -> None:
    package = (full / "web/package.json").read_text(encoding="utf-8")
    html = (full / "web/index.html").read_text(encoding="utf-8")

    assert '"name": "demo-app-web"' in package
    assert "<title>demo-app</title>" in html


def test_the_aura_blueprint_creates_no_database(minimal: Path) -> None:
    blueprint = yaml.safe_load((minimal / "render.yaml").read_text(encoding="utf-8"))

    assert "databases" not in blueprint
    keys = {v["key"] for v in blueprint["services"][0]["envVars"]}
    assert "NEO4J_URI" in keys
    assert "DATABASE_URL" not in keys


# --- refusals ----------------------------------------------------------------


def test_a_non_empty_destination_is_refused(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "precious.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(GenerateError, match="not empty"):
        generate(spec(database="aura"), dest)

    assert (dest / "precious.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_overwrite_renders_into_a_non_empty_destination(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "precious.txt").write_text("kept", encoding="utf-8")

    generate(spec(database="aura"), dest, overwrite=True)

    assert (dest / "README.md").is_file()
    assert (dest / "precious.txt").read_text(encoding="utf-8") == "kept"


# --- ADR-022: hosting per component ------------------------------------------


def test_the_render_blueprint_is_omitted_when_nothing_is_on_render(
    tmp_path: Path,
) -> None:
    """A project hosted elsewhere must not carry a Render blueprint.

    hosting.api has only one provider today, so the case is exercised through
    the web slot: web on Vercel is not renderable yet and is refused, which is
    the generator telling the truth rather than emitting a half-project.
    """
    elsewhere = spec(
        project_name="elsewhere", database="aura", web=True, hosting={"web": "vercel"}
    )
    with pytest.raises(GenerateError, match=r"hosting\.web: vercel"):
        generate(elsewhere, tmp_path / "elsewhere")


def test_the_render_blueprint_path_is_conditional() -> None:
    names = [p.name for p in (TEMPLATE_ROOT / "template").iterdir()]
    blueprint = [n for n in names if n.endswith("render.yaml{% endif %}.jinja")]

    assert len(blueprint) == 1
    assert "hosting_api == 'render'" in blueprint[0]
    assert "hosting_web == 'render'" in blueprint[0]
