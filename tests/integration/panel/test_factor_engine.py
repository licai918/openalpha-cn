"""The factor engine against a real panel (`V2-P3-002`).

The acceptance is "every observation records subject / as-of / value / coverage marker / input
reference / build manifest", and each of those is asserted here against a partition on disk
rather than against an in-memory object -- because the round trip is where a column that was
never written, or one that decodes into the wrong field, becomes visible.

## The frame, and why the values in it are exact

The generated panel with `daily.close_moves_between_sessions`, whose closes are
`10.0 + securities.index(code) + 0.5 * sessions.index(day)`. That is an arithmetic the test can
reproduce, so every `computed` value is asserted as a **number** and not as "not None". This
repository has the counter-example on file: a proof that only checks existence hangs the whole
claim on a free parameter, and a factor engine asserted with `is not None` would pass for an
engine that returned the close.

The `as_of`s used are the two that matter:

- `MID_WINDOW`, noon Asia/Shanghai on the sixth session, where `read_if_ready` refuses the whole
  year (roadmap section 11) and five of ten sessions are knowable. This is the case the issue
  exists for.
- `AS_OF` (the fixture's own, after the last session), where nothing is withheld -- the control.

## Four of the five coverage codes are provoked here, and the fifth says why not

`computed`, `not_in_universe`, `insufficient_history` and `input_missing` are each produced by a
real partition at a real `as_of`. `undefined_value` needs a zero denominator, which no writer in
this repository will store (`DAILY_PRICE_COLUMNS`: no null and no non-positive close across
58,055 bars from 2001 to 2026), so its two halves are split -- the arithmetic in
`tests/unit/test_factor_engine_rules.py`, the engine's non-finite handling here through an
injected evaluator. Splitting it is what keeps the code from being a table entry with nothing
behind it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from panel_fixtures import AS_OF, YEAR, GeneratedPanel, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.factor import (
    FactorBuildManifest,
    FactorDefinition,
    FactorField,
    FactorObservation,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_MANIFEST_DATASET,
    FACTOR_OBSERVATION_DATASET,
    REVERSAL_1D,
    FactorEngineError,
    FactorPanel,
    FactorWindow,
    compute_factor,
    factor_observation_requirement,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_basic_requirement,
    daily_requirement,
    write_panel_batch,
)

MID_WINDOW = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on the sixth session: sessions 1-5 knowable, 6-10 not."""

FIRST_SESSION_ONLY = datetime(2026, 1, 6, 4, 0, tzinfo=UTC)
"""Noon on the second session: exactly one session has published, so a 2-session window cannot
be formed for anybody. `insufficient_history` for the whole cross section."""

INPUT_STALENESS_BOUND = timedelta(days=5)
"""The freshness bound every input requirement here states, because the engine refuses a waiver.

Five days rather than a number chosen to make the fixture pass: it is one trading week, and the
widest gap this panel actually produces between an `as_of` and the newest session visible at it
is the weekend one (2026-01-12T04:00Z sees 2026-01-09, 2 days 21 hours). A bound that only just
cleared would make every assertion in this file depend on the arithmetic of one fixture."""

PROBE_STALENESS_BOUND = timedelta(days=30)
"""The bound the hand-written `probe_doubles` partition states, and why it is not the above.

That partition holds a single session (2026-01-08) with nothing to do with the generated panel's
calendar, and it is read at `AS_OF` on 2026-01-17 -- 8 days 21 hours, which
`INPUT_STALENESS_BOUND` would refuse for a reason unrelated to what that test is about. Widened
deliberately and named separately rather than by relaxing the bound the real requirements use:
one number moved to accommodate a probe is how a bound stops meaning anything."""

BUILT_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
COMMIT = "a1b2c3d"

SHAPES = ("daily.close_moves_between_sessions",)

HALTED = "601318.SH"
"""`panel_fixtures._halted_key`: the shapeless panel's one untimed halt is this security on the
fifth session, so it has no `daily` bar on 2026-01-09 and its own window ends a session early."""


@pytest.fixture
def panel() -> GeneratedPanel:
    return generate_panel(shapes=SHAPES)


