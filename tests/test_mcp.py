"""MCP server tests, over an in-memory transport.

No network, no sops, no gh. The assertions that matter most are the negative
ones: no tool takes or returns a value, and errors come back as messages a
person can act on rather than as crashes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from mcp.client.client import Client

from loftline import mcp_server
from loftline.doctor import Status
from loftline.mcp_server import server
from loftline.vault_sops import SopsAgeVault

pytestmark = pytest.mark.anyio

VAULT = """\
loftline:
    render:
        api_key:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-08-01T10:04:00Z"
    google:
        smtp_user:
            value: ENC[AES256_GCM,data:Zm9v,type:str]
            acquired_at: "2026-09-10T10:46:10Z"
        smtp_app_password:
            value: ENC[AES256_GCM,data:YmFy,type:str]
            acquired_at: "2026-09-10T10:46:40Z"
sops:
    version: 3.9.0
"""

SPEC = """\
project_name: plainapi
package_name: plainapi
database: aura
mobile: false
notifications: false
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A vault on disk, no sops, no gh, and shelling out is a failure."""
    vault = tmp_path / "vault.yml"
    vault.write_text(VAULT, encoding="utf-8")
    monkeypatch.setenv("LOFTLINE_VAULT", str(vault))
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not shell out")

    monkeypatch.setattr(subprocess, "run", forbidden)
    return vault


def text(result: Any) -> str:
    """The text content of a tool result."""
    return "".join(getattr(block, "text", "") for block in result.content)


def structured(result: Any) -> Any:
    """A tool's structured return, or its text parsed as JSON."""
    if result.structured_content is not None:
        content = result.structured_content
        return content.get("result", content) if isinstance(content, dict) else content
    return json.loads(text(result))


# --- the surface -------------------------------------------------------------


async def test_the_tools_are_exactly_the_commands_plus_the_views() -> None:
    async with Client(server) as client:
        tools = await client.list_tools()

    assert {t.name for t in tools.tools} == {
        "spec_schema",
        "features",
        "credentials",
        "doctor",
        "vault_list",
        "plan",
        "new",
        "secrets_write",
    }


async def test_no_tool_accepts_a_secret_value() -> None:
    """The one rule that matters: values never pass through a conversation."""
    async with Client(server) as client:
        tools = await client.list_tools()

    for tool in tools.tools:
        params = set(tool.input_schema.get("properties", {}))
        assert not params & {"value", "secret", "password", "token"}, tool.name
    assert "vault_set" not in {t.name for t in tools.tools}


async def test_instructions_tell_the_model_never_to_take_a_value() -> None:
    async with Client(server) as client:
        instructions = client.instructions

    assert instructions is not None
    assert "never ask for the value" in " ".join(instructions.split()).lower()


# --- read-only views ---------------------------------------------------------


async def test_spec_schema_lists_the_questions() -> None:
    async with Client(server) as client:
        schema = structured(await client.call_tool("spec_schema", {}))

    assert set(schema["properties"]) == {
        "project_name",
        "package_name",
        "database",
        "mobile",
        "web",
        "notifications",
        "payments",
        "hosting",
        "domain",
        "environments",
    }


async def test_features_are_returned_as_data() -> None:
    async with Client(server) as client:
        features = structured(await client.call_tool("features", {}))

    assert features["base"]["requires"] == ["jwt_secret", "smtp_user", "smtp_password"]
    assert features["notifications"]["enabled_when"] == {
        "field": "notifications",
        "is_true": True,
    }


async def test_credentials_carry_no_values() -> None:
    async with Client(server) as client:
        descriptors = structured(await client.call_tool("credentials", {}))

    names = {d["name"] for d in descriptors}
    assert {"render_api_key", "smtp_user", "jwt_secret"} <= names
    for d in descriptors:
        assert "value" not in d
        assert "acquire" in d or d["state"] in ("produced", "derivable", "held")


# --- commands ----------------------------------------------------------------


