"""The factor engine's rules that need no store (`V2-P3-002`).

Three groups, each closing a hole this repository has already fallen into once.

**The two tables must name the same factors.** `panel build`'s `PANEL_BUILD_TARGETS` gained
keys whose branches did not exist, and the command answered exit 0 with an empty partition list
-- a declared capability that reports success and does nothing. `FACTOR_DEFINITIONS` and
`FACTOR_EVALUATORS` are the same shape of pair, and `_refuse_table_drift` is the same shape of
audit, so it is exercised in both directions rather than trusted because it is called at import.

**The evaluator's own arithmetic, including the branch that never fires in production.**
`undefined_value` is one of five declared coverage codes, and a code that no branch can emit is
a table entry with nothing behind it. `_reversal_1d`'s zero-denominator guard is unreachable
through this repository's own writers (`daily_bars_from_panel_rows` refuses a non-positive
close), so it is driven directly here -- which is the only honest way to have both the guard and
the claim that it works.

**The reporting vocabularies must agree with the contract's.** `FACTOR_COVERAGE_ORDER` is a
second copy of `FACTOR_COVERAGE_CODES` that exists for a stable census key order, and two copies
of a closed set drift.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest

from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FactorDefinition,
    FactorField,
    FactorRegistry,
)
from openalpha_cn.panel_factors import (
    FACTOR_COVERAGE_ORDER,
    FACTOR_DEFINITIONS,
    FACTOR_EVALUATORS,
    FACTOR_MANIFEST_DATA_COLUMNS,
    FACTOR_OBSERVATION_DATA_COLUMNS,
    FACTOR_OBSERVATION_PANEL_COLUMNS,
    REVERSAL_1D,
    FactorEngineError,
    FactorWindow,
    _observation_from_row,
    _refuse_table_drift,
    _reversal_1d,
)

AS_OF = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)


def _window(*closes: float) -> FactorWindow:
    sessions = tuple(date(2026, 1, 5 + index) for index in range(len(closes)))
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=sessions,
        values=MappingProxyType({("daily", "close"): closes}),
    )


# --- the two tables ------------------------------------------------------------------------------


def test_the_shipped_registry_and_evaluator_table_name_exactly_the_same_factors() -> None:
    assert set(FACTOR_DEFINITIONS.qualified_keys) == set(FACTOR_EVALUATORS)
    assert FACTOR_DEFINITIONS.qualified_keys == ("reversal_1d/v1",)


def test_a_definition_with_no_evaluator_is_refused_rather_than_answered_emptily() -> None:
    """The `panel build` failure, in its factor-layer form: a declared factor a caller can ask
    for and nothing can compute would otherwise produce a full panel of observations all saying
    nothing was computable, which is an empty success with a coverage column on it."""
    orphan = FactorDefinition(
        key="declared_only",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        summary="declared with nothing behind it",
    )

    with pytest.raises(FactorEngineError, match="declared with no evaluator"):
        _refuse_table_drift(FactorRegistry((orphan,)), FACTOR_EVALUATORS)


def test_an_evaluator_with_no_definition_is_refused_too() -> None:
    """The other direction, which fails differently: a formula with no declared identity,
    lookback or required fields is one nothing can hash, gate or read the sign of."""
    with pytest.raises(FactorEngineError, match="implemented with no definition"):
        _refuse_table_drift(
            FACTOR_DEFINITIONS,
            MappingProxyType({**FACTOR_EVALUATORS, "ghost/v1": _reversal_1d}),
        )


def test_the_drift_audit_passes_only_on_agreement() -> None:
    """The sentinel: if `_refuse_table_drift` raised unconditionally, both tests above would
    pass while proving nothing about agreement."""
    assert _refuse_table_drift(FACTOR_DEFINITIONS, FACTOR_EVALUATORS) is None


# --- the shipped definition ----------------------------------------------------------------------


def test_the_verification_factor_declares_the_six_properties_the_engine_reads() -> None:
    """It is the engine's own probe rather than a `V2-P3-012` deliverable, and each of the six
    is chosen for what it makes reachable: one required column so `input_missing` has exactly
    one way to happen, a lookback of 2 so `insufficient_history` is reachable at all (a
    1-session window is satisfied by any security with one row), and a denominator so
    `undefined_value` is a branch rather than a declaration."""
    assert REVERSAL_1D.qualified_key == "reversal_1d/v1"
    assert REVERSAL_1D.family == "momentum_reversal"
    assert REVERSAL_1D.direction == "lower_is_better"
    assert REVERSAL_1D.required_fields == (FactorField(dataset="daily", column="close"),)
    assert REVERSAL_1D.lookback_sessions == 2
    assert REVERSAL_1D.datasets == ("daily",)
    assert "verification factor" in REVERSAL_1D.summary


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ((10.0, 11.0), 0.1),
        ((10.0, 9.0), -0.1),
        ((10.0, 10.0), 0.0),
        ((11.5, 12.0), 12.0 / 11.5 - 1.0),
    ],
)
def test_the_verification_factor_computes_the_session_return(
    closes: tuple[float, float], expected: float
) -> None:
    """The magnitude, not just the sign: a formula asserted only for sign would pass for
    `close[-1] - close[-2]`, which is a different factor with the same sign everywhere."""
    result = _reversal_1d(_window(*closes))

    assert result is not None
    assert result == pytest.approx(expected)


def test_the_verification_factor_reads_only_the_last_two_sessions_of_a_longer_window() -> None:
    """The engine hands an evaluator exactly `lookback_sessions` sessions, but an evaluator
    that indexed from the front would still pass every two-element case above."""
    assert _reversal_1d(_window(1.0, 2.0, 10.0, 11.0)) == pytest.approx(0.1)


def test_a_zero_prior_close_is_undefined_rather_than_a_crash() -> None:
    """The branch behind `undefined_value`. Unreachable through this repository's own writers --
    `DAILY_PRICE_COLUMNS` records no null and no non-positive close across 58,055 bars spanning
    2001 to 2026, and `daily_bars_from_panel_rows` refuses one -- so it is driven directly. A
    guard whose only evidence was a docstring would be the declaration this test exists to
    replace."""
    assert _reversal_1d(_window(0.0, 11.0)) is None


def test_an_evaluator_reaching_for_an_undeclared_column_is_refused_by_the_window() -> None:
    """A `KeyError` here would read as "the engine is broken"; what it means is that the
    definition's `required_fields` does not cover what the formula reads, which is the field
    the whole coverage check is built on."""
    window = FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(date(2026, 1, 8), date(2026, 1, 9)),
        values=MappingProxyType({("daily", "vol"): (1.0, 2.0)}),
    )

    with pytest.raises(FactorEngineError, match=r"did not declare daily\.close"):
        _reversal_1d(window)


# --- the vocabularies ----------------------------------------------------------------------------


def test_the_census_order_is_every_declared_coverage_code_and_no_other() -> None:
    """Two copies of a closed set drift; `panel_fixtures.STATEMENT_DATASETS` carries the same
    kind of pin against the domain's own tuple for the same reason."""
    assert set(FACTOR_COVERAGE_ORDER) == FACTOR_COVERAGE_CODES
    assert len(FACTOR_COVERAGE_ORDER) == len(FACTOR_COVERAGE_CODES)
    assert FACTOR_COVERAGE_ORDER[0] == "computed"


