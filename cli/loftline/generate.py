"""Render a project from the template.

Copier does the rendering; this module is the boundary that turns a validated
spec into Copier's answers and refuses the combinations the template cannot
honour yet. It performs no provisioning and touches no credential.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import copier
from copier.errors import DirtyLocalWarning

from .errors import GenerateError
from .models import Spec

# The repository root holds copier.yml; template/ is its `_subdirectory`.
TEMPLATE_ROOT = Path(__file__).resolve().parents[2]

# The database choices the template has a branch for. The Spec model accepts
# more, because the resolver can plan a project the template cannot render
# yet; that gap is closed by a second extraction (ADR-014), not a lie here.
RENDERABLE_DATABASES = ("aura",)


def answers_for(spec: Spec) -> dict[str, object]:
    """The Copier answers a spec implies. Exactly the five questions."""
    return {
        "project_name": spec.project_name,
        "package_name": spec.package_name,
        "database": spec.database,
        "mobile": spec.mobile,
        "notifications": spec.notifications,
    }


def generate(
    spec: Spec,
    destination: Path,
    *,
    template_root: Path = TEMPLATE_ROOT,
    overwrite: bool = False,
) -> Path:
    """Render `spec` into `destination`. Returns the destination.

    Refuses a destination that already has files in it unless `overwrite` is
    set, so a typo cannot render a project over an unrelated directory.
    """
    if spec.database not in RENDERABLE_DATABASES:
        raise GenerateError(
            f"database: {spec.database} has no template branch yet. The base was "
            f"extracted from a graph-backed project; see ADR-014. "
            f"Renderable today: {', '.join(RENDERABLE_DATABASES)}."
        )
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise GenerateError(
            f"{destination} is not empty. Pass --force to render into it anyway."
        )
    if not (template_root / "copier.yml").exists():
        raise GenerateError(
            f"no copier.yml at {template_root}; is this the Loftline repository?"
        )

    with warnings.catch_warnings():
        # Copier warns when the template has uncommitted changes and renders
        # them anyway. That is the behaviour wanted: the working tree is the
        # template while it is being developed.
        warnings.simplefilter("ignore", DirtyLocalWarning)
        copier.run_copy(
            str(template_root),
            str(destination),
            data=answers_for(spec),
            defaults=True,
            overwrite=overwrite,
            quiet=True,
            vcs_ref="HEAD",
        )
    return destination
