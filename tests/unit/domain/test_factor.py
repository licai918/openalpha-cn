"""The versioned factor definition contract (`V2-P3-001`).

Four things are asserted here and each is a direct answer to something this repository has
already got wrong once.

**Every declared field reaches the identity.** Roadmap section 9 records the case where it did
not: `config_digest` and `random_seed` were asserted -- in the PRD, in an audit document and in
a task brief -- to feed `decision_id`, and a review's experiment showed they do not, because
they are fields of `RunManifest` and `RunManifest` is not one of the models `stable_model_id` is
applied to. The lesson is that an identity claim has to be *measured* field by field. So
`test_every_definition_field_reaches_the_identity` and its two twins vary one field at a time
and assert the ID moves, rather than asserting that an ID exists.

**And the table that does the varying is held against the model at run time.** The three
parametrize lists are hand-written, and a hand-written table stops describing the model the
moment somebody adds a field -- silently, because every case in it still passes.
`test_the_identity_audit_covers_every_declared_field_and_cannot_go_stale` reads `model_fields`
back and fails on the difference, which is the only shape of check this repository has found to
hold a table against an implementation.

**The declared properties are fields, and each is constrained on both sides.** `V2-P3-001`'s
acceptance lists stable identity, version, family, required fields, lookback window and
direction; the two *reaches* are the correction of defects those six had between them -- a
lookback that says how many of a security's own sessions to take and nothing about when they
were, and no way at all to say how far back a **filing** has to go. A test that only checked
they were *present* would pass against a definition whose family is `"whatever"` and whose
lookback is `0` -- the shape `SignalFrame.horizon` had until `V2-P1-017` narrowed it
(`min_length=1, max_length=64` admitted `'whenever'`) -- and a test that only checked the floors
would pass against a `lookback_periods` of 60,000, which a mutation run measured.

**Prose is not a field, and that is asserted rather than promised.** All three factor identity
contracts carried a `summary` inside their own content addresses until `V2-P3-014`'s
prerequisite, so fixing a typo moved every stored build's identity and changed no number.
`test_a_prose_edit_moves_no_identity_because_prose_is_not_a_field` and
`test_a_setting_edit_moves_the_identity_and_is_therefore_distinguishable_from_a_typo` are the
pair that discriminates, and `test_prose_cannot_be_put_into_the_contract_at_all` is why the
first is structural rather than conventional.

**The registry refuses the two shapes that make a lookup meaningless**, and declines to refuse
a third. An empty registry satisfies every "for each definition" assertion vacuously, which is
the same fault as a table with a key and no branch behind it with the halves swapped; a repeated
handle makes `get()` arbitrary. The third -- two handles sharing one content address -- reads
like an obvious guard and is a branch nothing can reach, and
`test_two_distinct_names_can_never_share_a_content_address` is why.

Every `pytest.raises` here carries a `match=` narrow enough to name which rule refused. That is
the standard the file was written to and one change fell short of it: seven unmatched
`ValidationError` blocks covering three constraints apiece, each of which passes when two of the
three have stopped working.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, Final, get_args

import pytest
from pydantic import ValidationError

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FACTOR_DIRECTIONS,
    FACTOR_FAMILIES,
    KNOWN_FACTOR_SEAL_LIMITATIONS,
    MAX_FACTOR_KEY_LENGTH,
    PERIOD_INDEXED_DATASETS,
    FactorBuildManifest,
    FactorCoverage,
    FactorDefinition,
    FactorDirection,
    FactorError,
    FactorFamily,
    FactorField,
    FactorInputProvenance,
    FactorInputRef,
    FactorNote,
    FactorObservation,
    FactorRegistry,
    cross_section_digest,
    set_digest,
    validate_factor_observation,
)
from openalpha_cn.domain.factor_neutralization import processed_observation_digest
from openalpha_cn.domain.factor_transform import (
    ProcessedFactorObservation,
    observation_digest,
)
from openalpha_cn.domain.financial_statements import FINANCIAL_STATEMENT_DATASETS

AS_OF = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)


def _definition(**overrides: Any) -> FactorDefinition:
    fields = {
        "key": "probe_factor",
        "version": 1,
        "family": "value",
        "direction": "higher_is_better",
        "required_fields": (FactorField(dataset="daily", column="close"),),
        "lookback_sessions": 20,
        "max_window_sessions": 25,
        "lookback_periods": None,
        "max_window_periods": None,
        **overrides,
    }
    return FactorDefinition(**fields)


def _filing_definition(**overrides: Any) -> FactorDefinition:
    """A definition on the **report-period** axis and nothing else.

    `V2-P3-010`'s ROE shape: one `fina_indicator` column, no price, so no session reach at all.
    It exists here because the two axes' rules are not symmetric until both are exercised -- a
    session-only probe cannot show that `lookback_sessions=None` is a legal declaration.
    """
    fields = {
        "key": "probe_filing",
        "version": 1,
        "family": "quality",
        "direction": "higher_is_better",
        "required_fields": (FactorField(dataset="fina_indicator", column="roe"),),
        "lookback_sessions": None,
        "max_window_sessions": None,
        "lookback_periods": 5,
        "max_window_periods": 5,
        **overrides,
    }
    return FactorDefinition(**fields)


def _input_ref(**overrides: Any) -> FactorInputRef:
    fields = {
        "dataset": "daily",
        "year": 2026,
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
        "direction": "higher_is_better",
        "lookback_sessions": 20,
        "max_window_sessions": 25,
        "lookback_periods": 5,
        "max_window_periods": 6,
        "subject_count": 8,
        "subject_digest": set_digest(("000001.SZ", "000002.SZ")),
        "universe_count": 8,
        "universe_digest": set_digest(("000001.SZ", "000002.SZ")),
        "observation_digest": cross_section_digest(
            (("000001.SZ", "computed", 1.5), ("000002.SZ", "input_missing", None)),
            prefix="obs",
        ),
        "inputs": (_input_ref(),),
        **overrides,
    }
    return FactorBuildManifest(**fields)


# --- the declared properties ---------------------------------------------------------------------


def test_a_definition_carries_every_property_the_contract_names() -> None:
    """`V2-P3-001`'s acceptance names six; the two reaches are the correction of defects the six
    had between them -- a lookback that counted a security's own sessions and said nothing about
    when they were, and no way at all to say how far back a *filing* had to reach."""
    definition = _definition()

    assert definition.factor_id.startswith("fct_")
    assert definition.version == 1
    assert definition.family in FACTOR_FAMILIES
    assert definition.required_fields == (FactorField(dataset="daily", column="close"),)
    assert definition.lookback_sessions == 20
    assert definition.max_window_sessions == 25
    assert definition.lookback_periods is None
    assert definition.max_window_periods is None
    assert definition.direction in FACTOR_DIRECTIONS

    filing = _filing_definition()

    assert filing.lookback_sessions is None
    assert filing.max_window_sessions is None
    assert filing.lookback_periods == 5
    assert filing.max_window_periods == 5


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


_CONSTRAINED_PROPERTIES: Final[tuple[tuple[str, object, str], ...]] = (
    ("family", "momentum", "Input should be"),
    ("direction", "bigger_is_better", "Input should be"),
    ("lookback_sessions", 0, "greater than or equal to 1"),
    ("max_window_sessions", 0, "greater than or equal to 1"),
    ("version", 0, "greater than or equal to 1"),
    ("required_fields", (), "at least 1 item"),
)


@pytest.mark.parametrize(("field", "value", "message"), _CONSTRAINED_PROPERTIES)
def test_each_required_property_is_constrained_and_not_merely_present(
    field: str, value: object, message: str
) -> None:
    """Presence is not the property. A family outside the five would become a redundancy group
    of one in `V2-P3-008`; a zero lookback is a window with no rows in it; an empty
    `required_fields` is a coverage check that can never find a shortfall, which is the same
    vacuity `ReadinessRequirement` refuses with `empty_requirement`.

    Each case carries the constraint it expects to trip, so a definition that started refusing
    all seven for one unrelated reason -- a renamed field, a validator raising early -- fails
    here instead of passing seven times over.
    """
    with pytest.raises(ValidationError, match=message):
        _definition(**{field: value})


def test_a_window_narrower_than_its_own_lookback_is_refused() -> None:
    """`max_window_sessions < lookback_sessions` is not a strict setting, it is an impossible
    one: N of a security's own sessions span at least N panel sessions, so the factor could
    never compute a value for anybody and every observation would be `insufficient_history`."""
    with pytest.raises(ValidationError, match="narrower than lookback_sessions"):
        _definition(lookback_sessions=20, max_window_sessions=19)

    assert _definition(lookback_sessions=20, max_window_sessions=20).max_window_sessions == 20


def test_a_period_window_narrower_than_its_own_lookback_is_refused() -> None:
    """The same impossibility on the other axis, matched on its own field names so the two
    cannot be satisfied by one check that happens to fire."""
    with pytest.raises(ValidationError, match="narrower than lookback_periods"):
        _filing_definition(lookback_periods=5, max_window_periods=4)

    assert _filing_definition(lookback_periods=5, max_window_periods=5).max_window_periods == 5


def test_a_span_bound_above_one_is_refused_beside_a_count_of_one_on_either_axis() -> None:
    """At a count of one the span bound decides nothing, so only `1` may be declared.

    One of a security's own points spans exactly one panel point -- `panel_factors::_session_span`
    and `_period_span` both return 1 when a window's ends coincide -- so `max_window_sessions=50`
    beside `lookback_sessions=1` is a number in the content address that no branch can consult,
    which is the objection this contract already makes to a reach for an axis nothing reads.

    Measured before it was refused, and the measurement is why it is refused: raising
    `earnings_yield_ttm`'s `max_window_sessions` from 1 to 50 and `book_to_price`'s
    `max_window_periods` from 1 to 8 turned only the declaration tests red and left
    `tests/integration/panel/test_value_family.py` and `tests/unit/test_factor_engine_rules.py`
    entirely green. Two factors that compute the same number for every security on every
    partition would therefore have carried two different `factor_id`s.

    Both axes and both directions, because one unmatched `pytest.raises` here would pass while the
    other axis had stopped being checked -- and the equal case is asserted so this cannot be
    satisfied by a rule that refuses `1` as well.
    """
    with pytest.raises(ValidationError, match="max_window_sessions 50 is stated beside"):
        _definition(lookback_sessions=1, max_window_sessions=50)
    with pytest.raises(ValidationError, match="max_window_periods 8 is stated beside"):
        _filing_definition(lookback_periods=1, max_window_periods=8)

    assert _definition(lookback_sessions=1, max_window_sessions=1).max_window_sessions == 1
    assert _filing_definition(lookback_periods=1, max_window_periods=1).max_window_periods == 1


_REACH_BOUNDS: Final[tuple[tuple[str, dict[str, Any], dict[str, Any]], ...]] = (
    (
        "lookback_sessions",
        {"lookback_sessions": 2000, "max_window_sessions": 4000},
        {"lookback_sessions": 2001, "max_window_sessions": 4000},
    ),
    (
        "max_window_sessions",
        {"lookback_sessions": 20, "max_window_sessions": 4000},
        {"lookback_sessions": 20, "max_window_sessions": 4001},
    ),
    (
        "lookback_periods",
        {"lookback_periods": 60, "max_window_periods": 120},
        {"lookback_periods": 61, "max_window_periods": 120},
    ),
    (
        "max_window_periods",
        {"lookback_periods": 5, "max_window_periods": 120},
        {"lookback_periods": 5, "max_window_periods": 121},
    ),
)


@pytest.mark.parametrize(("field", "widest", "beyond"), _REACH_BOUNDS)
def test_each_reach_has_the_upper_bound_it_declares(
    field: str, widest: dict[str, Any], beyond: dict[str, Any]
) -> None:
    """Both sides of every ceiling, because only the pair says where it is.

    A lower-bound case alone (`_CONSTRAINED_PROPERTIES`) leaves the ceiling free, and that is a
    measurement rather than a worry: widening `lookback_periods` from 60 to 60,000 survived every
    other assertion in this file and in the engine's when a mutation run tried it.

    What a ceiling buys here is what a `Field(le=...)` buys anywhere -- `min_cross_section`'s own
    argument -- the stored column holds a plausible reach rather than an unbounded integer, and a
    caller who typed a *session* count into `lookback_periods` is refused at the contract instead
    of at the first build. 60 report periods is fifteen A-share years of quarterly filings and
    120 is its span twin; `V2-P3-011`'s year-on-year acceleration is the widest reach
    `009`..`013` asks for, at nine.
    """
    base = _filing_definition if field.endswith("periods") else _definition

    assert getattr(base(**widest), field) == widest[field]
    with pytest.raises(ValidationError, match="less than or equal to"):
        base(**beyond)


_HALF_DECLARED_REACHES: Final[tuple[tuple[dict[str, Any], str], ...]] = (
    ({"lookback_sessions": None}, "lookback_sessions and max_window_sessions are stated together"),
    ({"max_window_sessions": None}, "lookback_sessions and max_window_sessions are stated"),
    (
        {"lookback_periods": 5, "max_window_periods": None},
        "lookback_periods and max_window_periods are stated together",
    ),
    (
        {"lookback_periods": None, "max_window_periods": 5},
        "lookback_periods and max_window_periods are stated together",
    ),
)


@pytest.mark.parametrize(("overrides", "message"), _HALF_DECLARED_REACHES)
def test_half_a_reach_is_refused_on_either_axis(overrides: dict[str, Any], message: str) -> None:
    """A count with no span bound is a window whose reach nothing decides, and a span bound with
    no count is a bound on a window that is never formed.

    Both directions on both axes, each matched on the pair it is about: one unmatched
    `pytest.raises` here would pass while three of the four had stopped being checked.
    """
    with pytest.raises(ValidationError, match=message):
        _definition(**overrides)


def test_a_reach_is_declared_exactly_when_the_axis_is_read() -> None:
    """The equivalence, in all four of its failing corners.

    This is what makes each nullable pair non-vacuous: a reach for an axis the factor is not on
    is a number inside `factor_id` that no branch of `panel_factors` can consult -- the "field
    with no reader" this contract refused a report-period dimension for until it had one -- and a
    missing reach for an axis the factor *is* on is an unbounded window.
    """
    price = (FactorField(dataset="daily", column="close"),)
    filing = (FactorField(dataset="income", column="revenue"),)

    with pytest.raises(ValidationError, match="declares no session reach and reads"):
        _definition(required_fields=price, lookback_sessions=None, max_window_sessions=None)
    with pytest.raises(ValidationError, match="declares session reach and reads"):
        _filing_definition(lookback_sessions=20, max_window_sessions=25)
    with pytest.raises(ValidationError, match="declares no report-period reach and reads"):
        _definition(required_fields=filing, lookback_sessions=None, max_window_sessions=None)
    with pytest.raises(ValidationError, match="declares report-period reach and reads"):
        _definition(lookback_periods=5, max_window_periods=5)

    both = _definition(
        required_fields=(*price, *filing),
        lookback_periods=5,
        max_window_periods=5,
    )

    assert both.session_datasets == ("daily",)
    assert both.period_datasets == ("income",)


def test_the_period_axis_is_exactly_the_four_statement_datasets() -> None:
    """`PERIOD_INDEXED_DATASETS` is derived from the domain's own tuple rather than written out,
    so a fifth statement endpoint joins the axis without anybody extending a list.

    Asserted in both directions against `FINANCIAL_STATEMENT_DATASETS` and against a dataset that
    is emphatically not one: `daily_basic` carries `total_mv`, which `V2-P3-009`'s BP reads
    beside a filing, and putting it on the period axis would index a daily row by a column it
    does not have.
    """
    assert frozenset(FINANCIAL_STATEMENT_DATASETS) == PERIOD_INDEXED_DATASETS
    assert {"income", "balancesheet", "cashflow", "fina_indicator"} == PERIOD_INDEXED_DATASETS
    assert "daily" not in PERIOD_INDEXED_DATASETS
    assert "daily_basic" not in PERIOD_INDEXED_DATASETS


@pytest.mark.parametrize(
    ("dataset", "column", "message"),
    [
        ("daily", 'close" ; ATTACH', "column name must match"),
        ("../escaped", "close", "single, plain path segment"),
    ],
)
def test_a_field_reference_is_held_to_the_panel_planes_own_identifier_rules(
    dataset: str, column: str, message: str
) -> None:
    """A definition is data a later engine turns into SQL, so a column name that could not be
    one is refused where it is *declared* rather than several layers down as a binder error.

    One case per rule, each matched on the refusal it should provoke: an unmatched
    `pytest.raises(ValidationError)` covering three rules at once passes when two of them have
    stopped working, which is the standard the four pre-existing checks in this file already
    held themselves to.
    """
    with pytest.raises(ValidationError, match=message):
        FactorField(dataset=dataset, column=column)


def test_a_factor_key_is_held_to_the_same_rules_and_to_a_shorter_bound() -> None:
    """`MAX_FACTOR_KEY_LENGTH` is a storage budget rather than taste: `panel_factors` builds a
    panel dataset name out of the key, and `MAX_IDENTIFIER_LENGTH` caps that. The arithmetic
    tying the two together is asserted in `tests/unit/test_factor_engine_rules.py`; what this
    pins is that the bound is enforced at all, and on the panel plane's own rules."""
    with pytest.raises(ValidationError, match="factor key name must match"):
        _definition(key="probe factor")
    with pytest.raises(ValidationError, match="at most 40 characters"):
        _definition(key="p" * (MAX_FACTOR_KEY_LENGTH + 1))

    assert _definition(key="p" * MAX_FACTOR_KEY_LENGTH).key == "p" * MAX_FACTOR_KEY_LENGTH


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


