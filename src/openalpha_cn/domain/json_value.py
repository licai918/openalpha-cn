"""Canonical and deeply immutable JSON value helpers."""

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue


def freeze_json(value: JsonValue) -> object:
    """Return a deeply immutable representation of a JSON value."""
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: object) -> JsonValue:
    """Return ordinary JSON containers from a frozen JSON value."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return cast(JsonValue, value)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-compatible value deterministically."""
    return json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
