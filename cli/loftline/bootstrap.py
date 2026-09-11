"""First-run helpers: initialising a vault, and configuring a Claude client.

Both exist so that setting Loftline up on a new machine is a sequence of
commands rather than a sequence of files to hand-write. Neither touches a
credential value.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .doctor import KEY_ENV, VAULT_ENV, VaultConfig
from .errors import VaultError

SOPS_CONFIG = """\
# SOPS configuration for this vault. Written by `loftline vault init`.
#
# encrypted_regex: '^value$' encrypts only each entry's value and leaves the
# structure and acquired_at timestamps readable, which is what lets Loftline
# plan a project without decrypting anything.
#
# Two recipients, always: this machine's key and a backup held elsewhere.
# Losing the sole key destroys the vault with no recovery path.
creation_rules:
  - path_regex: vault\\.ya?ml$
    encrypted_regex: '^value$'
    age: {recipients}
"""

VAULT_GITIGNORE = """\
# The vault file itself IS committed: it is encrypted, and committing it is
# how it survives the machine. Key material and anything decrypted never are.
keys.txt
*.agekey
*.key
*.decrypted
*.decrypted.*
*.plain
*.plain.*
"""


def init_vault(
    directory: Path,
    recipients: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> Path:
    """Create an encrypted, empty vault in `directory` and initialise git.

    Returns the path of the vault file. Refuses a directory that already has a
    vault, and refuses fewer than two recipients.
    """
    run = run or subprocess.run
    if len(recipients) < 2:
        raise VaultError(
            "a vault needs at least two recipients: this machine's key and a backup "
            "held elsewhere. Pass --recipient twice."
        )
    bad = [r for r in recipients if not r.startswith("age1")]
    if bad:
        raise VaultError(
            f"not an age public key: {', '.join(bad)}. They start with age1."
        )
    sops = shutil.which("sops")
    if sops is None:
        raise VaultError("sops is not on PATH. Install it first; see docs/setup.md.")

    directory = Path(directory)
    vault = directory / "vault.yml"
    if vault.exists():
        raise VaultError(f"{vault} already exists. Refusing to overwrite a vault.")
    directory.mkdir(parents=True, exist_ok=True)

    (directory / ".sops.yaml").write_text(
        SOPS_CONFIG.format(recipients=",".join(recipients)), encoding="utf-8"
    )
    (directory / ".gitignore").write_text(VAULT_GITIGNORE, encoding="utf-8")
    vault.write_text("loftline: {}\n", encoding="utf-8")

    # sops finds .sops.yaml by walking up from the working directory, not from
    # the file, so name the config explicitly and run from the vault directory.
    encrypt = run(
        [sops, "--config", ".sops.yaml", "-e", "-i", "vault.yml"],
        cwd=str(directory),
        capture_output=True,
        text=True,
        check=False,
    )
    if encrypt.returncode != 0:
        vault.unlink(missing_ok=True)
        raise VaultError(
            f"sops could not encrypt the new vault: {encrypt.stderr.strip()}"
        )

    git = shutil.which("git")
    if git is not None and not (directory / ".git").exists():
        run(
            [git, "init", "-q", "-b", "main"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            check=False,
        )
        run(
            [git, "add", "-A"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            check=False,
        )
        run(
            [git, "commit", "-q", "-m", "Initialise the Loftline vault"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            check=False,
        )
    return vault


def mcp_config(
    client: str, *, config: VaultConfig | None = None, venv: Path | None = None
) -> str:
    """The JSON a Claude client needs to run Loftline's MCP server here.

    Claude Desktop launches servers with no working directory and a minimal
    environment, so every path is absolute and the vault and key locations
    are stated rather than inherited. Claude Code inherits the environment,
    so its entry can reference the variable.
    """
    config = config or VaultConfig.from_env()
    venv = venv or Path(sys.executable).resolve().parent
    entry = venv / ("loftline-mcp.exe" if os.name == "nt" else "loftline-mcp")

    if client == "code":
        body = {
            "mcpServers": {
                "loftline": {
                    "command": str(entry),
                    "args": [],
                    "env": {VAULT_ENV: f"${{{VAULT_ENV}}}"},
                }
            }
        }
        return json.dumps(body, indent=2)

    if client != "desktop":
        raise VaultError(f"unknown client {client!r}; use desktop or code")

    if config.vault_path is None:
        raise VaultError(
            f"{VAULT_ENV} is not set; the Desktop entry needs the vault's full path."
        )

    env = {
        VAULT_ENV: str(config.vault_path.resolve()),
        KEY_ENV: str(config.age_key_file),
    }
    # Desktop's PATH is minimal. Name the directories holding the tools the
    # server shells out to, so secrets_write can find sops, age and gh.
    tool_dirs: list[str] = []
    for tool in ("sops", "age", "gh", "git"):
        found = shutil.which(tool)
        if found:
            parent = str(Path(found).parent)
            if parent not in tool_dirs:
                tool_dirs.append(parent)
    if tool_dirs:
        env["PATH"] = os.pathsep.join(tool_dirs)

    body = {"mcpServers": {"loftline": {"command": str(entry), "args": [], "env": env}}}
    return json.dumps(body, indent=2)