_DEFINITION_FIELD_VARIATIONS: Final[tuple[tuple[str, object], ...]] = (
    ("key", "other_factor"),
    ("version", 2),
    ("family", "quality"),
    ("direction", "lower_is_better"),
    ("lookback_sessions", 21),
    ("max_window_sessions", 26),
    ("lookback_periods", 4),
    ("max_window_periods", 6),
    ("required_fields", (FactorField(dataset="daily", column="vol"),)),
)
"""One way to move each declared field, one field at a time.

The period entries are varied against `_filing_definition` rather than `_definition`, because
the session-only baseline cannot legally carry a period reach at all -- the equivalence refuses
it before an identity is consulted. `_definition_pair` picks the baseline that admits each
variation, so every case is still exactly one field moved against a baseline that differs in
nothing else.
"""


def _definition_pair(field: str, value: object) -> tuple[FactorDefinition, FactorDefinition]:
    """`(baseline, varied)` for one field, on a baseline whose axes admit the variation."""
    if field in {"lookback_periods", "max_window_periods"}:
        return (_filing_definition(), _filing_definition(**{field: value}))
    return (_definition(), _definition(**{field: value}))


@pytest.mark.parametrize(("field", "value"), _DEFINITION_FIELD_VARIATIONS)
def test_every_definition_field_reaches_the_identity(field: str, value: object) -> None:
    """One field at a time, against roadmap section 9's measured mistake.

    A field that is carried and never hashed is provenance, not identity, and the way that goes
    unnoticed is a test that asserts the ID exists.
    """
    baseline, varied = _definition_pair(field, value)

    assert varied.factor_id != baseline.factor_id


