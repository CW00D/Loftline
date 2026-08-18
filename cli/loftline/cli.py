"""The `loftline` command line.

Three commands so far: `doctor`, `vault list` and `plan`. None of them writes
anything, and none of them prints a credential value. There is deliberately no
`vault get`: invariant 1 forbids a value reaching stdout, so the only consumer
of `get` is the secret writer in step 5, which passes values to `gh` and never
to a terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
vault_app = typer.Typer(help="Inspect the credential vault.", no_args_is_help=True)
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


def _fail(message: str) -> None:
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
        return

    for path in paths:
        typer.echo(path)
    typer.echo(
        f"\n{len(paths)} path(s) in {config.vault_path}. No value was decrypted."
    )


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
        return

    typer.echo(render_plan(project, resolution, config.vault_path, spec, len(index)))


if __name__ == "__main__":  # pragma: no cover
    app()
