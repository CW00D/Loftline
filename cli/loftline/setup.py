"""`loftline setup`: a fresh machine to a working Loftline, one conversation.

Everything the install page says, done rather than described: the tools
Loftline drives, GitHub sign-in, an age key and a backup, an encrypted
vault in a private repository, the dashboard token, the loftline:// handler,
and the Claude Desktop entry. Each step checks first and asks before it
changes anything, so re-running is harmless and stops nowhere it need not.

Nothing here prints a credential. The age keys are written to files and
only their public halves are read back; the dashboard token goes from a
hidden prompt into the vault.

The console and the environment are injected so the whole conversation is
testable without a terminal, a package manager or a network.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .bootstrap import init_vault, mcp_config
from .doctor import VAULT_ENV, VaultConfig, default_age_key_file, run_checks
from .errors import LoftlineError
from .models import load_descriptors
from .paths import credentials_file
from .site import DEFAULT_SITE, SiteClient
from .urlhandler import UrlError, register
from .vault_sops import SopsAgeVault

DEFAULT_SITE_WEB = "https://staging.loftline.org"


class Console(Protocol):
    def say(self, text: str) -> None: ...
    def ask_yes(self, question: str, default: bool = True) -> bool: ...
    def ask(self, question: str, default: str = "") -> str: ...
    def ask_hidden(self, question: str) -> str: ...
    def launch(self, url: str) -> None: ...


class Machine(Protocol):
    """What the wizard touches on the machine, so a test can stand in."""

    system: str  # "Windows" | "Darwin" | "Linux"
    home: Path

    def which(self, tool: str) -> str | None: ...
    def run(
        self, argv: Sequence[str], *, interactive: bool = False
    ) -> tuple[int, str]: ...
    def persist_env(self, name: str, value: str) -> str: ...
    def elevated(self) -> bool: ...


class RealMachine:
    system = platform.system()
    home = Path.home()

    def which(self, tool: str) -> str | None:
        found = shutil.which(tool)
        if found is None and self.system == "Windows" and tool == "winget":
            # The App Execution Alias folder is on the user's PATH but not
            # always on the one a tool inherits.
            alias = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Microsoft"
                / "WindowsApps"
                / "winget.exe"
            )
            if alias.exists():
                return str(alias)
        return found

    def elevated(self) -> bool:
        if self.system != "Windows":
            return True
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def run(self, argv: Sequence[str], *, interactive: bool = False) -> tuple[int, str]:
        if interactive:
            code = subprocess.call(list(argv))
            return code, ""
        result = subprocess.run(list(argv), capture_output=True, text=True, check=False)
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def persist_env(self, name: str, value: str) -> str:
        """Set a user environment variable for future shells, and for now."""
        os.environ[name] = value
        if self.system == "Windows":
            subprocess.run(["setx", name, value], capture_output=True, check=False)
            return "set for your user account; open a new terminal to see it"
        shell = os.environ.get("SHELL", "")
        rc = self.home / (".zshrc" if shell.endswith("zsh") else ".bashrc")
        line = f'export {name}="{value}"'
        existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
        if line not in existing:
            with rc.open("a", encoding="utf-8") as f:
                f.write(f"\n# Loftline\n{line}\n")
        return f"added to {rc}; open a new terminal to see it"


@dataclass(frozen=True)
class Outcome:
    step: str
    done: bool
    detail: str


@dataclass
class SetupReport:
    outcomes: list[Outcome] = field(default_factory=list)

    def note(self, step: str, done: bool, detail: str) -> None:
        self.outcomes.append(Outcome(step, done, detail))


# --- 1. the tools -------------------------------------------------------------

# tool -> (why, {manager: command})
TOOLS: dict[str, tuple[str, dict[str, str]]] = {
    "git": (
        "versions every project",
        {
            "winget": "winget install --id Git.Git -e",
            "brew": "brew install git",
            "apt": "sudo apt install -y git",
        },
    ),
    "gh": (
        "creates repositories and writes secrets as you",
        {
            "winget": "winget install --id GitHub.cli -e",
            "brew": "brew install gh",
            "apt": "sudo apt install -y gh",
        },
    ),
    "age": (
        "encrypts your vault",
        {
            "winget": "winget install --id FiloSottile.age -e",
            "choco": "choco install age -y",
            "brew": "brew install age",
            "apt": "sudo apt install -y age",
        },
    ),
    "sops": (
        "edits your vault",
        {
            "choco": "choco install sops -y",
            "brew": "brew install sops",
            "winget": "winget install --id Mozilla.sops -e",
        },
    ),
    "terraform": (
        "creates each repository's branches and protection",
        {
            "winget": "winget install --id Hashicorp.Terraform -e",
            "choco": "choco install terraform -y",
            "brew": "brew install terraform",
        },
    ),
}


def _managers(machine: Machine) -> list[str]:
    order = {
        "Windows": ["winget", "choco"],
        "Darwin": ["brew"],
        "Linux": ["brew", "apt"],
    }
    return [m for m in order.get(machine.system, []) if machine.which(m)]


def step_tools(console: Console, machine: Machine, report: SetupReport) -> None:
    console.say("\n1. The tools Loftline drives")
    managers = _managers(machine)
    for tool, (why, commands) in TOOLS.items():
        if machine.which(tool):
            console.say(f"   {tool}: present")
            report.note(f"tool {tool}", True, "present")
            continue
        manager = next((m for m in managers if m in commands), None)
        command = commands[manager] if manager else None
        if command is None:
            console.say(
                f"   {tool} is missing ({why}). Install it by hand, then re-run setup."
            )
            report.note(f"tool {tool}", False, "missing, no package manager for it")
            continue
        if manager == "choco" and not machine.elevated():
            console.say(
                f"   {tool} is missing ({why}). Chocolatey needs an Administrator "
                "window: open PowerShell with Run as administrator and run\n"
                f"       {command}\n   then run loftline setup again."
            )
            report.note(
                f"tool {tool}", False, "needs an Administrator window; see above"
            )
            continue
        if console.ask_yes(
            f"   {tool} is missing ({why}). Install it now with: {command}"
        ):
            code, _ = machine.run(command.split(), interactive=True)
            ok = code == 0 and machine.which(tool) is not None
            report.note(
                f"tool {tool}",
                ok,
                "installed" if ok else "install did not succeed; see above",
            )
            if not ok:
                console.say(
                    f"   {tool} still is not on PATH. A new terminal may be "
                    "needed after installing."
                )
        else:
            report.note(f"tool {tool}", False, "skipped")


# --- 2. GitHub ---------------------------------------------------------------


def step_github(console: Console, machine: Machine, report: SetupReport) -> None:
    console.say("\n2. GitHub")
    if not machine.which("gh"):
        report.note("github", False, "gh is not installed")
        return
    code, _ = machine.run(["gh", "auth", "status"])
    if code == 0:
        console.say("   Already signed in.")
        report.note("github", True, "signed in")
        return
    if console.ask_yes("   Sign in to GitHub now? A browser window will open."):
        code, _ = machine.run(
            ["gh", "auth", "login", "--web", "--git-protocol", "https"],
            interactive=True,
        )
        report.note(
            "github",
            code == 0,
            "signed in" if code == 0 else "sign-in did not complete",
        )
    else:
        report.note("github", False, "skipped")


# --- 3. keys and the vault ---------------------------------------------------


def public_key_of(key_file: Path) -> str | None:
    """The public half from an age key file. The private line is never read out."""
    for line in key_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 1)[1].strip()
    return None


def _generate_key(machine: Machine, path: Path) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    code, _ = machine.run(["age-keygen", "-o", str(path)])
    if code != 0 or not path.exists():
        return None
    return public_key_of(path)


def step_vault(
    console: Console,
    machine: Machine,
    report: SetupReport,
    *,
    init: Callable[[Path, list[str]], Path] = init_vault,
) -> Path | None:
    console.say("\n3. Your vault")
    config = VaultConfig.from_env()
    if config.vault_path is not None and config.vault_path.exists():
        console.say(f"   Already at {config.vault_path}.")
        report.note("vault", True, f"already at {config.vault_path}")
        return config.vault_path
    if not (machine.which("age-keygen") and machine.which("sops")):
        report.note("vault", False, "age and sops are needed first")
        return None

    key_file = default_age_key_file()
    if key_file.exists():
        public = public_key_of(key_file)
        report.note("age key", public is not None, f"already at {key_file}")
    else:
        console.say(f"   Generating this machine's key at {key_file}")
        public = _generate_key(machine, key_file)
        report.note(
            "age key",
            public is not None,
            str(key_file) if public else "age-keygen failed",
        )
    if public is None:
        return None

    console.say(
        "   A vault needs a second key held somewhere else, or losing this "
        "machine loses everything. Paste the public key (age1...) of a key you "
        "already keep, or leave blank to generate a backup key file now and "
        "move it to a USB stick or password manager."
    )
    backup = console.ask("   Backup public key", "").strip()
    if not backup:
        backup_file = machine.home / "Desktop" / "loftline-backup-key.txt"
        if not backup_file.parent.exists():
            backup_file = machine.home / "loftline-backup-key.txt"
        backup = _generate_key(machine, backup_file) or ""
        if not backup:
            report.note("backup key", False, "age-keygen failed")
            return None
        console.say(
            f"   Backup key written to {backup_file}. Move it off this machine "
            "and never commit it."
        )
        report.note("backup key", True, str(backup_file))
    else:
        report.note("backup key", True, "supplied")

    where = Path(
        console.ask("   Where to keep the vault", str(machine.home / "loftline-vault"))
    )
    try:
        vault = init(where, [public, backup])
    except LoftlineError as exc:
        report.note("vault", False, str(exc))
        return None
    how = machine.persist_env(VAULT_ENV, str(vault.resolve()))
    report.note("vault", True, f"{vault} ({VAULT_ENV} {how})")

    if machine.which("gh") and machine.run(["gh", "auth", "status"])[0] == 0:
        if console.ask_yes(
            "   Push the vault to a new private GitHub repository named loftline-vault?"
        ):
            code, output = machine.run(
                [
                    "gh",
                    "repo",
                    "create",
                    "loftline-vault",
                    "--private",
                    "--source",
                    str(where),
                    "--push",
                ]
            )
            report.note(
                "vault repository",
                code == 0,
                "pushed" if code == 0 else output.strip()[-300:],
            )
    return vault


# --- 4. the dashboard --------------------------------------------------------


def step_dashboard(
    console: Console,
    machine: Machine,
    report: SetupReport,
    vault: Path | None,
    *,
    site: str = DEFAULT_SITE,
    site_web: str = DEFAULT_SITE_WEB,
    client_factory: Callable[[str], Any] | None = None,
) -> None:
    console.say("\n4. The dashboard")
    if vault is None:
        report.note("dashboard", False, "needs the vault first")
        return
    descriptor = load_descriptors(credentials_file())["loftline_site_token"]
    assert descriptor.vault_path is not None
    store = SopsAgeVault(vault)
    if descriptor.vault_path in store.list_paths():
        console.say("   Token already stored.")
        report.note("dashboard", True, "token already stored")
        return
    if not console.ask_yes(
        f"   Connect to {site_web}? Sign in there, then Settings, create a token."
    ):
        report.note("dashboard", False, "skipped")
        return
    console.launch(f"{site_web}/app/settings")
    token = console.ask_hidden("   Paste the token").strip()
    if not token:
        report.note("dashboard", False, "no token given")
        return
    make = client_factory or (lambda t: SiteClient(t, site=site))
    try:
        me = make(token).me()
    except LoftlineError as exc:
        report.note("dashboard", False, str(exc))
        return
    store.set(descriptor.vault_path, token)
    report.note("dashboard", True, f"signed in as {me.get('name')}")


# --- 5. the loftline:// handler and Claude ------------------------------------


def step_handler(console: Console, report: SetupReport) -> None:
    console.say("\n5. Store buttons on the dashboard")
    try:
        report.note("url handler", True, register())
    except UrlError as exc:
        report.note("url handler", False, str(exc))


def claude_desktop_config(machine: Machine) -> Path:
    if machine.system == "Windows":
        return (
            Path(os.environ.get("APPDATA", machine.home / "AppData" / "Roaming"))
            / "Claude"
            / "claude_desktop_config.json"
        )
    if machine.system == "Darwin":
        return (
            machine.home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return machine.home / ".config" / "Claude" / "claude_desktop_config.json"


def step_claude(
    console: Console,
    machine: Machine,
    report: SetupReport,
    *,
    config_path: Path | None = None,
    entry: Callable[[], str] = lambda: mcp_config("desktop"),
) -> None:
    console.say("\n6. Claude Desktop")
    path = config_path or claude_desktop_config(machine)
    if not path.parent.exists():
        report.note("claude desktop", False, "not installed here; skipped")
        return
    if not console.ask_yes(
        "   Add Loftline to Claude Desktop, so a conversation can drive it?"
    ):
        report.note("claude desktop", False, "skipped")
        return
    try:
        addition = json.loads(entry())
    except LoftlineError as exc:
        report.note("claude desktop", False, str(exc))
        return
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            report.note(
                "claude desktop",
                False,
                f"{path} is not valid JSON; add the entry by hand",
            )
            return
    servers = existing.setdefault("mcpServers", {})
    servers["loftline"] = addition["mcpServers"]["loftline"]
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    report.note(
        "claude desktop", True, f"entry written to {path}; restart Claude Desktop"
    )


# --- the whole conversation --------------------------------------------------


def run_setup(
    console: Console,
    machine: Machine | None = None,
    *,
    site: str = DEFAULT_SITE,
    site_web: str = DEFAULT_SITE_WEB,
) -> SetupReport:
    machine = machine or RealMachine()
    report = SetupReport()
    console.say(
        "Loftline setup. Each step checks first and asks before changing anything."
    )
    step_tools(console, machine, report)
    step_github(console, machine, report)
    vault = step_vault(console, machine, report)
    step_dashboard(console, machine, report, vault, site=site, site_web=site_web)
    step_handler(console, report)
    step_claude(console, machine, report)

    console.say("\nSummary")
    for o in report.outcomes:
        console.say(f"  {'ok  ' if o.done else 'todo'}  {o.step:18} {o.detail}")
    if vault is not None:
        failed = [
            c
            for c in run_checks(VaultConfig.from_env(vault))
            if c.status.value == "fail"
        ]
        if failed:
            console.say("\nloftline doctor still reports:")
            for c in failed:
                console.say(f"  {c.name}: {c.detail}")
        else:
            console.say("\nloftline doctor: all preconditions met.")
    return report


def frozen_executable() -> str | None:
    """The path of this executable when running as the built binary."""
    return sys.executable if getattr(sys, "frozen", False) else None
