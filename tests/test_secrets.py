"""Secret writer tests.

No `gh`, no vault, no network. The sink and the vault are fakes that record
what they were asked, and the assertions are mostly about what must not
happen: no value in an argument, no value in a report, nothing written when
the plan is not ready.
"""

from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from loftline.errors import SecretsError
from loftline.resolve import Defer, Derive, Inject, Request, Resolution
from loftline.secrets import DERIVATIONS, GitHubSink, derive, write_secrets

ENVS = ("staging", "production")


class FakeVault:
    """A vault that knows a few values and records every read."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.reads: list[str] = []

    def list_paths(self) -> list[str]:
        return sorted(self.values)

    def get(self, path: str) -> str:
        self.reads.append(path)
        return self.values[path]

    def set(self, path: str, value: str) -> None:
        raise AssertionError("the writer must never store into the vault")

    def acquired_at(self, path: str) -> datetime | None:
        return None


class FakeSink:
    def __init__(self, existing: set[tuple[str, str]] | None = None) -> None:
        self.environments: list[str] = []
        self.writes: list[tuple[str, str, str]] = []  # (name, env, value)
        self.existing = existing or set()
        self.preflighted = False

    def preflight(self) -> None:
        self.preflighted = True

    def ensure_environment(self, environment: str) -> None:
        self.environments.append(environment)

    def exists(self, name: str, environment: str) -> bool:
        return (name, environment) in self.existing

    def write(self, name: str, environment: str, value: str) -> None:
        self.writes.append((name, environment, value))
        self.existing.add((name, environment))


def inject(name: str, secret: str, path: str) -> Inject:
    return Inject(name=name, vault_path=path, github_secret=secret, environments=ENVS)


def resolution(**parts: object) -> Resolution:
    return Resolution(features=("base",), **parts)  # type: ignore[arg-type]


# --- held credentials --------------------------------------------------------


def test_held_values_are_read_once_and_written_to_every_environment() -> None:
    vault = FakeVault({"loftline/google/smtp_user": "me@gmail.com"})
    sink = FakeSink()

    report = write_secrets(
        resolution(
            inject=(inject("smtp_user", "SMTP_USER", "loftline/google/smtp_user"),)
        ),
        vault,
        sink,
    )

    assert vault.reads == ["loftline/google/smtp_user"]
    assert sink.writes == [
        ("SMTP_USER", "staging", "me@gmail.com"),
        ("SMTP_USER", "production", "me@gmail.com"),
    ]
    assert [w.source for w in report.written] == ["vault", "vault"]


def test_environments_are_created_before_anything_is_written() -> None:
    sink = FakeSink()
    order: list[str] = []

    def ensure_environment(environment: str) -> None:
        order.append(f"env:{environment}")

    def write(name: str, environment: str, value: str) -> None:
        order.append(f"write:{name}:{environment}")

    sink.ensure_environment = ensure_environment  # type: ignore[method-assign]
    sink.write = write  # type: ignore[method-assign]

    write_secrets(
        resolution(inject=(inject("smtp_user", "SMTP_USER", "p"),)),
        FakeVault({"p": "v"}),
        sink,
    )

    assert order[:2] == ["env:production", "env:staging"]
    assert all(step.startswith("write:") for step in order[2:])


# --- derived credentials -----------------------------------------------------


def test_derived_values_are_generated_per_environment() -> None:
    sink = FakeSink()

    report = write_secrets(
        resolution(
            derive=(
                Derive(
                    name="jwt_secret",
                    derivation="random_bytes_32_base64",
                    github_secret="JWT_SECRET",
                    environments=ENVS,
                ),
            )
        ),
        FakeVault({}),
        sink,
    )

    values = {env: value for name, env, value in sink.writes}
    assert set(values) == set(ENVS)
    assert values["staging"] != values["production"]
    assert all(len(v.encode()) >= 32 for v in values.values())
    assert [w.source for w in report.written] == ["generated", "generated"]


def test_an_existing_derived_secret_is_kept_not_regenerated() -> None:
    """Re-running the writer must not rotate every session token."""
    sink = FakeSink(existing={("JWT_SECRET", "staging")})

    report = write_secrets(
        resolution(
            derive=(Derive("jwt_secret", "random_bytes_32_base64", "JWT_SECRET", ENVS),)
        ),
        FakeVault({}),
        sink,
    )

    assert [(n, e) for n, e, _ in sink.writes] == [("JWT_SECRET", "production")]
    assert {(w.environment, w.source) for w in report.written} == {
        ("staging", "kept"),
        ("production", "generated"),
    }


def test_rotate_regenerates_even_when_present() -> None:
    sink = FakeSink(existing={("JWT_SECRET", "staging"), ("JWT_SECRET", "production")})

    write_secrets(
        resolution(
            derive=(Derive("jwt_secret", "random_bytes_32_base64", "JWT_SECRET", ENVS),)
        ),
        FakeVault({}),
        sink,
        rotate=True,
    )

    assert len(sink.writes) == 2


def test_an_unknown_derivation_fails_before_anything_is_written() -> None:
    sink = FakeSink()

    with pytest.raises(SecretsError, match="no derivation named 'wave hands'"):
        write_secrets(
            resolution(
                inject=(inject("smtp_user", "SMTP_USER", "p"),),
                derive=(Derive("jwt_secret", "wave hands", "JWT_SECRET", ENVS),),
            ),
            FakeVault({"p": "v"}),
            sink,
        )

    assert sink.writes == []
    assert sink.environments == []


def test_the_shipped_derivation_makes_a_long_enough_key() -> None:
    value = derive("random_bytes_32_base64")

    assert len(value.encode()) >= 32
    assert value != derive("random_bytes_32_base64")
    assert "random_bytes_32_base64" in DERIVATIONS


# --- readiness ---------------------------------------------------------------


def test_outstanding_credentials_block_the_whole_write() -> None:
    sink = FakeSink()
    vault = FakeVault({"p": "v"})

    with pytest.raises(SecretsError, match="render_api_key"):
        write_secrets(
            resolution(
                inject=(inject("smtp_user", "SMTP_USER", "p"),),
                request=(
                    Request(
                        "render_api_key",
                        "steps",
                        "loftline/render/api_key",
                        "absent from vault",
                        "render",
                    ),
                ),
            ),
            vault,
            sink,
        )

    assert sink.writes == []
    assert vault.reads == []


def test_partial_writes_what_is_held_and_reports_the_rest() -> None:
    sink = FakeSink()

    report = write_secrets(
        resolution(
            inject=(inject("smtp_user", "SMTP_USER", "p"),),
            request=(
                Request(
                    "render_api_key",
                    "steps",
                    "loftline/render/api_key",
                    "absent from vault",
                    "render",
                ),
            ),
        ),
        FakeVault({"p": "v"}),
        sink,
        partial=True,
    )

    assert len(sink.writes) == 2
    assert report.outstanding == ("render_api_key",)
    assert report.partial is True


def test_produced_credentials_are_reported_not_written() -> None:
    sink = FakeSink()

    report = write_secrets(
        resolution(
            defer=(
                Defer("database_url", "render_postgres_create", "DATABASE_URL", ENVS),
            )
        ),
        FakeVault({}),
        sink,
    )

    assert sink.writes == []
    assert report.deferred == ("database_url",)


def test_the_report_carries_no_values() -> None:
    report = write_secrets(
        resolution(inject=(inject("smtp_user", "SMTP_USER", "p"),)),
        FakeVault({"p": "hunter2-not-a-real-secret"}),
        FakeSink(),
    )

    assert "hunter2" not in repr(report)


# --- the GitHub sink ---------------------------------------------------------


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str | None]]:
    """A fake `gh` that records argv and stdin and always succeeds."""
    calls: list[tuple[list[str], str | None]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs.get("input")))  # type: ignore[arg-type]
        stdout = '[{"name":"JWT_SECRET"}]' if argv[1:3] == ["secret", "list"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_gh_receives_the_value_on_stdin_never_as_an_argument(
    gh: list[tuple[list[str], str | None]],
) -> None:
    GitHubSink("CW00D/demo").write("SMTP_USER", "staging", "me@gmail.com")

    argv, stdin = gh[0]
    assert argv[1:] == [
        "secret",
        "set",
        "SMTP_USER",
        "--repo",
        "CW00D/demo",
        "--env",
        "staging",
    ]
    assert stdin == "me@gmail.com"
    assert "me@gmail.com" not in argv


def test_gh_creates_an_environment_with_an_idempotent_put(
    gh: list[tuple[list[str], str | None]],
) -> None:
    GitHubSink("CW00D/demo").ensure_environment("production")

    argv, _ = gh[0]
    assert argv[1:] == [
        "api",
        "--method",
        "PUT",
        "repos/CW00D/demo/environments/production",
    ]


def test_gh_exists_reads_the_secret_list(
    gh: list[tuple[list[str], str | None]],
) -> None:
    sink = GitHubSink("CW00D/demo")

    assert sink.exists("JWT_SECRET", "staging") is True
    assert sink.exists("SMTP_USER", "staging") is False


def test_gh_preflight_checks_login_and_repo_access(
    gh: list[tuple[list[str], str | None]],
) -> None:
    GitHubSink("CW00D/demo").preflight()

    assert [argv[1:3] for argv, _ in gh] == [["auth", "status"], ["repo", "view"]]


def test_gh_missing_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(SecretsError, match="gh is not on PATH"):
        GitHubSink("CW00D/demo").preflight()


def test_gh_not_logged_in_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="not logged in"
        ),
    )

    with pytest.raises(SecretsError, match="gh auth login"):
        GitHubSink("CW00D/demo").preflight()


def test_a_failed_write_redacts_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr=f"rejected {k.get('input')}"
        ),
    )

    with pytest.raises(SecretsError) as excinfo:
        GitHubSink("CW00D/demo").write("SMTP_USER", "staging", "hunter2-not-real")

    assert "hunter2" not in str(excinfo.value)
    assert "[value redacted]" in str(excinfo.value)
    assert "SMTP_USER" in str(excinfo.value)


def test_repo_must_be_owner_slash_name() -> None:
    with pytest.raises(SecretsError, match="OWNER/NAME"):
        GitHubSink("demo")
