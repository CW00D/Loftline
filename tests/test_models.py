"""Descriptor and spec schema tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from loftline.errors import DescriptorError, SpecError
from loftline.models import Descriptor, DescriptorSet, Spec, load_descriptors, load_spec

from .conftest import descriptor

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- descriptor field requirements per docs/credentials.md -------------------


def test_manual_requires_acquire_instructions() -> None:
    with pytest.raises(ValidationError, match="acquire"):
        descriptor("apple_app_specific_password", state="manual")


def test_produced_requires_produced_by() -> None:
    with pytest.raises(ValidationError, match="produced_by"):
        descriptor("neo4j_password", state="produced", vault_path=None)


def test_held_requires_a_vault_path() -> None:
    with pytest.raises(ValidationError, match="vault_path"):
        descriptor("render_api_key", vault_path=None)


def test_manual_requires_a_vault_path() -> None:
    with pytest.raises(ValidationError, match="vault_path"):
        descriptor("expo_account_id", state="manual", acquire="steps", vault_path=None)


def test_derivable_requires_a_derivation() -> None:
    with pytest.raises(ValidationError, match="derivation"):
        descriptor("expo_account_id", state="derivable", vault_path=None)


def test_produced_must_not_carry_a_vault_path() -> None:
    """Invariant 4, enforced at the schema rather than left to the resolver."""
    with pytest.raises(ValidationError, match="vault_path"):
        descriptor(
            "neo4j_password",
            state="produced",
            produced_by="aura_instance_create",
            vault_path="loftline/aura/neo4j_password",
        )


def test_a_descriptor_carrying_a_value_is_rejected() -> None:
    """The first invariant, enforced by the schema and not by review."""
    with pytest.raises(ValidationError):
        descriptor("render_api_key", value="rnd_abc123")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        descriptor("render_api_key", vendour="render")


def test_unknown_state_is_rejected() -> None:
    with pytest.raises(ValidationError):
        descriptor("render_api_key", state="borrowed")


# --- expiry parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    ("expires", "expected"),
    [
        ("365d", timedelta(days=365)),
        ("90d", timedelta(days=90)),
        ("12h", timedelta(hours=12)),
        ("2w", timedelta(weeks=2)),
        (None, None),
    ],
)
def test_expires_parses_to_a_duration(
    expires: str | None, expected: timedelta | None
) -> None:
    entry = descriptor("render_api_key", expires=expires)
    assert entry.expires_after == expected


@pytest.mark.parametrize("expires", ["365", "one year", "365 days", "-3d", "0d"])
def test_malformed_expiry_is_rejected(expires: str) -> None:
    with pytest.raises(ValidationError):
        descriptor("render_api_key", expires=expires)


# --- descriptor set ----------------------------------------------------------


def test_two_credentials_may_not_share_a_github_secret() -> None:
    """A collision here silently overwrites one secret at provisioning time."""
    with pytest.raises(DescriptorError, match="RENDER_API_KEY"):
        DescriptorSet.model_validate(
            {
                "entries": {
                    "render_api_key": descriptor("render_api_key"),
                    "render_token": descriptor(
                        "render_token", github_secret="RENDER_API_KEY"
                    ),
                }
            }
        )


def test_two_credentials_may_not_share_a_vault_path() -> None:
    with pytest.raises(DescriptorError, match="loftline/acme/shared"):
        DescriptorSet.model_validate(
            {
                "entries": {
                    "alpha": descriptor("alpha", vault_path="loftline/acme/shared"),
                    "beta": descriptor(
                        "beta",
                        vault_path="loftline/acme/shared",
                        github_secret="BETA",
                    ),
                }
            }
        )


def test_descriptors_load_from_yaml_keyed_by_name(tmp_path: Path) -> None:
    path = tmp_path / "credentials.yml"
    path.write_text(
        "expo_push_token:\n"
        "  vendor: expo\n"
        "  scope: account\n"
        "  state: held\n"
        "  consumed_by: [backend]\n"
        "  environments: [staging, production]\n"
        "  github_secret: EXPO_PUSH_TOKEN\n"
        "  vault_path: loftline/expo/push_token\n"
        "  expires: null\n",
        encoding="utf-8",
    )

    loaded = load_descriptors(path)

    assert loaded["expo_push_token"].name == "expo_push_token"
    assert loaded["expo_push_token"].vendor == "expo"


def test_a_missing_credentials_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(DescriptorError, match=r"credentials\.yml"):
        load_descriptors(tmp_path / "credentials.yml")


def test_the_shipped_credentials_file_is_valid() -> None:
    loaded = load_descriptors(REPO_ROOT / "credentials.yml")

    assert "render_api_key" in loaded
    assert "expo_push_token" in loaded
    assert "aura_client_id" in loaded


def test_the_shipped_credentials_file_contains_no_values() -> None:
    """Belt and braces over the schema check: no value-shaped key in the text."""
    text = (REPO_ROOT / "credentials.yml").read_text(encoding="utf-8")
    keys = {
        line.split(":", 1)[0].strip()
        for line in text.splitlines()
        if ":" in line and not line.lstrip().startswith("#")
    }

    assert keys.isdisjoint({"value", "secret", "token", "password", "key"})


# --- spec --------------------------------------------------------------------


def test_spec_round_trips_the_documented_question_set(tmp_path: Path) -> None:
    path = tmp_path / "loftline.yml"
    path.write_text(
        "project_name: beerreel\n"
        "package_name: beerreel\n"
        "database: postgres\n"
        "mobile: true\n"
        "notifications: true\n"
        "environments: [staging, production]\n",
        encoding="utf-8",
    )

    loaded = load_spec(path)

    assert loaded.project_name == "beerreel"
    assert loaded.database == "postgres"
    assert loaded.mobile is True


def test_spec_defaults_the_environments() -> None:
    loaded = Spec.model_validate(
        {"project_name": "beerreel", "package_name": "beerreel", "database": "postgres"}
    )

    assert loaded.environments == ("staging", "production")
    assert loaded.mobile is False
    assert loaded.notifications is False


def test_spec_rejects_an_unknown_database() -> None:
    with pytest.raises(ValidationError):
        Spec.model_validate(
            {
                "project_name": "beerreel",
                "package_name": "beerreel",
                "database": "mongo",
            }
        )


def test_spec_rejects_questions_beyond_the_agreed_set() -> None:
    """ADR-001: the spec schema is the primary interface. It does not sprawl."""
    with pytest.raises(ValidationError):
        Spec.model_validate(
            {
                "project_name": "beerreel",
                "package_name": "beerreel",
                "database": "postgres",
                "authentication": "auth0",
            }
        )


@pytest.mark.parametrize("package_name", ["Beer-Reel", "9beer", "beer reel", ""])
def test_package_name_must_be_a_python_identifier(package_name: str) -> None:
    with pytest.raises(ValidationError):
        Spec.model_validate(
            {
                "project_name": "beerreel",
                "package_name": package_name,
                "database": "postgres",
            }
        )


def test_environments_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        Spec.model_validate(
            {
                "project_name": "beerreel",
                "package_name": "beerreel",
                "database": "postgres",
                "environments": ["production", "production"],
            }
        )


def test_a_missing_spec_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match=r"loftline\.yml"):
        load_spec(tmp_path / "loftline.yml")


def test_the_shipped_example_spec_is_valid() -> None:
    loaded = load_spec(REPO_ROOT / "examples" / "loftline.yml")

    assert loaded.project_name


def test_descriptor_is_immutable() -> None:
    entry: Descriptor = descriptor("render_api_key")

    with pytest.raises(ValidationError):
        entry.vendor = "other"


# --- ADR-022: families and hosting per component ------------------------------


def test_spec_defaults_the_adr022_fields() -> None:
    from .conftest import spec

    s = spec()

    assert s.web is False
    assert s.payments == ()
    assert s.hosting.api == "render"
    assert s.hosting.web == "render"


def test_spec_rejects_an_unknown_payment_module() -> None:
    from .conftest import spec

    with pytest.raises(ValidationError, match="unknown payments module"):
        spec(payments=["paypal"])


def test_spec_rejects_a_duplicate_payment_module() -> None:
    from .conftest import spec

    with pytest.raises(ValidationError, match="duplicates"):
        spec(payments=["checkout", "checkout"])


def test_spec_rejects_an_unknown_hosting_provider_or_slot() -> None:
    from .conftest import spec

    with pytest.raises(ValidationError):
        spec(hosting={"api": "fly"})
    with pytest.raises(ValidationError):
        spec(hosting={"database": "render"})


# --- ADR-031: the product's domain --------------------------------------------


def test_spec_accepts_an_apex_domain_and_rejects_the_rest() -> None:
    from .conftest import spec

    assert spec(domain="example.com").domain == "example.com"
    assert spec(domain="my-product.co.uk").hosting.dns == "cloudflare"
    for bad in ("Example.com", "https://example.com", "example", "-x.com", "a b.com"):
        with pytest.raises(ValidationError):
            spec(domain=bad)
