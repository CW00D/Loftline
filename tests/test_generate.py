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
from loftline.models import Spec

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
    """A project with both overlays."""
    dest = tmp_path_factory.mktemp("full") / "demo-app"
    return generate(
        spec(
            project_name="demo-app",
            package_name="demo_app",
            database="aura",
            mobile=True,
            notifications=True,
        ),
        dest,
    )


# --- the question set --------------------------------------------------------


def test_copier_asks_exactly_the_spec_questions() -> None:
    """The five questions in copier.yml are the Spec's fields, minus environments."""
    questions = {
        key
        for key in yaml.safe_load(
            (TEMPLATE_ROOT / "copier.yml").read_text(encoding="utf-8")
        )
        if not key.startswith("_")
    }

    assert questions == set(Spec.model_fields) - {"environments"}


def test_answers_are_exactly_the_spec() -> None:
    answers = answers_for(spec(project_name="beerreel", mobile=True, database="aura"))

    assert answers == {
        "project_name": "beerreel",
        "package_name": "beerreel",
        "database": "aura",
        "mobile": True,
        "notifications": False,
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


def test_nothing_is_left_unrendered(minimal: Path, full: Path) -> None:
    """No Jinja that Copier should have consumed survives.

    A bare `{{` is not evidence: JSX writes `style={{ flex: 1 }}` and GitHub
    writes `${{ secrets.X }}`. What must be gone is every template variable
    and every statement tag.
    """
    for root in (minimal, full):
        for path, text in rendered_text_files(root).items():
            assert "{%" not in text, path
            for variable in ("project_name", "package_name", "_copier"):
                assert "{{ " + variable not in text, (path, variable)


def test_the_project_name_reaches_the_hosting_blueprint(minimal: Path) -> None:
    blueprint = (minimal / "render.yaml").read_text(encoding="utf-8")

    assert "name: plainapi-api-staging" in blueprint
    assert "name: plainapi-api-prod" in blueprint


def test_no_jinja_suffix_survives(minimal: Path, full: Path) -> None:
    for root in (minimal, full):
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


def test_workflows_are_copied_byte_for_byte(minimal: Path, full: Path) -> None:
    """Invariant 5: `${{ secrets.X }}` must never meet a template engine."""
    ci = (WORKFLOWS / "ci.yml").read_bytes()
    assert (minimal / ".github/workflows/ci.yml").read_bytes() == ci
    assert (full / ".github/workflows/ci.yml").read_bytes() == ci

    eas = next(WORKFLOWS.glob("*eas-build-staging.yml*")).read_bytes()
    assert (full / ".github/workflows/eas-build-staging.yml").read_bytes() == eas


# --- overlays are conditional paths (invariant 6) ----------------------------


def test_overlays_are_absent_when_off(minimal: Path) -> None:
    assert not (minimal / "app").exists()
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


# --- refusals ----------------------------------------------------------------


def test_a_database_with_no_template_branch_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GenerateError, match="postgres"):
        generate(spec(database="postgres"), tmp_path / "out")

    assert not (tmp_path / "out").exists()


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