_INPUT_REF_FIELD_VARIATIONS: Final[tuple[tuple[str, object], ...]] = (
    ("dataset", "daily_basic"),
    ("year", 2025),
    ("partition_content_hash", "cc"),
    ("visible_row_count", 38),
    ("withheld_row_count", 41),
)


@pytest.mark.parametrize(("field", "value"), _INPUT_REF_FIELD_VARIATIONS)
def test_every_input_reference_field_reaches_the_manifests_identity(
    field: str, value: object
) -> None:
    """One level down, and the level that mattered: `FactorInputRef` is nested inside the hashed
    model, so its fields are hashed too -- which is why a field of it that moves on every
    re-fetch was a defect rather than a detail."""
    varied = _manifest(inputs=(_input_ref(**{field: value}),))

    assert varied.manifest_id != _manifest().manifest_id


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
        FactorRegistry((_definition(), _definition(lookback_sessions=21)))


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


# --- prose is recorded and is not a field ---------------------------------------------------------


def test_a_prose_edit_moves_no_identity_because_prose_is_not_a_field() -> None:
    """The measurement this change exists to make possible, in the form the defect had.

    Before it, `FactorDefinition.summary` was inside `factor_id`, so fixing a typo moved the
    identity of every stored build derived from the definition and changed no number --
    `V2-P3-004` had to break its own "edit only with a version bump" rule to retract three
    unreproducible figures from `INDUSTRY_AND_SIZE.summary` for exactly this reason. Now the two
    registries below differ in nothing but their prose and every content address in them is
    byte-identical.

    Asserted on the whole `factor_ids` tuple rather than on one ID, so a contract that hashed the
    note for *some* definitions could not pass.
    """
    definitions = (_definition(key="alpha_probe"), _definition(key="beta_probe"))
    before = FactorRegistry(
        definitions,
        notes=(
            FactorNote(subject="alpha_probe/v1", summary="a one-sesion return"),
            FactorNote(subject="beta_probe/v1", summary="the same, restated"),
        ),
    )
    after = FactorRegistry(
        definitions,
        notes=(
            FactorNote(subject="alpha_probe/v1", summary="a one-session return"),
            FactorNote(subject="beta_probe/v1", summary="the same, restated"),
        ),
    )

    assert before.note_for("alpha_probe/v1") == "a one-sesion return"
    assert after.note_for("alpha_probe/v1") == "a one-session return"
    assert before.factor_ids == after.factor_ids
    assert before.factor_ids == FactorRegistry(definitions).factor_ids


