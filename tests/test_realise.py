"""`loftline realise` with a fake runner: the exact sequence, nothing run."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from loftline.models import Spec
from loftline.realise import RealiseError, realise, spec_from_dashboard

from .conftest import spec


class FakeRunner:
    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], str, dict[str, str]]] = []
        self.fail = fail

    def __call__(
        self, argv: Sequence[str], cwd: Path, env: dict[str, str]
    ) -> tuple[int, str]:
        self.calls.append((tuple(argv), cwd.name, dict(env)))
        joined = " ".join(argv)
        if self.fail and self.fail in joined:
            return 1, f"boom: {joined}"
        if joined == "gh auth token":
            return 0, "gho_secret\n"
        if joined == "gh api user -q .login":
            return 0, "cw00d\n"
        return 0, ""


def fake_generate(s: Spec, directory: Path) -> Path:
    (directory / "infra").mkdir(parents=True)
    (directory / "LOFTLINE.md").write_text("# demo\n", encoding="utf-8")
    return directory


def test_the_dashboard_spec_is_filled_and_validated() -> None:
    filled = spec_from_dashboard({"project_name": "demo", "web": True})

    assert filled.package_name == "demo"
    assert filled.database == "postgres"
    assert filled.environments == ("staging",)

    with pytest.raises(RealiseError, match="not valid"):
        spec_from_dashboard({"project_name": "demo", "database": "oracle"})


def test_realise_runs_the_pipeline_in_order_and_stops_before_render(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    secrets_written: list[str] = []

    report = realise(
        spec(project_name="demo", database="postgres", environments=["staging"]),
        tmp_path / "demo",
        generate=fake_generate,
        write_secrets=lambda s, repo: secrets_written.append(repo),
        runner=runner,
        terraform="terraform",
    )

    commands = [" ".join(c[0]) for c in runner.calls]
    assert commands[:3] == [
        "gh auth token",
        "terraform init -input=false",
        "terraform apply -input=false -auto-approve",
    ]
    assert "git push -q origin dev" in commands
    assert any(c.startswith("gh pr create --repo cw00d/demo") for c in commands)
    assert any(c.startswith("gh pr merge --repo cw00d/demo --auto") for c in commands)
    assert report.repository == "cw00d/demo"
    assert secrets_written == ["cw00d/demo"]
    assert (
        (tmp_path / "demo" / "loftline.yml")
        .read_text(encoding="utf-8")
        .startswith("project_name: demo")
    )
    assert "Blueprint" in report.next_step
    assert [s.name for s in report.steps] == [
        "generate",
        "repository",
        "push",
        "secrets",
    ]


def test_the_github_token_reaches_terraform_only_through_the_environment(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()

    realise(
        spec(project_name="demo", database="postgres"),
        tmp_path / "demo",
        generate=fake_generate,
        write_secrets=lambda s, repo: None,
        runner=runner,
        terraform="terraform",
    )

    apply = next(c for c in runner.calls if c[0][1] == "apply")
    assert apply[2] == {"GITHUB_TOKEN": "gho_secret"}
    assert apply[1] == "infra"
    assert not any("gho_secret" in " ".join(c[0]) for c in runner.calls)


def test_a_failing_step_names_itself_and_nothing_later_runs(tmp_path: Path) -> None:
    runner = FakeRunner(fail="terraform apply")
    written: list[str] = []

    with pytest.raises(RealiseError, match="terraform apply failed"):
        realise(
            spec(project_name="demo", database="postgres"),
            tmp_path / "demo",
            generate=fake_generate,
            write_secrets=lambda s, repo: written.append(repo),
            runner=runner,
            terraform="terraform",
        )

    assert written == []
    assert not any(c[0][0] == "git" for c in runner.calls)


def test_a_non_empty_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "precious").write_text("x", encoding="utf-8")

    with pytest.raises(RealiseError, match="not empty"):
        realise(
            spec(project_name="demo", database="postgres"),
            tmp_path / "demo",
            generate=fake_generate,
            write_secrets=lambda s, repo: None,
            runner=FakeRunner(),
            terraform="terraform",
        )
