"""The versioned factor definition contract (`V2-P3-001`).

Three things are asserted here and each is a direct answer to something this repository has
already got wrong once.

**Every declared field reaches the identity.** Roadmap section 9 records the case where it did
not: `config_digest` and `random_seed` were asserted -- in the PRD, in an audit document and in
a task brief -- to feed `decision_id`, and a review's experiment showed they do not, because
they are fields of `RunManifest` and `RunManifest` is not one of the models `stable_model_id` is
applied to. The lesson is that an identity claim has to be *measured* field by field. So
`test_every_definition_field_reaches_the_identity` and its manifest twin vary one field at a
time and assert the ID moves, rather than asserting that an ID exists.

**The six required properties are fields, and each is constrained.** `V2-P3-001`'s acceptance
lists stable identity, version, family, required fields, lookback window and direction. A test
that only checked they were *present* would pass against a definition whose family is
`"whatever"` and whose lookback is `0` -- the shape `SignalFrame.horizon` had until
`V2-P1-017` narrowed it (`min_length=1, max_length=64` admitted `'whenever'`).

**The registry refuses the two shapes that make a lookup meaningless**, and declines to refuse
a third. An empty registry satisfies every "for each definition" assertion vacuously, which is
the same fault as a table with a key and no branch behind it with the halves swapped; a repeated
handle makes `get()` arbitrary. The third -- two handles sharing one content address -- reads
like an obvious guard and is a branch nothing can reach, and
`test_two_distinct_names_can_never_share_a_content_address` is why.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FACTOR_DIRECTIONS,
    FACTOR_FAMILIES,
    FactorBuildManifest,
    FactorCoverage,
    FactorDefinition,
    FactorDirection,
    FactorError,
    FactorFamily,
    FactorField,
    FactorInputRef,
    FactorObservation,
    FactorRegistry,
)

AS_OF = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)


def _definition(**overrides: Any) -> FactorDefinition:
    fields = {
        "key": "probe_factor",
        "version": 1,
        "family": "value",
        "direction": "higher_is_better",
        "required_fields": (FactorField(dataset="daily", column="close"),),
        "lookback_sessions": 20,
        "summary": "a probe",
        **overrides,
    }
    return FactorDefinition(**fields)


def _input_ref(**overrides: Any) -> FactorInputRef:
    fields = {
        "dataset": "daily",
        "year": 2026,
        "batch_digest": "sha256:aa",
        "partition_content_hash": "bb",
        "visible_row_count": 39,
        "withheld_row_count": 40,
        **overrides,
    }
    return FactorInputRef(**fields)


def _manifest(**overrides: Any) -> FactorBuildManifest:
    fields = {
        "factor_id": "fct_probe",
        "factor_key": "probe_factor",
        "factor_version": 1,
        "as_of": AS_OF,
        "date_timezone": "Asia/Shanghai",
        "code_commit": "abc1234",
        "lookback_sessions": 20,
        "subject_count": 8,
        "universe_count": 8,
        "inputs": (_input_ref(),),
        **overrides,
    }
    return FactorBuildManifest(**fields)


# --- the six properties ------------------------------------------------------------------------


def test_a_definition_carries_all_six_properties_the_acceptance_names() -> None:
    definition = _definition()

    assert definition.factor_id.startswith("fct_")
    assert definition.version == 1
    assert definition.family in FACTOR_FAMILIES
    assert definition.required_fields == (FactorField(dataset="daily", column="close"),)
    assert definition.lookback_sessions == 20
    assert definition.direction in FACTOR_DIRECTIONS


def test_the_family_and_direction_vocabularies_are_closed_and_match_their_literals() -> None:
    """The data copies and the `Literal`s cannot drift, because a check that enumerates one
    while a model validates the other would let a sixth family in through the model."""
    assert set(get_args(FactorFamily)) == FACTOR_FAMILIES
    assert set(get_args(FactorDirection)) == FACTOR_DIRECTIONS
    assert set(get_args(FactorCoverage)) == FACTOR_COVERAGE_CODES
    assert {
        "value",
        "quality",
        "growth",
        "momentum_reversal",
        "volatility_liquidity",
    } == FACTOR_FAMILIES


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", "momentum"),
        ("direction", "bigger_is_better"),
        ("lookback_sessions", 0),
        ("version", 0),
        ("required_fields", ()),
        ("summary", ""),
    ],
)
def test_each_required_property_is_constrained_and_not_merely_present(
    field: str, value: object
) -> None:
    """Presence is not the property. A family outside the five would become a redundancy group
    of one in `V2-P3-008`; a zero lookback is a window with no rows in it; an empty
    `required_fields` is a coverage check that can never find a shortfall, which is the same
    vacuity `ReadinessRequirement` refuses with `empty_requirement`."""
    with pytest.raises(ValidationError):
        _definition(**{field: value})


def test_a_field_reference_is_held_to_the_panel_planes_own_identifier_rules() -> None:
    """A definition is data a later engine turns into SQL, so a column name that could not be
    one is refused where it is *declared* rather than several layers down as a binder error."""
    with pytest.raises(ValidationError):
        FactorField(dataset="daily", column='close" ; ATTACH')
    with pytest.raises(ValidationError):
        FactorField(dataset="../escaped", column="close")
    with pytest.raises(ValidationError):
        _definition(key="probe factor")


@pytest.mark.parametrize(
    "column", ["subject", "event_time", "available_time", "ingested_time", "revision_time"]
)
def test_a_reserved_batch_column_cannot_be_a_factor_input(column: str) -> None:
    """`subject` is the security an observation is *about* and the four clocks are what decides
    whether a row may be read at all. A factor scoring one of them would be scoring the
    point-in-time machinery, and the engine's projection would carry the name twice."""
    with pytest.raises(ValidationError, match="reserved columns"):
        FactorField(dataset="daily", column=column)


