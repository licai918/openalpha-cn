"""`V2-P4-016`: the model artifact's content address, and the audit that keeps it honest.

`V2-P4-010` decided that `AlphaModelRef.artifact_id` must be something `stable_model_id`
produced and left the **prefix** and the **digest inputs** here. `V2-P4-011` built the artifact
carrying six of Implementation Decision 11's eleven fields and no id, on the argument that this
issue adds a computed field rather than changing one.

The digest inputs are decided by measurement rather than by reading D11 down the page, and the
measurement lives one package over in
`tests/unit/backtest/test_artifact_address_collisions.py`: five candidate address definitions,
each tried against artifacts fitted on `V2-P4-013`'s real folds, each failing on a different real
case. This module holds the contract-level half -- the prefix, the exclusion audit, the per-field
sweep, and the two properties an address exists to have.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final

import pytest
from pydantic import ValidationError

from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.alpha_model import (
    ALPHA_MODEL_ARTIFACT_ID_PATTERN,
    ALPHA_MODEL_ARTIFACT_PREFIX,
    ARTIFACT_UNADDRESSED_FIELDS,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.run import AlphaModelRef

CUTOFF: Final[datetime] = datetime(2026, 1, 14, 7, 0, tzinfo=UTC)


def _declaration(**overrides: Any) -> AlphaModelDeclaration:
    fields: dict[str, Any] = {
        "name": "momentum_5d_rank",
        "family": "cross_sectional_rank",
        "horizon": "5d",
        "feature_version": "feat_0123456789abcdef01234567",
        "seed": 7,
        "code_commit": "0123456789abcdef",
        "hyperparameters": (("winsorize", 0.01),),
    }
    return AlphaModelDeclaration(**{**fields, **overrides})


def _artifact(**overrides: Any) -> AlphaModelArtifact:
    fields: dict[str, Any] = {
        "declaration": _declaration(),
        "feature_ids": ("momentum_20d", "value_ep"),
        "training_cutoff": CUTOFF,
        "training_example_count": 32,
        "parameters": (("momentum_20d", -0.75), ("value_ep", 0.75)),
    }
    return AlphaModelArtifact(**{**fields, **overrides})


GOLDEN_ARTIFACT_ID: Final[str] = "mdl_e45f7fdb337468bd742f6bd1"
"""`_artifact().artifact_id`, pinned so a change to the digest inputs cannot be silent.

`GOLDEN_RUN_MANIFEST_ID`'s reason. Adding a key to `ARTIFACT_UNADDRESSED_FIELDS`, changing the
prefix, or reordering a canonicalisation all move this string, and every one of them is a
decision somebody should have to make on purpose.
"""

_ADDRESSED_ARTIFACT_VARIATIONS: Final[tuple[tuple[str, Any], ...]] = (
    ("feature_ids", ("momentum_20d", "value_ep", "volatility_60d")),
    ("training_cutoff", CUTOFF + timedelta(days=4)),
    ("training_example_count", 24),
    ("parameters", (("momentum_20d", -0.75), ("value_ep", 0.5))),
)
"""Every `AlphaModelArtifact` field but the two exempted, with a value a real fit could produce.

`feature_ids` gains a column rather than being replaced wholesale, and `parameters` moves one
coefficient rather than both, for `_ADDRESSED_MANIFEST_VARIATIONS`' reason: a variation that
changes everything at once passes against an implementation that hashes only the half a human
typed. `training_example_count` moves **down** while the cutoff holds, which is the real case
`tests/unit/backtest/test_artifact_address_collisions.py` measures -- a panel that starts later.
"""

_ADDRESSED_DECLARATION_VARIATIONS: Final[tuple[tuple[str, Any], ...]] = (
    ("name", "momentum_5d_rank_v2"),
    ("family", "gradient_boosted_rank_trees"),
    ("horizon", "10d"),
    ("feature_version", "feat_fedcba9876543210fedcba98"),
    ("seed", 8),
    ("code_commit", "fedcba9876543210"),
    ("hyperparameters", (("winsorize", 0.02),)),
)
"""Every `AlphaModelDeclaration` field but `schema_version`, which is a one-member `Literal`.