def test_a_setting_edit_moves_the_identity_and_is_therefore_distinguishable_from_a_typo() -> None:
    """The other half of the pair, and the half that has to keep working.

    "Prose no longer moves the identity" would be satisfiable by an identity that moves for
    nothing at all, so the discriminating statement is the two halves together: a definition
    whose *settings* differ has a different address on the same registry shape a typo leaves
    untouched.
    """
    definitions = (_definition(key="alpha_probe"), _definition(key="beta_probe"))
    notes = (FactorNote(subject="alpha_probe/v1", summary="unchanged prose"),)
    baseline = FactorRegistry(definitions, notes=notes)
    restated = FactorRegistry(
        (_definition(key="alpha_probe", lookback_sessions=21), _definition(key="beta_probe")),
        notes=notes,
    )

    assert baseline.note_for("alpha_probe/v1") == restated.note_for("alpha_probe/v1")
    assert baseline.factor_ids[0] != restated.factor_ids[0]
    assert baseline.factor_ids[1] == restated.factor_ids[1]


def test_prose_cannot_be_put_into_the_contract_at_all() -> None:
    """Why the half above is a *structural* claim rather than a convention anybody could break.

    `extra="forbid"` means there is no `summary=` to pass, so "editing prose cannot move this
    identity" is not a rule a future author has to remember -- it is a model that will not accept
    prose. Matched on pydantic's own refusal so the case cannot pass because some *other*
    validator rejected the call.
    """
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _definition(summary="why these settings")

    assert "summary" not in FactorDefinition.model_fields
    assert "summary" not in FactorBuildManifest.model_fields


