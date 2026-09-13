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
from .paths import bundle_root

# copier.yml and template/ (its `_subdirectory`): the checkout, or the copy
# shipped in the wheel. See paths.py.
TEMPLATE_ROOT = bundle_root()

# What the template has a branch for. The Spec model accepts more, because
# the resolver can plan a project the template cannot render yet; that gap
# is closed by an extraction (ADR-014, ADR-022), not a lie here.
RENDERABLE_DATABASES = ("postgres", "aura")
RENDERABLE_WEB_HOSTS = ("render",)
PAYMENTS_DATABASES = ("postgres",)


def answers_for(spec: Spec) -> dict[str, object]:
    """The Copier answers a spec implies. One per question in copier.yml."""
    return {
        "project_name": spec.project_name,
        "package_name": spec.package_name,
        "database": spec.database,
        "mobile": spec.mobile,
        "web": spec.web,
        "notifications": spec.notifications,
        "payments": list(spec.payments),
        "hosting_api": spec.hosting.api,
        "hosting_web": spec.hosting.web,
        "hosting_dns": spec.hosting.dns,
        "domain": spec.domain or "",
        "environments": list(spec.environments),
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
            f"database: {spec.database} has no template branch yet (ADR-023). "
            f"Renderable today: {', '.join(RENDERABLE_DATABASES)}."
        )
    if spec.payments and spec.database not in PAYMENTS_DATABASES:
        raise GenerateError(
            f"payments modules have files for database: "
            f"{', '.join(PAYMENTS_DATABASES)} only (ADR-026); the spec asks for "
            f"{', '.join(spec.payments)} on {spec.database}."
        )
    if spec.web and spec.hosting.web not in RENDERABLE_WEB_HOSTS:
        raise GenerateError(
            f"hosting.web: {spec.hosting.web} has no template files yet (ADR-022). "
            f"Renderable today: {', '.join(RENDERABLE_WEB_HOSTS)}."
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
