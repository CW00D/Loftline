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
                "hosting.api.render": {
                    "enabled_when": {"always": True},
                    "requires": ["render_api_key"],
                }
            },
        }
    )

    assert [f.name for f in features.enabled_for(spec())] == ["hosting.api.render"]


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
    assert features["hosting.api.render"].requires == ("render_api_key",)
    assert features["hosting.api.render"].produces == ("render_service_id",)
    assert features["hosting.web.render"].requires == ("render_api_key",)
    assert features["payments.subscriptions"].requires == (
        "stripe_secret_key",
        "stripe_publishable_key",
    )
    assert features["payments.checkout"].produces == ("stripe_webhook_secret",)


def test_the_two_database_features_are_mutually_exclusive() -> None:
    features = load_default_features()

    for database in ("postgres", "aura"):
        enabled = {f.name for f in features.enabled_for(spec(database=database))}
        assert len(enabled & {"database.postgres", "database.aura"}) == 1


def test_api_hosting_follows_the_hosting_slot() -> None:
    """hosting.api defaults to render, so hosting.api.render is on by default."""
    features = load_default_features()

    assert "hosting.api.render" in {f.name for f in features.enabled_for(spec())}


def test_web_hosting_needs_both_the_web_flag_and_the_slot() -> None:
    features = load_default_features()

    def enabled(**overrides: object) -> set[str]:
        return {f.name for f in features.enabled_for(spec(**overrides))}

    assert "hosting.web.render" not in enabled()
    assert "hosting.web.render" not in enabled(web=True, hosting={"web": "vercel"})
    assert {"web", "hosting.web.render"} <= enabled(web=True)


def test_payment_modules_are_independent_overlays() -> None:
    """Each member of the family is its own feature; selecting one selects only it."""
    features = load_default_features()

    def enabled(**overrides: object) -> set[str]:
        return {f.name for f in features.enabled_for(spec(**overrides))}

    def payment_features(**overrides: object) -> set[str]:
        return {n for n in enabled(**overrides) if n.startswith("payments.")}

    assert payment_features() == set()
    assert payment_features(payments=["subscriptions"]) == {"payments.subscriptions"}
    assert payment_features(payments=["subscriptions", "checkout"]) == {
        "payments.subscriptions",
        "payments.checkout",
    }


# --- the selector forms added by ADR-022 --------------------------------------


def test_a_nested_field_selector_walks_into_the_hosting_model() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "hosting.web.vercel": {
                    "enabled_when": {"field": "hosting.web", "equals": "vercel"},
                }
            },
        }
    )

    assert features.enabled_for(spec()) == ()
    assert [f.name for f in features.enabled_for(spec(hosting={"web": "vercel"}))] == [
        "hosting.web.vercel"
    ]


def test_a_contains_selector_tests_list_membership() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "payments.checkout": {
                    "enabled_when": {"field": "payments", "contains": "checkout"},
                }
            },
        }
    )

    assert features.enabled_for(spec(payments=["subscriptions"])) == ()
    assert [f.name for f in features.enabled_for(spec(payments=["checkout"]))] == [
        "payments.checkout"
    ]


def test_all_of_requires_every_selector() -> None:
    features = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "both": {
                    "enabled_when": {
                        "all_of": [
                            {"field": "web", "is_true": True},
                            {"field": "hosting.web", "equals": "render"},
                        ]
                    },
                }
            },
        }
    )

    assert features.enabled_for(spec(web=False)) == ()
    assert features.enabled_for(spec(web=True, hosting={"web": "vercel"})) == ()
    assert [f.name for f in features.enabled_for(spec(web=True))] == ["both"]


@pytest.mark.parametrize(
    "selector",
    [
        {"field": "hosting.nowhere", "equals": "render"},
        {"field": "database.api", "equals": "render"},
        {"field": "payments", "contains": "x", "equals": "y"},
        {"all_of": []},
        {"all_of": [{"always": True}], "equals": "x"},
    ],
)
def test_malformed_adr022_selectors_are_rejected(selector: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FeatureSet.model_validate(
            {"version": 1, "features": {"f": {"enabled_when": selector}}}
        )
