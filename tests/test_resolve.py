"""Resolver tests.

Every one of these runs with no vault, no `sops`, no age key and no network.
"""

from __future__ import annotations

import socket
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from loftline.errors import UnknownCredentialError
from loftline.features import FeatureSet
from loftline.models import DescriptorSet
from loftline.resolve import resolve
from loftline.vault import VaultIndex

from .conftest import NOW, always_on, descriptor, descriptors, produced, spec


def names(entries: tuple[Any, ...]) -> list[str]:
    return [entry.name for entry in entries]


# --- the four states ---------------------------------------------------------


def test_held_and_present_in_vault_is_injected() -> None:
    creds = descriptors(descriptor("render_api_key"))
    index = VaultIndex.from_paths(["loftline/acme/render_api_key"])

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert names(result.inject) == ["render_api_key"]
    assert result.derive == () and result.request == () and result.defer == ()
    entry = result.inject[0]
    assert entry.vault_path == "loftline/acme/render_api_key"
    assert entry.github_secret == "RENDER_API_KEY"
    assert entry.environments == ("staging", "production")


def test_held_but_absent_from_vault_is_requested() -> None:
    creds = descriptors(descriptor("render_api_key"))

    result = resolve(
        spec(),
        creds,
        VaultIndex.from_paths([]),
        features=always_on(requires=["render_api_key"]),
        now=NOW,
    )

    assert names(result.request) == ["render_api_key"]
    assert result.request[0].reason == "absent from vault"
    assert result.request[0].vault_path == "loftline/acme/render_api_key"
    assert result.inject == ()


def test_derivable_is_derived() -> None:
    creds = descriptors(
        descriptor(
            "expo_account_id",
            state="derivable",
            derivation="expo whoami",
            vault_path=None,
        )
    )

    result = resolve(
        spec(),
        creds,
        VaultIndex.from_paths([]),
        features=always_on(requires=["expo_account_id"]),
        now=NOW,
    )

    assert names(result.derive) == ["expo_account_id"]
    assert result.derive[0].derivation == "expo whoami"
    assert result.request == ()


def test_manual_and_absent_is_requested_with_instructions() -> None:
    creds = descriptors(
        descriptor(
            "apple_app_specific_password",
            state="manual",
            acquire="1. appleid.apple.com, sign in\n2. Generate\n",
        )
    )

    result = resolve(
        spec(),
        creds,
        VaultIndex.from_paths([]),
        features=always_on(requires=["apple_app_specific_password"]),
        now=NOW,
    )

    assert names(result.request) == ["apple_app_specific_password"]
    assert "appleid.apple.com" in (result.request[0].acquire or "")


def test_manual_but_already_in_vault_is_injected() -> None:
    """A manual credential is manual once per lifetime, not once per project."""
    creds = descriptors(
        descriptor("apple_app_specific_password", state="manual", acquire="steps")
    )
    index = VaultIndex.from_paths(["loftline/acme/apple_app_specific_password"])

    result = resolve(
        spec(),
        creds,
        index,
        features=always_on(requires=["apple_app_specific_password"]),
        now=NOW,
    )

    assert names(result.inject) == ["apple_app_specific_password"]
    assert result.request == ()


def test_produced_is_deferred_to_provisioning() -> None:
    creds = descriptors(produced("neo4j_password", "aura_instance_create"))

    result = resolve(
        spec(),
        creds,
        VaultIndex.from_paths([]),
        features=always_on(produces=["neo4j_password"]),
        now=NOW,
    )

    assert names(result.defer) == ["neo4j_password"]
    assert result.defer[0].produced_by == "aura_instance_create"
    assert result.defer[0].github_secret == "NEO4J_PASSWORD"
    assert result.request == ()


def test_produced_credential_is_never_requested_even_though_absent_from_vault() -> None:
    """Invariant 4: produced credentials belong to a project, never to the vault."""
    creds = descriptors(produced("database_url", "render_postgres_create"))

    result = resolve(
        spec(),
        creds,
        VaultIndex.from_paths([]),
        features=always_on(produces=["database_url"]),
        now=NOW,
    )

    assert names(result.defer) == ["database_url"]
    assert result.request == ()


# --- expiry ------------------------------------------------------------------


def test_elapsed_credential_is_promoted_from_inject_to_request() -> None:
    creds = descriptors(
        descriptor(
            "apple_app_specific_password",
            state="manual",
            acquire="regenerate it",
            expires="365d",
        )
    )
    index = VaultIndex.from_mapping(
        {"loftline/acme/apple_app_specific_password": NOW - timedelta(days=366)}
    )

    result = resolve(
        spec(),
        creds,
        index,
        features=always_on(requires=["apple_app_specific_password"]),
        now=NOW,
    )

    assert result.inject == ()
    assert names(result.request) == ["apple_app_specific_password"]
    assert result.request[0].reason == "expired"
    assert "regenerate it" in (result.request[0].acquire or "")


