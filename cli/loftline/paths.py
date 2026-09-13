"""Where the template, the feature map's companions and the Terraform
modules live.

Inside a checkout of the repository they sit beside `cli/`: `copier.yml`,
`template/`, `credentials.yml`, `infra/`. Installed as a tool, the same
files ship inside the package under `_bundle/`, copied in at build time
(see pyproject.toml). Everything that needs one of them asks here rather
than walking up from its own source file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def bundle_root() -> Path:
    """The directory holding copier.yml, template/, credentials.yml and infra/."""
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "copier.yml").is_file() and (checkout / "template").is_dir():
        return checkout
    bundled = Path(__file__).resolve().parent / "_bundle"
    if (bundled / "copier.yml").is_file():
        return bundled
    raise FileNotFoundError(
        "Loftline's template is missing: neither a repository checkout nor a "
        "bundled copy was found beside the package. Reinstall the tool."
    )


def credentials_file() -> Path:
    return bundle_root() / "credentials.yml"