`exclude` in `stable_model_id` reaches only the top level, so no declaration field can be kept
out of the address through `ARTIFACT_UNADDRESSED_FIELDS` at all -- which is why this sweep is
held against the declaration's own `model_fields` rather than folded into the artifact's.
"""


def test_the_artifact_carries_an_address_this_repository_computed() -> None:
    """The prefix, and that it is `stable_model_id`'s output rather than a second hash.

    `V2-P4-037` files the defect a second canonicalisation would be, so "this is the address"
    and "this is what that function returns" are asserted as the same sentence rather than as
    two that could drift.
    """
    artifact = _artifact()

    assert ALPHA_MODEL_ARTIFACT_PREFIX == "mdl"
    assert artifact.artifact_id == stable_model_id(
        prefix=ALPHA_MODEL_ARTIFACT_PREFIX, model=artifact
    )
    assert re.fullmatch(ALPHA_MODEL_ARTIFACT_ID_PATTERN, artifact.artifact_id)
    assert re.fullmatch(CONTENT_ADDRESS_PATTERN, artifact.artifact_id)


def test_the_address_is_stable_for_a_fixed_fit() -> None:
    """Re-derived from the same inputs, twice, and against a value pinned in this file."""
    assert _artifact().artifact_id == GOLDEN_ARTIFACT_ID
    assert _artifact().artifact_id == _artifact().artifact_id


@pytest.mark.parametrize(("field", "value"), _ADDRESSED_ARTIFACT_VARIATIONS)
def test_every_addressed_artifact_field_moves_the_address(field: str, value: object) -> None:
    """Direction one, per artifact field: what the fit consumed, changed, changes the address."""
    assert _artifact(**{field: value}).artifact_id != GOLDEN_ARTIFACT_ID


@pytest.mark.parametrize(("field", "value"), _ADDRESSED_DECLARATION_VARIATIONS)
def test_every_addressed_declaration_field_moves_the_address(field: str, value: object) -> None:
    """Direction one, per declaration field -- including the two D11 does not name.

    `name` and `family` are in the address because a `RunManifest.alpha_model_versions` entry
    pairs a *name* with a *digest*, so a name outside the digest is a half of that pair no
    reader can check (`test_a_reference_whose_name_disagrees_with_its_artifact_is_detectable`).
    `V2-P4-010`'s objection to `AgentVersion.version` -- a field whose value never varies -- does
    not apply to either: this sweep varies both and the address moves.
    """
    assert _artifact(declaration=_declaration(**{field: value})).artifact_id != GOLDEN_ARTIFACT_ID


def test_no_two_field_variations_produce_the_same_address() -> None:
    """A sweep in which two fields happen to land on one address cannot say which did the work.

    `test_no_two_field_variations_produce_the_same_address` on the run manifest, applied to the
    eleven variations above plus the fixture itself.
    """
    addresses = [
        GOLDEN_ARTIFACT_ID,
        *(
            _artifact(**{field: value}).artifact_id
            for field, value in _ADDRESSED_ARTIFACT_VARIATIONS
        ),
        *(
            _artifact(declaration=_declaration(**{field: value})).artifact_id
            for field, value in _ADDRESSED_DECLARATION_VARIATIONS
        ),
    ]

    assert len(addresses) == 12
    assert len(set(addresses)) == len(addresses)


def test_every_artifact_and_declaration_field_is_addressed_or_excluded_by_name() -> None:
    """The meta-audit: a fifteenth field fails until somebody decides about it.

    Fourteen today -- six on the artifact and eight on the declaration -- of which eleven are
    swept above and three are exempted below by name.

    `RUN_MANIFEST_UNADDRESSED_FIELDS`' arrangement, over two models instead of one. The three
    exemptions are named rather than tolerated:

    - `schema_version` on both, a one-member `Literal` there is no second value to vary it to,
      covered instead by `test_the_hashed_payload_is_exactly_the_declared_fields` asserting it is
      inside what gets hashed -- which is what makes an `alpha-model-artifact/v2` a different
      address from a v1 carrying the same numbers.
    - `declaration` on the artifact, because it is covered field by field by the second sweep,
      and this test asserts that sweep really is the declaration's whole field set.

    `V2-P4-013`, `V2-P4-014` and `V2-P4-015` each named one more of D11's eleven fields while
    this contract stood still, and `V2-P4-015` put four scalars into `hyperparameters`. Without
    this audit the next such field would join the digest with nobody deciding, which is the state
    `AgentVersion.version` was in for the whole of v1.
    """
    artifact_addressed = {name for name, _ in _ADDRESSED_ARTIFACT_VARIATIONS}
    declaration_addressed = {name for name, _ in _ADDRESSED_DECLARATION_VARIATIONS}
    unaddressed = set(ARTIFACT_UNADDRESSED_FIELDS)

    assert artifact_addressed & unaddressed == set()
    assert artifact_addressed | unaddressed == set(AlphaModelArtifact.model_fields) - {
        "schema_version",
        "declaration",
    }
    assert declaration_addressed == set(AlphaModelDeclaration.model_fields) - {"schema_version"}


def test_nothing_on_this_contract_is_recorded_without_being_addressed() -> None:
    """The exclusion mapping is empty, and empty is a measurement rather than a default.

    `RUN_MANIFEST_UNADDRESSED_FIELDS` holds five kinds of field -- two wall clocks, a lifecycle
    status, in-flight recovery bookkeeping and an observed host fact -- and this contract carries
    none of the five. The loop below asserts nothing today and becomes live the moment an entry
    arrives, which is the point: an exclusion with no stated reason is indistinguishable from an
    oversight, and the length floor is only there to stop `{"x": "clock"}` from satisfying it.

    Read-only rather than a plain dict, so "nothing is excluded" cannot be changed at run time by
    a caller that imported it.
    """
    assert dict(ARTIFACT_UNADDRESSED_FIELDS) == {}
    assert isinstance(ARTIFACT_UNADDRESSED_FIELDS, MappingProxyType)
    for field, reason in ARTIFACT_UNADDRESSED_FIELDS.items():
        assert field in AlphaModelArtifact.model_fields
        assert len(reason) > 60, field


def test_the_exclusion_mapping_is_wired_into_the_address_rather_than_documented_beside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding a key really removes that field from the digest -- driven, not described.

    The failure this rules out is a mapping that is **prose**: declared next to the call rather
    than passed into it, indistinguishable in review, and auditing nothing. While the mapping is
    empty that difference is invisible to any test that only *calls* `stable_model_id` with an
    exclusion of its own -- both forms answer identically -- so the mapping is substituted for one
    that names a field and the address is required to move. Two mutants in `V2-P4-016`'s sweep are
    what this kills and nothing else does: `exclude=frozenset(ARTIFACT_UNADDRESSED_FIELDS)`
    deleted outright, and the same expression hoisted to a module constant read once at import.

    Substituted rather than a second constant, because what is under test is that `artifact_id`
    reads *this* module attribute each time it is asked.
    """
    from openalpha_cn.domain import alpha_model

    assert _artifact().artifact_id == GOLDEN_ARTIFACT_ID

    monkeypatch.setattr(
        alpha_model,
        "ARTIFACT_UNADDRESSED_FIELDS",
        MappingProxyType({"training_example_count": "a probe, not a decision"}),
    )
    excluded = _artifact().artifact_id

    assert excluded != GOLDEN_ARTIFACT_ID
    assert excluded == stable_model_id(
        prefix=ALPHA_MODEL_ARTIFACT_PREFIX,
        model=_artifact(),
        exclude=frozenset({"training_example_count"}),
    )

    monkeypatch.undo()
    assert _artifact().artifact_id == GOLDEN_ARTIFACT_ID


