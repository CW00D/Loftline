"""The `loftline` command line.

Four commands so far: `doctor`, `vault list`, `vault set` and `plan`. None of
them prints a credential value. `vault set` is the only one that writes, and
it takes the value from a hidden prompt or stdin, never from an argument, so
it stays out of shell history. There is deliberately no `vault get`: invariant
1 forbids a value reaching stdout, so the only consumer of `get` is the secret
writer in step 5, which passes values to `gh` and never to a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .doctor import Capability, Status, VaultConfig, require, run_checks
from .errors import LoftlineError
from .models import load_descriptors, load_spec
from .report import render_plan
from .resolve import resolve
from .vault_sops import SopsAgeVault

app = typer.Typer(
    help="Scaffold and provision applications, credentials first.",
    no_args_is_help=True,
    add_completion=False,
)
vault_app = typer.Typer(
    help="Inspect the credential vault, or store a value in it.",
    no_args_is_help=True,
)
app.add_typer(vault_app, name="vault")

VaultOption = Annotated[
    Path | None,
    typer.Option(
        "--vault", help="Path to the SOPS-encrypted vault file.", show_default=False
    ),
]
CredentialsOption = Annotated[
    Path,
    typer.Option("--credentials", help="Path to credentials.yml."),
]

_SYMBOL = {Status.PASS: "ok  ", Status.FAIL: "FAIL", Status.UNKNOWN: "?   "}


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@app.command()
def doctor(vault: VaultOption = None) -> None:
    """Check the operational preconditions for using the vault."""
    config = VaultConfig.from_env(vault)
    results = run_checks(config)

    typer.echo("")
    for check in results:
        typer.echo(f"  {_SYMBOL[check.status]}  {check.name}")
        typer.echo(f"        {check.detail}")
    typer.echo("")

    failed = [r for r in results if r.status is Status.FAIL]
    unknown = [r for r in results if r.status is Status.UNKNOWN]
    if unknown:
        typer.echo(
            f"{len(unknown)} precondition(s) could not be determined. Check by hand."
        )
    if failed:
        _fail(f"{len(failed)} precondition(s) not met.")
    typer.echo("All preconditions met.")


@vault_app.command("list")
def vault_list(vault: VaultOption = None) -> None:
    """Print the paths held in the vault. Values are never read."""
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        paths = SopsAgeVault(config.vault_path).list_paths()
    except LoftlineError as exc:
        _fail(str(exc))

    for path in paths:
        typer.echo(path)
    typer.echo(
        f"\n{len(paths)} path(s) in {config.vault_path}. No value was decrypted."
    )


@vault_app.command("set")
def vault_set(
    name: Annotated[
        str, typer.Argument(help="The credential's name in credentials.yml.")
    ],
    vault: VaultOption = None,
    credentials: CredentialsOption = Path("credentials.yml"),
    replace: Annotated[
        bool,
        typer.Option(
            "--replace", help="Overwrite a value already in the vault, as on rotation."
        ),
    ] = False,
    stdin: Annotated[
        bool,
        typer.Option(
            "--stdin", help="Read the value from standard input instead of prompting."
        ),
    ] = False,
) -> None:
    """Store a credential's value in the vault.

    The value is taken from a hidden prompt, or from stdin with --stdin. It is
    never accepted as an argument, so it cannot land in shell history, and it
    is never echoed back.
    """
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.WRITE)
        assert config.vault_path is not None
        descriptors = load_descriptors(credentials)

        descriptor = descriptors.get(name)
        if descriptor is None:
            known = ", ".join(sorted(descriptors.entries)) or "none"
            _fail(
                f"No descriptor named {name} in {credentials}. Known credentials: "
                f"{known}.\nAdd a descriptor first; the vault path comes from it."
            )
        if descriptor.vault_path is None:
            _fail(
                f"{name} is {descriptor.state} and never lives in the vault. "
                "A produced credential is written to environment secrets at "
                "provisioning time; a derivable one is generated on demand."
            )

        store = SopsAgeVault(config.vault_path)
        if descriptor.vault_path in store.list_paths() and not replace:
            _fail(
                f"{name} is already in the vault at {descriptor.vault_path}. "
                "Pass --replace to overwrite it, for example after rotating it."
            )

        value = _read_value(name, stdin)
        store.set(descriptor.vault_path, value)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(f"Stored {name} at {descriptor.vault_path}.")
    typer.echo(
        f"{config.vault_path.name} has changed. Commit and push the vault repository "
        "so the value survives this machine."
    )


def _read_value(name: str, from_stdin: bool) -> str:
    """The value, from stdin or a hidden prompt. Only a trailing newline is dropped."""
    if from_stdin:
        raw = sys.stdin.read()
        value = raw[:-1] if raw.endswith("\n") else raw
        value = value[:-1] if value.endswith("\r") else value
    else:
        value = str(typer.prompt(f"Value for {name}", hide_input=True, default=""))
    if not value:
        _fail(f"An empty value was given for {name}. Nothing stored.")
    return value


@app.command()
def plan(
    spec: Annotated[Path, typer.Argument(help="Path to a loftline.yml project spec.")],
    vault: VaultOption = None,
    credentials: CredentialsOption = Path("credentials.yml"),
) -> None:
    """Report how every credential this spec needs will be resolved.

    Reads the encrypted vault for its path index only, so it needs no age key
    and no `sops` binary. It performs no side effects whatever.
    """
    config = VaultConfig.from_env(vault)
    try:
        require(config, Capability.READ_INDEX)
        assert config.vault_path is not None
        project = load_spec(spec)
        descriptors = load_descriptors(credentials)
        index = SopsAgeVault(config.vault_path).index()
        resolution = resolve(project, descriptors, index)
    except LoftlineError as exc:
        _fail(str(exc))

    typer.echo(render_plan(project, resolution, config.vault_path, spec, len(index)))


if __name__ == "__main__":  # pragma: no cover
    app()