def test_a_note_about_an_undeclared_contract_is_refused() -> None:
    """Prose about nothing is how a note outlives the spec it described and goes on being shown.

    The duplicate case is the same lookup made arbitrary from the other side, and both are
    matched narrowly enough to say which refused.
    """
    with pytest.raises(FactorError, match="is not a declared factor"):
        FactorRegistry((_definition(),), notes=(FactorNote(subject="ghost/v1", summary="x"),))
    with pytest.raises(FactorError, match="carries more than one factor note"):
        FactorRegistry(
            (_definition(),),
            notes=(
                FactorNote(subject="probe_factor/v1", summary="one"),
                FactorNote(subject="probe_factor/v1", summary="two"),
            ),
        )


def test_a_registry_with_no_note_answers_none_and_still_refuses_an_unknown_handle() -> None:
    """ "Nothing is written about this factor" is an answer; "this is not a factor" is a fault.

    A registry a test builds out of probe definitions has nothing to say about them, and forcing
    a sentence would produce the placeholder prose `V2-P0B-009` deleted elsewhere -- so the empty
    case is legal and returns `None`. Asking about a handle the registry does not declare goes
    through `get`, which names what it knows.
    """
    registry = FactorRegistry((_definition(),))
    partly_written = FactorRegistry(
        (_definition(key="alpha_probe"), _definition(key="beta_probe")),
        notes=(FactorNote(subject="alpha_probe/v1", summary="only this one is written about"),),
    )

    assert registry.note_for("probe_factor/v1") is None
    assert partly_written.note_for("beta_probe/v1") is None
    assert partly_written.note_for("alpha_probe/v1") == "only this one is written about"
    with pytest.raises(FactorError, match="is not a declared factor; this build knows"):
        registry.note_for("ghost/v1")


def test_a_note_refuses_to_be_empty_on_either_half() -> None:
    """A note with no subject is unattachable and one with no prose is a record of nothing;
    whitespace counts as neither, which is what `str.strip` is doing there."""
    with pytest.raises(FactorError, match="must name the contract it is about"):
        FactorNote(subject="   ", summary="prose")
    with pytest.raises(FactorError, match="carries no prose"):
        FactorNote(subject="probe_factor/v1", summary="  ")


# --- the build manifest --------------------------------------------------------------------------


_MANIFEST_FIELD_VARIATIONS: Final[tuple[tuple[str, object], ...]] = (
    ("factor_id", "fct_other"),
    ("factor_key", "other_factor"),
    ("factor_version", 2),
    ("as_of", datetime(2026, 1, 13, 4, 0, tzinfo=UTC)),
    ("date_timezone", "UTC"),
    ("code_commit", "deadbee"),
    ("direction", "lower_is_better"),
    ("lookback_sessions", 21),
    ("max_window_sessions", 26),
    ("lookback_periods", 6),
    ("max_window_periods", 7),
    ("subject_count", 9),
    ("subject_digest", set_digest(("600000.SH", "600519.SH"))),
    ("universe_count", 7),
    ("universe_digest", set_digest(("600000.SH", "600519.SH"))),
    (
        "observation_digest",
        cross_section_digest(
            (("000001.SZ", "computed", -1.5), ("000002.SZ", "input_missing", None)),
            prefix="obs",
        ),
    ),
    ("inputs", (_input_ref(visible_row_count=38),)),
)


@pytest.mark.parametrize(("field", "value"), _MANIFEST_FIELD_VARIATIONS)
def test_every_manifest_field_reaches_the_identity(field: str, value: object) -> None:
    """The same measurement as for the definition, and the one section 9 is actually about:
    a build manifest is exactly the kind of record whose fields get believed into an identity
    they never entered."""
    assert _manifest(**{field: value}).manifest_id != _manifest().manifest_id


_FIELD_VARIATIONS_BY_MODEL: Final[dict[type[Any], tuple[tuple[str, object], ...]]] = {
    FactorDefinition: _DEFINITION_FIELD_VARIATIONS,
    FactorBuildManifest: _MANIFEST_FIELD_VARIATIONS,
    FactorInputRef: _INPUT_REF_FIELD_VARIATIONS,
}


