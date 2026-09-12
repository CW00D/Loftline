"""loftline:// links: what they may ask for, and how a terminal is opened."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from loftline.urlhandler import UrlError, open_terminal, parse, register


def test_a_vault_set_link_names_a_credential_and_nothing_else() -> None:
    action = parse("loftline://vault/set/render_api_key")

    assert action.argv == ("loftline", "vault", "set", "render_api_key")
    assert action.title == "Store render_api_key"


@pytest.mark.parametrize(
    "link",
    [
        "https://vault/set/render_api_key",
        "loftline://vault/get/render_api_key",
        "loftline://vault/set/Render-Key",
        "loftline://vault/set/x/y",
        "loftline://shell/rm",
    ],
)
def test_anything_else_is_refused(link: str) -> None:
    with pytest.raises(UrlError):
        parse(link)


def test_the_terminal_runs_exactly_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[Sequence[str]] = []
    monkeypatch.setattr("loftline.urlhandler.sys.platform", "win32")

    open_terminal(parse("loftline://vault/set/smtp_user"), run=spawned.append)

    assert spawned[0][-1] == "loftline vault set smtp_user"
    assert spawned[0][:3] == ["cmd", "/c", "start"]


def test_linux_registration_writes_a_desktop_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("loftline.urlhandler.sys.platform", "linux")
    monkeypatch.setattr("loftline.urlhandler.subprocess.run", lambda *a, **k: None)

    message = register(executable="/usr/bin/loftline", home=tmp_path)

    entry = (tmp_path / ".local/share/applications/loftline-url.desktop").read_text(
        encoding="utf-8"
    )
    assert "Exec=/usr/bin/loftline url %u" in entry
    assert "MimeType=x-scheme-handler/loftline;" in entry
    assert "Registered loftline://" in message


def test_macos_says_it_cannot_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("loftline.urlhandler.sys.platform", "darwin")

    with pytest.raises(UrlError, match="Copy button"):
        register(executable="/usr/local/bin/loftline")