@pytest.fixture
def store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, panel)
    return built


def _close(panel: GeneratedPanel, code: str, day: date) -> float:
    """The fixture's own close arithmetic, restated so the expected values are derived and not
    copied: `10.0 + securities.index(code) + 0.5 * sessions.index(day)`."""
    return 10.0 + panel.securities.index(code) + 0.5 * panel.sessions.index(day)


def _requirements(
    panel: GeneratedPanel, as_of: datetime, *, datasets: tuple[str, ...] = (DAILY_DATASET,)
) -> dict[str, ReadinessRequirement]:
    builders = {
        DAILY_DATASET: daily_requirement,
        DAILY_BASIC_DATASET: daily_basic_requirement,
    }
    return {
        dataset: builders[dataset](
            panel.calendar(), years=(YEAR,), as_of=as_of, max_staleness=INPUT_STALENESS_BOUND
        )
        for dataset in datasets
    }


def _compute(
    store: PanelStore,
    panel: GeneratedPanel,
    *,
    as_of: datetime = MID_WINDOW,
    definition: FactorDefinition = REVERSAL_1D,
    subjects: tuple[str, ...] | None = None,
    universe: frozenset[str] | None = None,
    **overrides: object,
) -> FactorPanel:
    settings: dict[str, object] = {
        "as_of": as_of,
        "subjects": panel.securities if subjects is None else subjects,
        "universe": frozenset(panel.securities) if universe is None else universe,
        "requirements": _requirements(panel, as_of, datasets=definition.datasets),
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, definition, **settings)  # type: ignore[arg-type]


# --- the value, at an as_of the gated read refuses -------------------------------------------


