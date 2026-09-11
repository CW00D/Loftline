"""Exception types.

Errors name the credential, path or file involved. A vendor error is never
swallowed into a generic failure, and no error message ever carries a
credential value.
"""

from __future__ import annotations


class LoftlineError(Exception):
    """Base class for every error this tool raises deliberately."""


class SpecError(LoftlineError):
    """The project spec is missing, unreadable or invalid."""


class DescriptorError(LoftlineError):
    """`credentials.yml` is missing, unreadable or internally inconsistent."""


class FeatureError(LoftlineError):
    """The feature-to-requirements mapping is invalid."""


class VaultError(LoftlineError):
    """The vault file, or an operation against it, failed."""


class PreconditionError(LoftlineError):
    """An operational precondition checked by `loftline doctor` is not met."""


class GenerateError(LoftlineError):
    """A project could not be rendered from the template."""


class UnknownCredentialError(LoftlineError):
    """A feature requires a credential that has no descriptor.

    This is fatal rather than a warning. A silently skipped credential produces
    a project that generates cleanly and fails at deploy time, which is the
    failure mode the resolver exists to prevent.
    """

    def __init__(self, missing: tuple[tuple[str, str], ...]) -> None:
        self.missing = missing
        lines = [
            f"  {credential}  (required by feature {feature})"
            for feature, credential in missing
        ]
        plural = "s" if len(missing) > 1 else ""
        super().__init__(
            f"No descriptor for {len(missing)} credential{plural}:\n"
            + "\n".join(lines)
            + "\n\nAdd an entry for each to credentials.yml. Descriptors accumulate as"
            "\nthe resolver reports them missing; see the population strategy in"
            "\ncredentials.md."
        )