def test_the_stored_observation_columns_are_the_six_facts_the_acceptance_names() -> None:
    """`V2-P3-002` asks each observation to record subject, as-of, value, coverage marker, input
    reference and build manifest. The subject and the as-of are the batch's own columns; the
    other four are stored ones, and this pins that none of them was dropped."""
    assert FACTOR_OBSERVATION_PANEL_COLUMNS[0] == "subject"
    assert set(FACTOR_OBSERVATION_DATA_COLUMNS) >= {
        "value",
        "coverage",
        "manifest_id",
        "input_row_count",
        "input_session_first",
        "input_session_last",
        "factor_id",
    }


def test_a_stored_row_of_the_wrong_width_is_refused_rather_than_unpacked_positionally() -> None:
    """A partition written by a build with a different column list would otherwise decode into
    plausible values in the wrong fields -- a `manifest_id` read as a `factor_id`, a session
    date read as a coverage code. The width check is what makes that a refusal."""
    with pytest.raises(FactorEngineError, match="expected 11"):
        _observation_from_row(("too", "few"))


def test_a_stored_coverage_code_this_build_does_not_declare_is_refused() -> None:
    """The forward-compatibility direction, and the same judgement
    `PANEL_BATCH_SCHEMA_VERSIONS_READABLE` makes: a value this build cannot interpret is
    refused rather than guessed at, because a verdict computed from a field whose meaning is
    unknown is worse than no verdict."""
    row = (
        AS_OF,
        "000001.SZ",
        "fct_probe",
        "reversal_1d",
        1,
        None,
        "probably_fine",
        "fmn_probe",
        0,
        None,
        None,
    )

    with pytest.raises(FactorEngineError, match="which this build does not declare"):
        _observation_from_row(row)


def test_a_stored_row_whose_event_clock_is_not_an_instant_is_refused() -> None:
    row = (
        "2026-01-12",
        "000001.SZ",
        "fct_probe",
        "reversal_1d",
        1,
        None,
        "not_in_universe",
        "fmn_probe",
        0,
        None,
        None,
    )

    with pytest.raises(FactorEngineError, match="not a datetime"):
        _observation_from_row(row)


def test_a_well_formed_stored_row_decodes_into_every_field_it_came_from() -> None:
    """The positive half, without which the three refusals above would be satisfied by a
    decoder that rejected everything."""
    row = (
        AS_OF,
        "000001.SZ",
        "fct_probe",
        "reversal_1d",
        1,
        0.25,
        "computed",
        "fmn_probe",
        2,
        "2026-01-08",
        "2026-01-09",
    )

    observation = _observation_from_row(row)

    assert observation.subject == "000001.SZ"
    assert observation.as_of == AS_OF
    assert observation.value == pytest.approx(0.25)
    assert observation.coverage == "computed"
    assert observation.factor_id == "fct_probe"
    assert observation.manifest_id == "fmn_probe"
    assert observation.input_row_count == 2
    assert observation.input_session_first == date(2026, 1, 8)
    assert observation.input_session_last == date(2026, 1, 9)


def test_the_manifest_columns_carry_both_partition_hashes_and_both_row_counts() -> None:
    """The input reference's own acceptance: which partitions, proved two ways, and how much of
    each one was visible against how much was withheld."""
    assert {
        "input_dataset",
        "input_year",
        "input_batch_digest",
        "input_partition_hash",
        "input_visible_rows",
        "input_withheld_rows",
    } <= set(FACTOR_MANIFEST_DATA_COLUMNS)
    assert "code_commit" in FACTOR_MANIFEST_DATA_COLUMNS