async def test_doctor_reports_each_check(offline: Path) -> None:
    async with Client(server) as client:
        checks = structured(await client.call_tool("doctor", {}))

    assert {c["check"] for c in checks} >= {
        "vault file resolves and parses",
        "sops on PATH",
    }
    assert all(c["status"] in {"pass", "fail", "unknown"} for c in checks)


async def test_vault_list_reads_paths_only(offline: Path) -> None:
    async with Client(server) as client:
        paths = structured(await client.call_tool("vault_list", {}))

    assert paths == [
        "loftline/google/smtp_app_password",
        "loftline/google/smtp_user",
        "loftline/render/api_key",
    ]


async def test_plan_returns_the_report(offline: Path) -> None:
    async with Client(server) as client:
        result = await client.call_tool("plan", {"spec": SPEC})

    report = text(result)
    assert not result.is_error, report
    assert "Inject" in report and "render_api_key" in report
    assert "Acquire" in report and "aura_client_id" in report
    assert "ENC[" not in report


async def test_plan_with_an_invalid_spec_is_an_actionable_error(offline: Path) -> None:
    async with Client(server) as client:
        result = await client.call_tool("plan", {"spec": "project_name: x\n"})

    assert result.is_error
    assert "package_name" in text(result)


async def test_plan_with_garbage_is_an_actionable_error(offline: Path) -> None:
    async with Client(server) as client:
        result = await client.call_tool("plan", {"spec": "- just\n- a list\n"})

    assert result.is_error
    assert "mapping" in text(result)


async def test_new_renders_and_records_the_spec(offline: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    async with Client(server) as client:
        result = await client.call_tool("new", {"spec": SPEC, "destination": str(dest)})

    assert not result.is_error, text(result)
    assert (dest / "api" / "main.py").is_file()
    assert (
        (dest / "loftline.yml")
        .read_text(encoding="utf-8")
        .startswith("project_name: plainapi")
    )
    assert "Nothing has been provisioned" in text(result)


async def test_new_refuses_a_non_empty_destination(
    offline: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep", encoding="utf-8")
    async with Client(server) as client:
        result = await client.call_tool("new", {"spec": SPEC, "destination": str(dest)})

    assert result.is_error
    assert "not empty" in text(result)


async def test_secrets_write_never_returns_a_value(
    offline: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "keys.txt"
    key.write_text("AGE-SECRET-KEY-PLACEHOLDER\n", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    (offline.parent / ".sops.yaml").write_text(
        "creation_rules:\n  - path_regex: vault\\.ya?ml$\n    age: age1a,age1b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption", lambda: (Status.PASS, "on")
    )

    class FakeVault(SopsAgeVault):
        def get(self, path: str) -> str:
            return f"decrypted:{path}"

    monkeypatch.setattr(mcp_server, "SopsAgeVault", FakeVault)
    calls: list[tuple[list[str], str | None]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs.get("input")))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    async with Client(server) as client:
        result = await client.call_tool(
            "secrets_write", {"spec": SPEC, "repo": "CW00D/demo", "partial": True}
        )

    report = text(result)
    assert not result.is_error, report
    assert "decrypted:" not in report
    assert "SMTP_USER" in report and "JWT_SECRET" in report
    assert any(stdin and stdin.startswith("decrypted:") for _, stdin in calls)
    assert not any("decrypted:" in a for argv, _ in calls for a in argv)


async def test_secrets_write_refuses_outstanding_by_default(
    offline: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "keys.txt"
    key.write_text("k\n", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key))
    (offline.parent / ".sops.yaml").write_text(
        "creation_rules:\n  - path_regex: vault\\.ya?ml$\n    age: age1a,age1b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "loftline.doctor._full_disk_encryption", lambda: (Status.PASS, "on")
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="[]", stderr=""),
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "secrets_write", {"spec": SPEC, "repo": "CW00D/demo"}
        )

    assert result.is_error
    assert "still to acquire" in text(result)
