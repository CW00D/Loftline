"""Feature mapping tests.

The mapping is data. These tests exercise the generic interpreter over it, and
assert that the shipped `features.yml` matches the table in docs/credentials.md.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from loftline.features import FeatureSet, load_default_features

from .conftest import spec


def test_an_always_enabled_feature_is_always_enabled() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "hosting.render": {
                    "enabled_when": {"always": True},
                    "requires": ["render_api_key"],
                }
            },
        }
    )

    assert [f.name for f in features.enabled_for(spec())] == ["hosting.render"]


def test_a_flag_feature_follows_the_spec_flag() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "notifications": {
                    "enabled_when": {"field": "notifications", "is_true": True},
                    "requires": ["expo_push_token"],
                }
            },
        }
    )

    assert features.enabled_for(spec(notifications=False)) == ()
    assert [f.name for f in features.enabled_for(spec(notifications=True))] == [
        "notifications"
    ]


def test_a_choice_feature_follows_the_spec_value() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "database.aura": {
                    "enabled_when": {"field": "database", "equals": "aura"},
                    "requires": ["aura_client_id"],
                }
            },
        }
    )

    assert features.enabled_for(spec(database="postgres")) == ()
    assert len(features.enabled_for(spec(database="aura"))) == 1


def test_a_selector_naming_an_unknown_spec_field_is_rejected() -> None:
    """A typo in the mapping must not silently disable a feature."""
    with pytest.raises(ValidationError, match="authentication"):
        FeatureSet.model_validate(
            {
                "version": 1,
                "features": {
                    "auth": {
                        "enabled_when": {"field": "authentication", "is_true": True},
                        "requires": ["auth0_client_id"],
                    }
                },
            }
        )


def test_a_selector_must_state_exactly_one_condition() -> None:
    with pytest.raises(ValidationError):
        FeatureSet.model_validate(
            {
                "version": 1,
                "features": {
                    "confused": {
                        "enabled_when": {
                            "always": True,
                            "field": "mobile",
                            "is_true": True,
                        },
                        "requires": [],
                    }
                },
            }
        )


def test_an_empty_selector_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FeatureSet.model_validate(
            {"version": 1, "features": {"confused": {"enabled_when": {}}}}
        )


def test_enabled_features_come_back_sorted() -> None:
    features = load_default_features()

    enabled = [
        f.name for f in features.enabled_for(spec(mobile=True, notifications=True))
    ]

    assert enabled == sorted(enabled)


# --- the shipped mapping -----------------------------------------------------


def test_shipped_mapping_matches_the_documented_table() -> None:
    features = load_default_features()

    assert features["notifications"].requires == ("expo_push_token",)
    assert features["mobile"].requires == (
        "expo_access_token",
        "apple_team_id",
        "app_store_connect_key",
        "apple_app_specific_password",
    )
    assert features["database.aura"].requires == (
        "aura_client_id",
        "aura_client_secret",
    )
    assert features["database.aura"].produces == ("neo4j_uri", "neo4j_password")
    assert features["database.postgres"].produces == ("database_url",)
    assert features["hosting.render"].requires == ("render_api_key",)
    assert features["hosting.render"].produces == ("render_service_id",)


def test_the_two_database_features_are_mutually_exclusive() -> None:
    features = load_default_features()

    for database in ("postgres", "aura"):
        enabled = {f.name for f in features.enabled_for(spec(database=database))}
        assert len(enabled & {"database.postgres", "database.aura"}) == 1


def test_hosting_is_enabled_for_every_spec() -> None:
    """The spec has no hosting question, so hosting.render is always on."""
    features = load_default_features()

    assert "hosting.render" in {f.name for f in features.enabled_for(spec())}