def test_credential_inside_its_expiry_window_still_injects() -> None:
    creds = descriptors(descriptor("render_api_key", expires="365d"))
    index = VaultIndex.from_mapping(
        {"loftline/acme/render_api_key": NOW - timedelta(days=364, hours=23)}
    )

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert names(result.inject) == ["render_api_key"]


def test_credential_without_expires_never_elapses() -> None:
    creds = descriptors(descriptor("render_api_key", expires=None))
    index = VaultIndex.from_mapping(
        {"loftline/acme/render_api_key": NOW - timedelta(days=4000)}
    )

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert names(result.inject) == ["render_api_key"]


def test_expiring_credential_with_no_timestamp_is_requested() -> None:
    """Freshness that cannot be proved is not assumed."""
    creds = descriptors(descriptor("render_api_key", expires="90d", acquire="steps"))
    index = VaultIndex.from_mapping({"loftline/acme/render_api_key": None})

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert names(result.request) == ["render_api_key"]
    assert result.request[0].reason == "acquisition date unknown"


def test_expiry_is_reported_on_the_inject_entry() -> None:
    """The report warns before a credential elapses, not only after."""
    creds = descriptors(descriptor("render_api_key", expires="30d"))
    index = VaultIndex.from_mapping(
        {"loftline/acme/render_api_key": NOW - timedelta(days=25)}
    )

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert result.inject[0].expires_at == NOW + timedelta(days=5)


# --- failure modes -----------------------------------------------------------


def test_feature_requiring_a_credential_with_no_descriptor_fails_loudly() -> None:
    with pytest.raises(UnknownCredentialError) as excinfo:
        resolve(
            spec(),
            descriptors(),
            VaultIndex.from_paths([]),
            features=always_on(requires=["expo_account_id"]),
            now=NOW,
        )

    message = str(excinfo.value)
    assert "expo_account_id" in message
    assert "test.feature" in message
    assert "credentials.yml" in message


def test_every_missing_descriptor_is_reported_at_once() -> None:
    """One round trip per resolve, not one per missing credential."""
    with pytest.raises(UnknownCredentialError) as excinfo:
        resolve(
            spec(),
            descriptors(),
            VaultIndex.from_paths([]),
            features=always_on(
                requires=["alpha_key", "beta_key"], produces=["gamma_id"]
            ),
            now=NOW,
        )

    assert excinfo.value.missing == (
        ("test.feature", "alpha_key"),
        ("test.feature", "beta_key"),
        ("test.feature", "gamma_id"),
    )


def test_produced_credential_with_no_descriptor_also_fails_loudly() -> None:
    with pytest.raises(UnknownCredentialError) as excinfo:
        resolve(
            spec(),
            descriptors(),
            VaultIndex.from_paths([]),
            features=always_on(produces=["neo4j_uri"]),
            now=NOW,
        )

    assert "neo4j_uri" in str(excinfo.value)


# --- feature enablement ------------------------------------------------------


def test_disabled_features_contribute_nothing(
    features: FeatureSet, base_credentials: DescriptorSet
) -> None:
    creds = DescriptorSet(
        entries={
            **base_credentials.entries,
            "expo_push_token": descriptor("expo_push_token"),
            "expo_account_id": descriptor("expo_account_id"),
        }
    )

    result = resolve(
        spec(notifications=False),
        creds,
        VaultIndex.from_paths([]),
        features=features,
        now=NOW,
    )

    assert "expo_push_token" not in names(result.request)
    assert result.features == ("database.postgres", "hosting.api.render")


def test_enabling_a_feature_pulls_in_its_requirements(
    features: FeatureSet, base_credentials: DescriptorSet
) -> None:
    creds = DescriptorSet(
        entries={
            **base_credentials.entries,
            "expo_push_token": descriptor("expo_push_token"),
            "expo_account_id": descriptor("expo_account_id"),
        }
    )

    result = resolve(
        spec(notifications=True),
        creds,
        VaultIndex.from_paths([]),
        features=features,
        now=NOW,
    )

    assert "expo_push_token" in names(result.request)
    assert "notifications" in result.features


def test_database_choice_is_exclusive(features: FeatureSet) -> None:
    creds = descriptors(
        descriptor("render_api_key"),
        produced("render_service_id", "render_service_create"),
        descriptor("aura_client_id"),
        descriptor("aura_client_secret"),
        produced("neo4j_uri", "aura_instance_create"),
        produced("neo4j_password", "aura_instance_create"),
    )

    result = resolve(
        spec(database="aura"),
        creds,
        VaultIndex.from_paths([]),
        features=features,
        now=NOW,
    )

    assert "database.aura" in result.features
    assert "database.postgres" not in result.features
    assert names(result.defer) == [
        "neo4j_password",
        "neo4j_uri",
        "render_service_id",
    ]


