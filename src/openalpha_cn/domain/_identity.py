"""Stable identity helpers for versioned domain records."""

import json
from collections.abc import Set as AbstractSet
from hashlib import sha256

from pydantic import BaseModel

_NOTHING_EXCLUDED: frozenset[str] = frozenset()


def stable_model_id(
    *,
    prefix: str,
    model: BaseModel,
    exclude: AbstractSet[str] = _NOTHING_EXCLUDED,
) -> str:
    """Build a stable ID from a model's canonical serialized contract fields.

    `exclude` names fields that are **recorded but not addressed**: they stay on the model and
    in its stored payload, and they do not reach the ID. It exists for `V2-P4-001`'s
    `RunManifest.run_manifest_id`, whose contract carries five such fields (the two wall
    clocks, the run's terminal `status`, its `checkpoints`, and the observed `environment`) --
    see `domain/run.py`'s `RUN_MANIFEST_UNADDRESSED_FIELDS` for the per-field reason and for
    the audit that keeps that mapping from going stale.

    Excluding is an option here rather than a second hashing helper on purpose. Fourteen call
    sites across `domain/`, `providers/` and `backtest/` derive an identity, and every one of
    them goes through this function; the alternative -- a bespoke `sha256` next to the one
    contract that needed a subset -- is exactly the "另造哈希" this repository has avoided
    everywhere else, and it would have put two canonicalisations in play where a difference
    between them would be invisible until two IDs disagreed. The default is the empty set, so
    every existing caller hashes the whole field set exactly as before
    (`tests/unit/domain/test_contract_identity.py::test_the_default_excludes_nothing_so_no_existing_identity_moved`).
    """
    # `or None` rather than the empty set: pydantic's default is `exclude=None`, and passing
    # the default through unchanged is what makes "no existing identity moved" a structural
    # fact rather than a claim about how pydantic treats an empty container.
    payload = model.model_dump(
        mode="json", exclude_computed_fields=True, exclude=set(exclude) or None
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return f"{prefix}_{sha256(canonical).hexdigest()[:24]}"
