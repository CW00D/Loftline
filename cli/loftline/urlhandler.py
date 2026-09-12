"""`loftline://` links: a button on the dashboard opens a terminal here.

A browser cannot start a terminal, but it can open a link the operating
system has a handler for, the way vscode:// opens VS Code. `loftline
register-url-handler` tells the system that loftline:// links belong to
this CLI; a link then arrives as `loftline url <link>`, which opens a new
terminal window already running the command the link names.

Only one kind of link exists: `loftline://vault/set/<credential name>`,
which runs `loftline vault set <name>` and waits for the paste. The link
carries a name, never a value; the value goes into the hidden prompt in the
terminal window and nowhere else.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import LoftlineError

SCHEME = "loftline"
NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class UrlError(LoftlineError):
    pass


@dataclass(frozen=True)
class Action:
    argv: tuple[str, ...]  # the loftline command the link asks for
    title: str


def parse(link: str) -> Action:
    """The command a loftline:// link asks for, or a clear error."""
    parts = urlsplit(link)
    if parts.scheme != SCHEME:
        raise UrlError(f"not a {SCHEME}:// link: {link}")
    path = [p for p in (parts.netloc + parts.path).split("/") if p]
    if (
        len(path) == 3
        and path[0] == "vault"
        and path[1] == "set"
        and NAME.match(path[2])
    ):
        return Action(("loftline", "vault", "set", path[2]), f"Store {path[2]}")
    raise UrlError(
        f"unknown {SCHEME}:// link: {link}. The only kind is "
        f"{SCHEME}://vault/set/<credential name>."
    )


def open_terminal(
    action: Action, *, run: Callable[[Sequence[str]], None] | None = None
) -> None:
    """A new terminal window running the action, left open for the paste."""
    command = " ".join(shlex.quote(a) for a in action.argv)
    runner = run or _spawn
    if sys.platform == "win32":
        # `start` with a title, then cmd stays open (/k) with the prompt live.
        runner(["cmd", "/c", "start", action.title, "cmd", "/k", " ".join(action.argv)])
    elif sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "{command}"'
        runner(["osascript", "-e", script])
    else:
        runner(
            [
                "x-terminal-emulator",
                "-e",
                f"bash -c {shlex.quote(command + '; exec bash')}",
            ]
        )


def _spawn(argv: Sequence[str]) -> None:
    subprocess.Popen(list(argv))


def register(*, executable: str | None = None, home: Path | None = None) -> str:
    """Tell the operating system that loftline:// links open this CLI.
    Returns a sentence saying what was done."""
    exe = executable or _loftline_executable()
    if sys.platform == "win32":
        return _register_windows(exe)
    if sys.platform == "darwin":
        raise UrlError(
            "macOS registers URL schemes through an application bundle, which the "
            "CLI does not ship yet. Use the dashboard's Copy button instead."
        )
    return _register_linux(exe, home or Path.home())


def _loftline_executable() -> str:
    found = shutil_which("loftline")
    if found:
        return found
    raise UrlError(
        "loftline is not on PATH; install it with `uv tool install` or "
        "`pip install -e .`"
    )


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def _register_windows(exe: str) -> str:
    import winreg

    command = f'"{exe}" url "%1"'
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Loftline")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}\shell\open\command"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    return f"Registered {SCHEME}:// for the current user, opening {exe}."


def _register_linux(exe: str, home: Path) -> str:
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / "loftline-url.desktop"
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Loftline\n"
        f"Exec={exe} url %u\nTerminal=false\nNoDisplay=true\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["xdg-mime", "default", "loftline-url.desktop", f"x-scheme-handler/{SCHEME}"],
        check=False,
    )
    return f"Registered {SCHEME}:// through {desktop}, opening {exe}."