def test_a_credential_required_by_two_features_appears_once(
    features: FeatureSet, base_credentials: DescriptorSet
) -> None:
    shared = descriptor("expo_push_token")
    creds = DescriptorSet(
        entries={
            **base_credentials.entries,
            "expo_push_token": shared,
            "expo_account_id": descriptor("expo_account_id"),
            "apple_team_id": descriptor("apple_team_id"),
        }
    )
    both = FeatureSet.model_validate(
        {
            "version": 1,
            "features": {
                "notifications": {
                    "enabled_when": {"field": "notifications", "is_true": True},
                    "requires": ["expo_push_token"],
                },
                "mobile": {
                    "enabled_when": {"field": "mobile", "is_true": True},
                    "requires": ["expo_push_token"],
                },
            },
        }
    )

    result = resolve(
        spec(mobile=True, notifications=True),
        creds,
        VaultIndex.from_paths([]),
        features=both,
        now=NOW,
    )

    assert names(result.request) == ["expo_push_token"]


# --- the second run ----------------------------------------------------------


def test_second_run_has_an_empty_request_list(features: FeatureSet) -> None:
    """The whole point of the tool: on a second project nothing is asked for."""
    creds = descriptors(
        descriptor("render_api_key", state="held"),
        descriptor("expo_push_token", state="held"),
        descriptor("expo_account_id", state="manual", acquire="steps"),
        descriptor("apple_team_id", state="manual", acquire="steps", expires="365d"),
        produced("render_service_id", "render_service_create"),
        produced("database_url", "render_postgres_create"),
    )
    index = VaultIndex.from_mapping(
        {
            "loftline/acme/render_api_key": NOW - timedelta(days=10),
            "loftline/acme/expo_push_token": NOW - timedelta(days=10),
            "loftline/acme/expo_account_id": NOW - timedelta(days=10),
            "loftline/acme/apple_team_id": NOW - timedelta(days=10),
        }
    )

    result = resolve(
        spec(mobile=True, notifications=True), creds, index, features=features, now=NOW
    )

    assert result.request == ()
    assert names(result.inject) == [
        "apple_team_id",
        "expo_account_id",
        "expo_push_token",
        "render_api_key",
    ]
    assert names(result.defer) == ["database_url", "render_service_id"]


# --- shape and determinism ---------------------------------------------------


def test_environments_are_the_intersection_of_spec_and_descriptor() -> None:
    creds = descriptors(
        descriptor("render_api_key", environments=["staging", "production"])
    )
    index = VaultIndex.from_paths(["loftline/acme/render_api_key"])

    result = resolve(
        spec(environments=["production"]),
        creds,
        index,
        features=always_on(requires=["render_api_key"]),
        now=NOW,
    )

    assert result.inject[0].environments == ("production",)


def test_entries_are_sorted_by_name_for_stable_output() -> None:
    creds = descriptors(
        descriptor("zulu_key"), descriptor("alpha_key"), descriptor("mike_key")
    )
    index = VaultIndex.from_paths(
        ["loftline/acme/zulu_key", "loftline/acme/alpha_key", "loftline/acme/mike_key"]
    )

    result = resolve(
        spec(),
        creds,
        index,
        features=always_on(requires=["zulu_key", "alpha_key", "mike_key"]),
        now=NOW,
    )

    assert names(result.inject) == ["alpha_key", "mike_key", "zulu_key"]


def test_a_plain_list_of_paths_is_an_acceptable_index() -> None:
    """The documented contract says vault_index is a list of paths."""
    creds = descriptors(descriptor("render_api_key"))

    result = resolve(
        spec(),
        creds,
        ["loftline/acme/render_api_key"],
        features=always_on(requires=["render_api_key"]),
        now=NOW,
    )

    assert names(result.inject) == ["render_api_key"]


def test_resolution_carries_no_secret_values() -> None:
    creds = descriptors(descriptor("render_api_key"))
    index = VaultIndex.from_paths(["loftline/acme/render_api_key"])

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"]), now=NOW
    )

    assert not hasattr(result.inject[0], "value")
    assert "value" not in repr(result)


def test_resolver_performs_no_io(
    monkeypatch: pytest.MonkeyPatch,
    features: FeatureSet,
    base_credentials: DescriptorSet,
) -> None:
    """Purity, enforced rather than asserted in prose."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the resolver must not perform I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    resolve(
        spec(), base_credentials, VaultIndex.from_paths([]), features=features, now=NOW
    )


def test_now_defaults_to_the_current_time() -> None:
    creds = descriptors(descriptor("render_api_key", expires="1d", acquire="steps"))
    index = VaultIndex.from_mapping(
        {"loftline/acme/render_api_key": datetime.now(UTC) - timedelta(days=2)}
    )

    result = resolve(
        spec(), creds, index, features=always_on(requires=["render_api_key"])
    )

    assert names(result.request) == ["render_api_key"]
