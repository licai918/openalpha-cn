"""Stable identity helpers for versioned domain records."""

import json
from hashlib import sha256

from pydantic import BaseModel


def stable_model_id(*, prefix: str, model: BaseModel) -> str:
    """Build a stable ID from a model's canonical serialized contract fields."""
    payload = model.model_dump(mode="json", exclude_computed_fields=True)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return f"{prefix}_{sha256(canonical).hexdigest()[:24]}"