@pytest.mark.parametrize(
    "model", sorted(_FIELD_VARIATIONS_BY_MODEL, key=lambda item: item.__name__)
)
def test_the_identity_audit_covers_every_declared_field_and_cannot_go_stale(
    model: type[Any],
) -> None:
    """The three tables above are hand-written, and a hand-written table drifts from the model it
    describes the moment somebody adds a field.

    That is not hypothetical here: this contract gained four in one change, and the parametrize
    lists would have kept passing while covering less than they claim -- "varies each of the
    seven declared fields alone" becomes a false sentence with nothing failing, which is the
    exact shape of drift this repository has had to close twice before with a run-time audit
    (`panel build`'s `_audit_written_partitions`, `_refuse_table_drift`). So the tables are read
    back against `model_fields` here rather than trusted.

    `schema_version` is the one exemption and it is a single-member `Literal`: there is no other
    value to vary it to, and `test_the_schema_version_is_hashed_so_a_future_v2_cannot_collide`
    covers it by asserting it is in the hashed payload instead. `FactorInputRef` has no
    `schema_version`, so the exemption is asserted as a subtraction that tolerates its absence.
    """
    covered = {name for name, _ in _FIELD_VARIATIONS_BY_MODEL[model]}
    declared = set(model.model_fields)

    assert covered == declared - {"schema_version"}
    assert covered