def test_a_definition_refuses_to_declare_the_same_column_twice() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        _definition(
            required_fields=(
                FactorField(dataset="daily", column="close"),
                FactorField(dataset="daily", column="close"),
            )
        )


def test_the_datasets_and_columns_helpers_answer_in_declared_order() -> None:
    definition = _definition(
        required_fields=(
            FactorField(dataset="daily", column="close"),
            FactorField(dataset="daily_basic", column="total_mv"),
            FactorField(dataset="daily", column="vol"),
        )
    )

    assert definition.datasets == ("daily", "daily_basic")
    assert definition.columns_of("daily") == ("close", "vol")
    assert definition.columns_of("daily_basic") == ("total_mv",)
    assert definition.columns_of("income") == ()


# --- identity ------------------------------------------------------------------------------------


def test_identity_is_stable_model_id_and_not_a_hash_of_this_modules_own_devising() -> None:
    """`V2-P3-001` requires the reuse. Asserted against the helper itself rather than against a
    golden string, so the property under test is "the same canonicalisation everything else
    uses" and not "this exact digest"."""
    definition = _definition()

    assert definition.factor_id == stable_model_id(prefix="fct", model=definition)
    assert _definition().factor_id == definition.factor_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", "other_factor"),
        ("version", 2),
        ("family", "quality"),
        ("direction", "lower_is_better"),
        ("lookback_sessions", 21),
        ("summary", "a different probe"),
        ("required_fields", (FactorField(dataset="daily", column="vol"),)),
    ],
)
def test_every_definition_field_reaches_the_identity(field: str, value: object) -> None:
    """One field at a time, against roadmap section 9's measured mistake.

    A field that is carried and never hashed is provenance, not identity, and the way that goes
    unnoticed is a test that asserts the ID exists.
    """
    assert _definition(**{field: value}).factor_id != _definition().factor_id


def test_the_schema_version_is_hashed_so_a_future_v2_cannot_collide_with_a_v1() -> None:
    """Roadmap section 8 measured this on four contracts: `schema_version` is a real field, so
    `model_dump(exclude_computed_fields=True)` includes it and a bump moves every ID.

    Wanted here rather than tolerated -- a `factor-definition/v2` describes a different contract
    and its observations must not be indistinguishable from a `v1`'s. Asserted by hashing the
    same field values with a different stamp, which is what a v2 would do.
    """
    definition = _definition()
    payload = definition.model_dump(mode="json", exclude_computed_fields=True)

    assert payload["schema_version"] == "factor-definition/v1"
    assert "factor_id" not in payload
    assert "qualified_key" not in payload


