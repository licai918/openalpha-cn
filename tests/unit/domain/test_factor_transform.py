"""The preprocessing transform's contracts (`V2-P3-003`), against no store at all.

Three groups, and each one is the executable half of a claim `domain/factor_transform.py` makes
in prose.

**The identity is measured field by field, in both directions.** Roadmap section 9 is the
counter-example this repository has already paid for: `config_digest` and `random_seed` were
stated in the PRD, in an audit and in a task brief to feed `decision_id`, and an experiment
showed they do not. So every declared field of `FactorTransformSpec` -- including the nested
policies' fields, which is where a flat-model contract is easiest to get wrong -- is varied alone
and `transform_id` is asserted to move; the same for `FactorTransformManifest`. The other
direction, "what did not change must not move it", is here too, because a transform whose
identity drifted on a rebuild could never be written past `_refuse_to_drop_a_stored_build`.

**The policy's four fields are not one decision.** The refusal of a filled `not_in_universe` and
the refusal of `fill_neutral` beside an unstandardized spec are the two rules that make a
missing-value *policy* something other than a switch, and both are driven with a narrow `match=`
so a passing test says which rule caught it.

**The two provenance rules on a processed row.** A `processed` row must come from a `computed`
source and an `imputed` row must not. Without them, `coverage="processed",
source_coverage="input_missing"` is constructible and stores a number this repository invented
under the code that says a security produced it -- which is the one thing D8's "与原值分离" is
for.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FactorCoverage,
    FactorObservation,
)
from openalpha_cn.domain.factor_transform import (
    FILL_ACTIONS,
    MISSING_VALUE_ACTION_ORDER,
    MISSING_VALUE_ACTIONS,
    MISSING_VALUE_COVERAGE_ORDER,
    PROCESSED_COVERAGE_CODES,
    PROCESSED_COVERAGE_ORDER,
    PROCESSED_VALUE_CODES,
    STANDARDIZATION_METHODS,
    STANDARDIZATION_NEUTRAL,
    WINSORIZATION_METHODS,
    FactorTransformError,
    FactorTransformManifest,
    FactorTransformRegistry,
    FactorTransformSpec,
    FactorTransformStatistics,
    MissingValuePolicy,
    ProcessedFactorObservation,
    WinsorizationPolicy,
    _refuse_a_policy_that_cannot_answer_every_missing_code,
    observation_digest,
)

AS_OF: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)


def _policy(**overrides: str) -> MissingValuePolicy:
    settings: dict[str, Any] = {
        "not_in_universe": "exclude",
        "insufficient_history": "exclude",
        "input_missing": "fill_cross_sectional_median",
        "undefined_value": "refuse",
        **overrides,
    }
    return MissingValuePolicy(**settings)


def _spec(**overrides: Any) -> FactorTransformSpec:
    settings: dict[str, Any] = {
        "key": "probe",
        "version": 1,
        "winsorization": WinsorizationPolicy(
            method="quantile", lower_quantile=0.02, upper_quantile=0.98
        ),
        "standardization": "zscore",
        "missing_values": _policy(),
        "min_cross_section": 50,
        "summary": "a probe transform",
        **overrides,
    }
    return FactorTransformSpec(**settings)


def _manifest(**overrides: Any) -> FactorTransformManifest:
    settings: dict[str, Any] = {
        "transform_id": "ftx_probe",
        "transform_key": "probe",
        "transform_version": 1,
        "source_factor_id": "fct_probe",
        "source_factor_key": "reversal_1d",
        "source_factor_version": 1,
        "source_manifest_id": "fmn_probe",
        "source_observation_digest": "obs_probe",
        "as_of": AS_OF,
        "code_commit": "a1b2c3d",
        **overrides,
    }
    return FactorTransformManifest(**settings)


def _observation(**overrides: Any) -> FactorObservation:
    settings: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": AS_OF,
        "value": 0.25,
        "coverage": "computed",
        "factor_id": "fct_probe",
        "manifest_id": "fmn_probe",
        "input_row_count": 2,
        "input_session_first": None,
        "input_session_last": None,
        **overrides,
    }
    return FactorObservation(**settings)


# --- the spec's identity, field by field ---------------------------------------------------------


_SPEC_VARIATIONS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("key", {"key": "other_probe"}),
    ("version", {"version": 2}),
    (
        "winsorization.method",
        {"winsorization": WinsorizationPolicy(method="mad", mad_scale=3.0)},
    ),
    (
        "winsorization.lower_quantile",
        {
            "winsorization": WinsorizationPolicy(
                method="quantile", lower_quantile=0.05, upper_quantile=0.98
            )
        },
    ),
    (
        "winsorization.upper_quantile",
        {
            "winsorization": WinsorizationPolicy(
                method="quantile", lower_quantile=0.02, upper_quantile=0.95
            )
        },
    ),
    ("standardization", {"standardization": "rank"}),
    ("missing_values.not_in_universe", {"missing_values": _policy(not_in_universe="refuse")}),
    (
        "missing_values.insufficient_history",
        {"missing_values": _policy(insufficient_history="fill_neutral")},
    ),
    ("missing_values.input_missing", {"missing_values": _policy(input_missing="exclude")}),
    ("missing_values.undefined_value", {"missing_values": _policy(undefined_value="exclude")}),
    ("min_cross_section", {"min_cross_section": 51}),
    ("summary", {"summary": "a different probe transform"}),
)
"""Every declared field of `FactorTransformSpec`, including the nested policies', and one way to
move each. `schema_version` is a `Literal` with one member and is covered by the field-set
assertion below rather than by a variation nobody can construct."""


@pytest.mark.parametrize(("field", "overrides"), _SPEC_VARIATIONS)
def test_every_declared_transform_field_reaches_the_identity(
    field: str, overrides: dict[str, Any]
) -> None:
    """One field at a time, asserting `transform_id` **moves** rather than that it exists.

    Roadmap section 9's lesson: a field is in an identity only if it is a field of the model that
    is hashed, and nested models are where that is easiest to get wrong -- a
    `WinsorizationPolicy` carried by reference rather than dumped would leave every quantile out
    of the address while the spec looked complete.
    """
    baseline = _spec()

    assert _spec(**overrides).transform_id != baseline.transform_id, field


def test_the_variation_table_covers_every_declared_field_of_the_spec_and_its_policies() -> None:
    """The direction a parametrized list cannot cover: a *new* field.

    `test_every_declared_transform_field_reaches_the_identity` would keep passing while a
    thirteenth field arrived with nothing varying it, which is exactly how `subject_digest` came
    to be missing from `FactorBuildManifest` for a whole issue.
    """
    varied = {name.split(".", maxsplit=1)[0] for name, _ in _SPEC_VARIATIONS}
    nested = {name.split(".", maxsplit=1)[1] for name, _ in _SPEC_VARIATIONS if "." in name}

    assert varied | {"schema_version"} == set(FactorTransformSpec.model_fields)
    assert nested == set(WinsorizationPolicy.model_fields) - {"method", "mad_scale"} | set(
        MissingValuePolicy.model_fields
    ) | {"method"}


def test_the_same_declaration_reproduces_the_same_identity() -> None:
    """The other half of a content address: what did not change must not move it.

    A transform whose ID drifted between two identical declarations would make every stored
    partition unreachable from the spec that produced it, and `write_processed_factor_panels`
    would refuse the rebuild for dropping a build it had just recomputed.
    """
    assert _spec().transform_id == _spec().transform_id
    assert _spec().qualified_key == "probe/v1"


def test_a_transform_key_that_could_not_be_a_panel_identifier_is_refused() -> None:
    with pytest.raises(ValidationError, match="transform key"):
        _spec(key="probe/v2")


def test_a_prose_only_edit_moves_the_identity_and_changes_no_number() -> None:
    """`summary` is prose, it is inside `transform_id`, and that is a disclosed defect.

    The exact shape `FactorTransformManifest`'s docstring refuses `date_timezone` for -- a field
    that "reaches the identity and decides nothing" -- sitting inside the identity already. Two
    specs differing in one character of prose declare the same winsorization, the same
    standardization, the same floor and the same four actions, and get two `transform_id`s;
    every build stored under the first is then a build `FACTOR_TRANSFORMS.by_id` cannot resolve.

    It is not fixed here: `FactorDefinition.summary` has been inside `factor_id` since
    `V2-P3-001`, so removing it would either leave the two identity contracts disagreeing or move
    every stored `factor_id`. It is *pinned*, so that the cost is a measurement in the suite
    rather than a sentence in a docstring, and so that a later decision to take it out has a test
    to delete rather than a claim to re-derive.
    """
    original = _spec(summary="clip the tails, then z-score")
    typo_fixed = _spec(summary="clip the tails, then z-score.")

    assert original.transform_id != typo_fixed.transform_id
    assert original.winsorization == typo_fixed.winsorization
    assert original.standardization == typo_fixed.standardization
    assert original.missing_values == typo_fixed.missing_values
    assert original.min_cross_section == typo_fixed.min_cross_section
    assert original.qualified_key == typo_fixed.qualified_key


def test_the_floor_bound_is_a_range_check_and_admits_a_transform_wider_than_the_market() -> None:
    """`min_cross_section`'s upper bound does **not** rule out a floor above the whole market,
    and the docstring no longer says it does.

    It read that 10,000 is "comfortably above the ~5,500-name whole-market cross section ...
    a `min_cross_section` above the market is a transform that can never produce anything, which
    is the vacuity `FactorRegistry` refuses an empty registry for". A bound of 10,000 admits
    exactly that transform, which is what the first two assertions measure. The rest is why that
    is acceptable rather than a hole: a floor above the cross section is an *answer* --
    `insufficient_cross_section` for every security, with the participant count recorded -- and
    not an error, so refusing it at declaration time would be hard-coding today's listing count
    into a contract.
    """
    above_the_market = _spec(min_cross_section=10_000)

    assert above_the_market.min_cross_section == 10_000
    with pytest.raises(ValidationError, match="less than or equal to 10000"):
        _spec(min_cross_section=10_001)
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _spec(min_cross_section=0)


# --- the manifest's identity ---------------------------------------------------------------------


_MANIFEST_VARIATIONS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("transform_id", {"transform_id": "ftx_other"}),
    ("transform_key", {"transform_key": "other"}),
    ("transform_version", {"transform_version": 2}),
    ("source_factor_id", {"source_factor_id": "fct_other"}),
    ("source_factor_key", {"source_factor_key": "momentum_20d"}),
    ("source_factor_version", {"source_factor_version": 2}),
    ("source_manifest_id", {"source_manifest_id": "fmn_other"}),
    ("source_observation_digest", {"source_observation_digest": "obs_other"}),
    ("as_of", {"as_of": datetime(2026, 1, 13, 4, 0, tzinfo=UTC)}),
    ("code_commit", {"code_commit": "0000000"}),
)


@pytest.mark.parametrize(("field", "overrides"), _MANIFEST_VARIATIONS)
def test_every_declared_manifest_field_reaches_the_identity(
    field: str, overrides: dict[str, Any]
) -> None:
    baseline = _manifest()

    assert _manifest(**overrides).transform_manifest_id != baseline.transform_manifest_id, field


def test_the_manifest_declares_exactly_the_ten_fields_the_variation_table_varies() -> None:
    """A new manifest field fails here until somebody shows it moves the ID.

    The exact set rather than a subset, because both directions are faults: a field nobody varies
    is a field nobody has shown reaches the address, and a variation naming a field that no longer
    exists is a test asserting about nothing.
    """
    varied = {name for name, _ in _MANIFEST_VARIATIONS}

    assert varied | {"schema_version"} == set(FactorTransformManifest.model_fields)


def test_the_manifest_carries_no_wall_clock_and_no_timezone() -> None:
    """Two absences, and both are claims this contract makes in prose.

    `built_at` is out because re-applying the same transform to the same source build must
    reproduce its ID, or a rebuild can never be written past the drop guard. `date_timezone` is
    out because a transform resolves no date -- a field that reached the identity and decided
    nothing would be roadmap section 9's defect with the halves swapped.
    """
    fields = set(FactorTransformManifest.model_fields)

    assert "built_at" not in fields
    assert "date_timezone" not in fields
    assert {"as_of", "code_commit", "source_observation_digest"} <= fields


# --- the winsorization policy's cross-field rules ------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"method": "none", "lower_quantile": 0.01}, "reads no parameter"),
        ({"method": "none", "mad_scale": 3.0}, "reads no parameter"),
        (
            {
                "method": "quantile",
                "lower_quantile": 0.01,
                "upper_quantile": 0.99,
                "mad_scale": 3.0,
            },
            "does not read mad_scale",
        ),
        ({"method": "quantile", "lower_quantile": 0.01}, "needs both lower_quantile"),
        ({"method": "mad"}, "needs mad_scale"),
        ({"method": "mad", "mad_scale": 3.0, "lower_quantile": 0.01}, "does not read"),
    ],
)
def test_a_winsorization_parameter_the_declared_method_ignores_is_refused(
    settings: dict[str, Any], expected: str
) -> None:
    """An inert parameter is not untidiness: it enters `transform_id`.

    `WinsorizationPolicy(method="mad", mad_scale=3.0, lower_quantile=0.01)` reads to anybody
    scanning a stored manifest row like a 1% winsorization and is not one, and it gives two
    behaviourally identical transforms two content addresses and two partitions of numbers that
    nothing can reconcile.
    """
    with pytest.raises(ValidationError, match=expected):
        WinsorizationPolicy(**settings)


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [
        (0.5, 0.5, "is not below"),
        (0.9, 0.1, "is not below"),
        (-0.1, 0.9, r"fraction in \[0, 1\]"),
        (0.1, 1.5, r"fraction in \[0, 1\]"),
    ],
)
def test_quantiles_must_be_ordered_fractions(lower: float, upper: float, expected: str) -> None:
    """Equal quantiles put both bounds on one point, which clips the whole cross section to it.

    Refused at declaration time so that `_refuse_a_scale_estimator_that_collapsed`'s run-time
    refusal is unambiguously about the *data* rather than about a spec that could never have
    winsorized anything.
    """
    with pytest.raises(ValidationError, match=expected):
        WinsorizationPolicy(method="quantile", lower_quantile=lower, upper_quantile=upper)


@pytest.mark.parametrize("scale", [0.0, -1.0])
def test_a_mad_scale_that_is_not_strictly_positive_is_refused(scale: float) -> None:
    with pytest.raises(ValidationError, match="strictly positive"):
        WinsorizationPolicy(method="mad", mad_scale=scale)


def test_the_three_declared_winsorization_methods_are_each_constructible() -> None:
    """The positive half, without which every refusal above is satisfied by a policy that
    refuses everything."""
    built = {
        WinsorizationPolicy(method="none").method,
        WinsorizationPolicy(method="quantile", lower_quantile=0.01, upper_quantile=0.99).method,
        WinsorizationPolicy(method="mad", mad_scale=3.0).method,
    }

    assert built == WINSORIZATION_METHODS


# --- the missing-value policy --------------------------------------------------------------------


@pytest.mark.parametrize("action", sorted(FILL_ACTIONS))
def test_a_filled_not_in_universe_is_refused(action: str) -> None:
    """The one rule the policy enforces rather than records, and the reason it is not a taste.

    A security the caller declared outside the cross section is not one with missing data: it had
    not listed yet, or had already delisted. Imputing a value puts it into the scored cross
    section with a number derived from securities it was never comparable to -- and every
    historical cross section a research programme walks over has such names in it.
    """
    with pytest.raises(ValidationError, match="not a security with missing data"):
        _policy(not_in_universe=action)


@pytest.mark.parametrize("code", ["insufficient_history", "input_missing", "undefined_value"])
def test_the_other_three_codes_may_be_filled(code: str) -> None:
    """The asymmetry is the point: three of the four are the caller's declared judgement.

    Without this, the refusal above would be indistinguishable from a blanket ban on filling.
    """
    policy = _policy(**{code: "fill_cross_sectional_median"})

    assert policy.action_for(code) == "fill_cross_sectional_median"  # type: ignore[arg-type]


def test_fill_neutral_beside_an_unstandardized_spec_is_refused() -> None:
    """There is no neutral point on a scale nothing was centred on.

    A factor whose raw values sit around 50 would have every missing name imputed at `0.0`, which
    is an extreme of the cross section rather than the middle of it. The refusal names the codes
    that asked for it, because a spec with four actions gives four places to look.
    """
    with pytest.raises(ValidationError, match="has no neutral point"):
        _spec(standardization="none", missing_values=_policy(input_missing="fill_neutral"))


def test_fill_neutral_is_legal_beside_a_method_that_has_one() -> None:
    for method in sorted(STANDARDIZATION_METHODS - {"none"}):
        spec = _spec(standardization=method, missing_values=_policy(input_missing="fill_neutral"))

        assert STANDARDIZATION_NEUTRAL[spec.standardization] == 0.0


def test_the_policy_answers_every_non_computed_code_and_refuses_the_computed_one() -> None:
    """`action_for` is the whole dispatch, so both of its ends are pinned here."""
    policy = _policy()

    assert {code: policy.action_for(code) for code in MISSING_VALUE_COVERAGE_ORDER} == {
        "not_in_universe": "exclude",
        "insufficient_history": "exclude",
        "input_missing": "fill_cross_sectional_median",
        "undefined_value": "refuse",
    }
    with pytest.raises(FactorTransformError, match="not a hole in it"):
        policy.action_for("computed")


def test_a_policy_carrying_an_action_this_build_does_not_declare_is_refused_where_it_is_read() -> (
    None
):
    """`action_for` returns *from* `MISSING_VALUE_ACTION_ORDER` rather than casting, so a policy
    that skipped validation cannot hand back a `MissingValueAction` the type system believes is
    one of four and is not.

    Reached through `model_construct`, which skips every validator -- the same hole
    `panel/catalog.py` argues about an overridable `__post_init__`, and the reason the engine's
    winsorizers re-check what the contract already refuses.
    """
    smuggled = MissingValuePolicy.model_construct(
        not_in_universe="exclude",
        insufficient_history="exclude",
        input_missing="interpolate",
        undefined_value="exclude",
    )

    with pytest.raises(FactorTransformError, match="which is not one of"):
        smuggled.action_for("input_missing")


def test_the_policy_has_one_field_per_non_computed_coverage_code() -> None:
    """The import-time audit's passing case, run explicitly so it is a test and not a side effect.

    A sixth `FactorCoverage` member arriving with no policy field would make `action_for` raise
    at run time, in production, on the first cross section that carried one.
    """
    assert set(MissingValuePolicy.model_fields) == set(FACTOR_COVERAGE_CODES) - {"computed"}
    assert (
        _refuse_a_policy_that_cannot_answer_every_missing_code(
            MissingValuePolicy.model_fields, MISSING_VALUE_COVERAGE_ORDER
        )
        is None
    )


def test_the_audit_fails_on_a_policy_that_is_missing_a_code_and_on_one_that_invents_one() -> None:
    """The sentinel. An audit whose only call site is the one that passes is an audit nobody has
    seen fail, and this repository has the counter-example on file: `_refuse_table_drift` earned
    a third test for exactly this reason."""
    with pytest.raises(FactorTransformError, match="every reason a security has no raw value"):
        _refuse_a_policy_that_cannot_answer_every_missing_code(
            ["not_in_universe", "input_missing"], MISSING_VALUE_COVERAGE_ORDER
        )
    with pytest.raises(FactorTransformError, match="every reason a security has no raw value"):
        _refuse_a_policy_that_cannot_answer_every_missing_code(
            [*MISSING_VALUE_COVERAGE_ORDER, "invented_code"], MISSING_VALUE_COVERAGE_ORDER
        )
    with pytest.raises(FactorTransformError, match="four stored column names"):
        _refuse_a_policy_that_cannot_answer_every_missing_code(
            MissingValuePolicy.model_fields, ("not_in_universe", "input_missing")
        )


def test_the_four_actions_are_the_declared_vocabulary_in_a_stable_order() -> None:
    assert set(MISSING_VALUE_ACTION_ORDER) == MISSING_VALUE_ACTIONS
    assert len(MISSING_VALUE_ACTION_ORDER) == len(MISSING_VALUE_ACTIONS) == 4
    assert FILL_ACTIONS < MISSING_VALUE_ACTIONS
    assert "refuse" not in FILL_ACTIONS


# --- the source cross section's digest ------------------------------------------------------------


def test_the_observation_digest_moves_with_a_value_and_not_with_row_order() -> None:
    """Both halves of what makes `panel` an audited determinant rather than an exempted one.

    A build manifest identifies a computation's *inputs*, so two panels carrying one
    `manifest_id` and different observations are constructible; without this digest a transform's
    identity would be blind to the very numbers it transformed. And the order of a cross section
    is not part of what it is -- `apply_factor_transform` produces one independent row per
    subject -- so an identity that moved for a shuffle would fail the contract's other half.
    """
    first = _observation(subject="000001.SZ", value=0.25)
    second = _observation(subject="000002.SZ", value=0.50)
    moved = _observation(subject="000002.SZ", value=0.51)

    assert observation_digest([first, second]) == observation_digest([second, first])
    assert observation_digest([first, second]) != observation_digest([first, moved])


def test_the_observation_digest_moves_when_only_a_coverage_code_moves() -> None:
    """A cross section where one name went from `computed` to `input_missing` is a different
    cross section even though no number changed -- the missing-value policy will act on it."""
    computed = _observation(subject="000002.SZ", value=0.50)
    missing = _observation(subject="000002.SZ", value=None, coverage="input_missing")

    assert observation_digest([computed]) != observation_digest([missing])


def test_the_observation_digest_refuses_a_duplicated_subject() -> None:
    """Two answers to one question would give two different cross sections one address."""
    with pytest.raises(FactorTransformError, match="appears more than once"):
        observation_digest([_observation(), _observation(value=0.30)])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_value_is_refused_by_a_message_that_names_the_security(value: float) -> None:
    """`allow_nan=False` was already load-bearing; what it raised was not actionable.

    The refusal is right -- a cross section carrying a nan has no reproducible content address,
    so minting one is worse than failing. But `json.dumps` raises a bare
    `ValueError: Out of range float values are not JSON compliant`, which names no security, no
    dataset and no remedy, and the reader of it is holding a panel of thousands of rows. The
    translation carries the subject and the door the row came through, which is the only door it
    can have come through: `validate_factor_observation` refuses a non-finite value at both of
    its call sites, so a subclass overrode `__post_init__`.
    """

    class _Unchecked(FactorObservation):
        def __post_init__(self) -> None:
            return None

    smuggled = _Unchecked(
        subject="600519.SH",
        as_of=AS_OF,
        value=value,
        coverage="computed",
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=2,
        input_session_first=None,
        input_session_last=None,
    )

    with pytest.raises(FactorTransformError, match=r"600519\.SH.*non-finite value"):
        observation_digest([_observation(), smuggled])


# --- the processed observation's rules ------------------------------------------------------------


def _processed(**overrides: Any) -> ProcessedFactorObservation:
    settings: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": AS_OF,
        "value": 1.5,
        "coverage": "processed",
        "transform_id": "ftx_probe",
        "transform_manifest_id": "ftm_probe",
        "source_factor_id": "fct_probe",
        "source_manifest_id": "fmn_probe",
        "source_coverage": "computed",
        **overrides,
    }
    return ProcessedFactorObservation(**settings)


@pytest.mark.parametrize("code", sorted(PROCESSED_VALUE_CODES))
def test_exactly_the_two_value_codes_may_carry_a_number(code: str) -> None:
    """`FactorCoverage`'s "exactly `computed` carries a value" has two members on this plane, and
    the second is the whole point of having a missing-value policy: an imputed number is one this
    repository made up, and a reader who cannot tell it from a measurement computes an
    information coefficient partly on the median it imputed."""
    source: FactorCoverage = "computed" if code == "processed" else "input_missing"

    assert _processed(coverage=code, value=1.5, source_coverage=source).value == 1.5
    with pytest.raises(FactorTransformError, match="carry a value"):
        _processed(coverage=code, value=None, source_coverage=source)


@pytest.mark.parametrize(
    "code", ["source_not_computed", "insufficient_cross_section", "degenerate_cross_section"]
)
def test_the_three_valueless_codes_may_not_carry_a_number(code: str) -> None:
    assert _processed(coverage=code, value=None, source_coverage="input_missing").value is None
    with pytest.raises(FactorTransformError, match="carry a value"):
        _processed(coverage=code, value=0.0, source_coverage="input_missing")


def test_a_processed_row_must_have_come_from_a_computed_source() -> None:
    """The first of the two rules that make "与原值分离" structural rather than documentary."""
    with pytest.raises(FactorTransformError, match="transformed measurement"):
        _processed(coverage="processed", value=1.5, source_coverage="input_missing")


def test_an_imputed_row_must_not_have_come_from_a_computed_source() -> None:
    """The second. A security that had a measured value gave the policy nothing to stand in for,
    so an `imputed` row over it would be a number that silently replaced one that existed."""
    with pytest.raises(FactorTransformError, match="nothing for the missing-value policy"):
        _processed(coverage="imputed", value=1.5, source_coverage="computed")


def test_a_source_not_computed_row_over_a_computed_source_is_refused() -> None:
    with pytest.raises(FactorTransformError, match="each other's negation"):
        _processed(coverage="source_not_computed", value=None, source_coverage="computed")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_processed_value_is_refused(value: float) -> None:
    """No declared processed coverage code carries one. `undefined_value` is the raw plane's
    answer to a non-finite arithmetic and there is no counterpart here, so an infinity is a row
    nothing describes -- and one that poisons every mean, rank and regression downstream."""
    with pytest.raises(FactorTransformError, match="a non-finite processed value poisons"):
        _processed(value=value)


def test_a_processed_row_refuses_an_empty_subject_and_an_undeclared_code() -> None:
    with pytest.raises(FactorTransformError, match="must name a subject"):
        _processed(subject="")
    with pytest.raises(FactorTransformError, match="not a declared processed coverage code"):
        _processed(coverage="probably_fine")
    with pytest.raises(FactorTransformError, match="not a declared factor coverage code"):
        _processed(coverage="imputed", source_coverage="probably_fine")


def test_the_processed_vocabulary_is_the_five_codes_this_build_declares() -> None:
    """A closed set asserted as a set literal, `FACTOR_COVERAGE_ORDER`'s treatment: an assertion
    of membership is additive and can see a renamed code but never a removed one."""
    assert {
        "processed",
        "imputed",
        "source_not_computed",
        "insufficient_cross_section",
        "degenerate_cross_section",
    } == PROCESSED_COVERAGE_CODES
    assert set(PROCESSED_COVERAGE_ORDER) == PROCESSED_COVERAGE_CODES
    assert PROCESSED_COVERAGE_ORDER[0] == "processed"
    assert {"processed", "imputed"} == PROCESSED_VALUE_CODES


# --- the registry and the statistics --------------------------------------------------------------


def test_the_registry_refuses_an_empty_declaration_and_a_repeated_name() -> None:
    """`FactorRegistry`'s two refusals, for its two reasons: an empty registry satisfies every
    per-spec check vacuously, and two specs answering to one name make a lookup arbitrary."""
    with pytest.raises(FactorTransformError, match="at least one"):
        FactorTransformRegistry(())
    with pytest.raises(FactorTransformError, match="declared more than once"):
        FactorTransformRegistry((_spec(), _spec(min_cross_section=51)))


def test_the_registry_resolves_a_spec_by_name_and_by_content_address() -> None:
    registry = FactorTransformRegistry((_spec(), _spec(key="other")))

    assert registry.get("probe/v1") == _spec()
    assert registry.by_id(_spec().transform_id) == _spec()
    assert registry.qualified_keys == ("probe/v1", "other/v1")
    with pytest.raises(FactorTransformError, match="not a declared transform"):
        registry.get("absent/v1")
    with pytest.raises(FactorTransformError, match="not a transform this build declares"):
        registry.by_id("ftx_absent")


def _statistics(**overrides: Any) -> FactorTransformStatistics:
    settings: dict[str, Any] = {
        "participant_count": 10,
        "winsorized_low_count": 1,
        "winsorized_high_count": 1,
        "imputed_count": 0,
        "lower_bound": -1.0,
        "upper_bound": 1.0,
        "location": 0.0,
        "scale": 0.5,
        **overrides,
    }
    return FactorTransformStatistics(**settings)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"participant_count": -1}, "cannot be negative"),
        ({"winsorized_low_count": 6, "winsorized_high_count": 6}, "cannot move a value"),
        ({"lower_bound": None}, "both present or both absent"),
        ({"lower_bound": 2.0}, "runs backwards"),
        ({"scale": 0.0}, "degenerate_cross_section"),
        ({"scale": float("inf")}, "not a finite number"),
    ],
)
def test_the_statistics_refuse_a_record_that_could_not_have_happened(
    overrides: dict[str, Any], expected: str
) -> None:
    """Every one of these would be a provenance field that is *wrong* rather than absent, which
    is the failure `_batch_digests_by_partition` refuses one plane down: a stored `scale` of zero
    would claim a completed standardization on a cross section that had none."""
    with pytest.raises(FactorTransformError, match=expected):
        _statistics(**overrides)


def test_the_statistics_accept_the_shapes_the_engine_actually_produces() -> None:
    """The positive half: a `none` winsorization has no bounds and a `rank` standardization has
    no location or scale, so a record with four `None`s is legal rather than incomplete."""
    assert _statistics(lower_bound=None, upper_bound=None, location=None, scale=None).scale is None
    assert _statistics(location=None, scale=None).location is None


def test_a_spec_is_frozen_and_a_deep_copy_of_one_keeps_its_identity() -> None:
    """Frozen because a spec whose parameters could be edited after a build would leave a stored
    `transform_id` naming something that no longer exists."""
    spec = _spec()

    with pytest.raises(ValidationError):
        spec.min_cross_section = 60  # type: ignore[misc]
    assert copy.deepcopy(spec).transform_id == spec.transform_id
