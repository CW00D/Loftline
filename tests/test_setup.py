"""`loftline setup` as a scripted conversation against a fake machine."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from loftline import setup as wizard
from loftline.errors import LoftlineError


class ScriptedConsole:
    def __init__(
        self, yes: dict[str, bool] | None = None, answers: dict[str, str] | None = None
    ) -> None:
        self.said: list[str] = []
        self.yes = yes or {}
        self.answers = answers or {}
        self.launched: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)

    def ask_yes(self, question: str, default: bool = True) -> bool:
        for key, value in self.yes.items():
            if key in question:
                return value
        return default

    def ask(self, question: str, default: str = "") -> str:
        for key, value in self.answers.items():
            if key in question:
                return value
        return default

    def ask_hidden(self, question: str) -> str:
        return self.answers.get("token", "")

    def launch(self, url: str) -> None:
        self.launched.append(url)


class FakeMachine:
    def __init__(self, tmp: Path, present: set[str], system: str = "Windows") -> None:
        self.system = system
        self.home = tmp
        self.present = set(present)
        self.runs: list[tuple[tuple[str, ...], bool]] = []
        self.env: dict[str, str] = {}
        self.gh_signed_in = False

    def which(self, tool: str) -> str | None:
        return f"/bin/{tool}" if tool in self.present else None

    def run(self, argv: Sequence[str], *, interactive: bool = False) -> tuple[int, str]:
        self.runs.append((tuple(argv), interactive))
        joined = " ".join(argv)
        if joined.startswith("winget install") or joined.startswith("choco install"):
            ident = argv[-2] if argv[0] == "winget" else argv[2]
            self.present.add(ident.split(".")[-1].lower())
            return 0, ""
        if joined == "gh auth status":
            return (0, "") if self.gh_signed_in else (1, "not logged in")
        if joined.startswith("gh auth login"):
            self.gh_signed_in = True
            return 0, ""
        if argv[0] == "age-keygen":
            path = Path(argv[2])
            path.parent.mkdir(parents=True, exist_ok=True)
            n = len([r for r in self.runs if r[0][0] == "age-keygen"])
            path.write_text(
                f"# created: now\n# public key: age1fake{n}\nAGE-SECRET-KEY-FAKE{n}\n",
                encoding="utf-8",
            )
            return 0, ""
        if joined.startswith("gh repo create"):
            return 0, "https://github.com/me/loftline-vault"
        return 0, ""

    def persist_env(self, name: str, value: str) -> str:
        self.env[name] = value
        return "persisted"


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("LOFTLINE_VAULT", raising=False)
    monkeypatch.setattr(
        wizard, "default_age_key_file", lambda: tmp_path / "sops" / "keys.txt"
    )
    return tmp_path


def test_missing_tools_are_offered_with_the_platforms_installer(isolated: Path) -> None:
    machine = FakeMachine(isolated, present={"winget", "git", "gh"})
    console = ScriptedConsole(yes={"sops is missing": False})
    report = wizard.SetupReport()

    wizard.step_tools(console, machine, report)

    installed = [r[0] for r in machine.runs if r[0][0] == "winget"]
    assert ("winget", "install", "--id", "FiloSottile.age", "-e") in installed
    assert ("winget", "install", "--id", "Hashicorp.Terraform", "-e") in installed
    by_step = {o.step: o for o in report.outcomes}
    assert by_step["tool age"].done is True
    assert (
        by_step["tool sops"].done is False and by_step["tool sops"].detail == "skipped"
    )
    assert by_step["tool git"].detail == "present"


def test_github_sign_in_runs_interactively_only_when_needed(isolated: Path) -> None:
    machine = FakeMachine(isolated, present={"gh"})
    report = wizard.SetupReport()

    wizard.step_github(ScriptedConsole(), machine, report)

    assert any(r[0][:3] == ("gh", "auth", "login") and r[1] for r in machine.runs)
    assert report.outcomes[-1].done is True
    machine.runs.clear()
    wizard.step_github(ScriptedConsole(), machine, report)
    assert not any(r[0][:3] == ("gh", "auth", "login") for r in machine.runs)


def test_the_vault_step_makes_two_keys_a_vault_and_persists_the_variable(
    isolated: Path,
) -> None:
    machine = FakeMachine(isolated, present={"age-keygen", "sops", "gh"})
    machine.gh_signed_in = True
    console = ScriptedConsole(answers={"Where to keep": str(isolated / "vault-dir")})
    report = wizard.SetupReport()
    calls: list[tuple[Path, list[str]]] = []

    def fake_init(directory: Path, recipients: list[str]) -> Path:
        calls.append((directory, recipients))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vault.yml").write_text("loftline: {}\n", encoding="utf-8")
        return directory / "vault.yml"

    vault = wizard.step_vault(console, machine, report, init=fake_init)

    assert vault == isolated / "vault-dir" / "vault.yml"
    assert calls[0][1] == ["age1fake1", "age1fake2"]
    assert (isolated / "sops" / "keys.txt").exists()
    assert machine.env["LOFTLINE_VAULT"] == str(vault.resolve())
    assert any(r[0][:3] == ("gh", "repo", "create") for r in machine.runs)
    assert not any("AGE-SECRET" in s for s in console.said)


def test_an_existing_vault_is_left_alone(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = isolated / "have" / "vault.yml"
    existing.parent.mkdir()
    existing.write_text("loftline: {}\n", encoding="utf-8")
    monkeypatch.setenv("LOFTLINE_VAULT", str(existing))
    machine = FakeMachine(isolated, present={"age-keygen", "sops"})
    report = wizard.SetupReport()

    vault = wizard.step_vault(ScriptedConsole(), machine, report)

    assert vault == existing
    assert machine.runs == []


def test_the_dashboard_step_checks_the_token_before_storing_it(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: list[tuple[str, str]] = []

    class FakeStore:
        def __init__(self, path: Path) -> None:
            pass

        def list_paths(self) -> list[str]:
            return []

        def set(self, path: str, value: str) -> None:
            stored.append((path, value))

    class FakeClient:
        def __init__(self, token: str) -> None:
            self.token = token

        def me(self) -> dict[str, Any]:
            if self.token != "llt_good":
                raise LoftlineError("refused")
            return {"name": "Dev One"}

    monkeypatch.setattr(wizard, "SopsAgeVault", FakeStore)
    report = wizard.SetupReport()
    console = ScriptedConsole(answers={"token": "llt_good"})

    wizard.step_dashboard(
        console,
        FakeMachine(isolated, set()),
        report,
        isolated / "vault.yml",
        client_factory=FakeClient,
    )

    assert console.launched == ["https://staging.loftline.org/app/settings"]
    assert stored == [("loftline/site/token", "llt_good")]
    assert report.outcomes[-1].detail == "signed in as Dev One"


def test_the_claude_entry_is_merged_into_an_existing_config(isolated: Path) -> None:
    config = isolated / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8"
    )
    report = wizard.SetupReport()

    wizard.step_claude(
        ScriptedConsole(),
        FakeMachine(isolated, set()),
        report,
        config_path=config,
        entry=lambda: json.dumps(
            {
                "mcpServers": {
                    "loftline": {"command": "loftline", "args": ["mcp", "serve"]}
                }
            }
        ),
    )

    written = json.loads(config.read_text(encoding="utf-8"))
    assert set(written["mcpServers"]) == {"other", "loftline"}
    assert written["mcpServers"]["loftline"]["args"] == ["mcp", "serve"]
    assert report.outcomes[-1].done is True


def test_no_claude_desktop_means_a_quiet_skip(isolated: Path) -> None:
    report = wizard.SetupReport()

    wizard.step_claude(
        ScriptedConsole(),
        FakeMachine(isolated, set()),
        report,
        config_path=isolated / "nowhere" / "c.json",
    )

    assert report.outcomes[-1].done is False
    assert "not installed" in report.outcomes[-1].detail
