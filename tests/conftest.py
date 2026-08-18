"""Shared fixtures.

Nothing here touches the network, a vendor account, `sops`, `age` or a vault
key. That is the point: the resolver is testable in full without any of them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from loftline.features import FeatureSet
from loftline.models import Descriptor, DescriptorSet, Spec

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


def descriptor(name: str, **overrides: object) -> Descriptor:
    """Build a descriptor with sensible defaults, overridden per test."""
    fields: dict[str, object] = {
        "name": name,
        "vendor": "acme",
        "scope": "account",
        "state": "held",
        "consumed_by": ["backend"],
        "environments": ["staging", "production"],
        "github_secret": name.upper(),
        "vault_path": f"loftline/acme/{name}",
    }
    fields.update(overrides)
    return Descriptor.model_validate(fields)


def produced(name: str, produced_by: str, **overrides: object) -> Descriptor:
    """A `produced` descriptor, which carries no vault path by definition."""
    fields: dict[str, object] = {
        "scope": "project",
        "state": "produced",
        "produced_by": produced_by,
        "vault_path": None,
    }
    fields.update(overrides)
    return descriptor(name, **fields)


def descriptors(*items: Descriptor) -> DescriptorSet:
    return DescriptorSet(entries={item.name: item for item in items})


def spec(**overrides: object) -> Spec:
    fields: dict[str, object] = {
        "project_name": "beerreel",
        "package_name": "beerreel",
        "database": "postgres",
        "mobile": False,
        "notifications": False,
        "environments": ["staging", "production"],
    }
    fields.update(overrides)
    return Spec.model_validate(fields)


def always_on(
    requires: list[str] | None = None, produces: list[str] | None = None
) -> FeatureSet:
    """A single always-enabled feature, for exercising the state matrix directly."""
    return FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "test.feature": {
                    "enabled_when": {"always": True},
                    "requires": requires or [],
                    "produces": produces or [],
                }
            },
        }
    )


@pytest.fixture
def features() -> FeatureSet:
    """A feature set independent of the shipped credentials.yml worked examples."""
    return FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "hosting.render": {
                    "enabled_when": {"always": True},
                    "requires": ["render_api_key"],
                    "produces": ["render_service_id"],
                },
                "database.postgres": {
                    "enabled_when": {"field": "database", "equals": "postgres"},
                    "produces": ["database_url"],
                },
                "database.aura": {
                    "enabled_when": {"field": "database", "equals": "aura"},
                    "requires": ["aura_client_id", "aura_client_secret"],
                    "produces": ["neo4j_uri", "neo4j_password"],
                },
                "mobile": {
                    "enabled_when": {"field": "mobile", "is_true": True},
                    "requires": ["apple_team_id"],
                },
                "notifications": {
                    "enabled_when": {"field": "notifications", "is_true": True},
                    "requires": ["expo_push_token", "expo_account_id"],
                },
            },
        }
    )


@pytest.fixture
def base_credentials() -> DescriptorSet:
    """Descriptors covering the always-enabled hosting feature and postgres."""
    return descriptors(
        descriptor("render_api_key"),
        produced("render_service_id", "render_service_create"),
        produced("database_url", "render_postgres_create"),
    )