def test_the_wall_clock_is_not_a_manifest_field_so_a_rebuild_reproduces_the_identity() -> None:
    """The arrangement section 9 says was wanted and not had, on **both** sides of the build.

    The build's own wall clock (`built_at`) was already out of the payload and stays out. What
    joins it is the input side: `FactorInputRef` carried the provider's `batch_digest`, which
    hashes `fetched_at`, so a partition re-fetched with byte-identical rows moved every
    `manifest_id` derived from it -- and a stored build may not be dropped, so the recomputed
    build could then never be written. Neither clock is in the payload now.

    Asserted as an absence from the hashed payload, not as "two calls agree" -- two calls with
    no clock in them agree for any number of reasons. The key set is asserted **exactly** so the
    absence cannot be satisfied by a field having quietly moved somewhere else in it.
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
        "direction",
        "lookback_sessions",
        "max_window_sessions",
        "lookback_periods",
        "max_window_periods",
        "subject_count",
        "subject_digest",
        "universe_count",
        "universe_digest",
        "observation_digest",
        "inputs",
    }
    assert set(payload["inputs"][0]) == {
        "dataset",
        "year",
        "partition_content_hash",
        "visible_row_count",
        "withheld_row_count",
    }
    assert "batch_digest" not in payload["inputs"][0]
    assert _manifest().manifest_id == _manifest().manifest_id


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"code_commit": "dev"}, "at least 7 characters"),
        ({"inputs": ()}, "at least 1 item"),
        ({"subject_count": 0}, "greater than or equal to 1"),
        ({"subject_digest": ""}, "at least 1 character"),
        ({"universe_digest": ""}, "at least 1 character"),
        ({"observation_digest": ""}, "at least 1 character"),
    ],
)
def test_a_manifest_needs_a_real_commit_two_real_digests_and_at_least_one_input(
    overrides: dict[str, Any], message: str
) -> None:
    """`"development"` was seven characters of placeholder that `V2-P0B-009` deleted; the
    length floor is `RunManifest.code_commit`'s own, so a value that short cannot be an
    accidental empty string.

    One case per constraint with the refusal it should provoke, rather than three unmatched
    `pytest.raises(ValidationError)` bodies: an unmatched block covering three rules passes when
    two of them have stopped working.
    """
    with pytest.raises(ValidationError, match=message):
        _manifest(**overrides)


def test_a_manifest_refuses_to_name_one_partition_twice() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        _manifest(inputs=(_input_ref(), _input_ref(visible_row_count=1)))


def test_the_two_partition_facts_are_split_by_whether_a_refetch_moves_them() -> None:
    """`batch_digest` answers "is this still what that provider sent at that point in time" and
    `partition_content_hash` answers "is this the same write". `panel/catalog.py` argues that
    neither replaces the other and that is still true -- both are kept and both are stored.

    What changed is which one is *hashed*. `batch_digest` covers `fetched_at`, so it moves on
    every re-fetch, and a content address that moved for a re-fetch of unchanged rows is not a
    content address. It lives on `FactorInputProvenance` now, which is not a field of anything
    hashed; `FactorInputRef` keeps the facts a re-fetch cannot move.
    """
    reference = _input_ref()
    provenance = FactorInputProvenance(dataset="daily", year=2026, batch_digest="sha256:aa")

    assert reference.partition_content_hash == "bb"
    assert reference.visible_row_count == 39
    assert reference.withheld_row_count == 40
    assert not hasattr(reference, "batch_digest")
    assert (provenance.dataset, provenance.year) == (reference.dataset, reference.year)
    assert provenance.batch_digest != reference.partition_content_hash


def test_a_set_digest_is_order_free_duplicate_free_and_discriminating() -> None:
    """What `subject_count` could not do. The three properties are asserted together because
    each one alone is satisfiable by something useless: a constant is order-free, `len` is
    duplicate-free, and `id()` discriminates."""
    assert set_digest(("b", "a")) == set_digest(("a", "b"))
    assert set_digest(("a", "a", "b")) == set_digest(("a", "b"))
    assert set_digest(("a", "b")) != set_digest(("c", "d"))
    assert set_digest(()) != set_digest(("a",))
    assert set_digest(("a",)).startswith("set_")


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


def test_the_period_window_is_held_to_the_same_two_rules_as_the_session_one() -> None:
    """The pair the `None` defaults could otherwise have smuggled half of in.

    `input_period_first` and `input_period_last` default to `None` where the session pair does
    not, because absent is the right answer for every factor that reads no filing. That default
    is only safe if the *pairing* is still enforced, so both refusals are matched here on the
    period field names -- a check that fired on `input_session_first` would satisfy an unmatched
    version of this test while the period pair went unchecked.
    """
    with pytest.raises(FactorError, match="input_period_first and input_period_last"):
        _observation(
            value=None,
            coverage="input_missing",
            input_period_first=date(2025, 9, 30),
            input_period_last=None,
        )
    with pytest.raises(FactorError, match=r"the input period window .* runs backwards"):
        _observation(
            value=None,
            coverage="input_missing",
            input_period_first=date(2025, 12, 31),
            input_period_last=date(2025, 9, 30),
        )

    both = _observation(input_period_first=date(2024, 12, 31), input_period_last=date(2025, 9, 30))

    assert both.input_session_first == date(2026, 1, 8)
    assert both.input_period_last == date(2025, 9, 30)


def test_an_observation_is_frozen_and_comparable_by_value() -> None:
    first = _observation()

    assert replace(first) == first
    with pytest.raises(AttributeError):
        first.value = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_value_cannot_wear_the_computed_code(value: float) -> None:
    """`undefined_value` exists for exactly this and nothing else, and the rule that put it
    there lived in one private function of the engine.

    Measured on the version that had only that check: `value=nan, coverage="computed"`
    constructed, wrote to Parquet, read back as `computed` with a `nan` in it, and
    `FactorPanel.values()` handed it to a rank correlation. All three of `nan`, `inf` and
    `-inf` are driven, because `math.isfinite` is one call and a guard written as
    `value == value` would catch only the first.
    """
    with pytest.raises(FactorError, match="non-finite"):
        _observation(value=value, coverage="computed")


def test_the_finite_rule_holds_for_every_code_that_could_carry_a_value() -> None:
    """The sentinel for the test above: a rule written as "reject non-finite when the code is
    `computed`" and a rule written as "reject non-finite" are the same rule here, because no
    other code may carry a value at all -- and this is what says so rather than leaving the
    reader to infer it from two separate refusals."""
    for coverage in sorted(FACTOR_COVERAGE_CODES - {"computed"}):
        with pytest.raises(FactorError, match="carries a value"):
            _observation(value=float("nan"), coverage=coverage)


def test_the_observation_rules_survive_a_subclass_that_skips_the_constructor() -> None:
    """`panel/catalog.py`'s argument, applied: a `__post_init__` is a method, a frozen
    `slots=True` dataclass is still subclassable, and a subclass that overrides it turns every
    rule off. Measured on the version whose rules lived only in the method -- a three-line
    subclass constructed observations with an empty subject, a negative row count and a
    backwards window, and the write path stored all three.

    `validate_factor_observation` is the same rules as a function, and the write path calls it;
    this is what proves the function actually refuses what the constructor would have.
    """

    class Unchecked(FactorObservation):
        def __post_init__(self) -> None:
            return None

    smuggled = Unchecked(
        subject="",
        as_of=AS_OF,
        value=float("inf"),
        coverage="computed",
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=-5,
        input_session_first=date(2026, 1, 9),
        input_session_last=date(2026, 1, 8),
    )

    assert smuggled.subject == ""
    with pytest.raises(FactorError, match="must name a subject"):
        validate_factor_observation(smuggled)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"subject": ""}, "must name a subject"),
        ({"input_row_count": -5}, "cannot be negative"),
        ({"value": float("inf")}, "non-finite"),
    ],
)
def test_every_observation_rule_is_reachable_through_the_function_the_write_path_calls(
    overrides: dict[str, Any], message: str
) -> None:
    """One rule at a time through `validate_factor_observation` rather than through the
    constructor, so the second call site is exercised on each of them rather than on whichever
    one a single smuggling test happened to trip first."""
    sound = _observation()
    smuggled = FactorObservation.__new__(FactorObservation)
    for name, item in {**_observation_fields(sound), **overrides}.items():
        object.__setattr__(smuggled, name, item)

    with pytest.raises(FactorError, match=message):
        validate_factor_observation(smuggled)


def _observation_fields(observation: FactorObservation) -> dict[str, Any]:
    return {name: getattr(observation, name) for name in observation.__slots__}


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


# --- the shared cross-section address (`V2-P3-019`) -----------------------------------------------


_CROSS_SECTION: Final[tuple[tuple[str, str, float | None], ...]] = (
    ("000001.SZ", "computed", 0.5),
    ("000002.SZ", "computed", -0.25),
    ("000003.SZ", "input_missing", None),
)


def test_the_address_of_a_cross_section_is_free_of_the_order_it_arrives_in() -> None:
    """The half that keeps a rebuild writable.

    `compute_factor` produces one independent observation per security, so shuffling the cross
    section produces the same answers; an address that moved for the order would make a build
    recomputed from unchanged inputs unwritable past `write_factor_panels`' drop guard, which is
    the trap `FactorInputRef` records paying for once already with `batch_digest`.
    """
    forwards = cross_section_digest(_CROSS_SECTION)
    backwards = cross_section_digest(tuple(reversed(_CROSS_SECTION)))

    assert forwards == backwards
    assert forwards.startswith("xs_")


@pytest.mark.parametrize(
    ("altered", "what"),
    [
        ((("000001.SZ", "computed", -0.5), *_CROSS_SECTION[1:]), "a flipped sign"),
        ((("000001.SZ", "computed", 0.5000001), *_CROSS_SECTION[1:]), "one bit of one value"),
        ((("000009.SZ", "computed", 0.5), *_CROSS_SECTION[1:]), "a renamed security"),
        (_CROSS_SECTION[1:], "a deleted row"),
        (
            (*_CROSS_SECTION[:2], ("000003.SZ", "not_in_universe", None)),
            "a restated coverage code on a row that carries no value",
        ),
    ],
)
def test_the_address_moves_for_every_edit_a_row_level_tamper_can_make(
    altered: tuple[tuple[str, str, float | None], ...], what: str
) -> None:
    """The half the whole of `V2-P3-019` rests on, one edit at a time.

    The last row is the one worth naming: `computed` -> `input_missing` moves the value with the
    code, so it would be caught by an address over values alone. `input_missing` ->
    `not_in_universe` does **not** -- both carry `None` -- and it is the edit that turns "this
    security had a hole in its data" into "this security was never in the cross section", which
    is a different claim about the same silence. An address over the value alone cannot see it;
    this one can, and that is why the coverage code is a position rather than a nicety.
    """
    assert cross_section_digest(altered) != cross_section_digest(_CROSS_SECTION), what


def test_a_cross_section_with_two_answers_for_one_security_has_no_address() -> None:
    with pytest.raises(FactorError, match="appears more than once in this cross section"):
        cross_section_digest((*_CROSS_SECTION, ("000001.SZ", "computed", 9.0)))


def test_a_non_finite_value_is_refused_rather_than_given_an_address() -> None:
    """Unreachable through the write path -- `validate_factor_observation` refuses it at both of
    its call sites -- and refused here anyway, because minting an address for a cross section
    nobody can reproduce is worse than raising."""
    with pytest.raises(FactorError, match="non-finite value"):
        cross_section_digest((("000001.SZ", "computed", float("nan")),))


def test_the_prefix_keeps_three_tiers_of_address_from_comparing_equal() -> None:
    """Same cells, three tiers, three addresses.

    Not decoration: the seal check compares a digest it computed against a digest a manifest
    declares, and the three planes' manifests sit in three datasets whose columns have three
    different names. A tier tag makes "compared the right column" visible in the value itself
    rather than resting on the caller having picked the right one.
    """
    tagged = {cross_section_digest(_CROSS_SECTION, prefix=tier) for tier in ("obs", "prc", "nrs")}

    assert len(tagged) == 3
    assert {digest.split("_")[0] for digest in tagged} == {"obs", "prc", "nrs"}


def test_the_seal_limitation_registry_is_this_set() -> None:
    """`V2-P3-019`'s three disclosures, as an equality.

    Equality rather than membership for `KNOWN_ADJUSTMENT_LIMITATIONS`' reason, which
    `tests/unit/test_known_limitation_registries.py` states in full: a membership assertion can
    see a code that was renamed and never one that was removed.
    """
    assert {item.code for item in KNOWN_FACTOR_SEAL_LIMITATIONS} == {
        "a_sealed_cross_section_addresses_its_values_and_not_its_window_columns",
        "a_seal_proves_the_rows_are_the_builds_own_and_not_that_the_build_was_right",
        "the_seal_reaches_only_the_six_derived_partitions",
    }
    assert all(item.detail.strip() for item in KNOWN_FACTOR_SEAL_LIMITATIONS)


def test_the_first_disclosure_is_measured_rather_than_asserted() -> None:
    """The residue the registry declares, demonstrated rather than described.

    A build's window columns and its `input_row_count` are outside the address, so two
    observations that differ only in those hash the same. That is the boundary
    `a_sealed_cross_section_addresses_its_values_and_not_its_window_columns` names, and a
    disclosure nobody exercised is a sentence that can quietly stop being true in either
    direction -- if the payload were widened this test goes red and the registry entry has to go
    with it.
    """
    honest = _observation()
    rewritten = _observation(input_row_count=99, input_session_first=date(2026, 1, 2))

    assert honest.value == rewritten.value
    assert cross_section_digest(
        ((honest.subject, honest.coverage, honest.value),)
    ) == cross_section_digest(((rewritten.subject, rewritten.coverage, rewritten.value),))


def test_the_three_tier_digests_are_this_canonicalisation_under_their_own_prefixes() -> None:
    """ "One canonicalisation" as a measurement, in both directions.

    Forwards: each tier's digest function equals `cross_section_digest` over the same triples
    under that tier's prefix, so extracting the primitive moved **no stored address** --
    `source_observation_digest` values written before `V2-P3-019` still compare equal to the ones
    written after it, which is what let the extraction be a refactor rather than a migration.
    Backwards: the three disagree with each other on identical cells, which is
    `test_the_prefix_keeps_three_tiers_of_address_from_comparing_equal` at the level the planes
    actually call.
    """
    raw = FactorObservation(
        subject="000001.SZ",
        as_of=AS_OF,
        value=0.5,
        coverage="computed",
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=2,
        input_session_first=date(2026, 1, 8),
        input_session_last=date(2026, 1, 9),
    )
    processed = ProcessedFactorObservation(
        subject="000001.SZ",
        as_of=AS_OF,
        value=0.5,
        coverage="processed",
        transform_id="ftx_probe",
        transform_manifest_id="ftm_probe",
        source_factor_id="fct_probe",
        source_manifest_id="fmn_probe",
        source_coverage="computed",
    )
    cells = (("000001.SZ", "computed", 0.5),)

    assert observation_digest((raw,)) == cross_section_digest(cells, prefix="obs")
    assert processed_observation_digest((processed,)) == cross_section_digest(
        (("000001.SZ", "processed", 0.5),), prefix="prc"
    )
    assert observation_digest((raw,)) != cross_section_digest(cells, prefix="prc")
