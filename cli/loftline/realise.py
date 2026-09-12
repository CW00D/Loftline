"""`loftline realise`: a project defined on the dashboard becomes real.

The dashboard holds a spec someone wrote there. This runs the pipeline on
the administrator's machine, where the vault and the GitHub token are, in
the order a person would: generate, create the repository with Terraform,
commit on top of its first commit and push, open the promotion pull
request, write the secrets, and report back to the dashboard. It stops
before the one step only a person can do, connecting the blueprint on
Render, and says so.

Every external command goes through an injectable runner so the sequence
is testable without Terraform, git or GitHub. Nothing here prints or
returns a credential; the GitHub token reaches Terraform through the
environment, from `gh auth token`, and is never written down.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import LoftlineError
from .models import Spec

# (argv, cwd, extra env) -> (returncode, stdout+stderr)
Runner = Callable[[Sequence[str], Path, dict[str, str]], tuple[int, str]]


class RealiseError(LoftlineError):
    pass


def subprocess_runner(
    argv: Sequence[str], cwd: Path, env: dict[str, str]
) -> tuple[int, str]:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


@dataclass(frozen=True)
class Step:
    name: str
    done: bool
    detail: str = ""


@dataclass(frozen=True)
class RealiseReport:
    project: str
    directory: Path
    repository: str | None
    steps: tuple[Step, ...] = field(default_factory=tuple)
    next_step: str = ""


def spec_from_dashboard(data: dict[str, Any]) -> Spec:
    """A Spec from what the dashboard stored, filling what the dashboard does
    not ask. Invalid choices fail here, before anything is created."""
    filled = dict(data)
    filled.setdefault(
        "package_name", str(filled.get("project_name", "")).replace("-", "_")
    )
    filled.setdefault("environments", ["staging"])
    filled.setdefault("database", "postgres")
    try:
        return Spec.model_validate(filled)
    except ValueError as exc:
        raise RealiseError(f"the dashboard's spec is not valid:\n{exc}") from exc


def _run(
    runner: Runner, argv: Sequence[str], cwd: Path, env: dict[str, str], what: str
) -> str:
    code, output = runner(argv, cwd, env)
    if code != 0:
        raise RealiseError(f"{what} failed:\n{output.strip()[-2000:]}")
    return output


def _github_token(runner: Runner, cwd: Path) -> str:
    token = _run(runner, ["gh", "auth", "token"], cwd, {}, "gh auth token").strip()
    if not token:
        raise RealiseError("gh auth token gave nothing; run `gh auth login` first")
    return token


def realise(
    spec: Spec,
    directory: Path,
    *,
    generate: Callable[[Spec, Path], Path],
    write_secrets: Callable[[Spec, str], None],
    runner: Runner = subprocess_runner,
    terraform: str | None = None,
    owner: str | None = None,
) -> RealiseReport:
    """Run the pipeline for a spec into `directory`. Idempotent where the
    tools are: an existing directory is refused, an existing repository is
    reused by Terraform's own state."""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise RealiseError(f"{directory} is not empty; choose a fresh directory")
    steps: list[Step] = []

    # 1. generate, and keep the spec with the project
    generate(spec, directory)
    (directory / "loftline.yml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    steps.append(Step("generate", True, str(directory)))

    # 2. the repository
    tf = terraform or shutil.which("terraform")
    if not tf:
        raise RealiseError(
            "terraform is not on PATH. Install it, or pass --terraform with its path."
        )
    infra = directory / "infra"
    token_env = {"GITHUB_TOKEN": _github_token(runner, directory)}
    _run(runner, [tf, "init", "-input=false"], infra, {}, "terraform init")
    _run(
        runner,
        [tf, "apply", "-input=false", "-auto-approve"],
        infra,
        token_env,
        "terraform apply",
    )
    login = (
        owner
        or _run(
            runner, ["gh", "api", "user", "-q", ".login"], directory, {}, "gh api user"
        ).strip()
    )
    repository = f"{login}/{spec.project_name}"
    steps.append(Step("repository", True, repository))

    # 3. commit on top of Terraform's first commit, push, open the promotion
    clone_url = f"https://github.com/{repository}.git"
    for argv, what in (
        (["git", "init", "-q", "-b", "dev"], "git init"),
        (["git", "remote", "add", "origin", clone_url], "git remote add"),
        (["git", "fetch", "-q", "origin"], "git fetch"),
        (["git", "reset", "-q", "--soft", "origin/dev"], "git reset"),
        (["git", "add", "-A"], "git add"),
        (["git", "commit", "-q", "-m", "Generated by Loftline"], "git commit"),
        (["git", "push", "-q", "origin", "dev"], "git push"),
    ):
        _run(runner, argv, directory, {}, what)
    _run(
        runner,
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            "staging",
            "--head",
            "dev",
            "--title",
            "Promote to staging",
            "--body",
            "Generated by Loftline.",
        ],
        directory,
        {},
        "gh pr create",
    )
    _run(
        runner,
        ["gh", "pr", "merge", "--repo", repository, "--auto", "--merge", "dev"],
        directory,
        {},
        "gh pr merge --auto",
    )
    steps.append(
        Step(
            "push", True, "dev pushed; pull request to staging opened, auto-merge armed"
        )
    )

    # 4. secrets
    write_secrets(spec, repository)
    steps.append(Step("secrets", True, "written to the GitHub environments"))

    return RealiseReport(
        project=spec.project_name,
        directory=directory,
        repository=repository,
        steps=tuple(steps),
        next_step=(
            "Connect the repository's render.yaml as a Blueprint in the Render "
            f"dashboard (New, Blueprint, {repository}), then run "
            f"`loftline provision {directory / 'loftline.yml'} --repo {repository}`."
        ),
    )