def test_the_engine_answers_at_a_mid_year_as_of_the_gated_read_blocks_whole(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Roadmap section 11's constraint and the exit from it, end to end.

    The premise is asserted alongside the result: `read_if_ready` refuses the 2026 partition at
    this `as_of` for the whole cross section, and the engine still produces a value for every
    security -- computed from the five sessions that had published and none of the five that
    had not.
    """
    requirement = _requirements(panel, MID_WINDOW)[DAILY_DATASET]
    gated = store.read_if_ready(requirement, year=YEAR, columns=("subject", "close"))

    result = _compute(store, panel)

    assert gated.is_blocked
    assert {issue.code for issue in gated.readiness.issues} == {"not_yet_knowable"}
    assert dict(result.coverage_census())["computed"] == len(panel.securities)


def test_every_computed_value_is_the_return_over_the_last_two_visible_sessions(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The magnitude. Each expected number is derived from the fixture's own close arithmetic
    over the sessions the availability filter left, so an engine that used the last two
    *stored* sessions (2026-01-15 and 2026-01-16) would produce a different number for every
    security and fail here rather than pass with a plausible one."""
    result = _compute(store, panel)
    values = result.values()

    for code in panel.securities:
        window = (date(2026, 1, 8), date(2026, 1, 9))
        if code == HALTED:
            window = (date(2026, 1, 7), date(2026, 1, 8))
        expected = _close(panel, code, window[1]) / _close(panel, code, window[0]) - 1.0
        assert values[code] == pytest.approx(expected), code
    assert values["000001.SZ"] == pytest.approx(12.0 / 11.5 - 1.0)


def test_the_halted_security_falls_back_to_its_own_last_two_traded_sessions(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A window is per security, not per calendar. The fixture's one untimed halt leaves
    `601318.SH` without a bar on the fifth session, so its window ends a session earlier than
    everybody else's -- and the observation says so, rather than reporting a value computed
    across the hole."""
    by_subject = {item.subject: item for item in _compute(store, panel).observations}

    assert by_subject[HALTED].input_session_last == date(2026, 1, 8)
    assert by_subject["000001.SZ"].input_session_last == date(2026, 1, 9)
    assert by_subject[HALTED].coverage == "computed"


# --- the coverage marker, code by code ---------------------------------------------------------


def test_a_security_outside_the_declared_universe_is_marked_and_not_scored(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`not_in_universe` is not a data fault and must not read as one: the same security, in
    the same partition, with the same rows, differs only in whether the caller said it was in
    the cross section."""
    excluded = panel.securities[0]
    narrowed = frozenset(panel.securities) - {excluded}

    result = _compute(store, panel, universe=narrowed)
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[excluded].coverage == "not_in_universe"
    assert by_subject[excluded].value is None
    assert by_subject[excluded].input_row_count == 0
    assert by_subject[excluded].input_session_first is None
    assert excluded not in result.values()
    assert dict(result.coverage_census()) == {
        "computed": len(panel.securities) - 1,
        "not_in_universe": 1,
        "insufficient_history": 0,
        "input_missing": 0,
        "undefined_value": 0,
    }


def test_a_window_the_visible_panel_cannot_fill_is_insufficient_history_not_a_null(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """One session has published at `FIRST_SESSION_ONLY`, so a two-session window cannot be
    formed for anybody. The partition is the same one that answers at `MID_WINDOW`; only the
    `as_of` moved, which is what makes this attributable to the lookback and not to the data."""
    result = _compute(store, panel, as_of=FIRST_SESSION_ONLY)

    assert dict(result.coverage_census())["insufficient_history"] == len(panel.securities)
    assert result.values() == {}
    assert all(item.input_session_first is None for item in result.observations)
    assert all(item.input_row_count == 1 for item in result.observations)


def test_a_required_column_absent_on_a_window_session_is_input_missing(tmp_path: Path) -> None:
    """`daily_basic.bar_without_valuation`: `000001.SZ` has a bar and no valuation on the last
    session. A factor requiring both datasets over the last two sessions therefore cannot be
    formed for that one security and can for every other -- which is the pairing that makes the
    code attributable to the missing row.
    """
    panel = generate_panel(shapes=(*SHAPES, "daily.bar_without_valuation"))
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    two_dataset = FactorDefinition(
        key="probe_two_datasets",
        version=1,
        family="volatility_liquidity",
        direction="higher_is_better",
        required_fields=(
            FactorField(dataset=DAILY_DATASET, column="close"),
            FactorField(dataset=DAILY_BASIC_DATASET, column="total_mv"),
        ),
        lookback_sessions=2,
        summary="a probe that needs both halves of the price panel",
    )

    result = compute_factor(
        store,
        two_dataset,
        as_of=AS_OF,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements=_requirements(panel, AS_OF, datasets=(DAILY_DATASET, DAILY_BASIC_DATASET)),
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={
            two_dataset.qualified_key: lambda window: window.series(DAILY_DATASET, "close")[-1]
        },
    )
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject["000001.SZ"].coverage == "input_missing"
    assert by_subject["000001.SZ"].value is None
    assert by_subject["000001.SZ"].input_session_last == date(2026, 1, 16)
    # Three rows, not four: two `daily` bars over the window and one `daily_basic` valuation.
    # A count derived as `len(window) * len(datasets)` would say four and would be wrong on
    # exactly the observation where the number is the evidence.
    assert by_subject["000001.SZ"].input_row_count == 3
    assert by_subject["000002.SZ"].coverage == "computed"
    assert by_subject["000002.SZ"].input_row_count == 4
    assert dict(result.coverage_census())["input_missing"] == 1


def test_a_non_finite_result_is_undefined_rather_than_stored_as_a_number(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The engine's half of `undefined_value`; `_reversal_1d`'s zero-denominator guard is the
    other half and is driven in `tests/unit/test_factor_engine_rules.py`.

    An evaluator that computes its way to `inf` has said "undefined" less deliberately than one
    that returns `None`, and both have to land in the same place -- otherwise an `inf` reaches
    Parquet, and every downstream mean and rank built on that column is poisoned by one row.
    """

    def diverges(window: FactorWindow) -> float | None:
        return float("inf") if window.subject == "000001.SZ" else float("nan")

    result = _compute(store, panel, evaluators={REVERSAL_1D.qualified_key: diverges})

    assert dict(result.coverage_census())["undefined_value"] == len(panel.securities)
    assert all(item.value is None for item in result.observations)
    assert result.values() == {}


def test_the_census_reports_every_declared_code_including_the_ones_at_zero(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A report that omitted the zeros would make "no security was `input_missing`" and "this
    build did not look" the same output."""
    census = _compute(store, panel).coverage_census()

    assert set(census) == {
        "computed",
        "not_in_universe",
        "insufficient_history",
        "input_missing",
        "undefined_value",
    }
    assert sum(census.values()) == len(panel.securities)


# --- the build manifest ---------------------------------------------------------------------------


def test_the_manifest_names_the_partition_it_read_with_both_hashes_and_both_counts(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The input reference, checked against the catalog rather than against itself: the digests
    on the manifest are the ones `record_coverage` stored, and the two row counts are the two
    halves of the filtered read, which add up to the partition."""
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None

    manifest = _compute(store, panel).manifest
    (reference,) = manifest.inputs

    assert reference.dataset == DAILY_DATASET
    assert reference.year == YEAR
    assert reference.batch_digest == coverage.batch_digest
    assert reference.partition_content_hash == coverage.partition_content_hash
    assert reference.visible_row_count + reference.withheld_row_count == coverage.row_count
    assert reference.withheld_row_count > 0


def test_rebuilding_the_same_factor_from_the_same_partition_reproduces_the_manifest_id(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Reproducibility, and the way roadmap section 9 says to check it: not "an ID exists" but
    "the wall clock is out of it and everything else is in it". `built_at` moves by two months
    and the identity does not; `code_commit` moves by one character and it does."""
    first = _compute(store, panel)
    later = _compute(store, panel, built_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    recompiled = _compute(store, panel, code_commit="0000000")

    assert first.manifest.manifest_id == later.manifest.manifest_id
    assert first.manifest.manifest_id != recompiled.manifest.manifest_id
    assert first.built_at != later.built_at


def test_every_observation_carries_the_manifest_of_the_build_that_produced_it(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    result = _compute(store, panel)

    assert {item.manifest_id for item in result.observations} == {result.manifest.manifest_id}
    assert {item.factor_id for item in result.observations} == {REVERSAL_1D.factor_id}
    assert {item.as_of for item in result.observations} == {MID_WINDOW}


# --- the requirements the engine refuses to invent -----------------------------------------------


def test_the_engine_refuses_a_requirement_set_that_is_not_the_datasets_it_reads(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    with pytest.raises(FactorEngineError, match="reads \\['daily'\\]"):
        compute_factor(
            store,
            REVERSAL_1D,
            as_of=MID_WINDOW,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements=_requirements(panel, MID_WINDOW, datasets=(DAILY_BASIC_DATASET,)),
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_the_engine_refuses_a_requirement_written_for_a_different_instant(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A readiness verdict taken at a different `as_of` is a verdict about a different read --
    and, here, the difference between refusing five sessions and refusing none."""
    with pytest.raises(FactorEngineError, match="is written for as_of"):
        compute_factor(
            store,
            REVERSAL_1D,
            as_of=MID_WINDOW,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements=_requirements(panel, AS_OF),
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_the_engine_refuses_a_requirement_that_does_not_require_the_columns_it_reads(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`panel_gate`'s argument, applied: an engine that accepted a weaker question would let
    readiness clear a partition that cannot answer this factor, and the failure would surface as
    a binder error several layers down instead of as a verdict."""
    sound = _requirements(panel, MID_WINDOW)[DAILY_DATASET]
    waived = replace(sound, required_fields=None)
    narrowed = replace(sound, required_fields=("subject", "trade_date"))

    for requirement in (waived, narrowed):
        with pytest.raises(FactorEngineError, match=r"required_fields|does not require"):
            compute_factor(
                store,
                REVERSAL_1D,
                as_of=MID_WINDOW,
                subjects=panel.securities,
                universe=frozenset(panel.securities),
                requirements={DAILY_DATASET: requirement},
                code_commit=COMMIT,
                built_at=BUILT_AT,
            )


def test_the_engine_refuses_an_input_requirement_that_waives_its_freshness_bound(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The sibling of the test above, added by `V2-P3-002`'s review, and for a sharper reason.

    A waived `required_fields` lets readiness clear a partition without the columns this factor
    reads. A waived `max_staleness` lets it clear a partition whose **visible slice** reaches
    arbitrarily far short of `as_of` -- and unlike the columns case there is no binder error
    downstream to catch it: the build succeeds, every observation reads `coverage="computed"`,
    and the `as_of` stamped on it is months ahead of the newest session behind it. Measured at
    172 days on a 14-row partition, with `withheld_row_count` at 4, so the shortness the design
    relies on being *stated* was small while the answer was worst.

    The pair matters as much as the refusal: the same call with a bound stated answers, so the
    refusal is attributable to the waiver rather than to anything else about this fixture.
    """
    sound = _requirements(panel, MID_WINDOW)[DAILY_DATASET]
    waived = replace(sound, max_staleness=None)

    with pytest.raises(FactorEngineError, match=r"waives max_staleness"):
        compute_factor(
            store,
            REVERSAL_1D,
            as_of=MID_WINDOW,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements={DAILY_DATASET: waived},
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )

    assert _compute(store, panel).observations != ()


def test_a_blocked_input_partition_raises_instead_of_becoming_a_panel_of_coverage_codes(
    tmp_path: Path, panel: GeneratedPanel
) -> None:
    """`V2-P1-013`'s "assert blocking, not an empty success", one plane up. A panel of five
    thousand `input_missing` rows is an empty success with a coverage column on it -- and it is
    the shape an engine that swallowed a blocked read would produce.

    Injected by never writing the partition at all, which readiness reports as
    `partition_missing` -- a code no row predicate can compensate.
    """
    empty = PanelStore(tmp_path / "empty")
    write_generated_panel(empty, panel, datasets=("trade_cal",))

    with pytest.raises(FactorEngineError, match="partition_missing"):
        _compute(empty, panel)


def test_a_definition_the_evaluator_table_does_not_implement_is_refused_at_the_call(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`_refuse_table_drift` guards the module's own pair at import; this is the second guard,
    at the call, which is what stops an injected evaluator table smuggling the gap back in --
    the `evaluators=` argument exists for tests and would otherwise be a way around the audit.
    """
    unimplemented = FactorDefinition(
        key="not_implemented",
        version=1,
        family="growth",
        direction="higher_is_better",
        required_fields=(FactorField(dataset=DAILY_DATASET, column="close"),),
        lookback_sessions=2,
        summary="declared for this test and implemented nowhere",
    )

    with pytest.raises(FactorEngineError, match="has no evaluator"):
        _compute(store, panel, definition=unimplemented)


def test_a_requirement_filed_under_the_wrong_dataset_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A verdict about one dataset cannot gate a read of another, and the mapping key is the
    only thing that would have said otherwise."""
    misfiled = {
        DAILY_DATASET: _requirements(panel, MID_WINDOW, datasets=(DAILY_BASIC_DATASET,))[
            DAILY_BASIC_DATASET
        ]
    }

    with pytest.raises(FactorEngineError, match=r"is for 'daily_basic'"):
        compute_factor(
            store,
            REVERSAL_1D,
            as_of=MID_WINDOW,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements=misfiled,
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_a_requirement_naming_no_year_is_refused(store: PanelStore, panel: GeneratedPanel) -> None:
    """`evaluate_readiness` would block it as `no_years_requested`, but only after the engine
    had built a manifest describing an input it never read. Refusing at the boundary keeps
    "there is no partition to read" from arriving as a coverage code."""
    yearless = replace(_requirements(panel, MID_WINDOW)[DAILY_DATASET], years=())

    with pytest.raises(FactorEngineError, match="names no year"):
        compute_factor(
            store,
            REVERSAL_1D,
            as_of=MID_WINDOW,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements={DAILY_DATASET: yearless},
            code_commit=COMMIT,
            built_at=BUILT_AT,
        )


def test_a_factor_declaring_a_non_numeric_column_is_refused_rather_than_marked(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`daily.trade_date` is a stored `VARCHAR`. Reporting `input_missing` for it would tell a
    reader to re-fetch, which would never fix it: the fault is in the definition, and it is the
    same for every security in the cross section rather than a property of any one of them."""
    textual = FactorDefinition(
        key="reads_a_string",
        version=1,
        family="quality",
        direction="higher_is_better",
        required_fields=(FactorField(dataset=DAILY_DATASET, column="trade_date"),),
        lookback_sessions=2,
        summary="a probe that declares a text column as a factor input",
    )

    with pytest.raises(FactorEngineError, match="cannot be one of this factor's required_fields"):
        _compute(
            store,
            panel,
            definition=textual,
            evaluators={textual.qualified_key: lambda window: 1.0},
        )


def test_a_dataset_serving_two_rows_for_one_security_and_session_is_refused(
    tmp_path: Path, panel: GeneratedPanel
) -> None:
    """The reducer this engine does not have, named rather than guessed at.

    `daily` cannot produce this shape -- `write_daily_panel` refuses it -- but the statement
    datasets can and do: roadmap section 7 measured `fina_indicator` carrying two rows for
    81.7% of its keys with byte-identical four-clock timelines. Taking "the last one" would be
    picking `V2-P1-011`'s disambiguation strategy inside a factor engine, so the engine refuses
    and says what is missing. Driven here through a hand-written partition, because no writer in
    this repository will store the shape on a price dataset.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    instant = datetime(2026, 1, 8, 7, 0, tzinfo=UTC)
    published = datetime(2026, 1, 8, 8, 30, tzinfo=UTC)
    doubled = ColumnarPanelBatch(
        provider_id="synthetic",
        dataset="probe_doubles",
        kind="probe",
        as_of=AS_OF,
        fetched_at=AS_OF,
        status="success",
        subjects=("000001.SZ", "000001.SZ"),
        timeline=TimelineColumns(
            event_time=(instant, instant),
            available_time=(published, published),
            ingested_time=(published, published),
            revision_time=(published, published),
        ),
        columns=(PanelColumn("score", "float", (1.0, 2.0)),),
    )
    write_panel_batch(store, doubled, year=YEAR)
    definition = FactorDefinition(
        key="reads_a_doubled_dataset",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="probe_doubles", column="score"),),
        lookback_sessions=1,
        summary="a probe that reads a dataset with two rows for one key",
    )
    requirement = ReadinessRequirement(
        dataset="probe_doubles",
        as_of=AS_OF,
        years=(YEAR,),
        required_dates=None,
        required_subjects=None,
        required_fields=("subject", "score"),
        max_staleness=PROBE_STALENESS_BOUND,
    )

    with pytest.raises(FactorEngineError, match=r"more than one row for 000001\.SZ"):
        compute_factor(
            store,
            definition,
            as_of=AS_OF,
            subjects=("000001.SZ",),
            universe=frozenset({"000001.SZ"}),
            requirements={"probe_doubles": requirement},
            code_commit=COMMIT,
            built_at=BUILT_AT,
            evaluators={definition.qualified_key: lambda window: 1.0},
        )


def test_an_unknown_timezone_is_refused_by_name(store: PanelStore, panel: GeneratedPanel) -> None:
    """The session grouping resolves `event_time` in this zone, so a bad label would otherwise
    surface as a `ZoneInfoNotFoundError` from inside a loop over rows."""
    with pytest.raises(FactorEngineError, match="is not a known IANA time zone"):
        _compute(store, panel, date_timezone="Mars/Olympus_Mons")


def test_an_empty_or_duplicated_cross_section_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    with pytest.raises(FactorEngineError, match="at least one subject"):
        _compute(store, panel, subjects=())
    with pytest.raises(FactorEngineError, match="more than once"):
        _compute(store, panel, subjects=("000001.SZ", "000001.SZ"))


# --- the round trip -------------------------------------------------------------------------------


def test_observations_and_their_manifest_land_on_the_panel_plane_and_read_back(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The acceptance's six facts, after a real Parquet round trip.

    Read back through `load_factor_observations`, which takes the filtered read for the same
    reason the inputs do: an observation's `available_time` is the `as_of` it was computed at,
    so a year holding a year of cross sections has a `max_available_time` in December.
    """
    result = _compute(store, panel)

    references = write_factor_panels(store, [result])
    stored = load_factor_observations(store, years=(YEAR,), as_of=MID_WINDOW)

    assert {reference.dataset for reference in references} == {
        FACTOR_OBSERVATION_DATASET,
        FACTOR_MANIFEST_DATASET,
    }
    assert sorted(stored, key=lambda item: item.subject) == sorted(
        result.observations, key=lambda item: item.subject
    )
    assert all(isinstance(item, FactorObservation) for item in stored)
    assert isinstance(result.manifest, FactorBuildManifest)


def test_a_second_as_of_written_alongside_the_first_keeps_both(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A partition is replaced whole, so both cross sections have to reach the store in one
    call -- and once they have, the filtered read gives each `as_of` its own answer: reading at
    `MID_WINDOW` returns the earlier build's rows and not the later one's, because the later
    one was not knowable then."""
    early = _compute(store, panel, as_of=FIRST_SESSION_ONLY)
    late = _compute(store, panel)

    write_factor_panels(store, [early, late])
    at_mid = load_factor_observations(store, years=(YEAR,), as_of=MID_WINDOW)
    at_first = load_factor_observations(store, years=(YEAR,), as_of=FIRST_SESSION_ONLY)

    assert {item.as_of for item in at_mid} == {FIRST_SESSION_ONLY, MID_WINDOW}
    assert {item.as_of for item in at_first} == {FIRST_SESSION_ONLY}
    assert len(at_mid) == 2 * len(panel.securities)


def test_a_write_that_would_drop_a_stored_build_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The guard on overwrite-per-partition, and the reason the manifest dataset's subject is a
    `manifest_id`: the stored build list is `PartitionCoverage.subjects`, so the refusal costs
    one catalog row and no partition scan."""
    early = _compute(store, panel, as_of=FIRST_SESSION_ONLY)
    late = _compute(store, panel)
    write_factor_panels(store, [early, late])

    with pytest.raises(FactorEngineError, match="would drop"):
        write_factor_panels(store, [late])
    assert len(load_factor_observations(store, years=(YEAR,), as_of=MID_WINDOW)) == 2 * len(
        panel.securities
    )


def test_reading_back_narrows_to_one_factor_in_sql(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A partition holding several factors is not materialised in full to read one of them."""
    result = _compute(store, panel)
    write_factor_panels(store, [result])

    mine = load_factor_observations(
        store, years=(YEAR,), as_of=MID_WINDOW, factor_id=REVERSAL_1D.factor_id
    )
    other = load_factor_observations(store, years=(YEAR,), as_of=MID_WINDOW, factor_id="fct_none")

    assert len(mine) == len(panel.securities)
    assert other == ()


def test_a_rebuild_of_an_unchanged_factor_rewrites_nothing_but_its_provenance(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """No wall clock is inside a row, so the partition's content hash is stable across rebuilds
    while `PartitionCoverage.fetched_at` moves -- which is the arrangement `write_panel_batch`
    documents for a refetch of unchanged rows, reached here on purpose rather than by luck."""
    first = _compute(store, panel)
    (observations, _) = write_factor_panels(store, [first])

    later = replace(first, built_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    (rewritten, _) = write_factor_panels(store, [later])
    coverage = store.read_coverage(FACTOR_OBSERVATION_DATASET, YEAR)

    assert rewritten.content_hash == observations.content_hash
    assert coverage is not None
    assert coverage.fetched_at == datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def test_the_stored_observation_partition_passes_the_stores_own_readiness_contract(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The factor plane is the panel plane: the partition the engine writes is assessed by the
    same rule table as `daily`, with the same coverage record behind it. A derived dataset that
    could not clear its own readiness would be a second storage format wearing the first one's
    directory layout."""
    write_factor_panels(store, [_compute(store, panel)])

    readiness = store.assess_readiness(
        factor_observation_requirement(years=(YEAR,), as_of=MID_WINDOW)
    )

    assert readiness.state == "ready"
    assert readiness.issues == ()
    assert readiness.checks_waived == ("required_dates", "required_subjects", "max_staleness")
    assert readiness.row_count == len(panel.securities)
