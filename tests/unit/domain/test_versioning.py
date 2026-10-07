"""Tests for the version-dispatched contract reader (V2-P0B-005).

The models here are a synthetic, test-only "demo-widget" contract -- not a real domain
contract -- used exactly the way `storage/migrations.py`'s demo migration proves the
migration engine works: it demonstrates a real v1 -> v2 upgrade chain without this task
cutting a v2 of any actual contract (that is Phase P4's job).
"""

import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from openalpha_cn.domain.versioning import (
    ContractVersions,
    UnknownSchemaVersionError,
    read_versioned,
    single_version,
)


class _DemoWidgetV1(BaseModel):
    """The synthetic contract's original shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["demo-widget/v1"] = "demo-widget/v1"
    name: str


class _DemoWidgetV2(BaseModel):
    """The synthetic contract's next shape: adds a required `priority` field.

    `priority` has no default, so a v1 payload cannot validate directly against this
    class -- it can only reach it through `_upgrade_demo_widget_v1_to_v2`, which is
    exactly what proves the upgrade chain (not a lenient default) is doing the work.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["demo-widget/v2"] = "demo-widget/v2"
    name: str
    priority: int


def _upgrade_demo_widget_v1_to_v2(old: BaseModel) -> BaseModel:
    assert isinstance(old, _DemoWidgetV1)
    return _DemoWidgetV2(name=old.name, priority=0)


DEMO_WIDGET_VERSIONS: ContractVersions[_DemoWidgetV2] = ContractVersions(
    name="demo-widget",
    current_version="demo-widget/v2",
    versions={"demo-widget/v1": _DemoWidgetV1, "demo-widget/v2": _DemoWidgetV2},
    upgrades={"demo-widget/v1": _upgrade_demo_widget_v1_to_v2},
)


class _Gadget(BaseModel):
    """A stand-in for the storage models that never had a `schema_version` field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str


def test_read_versioned_upgrades_a_v1_payload_to_the_current_version() -> None:
    raw = _DemoWidgetV1(name="alpha").model_dump_json()

    result = read_versioned(DEMO_WIDGET_VERSIONS, raw)

    assert isinstance(result, _DemoWidgetV2)
    assert result.schema_version == "demo-widget/v2"
    assert result.name == "alpha"
    assert result.priority == 0


def test_read_versioned_does_not_require_new_v2_fields_when_reading_a_v1_payload() -> None:
    """The crux of the brief's warning: schema_version must be read before validation.

    `_DemoWidgetV2` directly rejects a v1-shaped payload (missing `priority`, no
    default) -- proving that a naive `model_validate_json` against the current model,
    guarded by try/except, could not have produced this result. `read_versioned` must
    instead read `schema_version` out of the raw JSON first, dispatch to `_DemoWidgetV1`,
    and only then upgrade.
    """
    raw = _DemoWidgetV1(name="alpha").model_dump_json()

    with pytest.raises(ValidationError, match="priority"):
        _DemoWidgetV2.model_validate_json(raw)

    result = read_versioned(DEMO_WIDGET_VERSIONS, raw)
    assert result.priority == 0


def test_read_versioned_reads_a_current_version_payload_without_upgrading() -> None:
    raw = _DemoWidgetV2(name="beta", priority=5).model_dump_json()

    result = read_versioned(DEMO_WIDGET_VERSIONS, raw)

    assert result == _DemoWidgetV2(name="beta", priority=5)


def test_read_versioned_raises_a_named_error_for_an_unknown_schema_version() -> None:
    raw = json.dumps({"schema_version": "demo-widget/v99", "name": "x"})

    with pytest.raises(UnknownSchemaVersionError) as exc_info:
        read_versioned(DEMO_WIDGET_VERSIONS, raw)

    error = exc_info.value
    assert error.contract == "demo-widget"
    assert error.found_version == "demo-widget/v99"
    assert set(error.supported_versions) == {"demo-widget/v1", "demo-widget/v2"}
    message = str(error)
    assert "demo-widget" in message
    assert "demo-widget/v99" in message
    assert "demo-widget/v1" in message
    assert "demo-widget/v2" in message


def test_read_versioned_lets_a_genuine_validation_error_propagate_unchanged() -> None:
    """A malformed-but-correctly-versioned payload must fail as itself, not as an
    unknown-version error -- the whole point of extracting schema_version separately
    from full validation is to keep these two failure modes distinguishable."""
    raw = json.dumps({"schema_version": "demo-widget/v1"})  # missing required "name"

    with pytest.raises(ValidationError):
        read_versioned(DEMO_WIDGET_VERSIONS, raw)


def test_single_version_contract_reads_a_payload_with_no_schema_version_field() -> None:
    registry = single_version("gadget", _Gadget)
    raw = _Gadget(label="widget").model_dump_json()

    result = read_versioned(registry, raw)

    assert result == _Gadget(label="widget")


def test_contract_versions_rejects_a_registry_missing_an_upgrade_for_a_non_current_version() -> (
    None
):
    with pytest.raises(ValueError, match="upgrade"):
        ContractVersions(
            name="broken",
            current_version="broken/v2",
            versions={"broken/v1": _DemoWidgetV1, "broken/v2": _DemoWidgetV2},
            upgrades={},
        )


def test_contract_versions_rejects_a_current_version_absent_from_versions() -> None:
    with pytest.raises(ValueError, match="current_version"):
        ContractVersions(
            name="broken",
            current_version="broken/v3",
            versions={"broken/v1": _DemoWidgetV1},
            upgrades={},
        )