def test_the_computed_handles_are_excluded_from_the_hash_they_derive_from() -> None:
    """`qualified_key` is a computed field, so it adds no second canonicalisation that could
    disagree with the first -- the failure mode a hand-written second hash would have."""
    definition = _definition(key="probe_factor", version=3)

    assert definition.qualified_key == "probe_factor/v3"
    assert definition.factor_id == stable_model_id(prefix="fct", model=definition)


# --- the registry ---------------------------------------------------------------------------------


def test_a_registry_refuses_to_be_empty() -> None:
    """An empty registry satisfies every per-definition assertion vacuously, which is the same
    fault as a table key with no branch behind it."""
    with pytest.raises(FactorError, match="at least one definition"):
        FactorRegistry(())


def test_a_registry_refuses_two_definitions_answering_to_one_name() -> None:
    with pytest.raises(FactorError, match="declared more than once"):
        FactorRegistry((_definition(), _definition(summary="restated, version not bumped")))


def test_two_distinct_names_can_never_share_a_content_address() -> None:
    """Why `FactorRegistry` has no `factor_id`-uniqueness check, asserted rather than argued.

    `key` and `version` are both hashed into `factor_id`, so distinct handles imply distinct
    IDs and the guard that "reads like an obvious one" would be a branch nothing can reach. Two
    definitions differing in *nothing but* the key or the version are the tightest available
    probe of that, and are the pair a duplicate-ID check would have been written for.
    """
    by_key = (_definition(key="alpha_probe"), _definition(key="beta_probe"))
    by_version = (_definition(version=1), _definition(version=2))

    for pair in (by_key, by_version):
        registry = FactorRegistry(pair)
        assert len(set(registry.factor_ids)) == 2
        assert len(set(registry.qualified_keys)) == 2


def test_a_registry_resolves_by_name_and_by_content_address_and_names_what_it_knows() -> None:
    first = _definition(key="alpha_probe")
    second = _definition(key="beta_probe", family="growth")
    registry = FactorRegistry((first, second))

    assert registry.qualified_keys == ("alpha_probe/v1", "beta_probe/v1")
    assert registry.get("beta_probe/v1") is second
    assert registry.by_id(first.factor_id) is first
    with pytest.raises(FactorError, match=r"\['alpha_probe/v1', 'beta_probe/v1'\]"):
        registry.get("gamma_probe/v1")
    with pytest.raises(FactorError, match="not a factor this build declares"):
        registry.by_id("fct_nothing")


def test_two_versions_of_one_key_coexist_and_have_different_identities() -> None:
    """The point of versioning: a restatement does not overwrite the observations of the factor
    it replaces, because the stored `factor_id` differs."""
    registry = FactorRegistry((_definition(version=1), _definition(version=2)))

    assert registry.qualified_keys == ("probe_factor/v1", "probe_factor/v2")
    assert len(set(registry.factor_ids)) == 2


# --- the build manifest --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("factor_id", "fct_other"),
        ("factor_key", "other_factor"),
        ("factor_version", 2),
        ("as_of", datetime(2026, 1, 13, 4, 0, tzinfo=UTC)),
        ("date_timezone", "UTC"),
        ("code_commit", "deadbee"),
        ("lookback_sessions", 21),
        ("subject_count", 9),
        ("universe_count", 7),
        ("inputs", (_input_ref(visible_row_count=38),)),
    ],
)
def test_every_manifest_field_reaches_the_identity(field: str, value: object) -> None:
    """The same measurement as for the definition, and the one section 9 is actually about:
    a build manifest is exactly the kind of record whose fields get believed into an identity
    they never entered."""
    assert _manifest(**{field: value}).manifest_id != _manifest().manifest_id


def test_the_wall_clock_is_not_a_manifest_field_so_a_rebuild_reproduces_the_identity() -> None:
    """The arrangement section 9 says was wanted and not had: the wall clock is recorded
    somewhere (the observation partition's `fetched_at`) and kept out of the content address,
    so recomputing an unchanged factor at an unchanged `as_of` yields the same `manifest_id`.

    Asserted as an absence from the hashed payload, not as "two calls agree" -- two calls with
    no clock in them agree for any number of reasons.
    """
    payload = _manifest().model_dump(mode="json", exclude_computed_fields=True)

    assert "built_at" not in payload
    assert set(payload) == {
        "schema_version",
        "factor_id",
        "factor_key",
        "factor_version",
        "as_of",
        "date_timezone",
        "code_commit",
        "lookback_sessions",
        "subject_count",
        "universe_count",
        "inputs",
    }
    assert _manifest().manifest_id == _manifest().manifest_id