def test_the_hashed_payload_is_exactly_the_declared_fields() -> None:
    """Including both `schema_version`s, which the per-field sweep cannot reach.

    Compared as an exact key set rather than a containment, so a field that quietly stopped
    reaching the digest shows here even when no variation names it.
    """
    hashed = _artifact().model_dump(
        mode="json",
        exclude_computed_fields=True,
        exclude=set(ARTIFACT_UNADDRESSED_FIELDS) or None,
    )

    assert set(hashed) == {
        "schema_version",
        "declaration",
        "feature_ids",
        "training_cutoff",
        "training_example_count",
        "parameters",
    }
    assert set(hashed["declaration"]) == {
        "schema_version",
        "name",
        "family",
        "horizon",
        "feature_version",
        "seed",
        "code_commit",
        "hyperparameters",
    }


def test_two_artifacts_that_compare_equal_share_one_address() -> None:
    """Direction two, on the one way this contract could produce two spellings of one number.

    `-0.0 == 0.0` is `True`, pydantic compares field values, and every arithmetic use of a
    coefficient here treats the two identically -- so these two objects are the same fitted
    model. `json.dumps` spells them apart, so before `_unsign_zero` they addressed apart: two
    equal objects, two content addresses. Asserted on both `parameters` and the declaration's
    `hyperparameters`, because the normalisation had to be written twice and one copy could
    have been forgotten.

    **Which spelling survives is asserted through `math.copysign`, and that is a mutation
    survivor made into an assertion.** A normalisation that collapsed onto `-0.0` instead makes
    the two agree just as well, so every equality below still held -- and `==` cannot see the
    difference, since `-0.0 == 0.0`. The surviving spelling has to be `+0.0`: it is what a reader
    sees in a stored payload and what `json.dumps` writes for an unsigned zero.
    """
    positive = _artifact(parameters=(("momentum_20d", 0.0),))
    negative = _artifact(parameters=(("momentum_20d", -0.0),))

    assert positive == negative
    assert positive.artifact_id == negative.artifact_id
    assert math.copysign(1.0, negative.parameters[0][1]) == 1.0
    assert "-0.0" not in negative.model_dump_json(exclude_computed_fields=True)

    declared_positive = _artifact(declaration=_declaration(hyperparameters=(("winsorize", 0.0),)))
    declared_negative = _artifact(declaration=_declaration(hyperparameters=(("winsorize", -0.0),)))

    assert declared_positive == declared_negative
    assert declared_positive.artifact_id == declared_negative.artifact_id
    assert math.copysign(1.0, declared_negative.declaration.hyperparameters[0][1]) == 1.0


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ((("value_ep", 1.0), ("momentum_20d", 2.0)), "not strictly increasing"),
        ((("momentum_20d", 1.0), ("momentum_20d", 2.0)), "not strictly increasing"),
        ((("momentum_20d", float("nan")),), "not finite"),
        ((("momentum_20d", float("inf")),), "not finite"),
    ],
)
def test_a_fit_that_could_be_spelled_two_ways_is_refused_before_it_has_an_address(
    parameters: tuple[tuple[str, float], ...], expected: str
) -> None:
    """`validate_parameters` is what makes "one fit, one address" true, and nothing tested it.

    A mutation sweep found both of its refusals uncovered by the whole suite, which matters more
    since `V2-P4-016` than it did before: an unsorted or repeated parameter list is one fitted
    model with two canonical JSON spellings and therefore two addresses -- the same direction-two
    failure the signed zero was -- and `allow_nan=False` inside `stable_model_id` means a
    non-finite coefficient does not produce a bad address but no address at all, a `ValueError`
    out of `json.dumps` naming neither the model nor the parameter.

    `AlphaModelDeclaration.hyperparameters` already had this pair
    (`test_a_declaration_refuses_a_hyperparameter_list_with_two_canonical_spellings`); the
    artifact's own list did not.
    """
    with pytest.raises(ValidationError, match=expected):
        _artifact(parameters=parameters)


