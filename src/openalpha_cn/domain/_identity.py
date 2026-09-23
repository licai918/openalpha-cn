"""Stable identity helpers for versioned domain records."""

import json
from collections.abc import Set as AbstractSet
from hashlib import sha256
from typing import Final

from pydantic import BaseModel

_NOTHING_EXCLUDED: frozenset[str] = frozenset()

CONTENT_ADDRESS_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*_[0-9a-f]{24}$"
"""The shape of every content address this repository computes.

Declared beside the function that produces most of them rather than beside any one contract
that references one, because it is a statement about output rather than about a contract: a
lowercase prefix the caller chooses, an underscore, and the first 24 hex digits of a SHA-256
over canonical JSON. The prefix is deliberately not enumerated here, so a contract can require
"an address this repository computed" without also deciding which builder computed it.

**Seven builders, one canonicalisation, and the count corrected twice.** This docstring used
to say "twenty-five prefixes are in use" and to claim the shape belonged to `stable_model_id`
alone. Both were wrong, and measured so by `V2-P4-016`: of the twenty-five, **six were not
addresses at all** -- `panel_doctor.FactorPlaneSeal`'s dataset-name prefixes, swept up by a text
search for `prefix=` -- while three that are (`feat`, `set`, `xs`) were missing. The live census
is **24 distinct prefixes over 27 call sites**, none containing an underscore, read off the
source tree by `tests/unit/domain/test_manifest_component_provenance.py::live_prefixes` rather
than written down here, because a hand-written list checked against its own length is a
tautology -- which is how `V2-P4-012`'s `feat` went unrecorded for four issues.

Then this sentence said **three** builders, and that was the same mistake one size smaller: it
counted the two that take a caller's prefix plus `set_digest`, and `chr_`, `rkc_`, `sla_` and
`ev_` all match this pattern too. `V2-P4-037` read the tree instead. Seven functions mint a
string of this shape, an eighth truncates a digest to the same width for a **file name**
(`ParquetEvidenceStore.append`'s `part-<24 hex>`, which this pattern rejects), and the live
table with a reason per entry is `DECLARED_CONTENT_ADDRESS_MINTS` in
`tests/unit/domain/test_contract_identity.py` -- read off the source tree by AST, equality in
both directions, so an eighth minter is red rather than recorded here after the fact
(`tests/unit/domain/test_contract_identity.py::
test_every_content_address_in_the_source_tree_is_minted_where_this_module_says`).

Attached to a field, it says the thing named there is a content address rather than a name.
`domain/run.py`'s `RUN_MANIFEST_ID_PATTERN` is the same idea pinned to one prefix, and its
docstring states the reason this generic form inherits: a content address that is only
conventionally a content address stops being one the first time it is convenient.
`AlphaModelRef.artifact_id` is the first user, and it is the reason the pattern is generic --
`V2-P4-016` owned what a quantitative model artifact is made of, including its prefix, and this
constrains only that the answer be an address this repository computed rather than a second
canonicalisation somebody wrote next to it (the defect `V2-P4-037` files). That issue has
landed: the prefix is `domain/alpha_model.py`'s `ALPHA_MODEL_ARTIFACT_PREFIX`, and the narrower
`ALPHA_MODEL_ARTIFACT_ID_PATTERN` beside it is what a consumer names when it wants exactly a
fitted model rather than any address.
"""


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

    Excluding is an option here rather than a second hashing helper on purpose. Every call site
    in this repository that derives an identity from a `BaseModel` goes through this function --
    the live census is `tests/unit/domain/test_manifest_component_provenance.py::live_prefixes`,
    read off the source tree rather than written down, because `V2-P4-016` found the two counts
    that *were* written down here and next to `CONTENT_ADDRESS_PATTERN` both stale; the
    alternative -- a bespoke `sha256` next to the one
    contract that needed a subset -- is exactly the "另造哈希" this repository has avoided
    everywhere else, and it would have put two canonicalisations in play where a difference
    between them would be invisible until two IDs disagreed.

    **That last sentence was a claim with nothing under it until `V2-P4-037`.** Rewriting one
    `computed_field` to spell its own `json.dumps` moved that contract's address; *adding* a
    second address beside an existing one left every gate green and moved not even the prefix
    census, because a count of who calls this function cannot see who else is hashing.
    `tests/unit/domain/test_contract_identity.py`'s `DECLARED_CONTENT_ADDRESS_MINTS` is the
    audit: every truncation to a content address's width under `src/`, keyed by the function it
    sits in, and every one of those functions' `json.dumps` keywords held to this one's.
    The default is the empty set, so
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