def test_a_manifest_needs_a_real_commit_and_at_least_one_input() -> None:
    """`"development"` was seven characters of placeholder that `V2-P0B-009` deleted; the
    length floor is `RunManifest.code_commit`'s own, so a value that short cannot be an
    accidental empty string."""
    with pytest.raises(ValidationError):
        _manifest(code_commit="dev")
    with pytest.raises(ValidationError):
        _manifest(inputs=())
    with pytest.raises(ValidationError):
        _manifest(subject_count=0)


def test_a_manifest_refuses_to_name_one_partition_twice() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        _manifest(inputs=(_input_ref(), _input_ref(visible_row_count=1)))


def test_an_input_reference_keeps_both_hashes_because_neither_replaces_the_other() -> None:
    """`batch_digest` answers "is this still what that provider sent at that point in time" and
    `partition_content_hash` answers "is this the same write". `panel/catalog.py` argues the
    distinction; a manifest that carried one of them would inherit the confusion."""
    reference = _input_ref()

    assert reference.batch_digest != reference.partition_content_hash
    assert reference.visible_row_count == 39
    assert reference.withheld_row_count == 40


# --- the observation -----------------------------------------------------------------------------


def test_exactly_the_computed_code_carries_a_value() -> None:
    """The one invariant whose violation would be a silent lie downstream: `value=None,
    coverage="computed"` reads as a missing number and `value=0.0,
    coverage="not_in_universe"` reads as a real zero in a cross section the security was never
    in."""
    with pytest.raises(FactorError, match="carries a value"):
        _observation(value=None, coverage="computed")
    with pytest.raises(FactorError, match="carries a value"):
        _observation(value=0.0, coverage="not_in_universe")


@pytest.mark.parametrize("coverage", sorted(FACTOR_COVERAGE_CODES - {"computed"}))
def test_every_non_computed_code_is_constructible_with_no_value(coverage: str) -> None:
    """The positive half of the invariant above, over the whole vocabulary: a code that could
    not be constructed would be one the engine could never report."""
    assert _observation(value=None, coverage=coverage).value is None


def test_an_undeclared_coverage_code_is_refused() -> None:
    with pytest.raises(FactorError, match="not a declared coverage code"):
        _observation(value=None, coverage="probably_fine")


def test_the_as_of_is_normalised_so_one_instant_is_one_key() -> None:
    """A stored observation read back out of DuckDB arrives tagged with the session's timezone
    rather than UTC (`domain/panel_batch.py` measured `2024-06-28T07:00Z` reading back as
    `America/Toronto`). `V2-P3-005` groups by `as_of`, and two labels for one instant would be
    two groups."""
    shanghai = datetime.fromisoformat("2026-01-12T12:00:00+08:00")

    observation = _observation(as_of=shanghai)

    assert observation.as_of == AS_OF
    assert observation.as_of.utcoffset() == AS_OF.utcoffset()
    with pytest.raises(ValueError, match="timezone-aware"):
        _observation(as_of=datetime(2026, 1, 12, 4, 0))


def test_a_window_with_one_end_or_a_backwards_one_is_refused() -> None:
    with pytest.raises(FactorError, match="both present or both absent"):
        _observation(
            value=None,
            coverage="input_missing",
            input_session_first=date(2026, 1, 8),
            input_session_last=None,
        )
    with pytest.raises(FactorError, match="runs backwards"):
        _observation(
            value=None,
            coverage="input_missing",
            input_session_first=date(2026, 1, 9),
            input_session_last=date(2026, 1, 8),
        )


def test_an_observation_is_frozen_and_comparable_by_value() -> None:
    first = _observation()

    assert replace(first) == first
    with pytest.raises(AttributeError):
        first.value = 1.0  # type: ignore[misc]


def _observation(**overrides: Any) -> FactorObservation:
    fields: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": AS_OF,
        "value": 0.5,
        "coverage": "computed",
        "factor_id": "fct_probe",
        "manifest_id": "fmn_probe",
        "input_row_count": 2,
        "input_session_first": date(2026, 1, 8),
        "input_session_last": date(2026, 1, 9),
        **overrides,
    }
    return FactorObservation(**fields)