def test_a_hyperparameters_declared_type_is_part_of_the_address() -> None:
    """The other direction of the same question, where the address is deliberately the stricter.

    `1`, `1.0` and `True` all compare equal in Python and are three different things to write in
    a declaration; `test_a_declaration_keeps_each_hyperparameter_at_the_type_it_was_given` is the
    shipped decision that the contract records which one its author wrote. So unlike the signed
    zero, these are not one number with two spellings, and the address separates them.
    """
    integer = _artifact(declaration=_declaration(hyperparameters=(("winsorize", 1),)))
    real = _artifact(declaration=_declaration(hyperparameters=(("winsorize", 1.0),)))
    boolean = _artifact(declaration=_declaration(hyperparameters=(("winsorize", True),)))

    assert integer.declaration.hyperparameters == real.declaration.hyperparameters
    assert len({integer.artifact_id, real.artifact_id, boolean.artifact_id}) == 3


def test_a_reference_whose_name_disagrees_with_its_artifact_is_detectable() -> None:
    """Why `AlphaModelDeclaration.name` is inside the digest and not beside it.

    `AlphaModelRef` pairs a name a human chose with a digest a build produced. With the name
    outside the digest, two references carrying two names and one `artifact_id` would both be
    consistent and a reader could not tell which run consumed which model. With it inside, the
    pair is checkable by recomputation, which is exactly what `V2-P4-010` said a digest buys
    over a name.
    """
    artifact = _artifact()
    honest = AlphaModelRef(name=artifact.declaration.name, artifact_id=artifact.artifact_id)
    mislabelled = AlphaModelRef(name="something_else", artifact_id=artifact.artifact_id)

    assert honest.artifact_id == artifact.artifact_id
    assert honest.name == artifact.declaration.name
    assert mislabelled.name != artifact.declaration.name
    assert _artifact(declaration=_declaration(name="something_else")).artifact_id != (
        mislabelled.artifact_id
    )


def test_the_address_survives_the_transport_form_a_store_will_use() -> None:
    """`V2-P4-017` stores these, and an address that did not survive storage would address nothing.

    The payload is the declared fields only -- this repository's standing rule for a model whose
    identity is a `computed_field` -- so the address is absent from the bytes and recomputed on
    the far side rather than trusted.
    """
    artifact = _artifact()
    payload = artifact.model_dump_json(exclude_computed_fields=True)

    assert artifact.artifact_id not in payload
    assert AlphaModelArtifact.model_validate_json(payload).artifact_id == artifact.artifact_id


def test_a_batch_names_its_model_by_value_and_therefore_carries_the_address_too() -> None:
    """`V2-P4-011`'s premise, now that there is an address to compare it against.

    A `PredictionBatch` names its artifact by value, which is strictly more than an address --
    and "strictly more" is checkable rather than rhetorical once the address exists: the batch
    can produce it without a lookup.
    """
    artifact = _artifact()
    batch = PredictionBatch(
        as_of=CUTOFF + timedelta(days=1),
        predicted_at=CUTOFF + timedelta(days=1),
        artifact=artifact,
        predictions=(Prediction(ts_code="000001.SZ", score=0.1),),
    )

    assert batch.artifact.artifact_id == GOLDEN_ARTIFACT_ID
