"""The feature-to-requirements mapping.

The mapping itself is data, in `features.yml`. This module is a generic
interpreter over that data and names no feature. Adding a feature is an edit to
a YAML file; it is not a code change, and it cannot become one without someone
noticing.

A selector takes one of three forms:

    {always: true}                        always enabled
    {field: notifications, is_true: true} enabled by a boolean spec flag
    {field: database, equals: aura}       enabled by a spec choice
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import FeatureError
from .models import Spec

FEATURES_FILE = Path(__file__).with_name("features.yml")


class Selector(BaseModel):
    """A declarative condition evaluated against the spec."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    always: bool | None = None
    field_name: str | None = Field(default=None, alias="field")
    equals: str | None = None
    is_true: bool | None = None

    @model_validator(mode="after")
    def _check_exactly_one_form(self) -> Self:
        if self.always is not None:
            if self.field_name or self.equals or self.is_true is not None:
                raise ValueError(
                    "a selector states either always, or a field condition, not both"
                )
            return self

        if not self.field_name:
            raise ValueError("a selector needs either always or a field to test")
        if (self.equals is None) == (self.is_true is None):
            raise ValueError(
                f"field {self.field_name} needs exactly one of equals or is_true"
            )
        if self.field_name not in Spec.model_fields:
            raise ValueError(
                f"selector names {self.field_name}, which is not a spec field. "
                f"The spec fields are: {', '.join(sorted(Spec.model_fields))}"
            )
        return self

    def matches(self, spec: Spec) -> bool:
        if self.always is not None:
            return self.always
        assert self.field_name is not None
        value = getattr(spec, self.field_name)
        if self.is_true is not None:
            return bool(value) is self.is_true
        return bool(value == self.equals)


class Feature(BaseModel):
    """One optional capability, and the credentials it implies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = ""
    enabled_when: Selector
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()


class FeatureSet(BaseModel):
    """The whole of `features.yml`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    features: dict[str, Feature]

    @model_validator(mode="before")
    @classmethod
    def _name_each_feature(cls, data: Any) -> Any:  # noqa: ANN401 - parsed YAML
        if not isinstance(data, dict):
            return data
        features = data.get("features")
        if not isinstance(features, dict):
            return data
        named = {
            name: {"name": name, **fields} if isinstance(fields, dict) else fields
            for name, fields in features.items()
        }
        return {**data, "features": named}

    def enabled_for(self, spec: Spec) -> tuple[Feature, ...]:
        """Every feature the spec turns on, sorted by name."""
        return tuple(
            sorted(
                (f for f in self.features.values() if f.enabled_when.matches(spec)),
                key=lambda f: f.name,
            )
        )

    def __getitem__(self, name: str) -> Feature:
        return self.features[name]

    def __contains__(self, name: object) -> bool:
        return name in self.features


def load_features(path: Path) -> FeatureSet:
    """Load and validate a feature mapping file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FeatureError(f"{path} could not be read: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FeatureError(f"{path} is not valid YAML: {exc}") from exc
    try:
        return FeatureSet.model_validate(data)
    except ValueError as exc:
        raise FeatureError(f"{path} is not a valid feature mapping:\n{exc}") from exc


@lru_cache(maxsize=1)
def load_default_features() -> FeatureSet:
    """The mapping shipped with the tool."""
    return load_features(FEATURES_FILE)
