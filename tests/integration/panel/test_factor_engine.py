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

## Four of the six coverage codes are provoked here, and the other two say why not

`computed`, `not_in_universe`, `insufficient_history` and `input_missing` are each produced by a
real partition at a real `as_of`. `undefined_value` needs a zero denominator, which no writer in
this repository will store (`DAILY_PRICE_COLUMNS`: no null and no non-positive close across
58,055 bars from 2001 to 2026), so its two halves are split -- the arithmetic in
`tests/unit/test_factor_engine_rules.py`, the engine's non-finite handling here through an
injected evaluator. Splitting it is what keeps the code from being a table entry with nothing
behind it.

`ambiguous_filing` (`V2-P3-018`) is unreachable here for a structural reason rather than a
fixture one: it can only be produced on the **report-period** axis, and every factor this file
drives reads `daily` alone. It is provoked end to end in
`tests/integration/panel/test_value_family.py` and
`tests/integration/panel/test_quality_family.py`, on partitions built from the two rows real
endpoints serve.

## The identity is measured in both directions here, and one of them needs a store

`tests/unit/domain/test_factor.py` proves that every declared field of the manifest reaches
`manifest_id`. That is one half of an identity contract and this file holds the other two:

- **What did not change must not move it.** A no-op re-fetch of an input partition -- the same
  rows, a new `fetched_at` -- must reproduce the ID, and only a real store can produce that
  state, because `PartitionCoverage.batch_digest` is what moves and it is written by
  `record_coverage`.
- **What decides the answers must be declared.** `test_every_determinant_of_this_build_is_
  either_in_the_identity_or_exempted_by_name` reads `compute_factor`'s own signature and fails
  on a parameter that is neither shown to move the ID nor listed with a reason it does not. A
  parametrized equivalence test cannot reach that: it varies what the model declares, and the
  defect was a determinant the model did not declare.

## What is deliberately in the sibling file instead

Every definition here is on the **session** axis, which is the axis this file's fixture panel
has. The report-period axis -- what `PERIOD_INDEXED_DATASETS` changes about `_read_dataset`, how
a filing window is formed and bounded, and which multiplicity refusals did and did not change --
is measured in `test_factor_report_periods.py`, against `income` partitions of its own. The two
files share no fixture on purpose: a corpus of trading sessions cannot exhibit two periods
disclosed on one day, which is the shape the second axis exists for.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import AS_OF, YEAR, GeneratedPanel, generate_panel, write_generated_panel

from openalpha_cn import panel_factors
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.factor import (
    FactorBuildManifest,
    FactorDefinition,
    FactorError,
    FactorField,
    FactorObservation,
)
from openalpha_cn.domain.factor_transform import observation_digest
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    _UNSEALED_MANIFEST_ID,
    REVERSAL_1D,
    FactorEngineError,
    FactorPanel,
    FactorWindow,
    _refuse_rows_that_are_not_the_answers_their_manifest_addresses,
    compute_factor,
    factor_manifest_dataset,
    factor_observation_dataset,
    factor_observation_requirement,
    load_factor_manifests,
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

EARLY_WINDOW = datetime(2026, 1, 9, 4, 0, tzinfo=UTC)
"""Noon on the fifth session: sessions 1-4 knowable. A second, earlier cross section that a
2-session factor can still answer, so a partition can legitimately hold two `as_of`s."""

FIRST_SESSION_ONLY = datetime(2026, 1, 6, 4, 0, tzinfo=UTC)
"""Noon on the second session: exactly one session has published, so a 2-session window cannot
be formed for **anybody**. That is a fault in the request rather than an answer about the data,
and `compute_factor` refuses it."""

AFTER_THE_HALT = datetime(2026, 1, 13, 4, 0, tzinfo=UTC)
"""Noon on the seventh session: sessions 1-6 knowable, so `601318.SH`'s two most recent traded
sessions are 2026-01-08 and 2026-01-12 with the halted 2026-01-09 between them -- a two-session
window spanning three panel sessions."""

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

OBSERVATIONS: Final[str] = factor_observation_dataset(REVERSAL_1D)
MANIFESTS: Final[str] = factor_manifest_dataset(REVERSAL_1D)


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


def _probe(
    *,
    key: str,
    lookback_sessions: int,
    max_window_sessions: int,
    dataset: str = DAILY_DATASET,
    column: str = "close",
) -> FactorDefinition:
    """A one-column probe definition, so a test that needs a different window says only that."""
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction="higher_is_better",
        required_fields=(FactorField(dataset=dataset, column=column),),
        lookback_sessions=lookback_sessions,
        max_window_sessions=max_window_sessions,
        lookback_periods=None,
        max_window_periods=None,
    )


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
        "ambiguous_filing": 0,
        "input_missing": 0,
        "undefined_value": 0,
    }


def test_a_security_the_visible_panel_cannot_fill_a_window_for_is_insufficient_history(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`insufficient_history` is a statement about *one security*, and this is the shape that
    makes it one: a five-session lookback at `MID_WINDOW`, where five sessions have published,
    so every name that traded them qualifies and the one that was halted on the fifth does not.

    That pairing is what makes the code attributable. The earlier version of this test used an
    `as_of` at which *nobody* could qualify, which is a fault in the request rather than an
    answer about the data -- `compute_factor` now refuses it, and
    `test_a_panel_narrower_than_the_lookback_is_refused_rather_than_written_as_a_census` is
    where that lives.
    """
    five_sessions = _probe(key="probe_five_sessions", lookback_sessions=5, max_window_sessions=5)

    result = _compute(
        store,
        panel,
        definition=five_sessions,
        evaluators={
            five_sessions.qualified_key: lambda window: window.series("daily", "close")[-1]
        },
    )
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[HALTED].coverage == "insufficient_history"
    assert by_subject[HALTED].value is None
    assert by_subject[HALTED].input_session_first is None
    assert by_subject[HALTED].input_row_count == 4
    assert by_subject["000001.SZ"].coverage == "computed"
    assert dict(result.coverage_census()) == {
        "computed": len(panel.securities) - 1,
        "not_in_universe": 0,
        "insufficient_history": 1,
        "ambiguous_filing": 0,
        "input_missing": 0,
        "undefined_value": 0,
    }


def test_a_panel_narrower_than_the_lookback_is_refused_rather_than_written_as_a_census(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """One session has published at `FIRST_SESSION_ONLY`, so a two-session window cannot be
    formed for **anybody** -- every security's sessions are a subset of the panel's, so the
    census is arithmetic rather than a finding.

    Before the refusal this returned a full panel of `insufficient_history`, `write_factor_panels`
    stored it, and nothing said anything: `coverage_census()` speaks only to a caller that asks
    and no face asks yet. That is the fail-open dressed as coverage `FactorEngineError`'s own
    docstring names.

    The message has to name the years, because that is where the fault is: the sessions a factor
    can see are the ones in `requirement.years`, and a window that spans a year boundary needs
    the earlier year named. Asserted, so a refusal that said only "insufficient history" would
    fail here.
    """
    with pytest.raises(FactorEngineError, match="needs 2 sessions") as raised:
        _compute(store, panel, as_of=FIRST_SESSION_ONLY)

    assert "holds 1" in str(raised.value)
    assert "[2026]" in str(raised.value)
    assert "`years`" in str(raised.value)


def test_the_refusal_is_the_panels_sessions_and_not_the_partitions(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The sentinel for the test above. The 2026 partition holds ten sessions at both `as_of`s,
    so a guard that counted *stored* sessions would never fire and a guard that fired on any
    all-`insufficient_history` census would fire on legitimate answers too.

    What decides it is the **visible** panel: one session at `FIRST_SESSION_ONLY`, five at
    `MID_WINDOW`, same partition, same requirement builder, only the `as_of` moved.
    """
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None
    assert len(coverage.dates) == len(panel.sessions)

    assert dict(_compute(store, panel).coverage_census())["computed"] == len(panel.securities)
    with pytest.raises(FactorEngineError, match="holds 1"):
        _compute(store, panel, as_of=FIRST_SESSION_ONLY)


def test_a_window_stretched_across_a_halt_is_insufficient_history_and_says_so(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`lookback_sessions` counts a security's own rows and says nothing about when they were.

    At `AFTER_THE_HALT`, `601318.SH`'s two most recently traded sessions are 2026-01-08 and
    2026-01-12 with the halted 2026-01-09 between them: a two-session window spanning three panel
    sessions. `REVERSAL_1D` says "one session's close-to-close simple return" and declares
    `max_window_sessions=2`, so this is not one and the observation says so instead of reporting
    a two-session return as a one-session one.

    Scaled up, this is the shape that matters: a 120-session momentum over a name halted for
    three months spans 210 calendar days and was marked `computed`, and the only way a consumer
    could have noticed was to fetch a calendar and compare.

    The window **is** recorded on the refused observation, which is what distinguishes this from
    the other `insufficient_history` -- too few sessions has no window to record, and a window
    too stretched has one.
    """
    result = _compute(store, panel, as_of=AFTER_THE_HALT)
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[HALTED].coverage == "insufficient_history"
    assert by_subject[HALTED].value is None
    assert by_subject[HALTED].input_session_first == date(2026, 1, 8)
    assert by_subject[HALTED].input_session_last == date(2026, 1, 12)
    assert HALTED not in result.values()
    assert by_subject["000001.SZ"].coverage == "computed"
    assert by_subject["000001.SZ"].input_session_first == date(2026, 1, 9)
    assert dict(result.coverage_census())["insufficient_history"] == 1


def test_a_factor_that_tolerates_the_halt_declares_it_and_gets_the_stretched_window(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The other half, without which the refusal above would be indistinguishable from an engine
    that simply refuses every gap.

    `max_window_sessions` is a declared property of the factor rather than an engine constant
    precisely because a 120-session momentum tolerates a missed session and a one-session return
    does not. The same security, the same partition, the same `as_of`; only the declaration
    moved, and the observation is `computed` over the window the other one was refused for.
    """
    tolerant = _probe(key="probe_tolerates_a_halt", lookback_sessions=2, max_window_sessions=3)

    result = _compute(
        store,
        panel,
        as_of=AFTER_THE_HALT,
        definition=tolerant,
        evaluators={tolerant.qualified_key: lambda window: window.series("daily", "close")[-1]},
    )
    by_subject = {item.subject: item for item in result.observations}

    assert by_subject[HALTED].coverage == "computed"
    assert by_subject[HALTED].input_session_first == date(2026, 1, 8)
    assert by_subject[HALTED].input_session_last == date(2026, 1, 12)
    assert by_subject[HALTED].value == pytest.approx(_close(panel, HALTED, date(2026, 1, 12)))


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
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
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
        "ambiguous_filing",
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
    halves of the filtered read, which add up to the partition.

    Both hashes are still carried and both are still checked. What moved is which of them is a
    field of the *hashed* manifest: `batch_digest` covers the provider batch's `fetched_at`, so
    it is on `FactorPanel.input_provenance` and out of the content address, while
    `partition_content_hash` is a fact about the rows and stays in.
    """
    coverage = store.read_coverage(DAILY_DATASET, YEAR)
    assert coverage is not None

    result = _compute(store, panel)
    (reference,) = result.manifest.inputs
    (provenance,) = result.input_provenance

    assert reference.dataset == DAILY_DATASET
    assert reference.year == YEAR
    assert reference.partition_content_hash == coverage.partition_content_hash
    assert reference.visible_row_count + reference.withheld_row_count == coverage.row_count
    assert reference.withheld_row_count > 0
    assert (provenance.dataset, provenance.year) == (DAILY_DATASET, YEAR)
    assert provenance.batch_digest == coverage.batch_digest


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


def test_a_no_op_refetch_of_an_input_partition_does_not_move_the_manifest_id(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The direction the rebuild test above could not reach, because it never touched the store.

    Re-writing the same rows with a new `fetched_at` is the case `write_panel_batch` documents as
    the ordinary one, and it is what a scheduled re-fetch of yesterday's partition does. It moves
    `PartitionCoverage.batch_digest` -- that class's docstring says the digest changes on every
    re-fetch -- and while that digest was a field of `FactorInputRef` it moved every
    `manifest_id` built from the partition, for a build whose every observation was identical.

    All four facts are asserted together, because each alone is satisfiable by something broken:
    the digest *did* move (or the probe proves nothing), the content hash did not (or the rows
    changed), the values are identical (or the build is not the same build), and the ID held.
    """
    first = _compute(store, panel)
    before = store.read_coverage(DAILY_DATASET, YEAR)
    assert before is not None

    refetched = dataclasses.replace(
        panel.batch(DAILY_DATASET), fetched_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    )
    write_panel_batch(store, refetched, year=YEAR)
    after = store.read_coverage(DAILY_DATASET, YEAR)
    assert after is not None
    second = _compute(store, panel)

    assert after.batch_digest != before.batch_digest
    assert after.partition_content_hash == before.partition_content_hash
    assert second.values() == first.values()
    assert second.manifest.manifest_id == first.manifest.manifest_id
    assert second.input_provenance[0].batch_digest == after.batch_digest


def test_a_rebuild_after_a_refetch_can_be_written_over_the_build_it_supersedes(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The consequence, which is what made the moving ID a defect rather than an oddity.

    A stored build may not be dropped, so a rebuild whose ID moved for no reason was a rebuild
    that could never be written -- and its predecessor could no longer be re-derived either, the
    coverage record holding the old `batch_digest` having been overwritten by the re-fetch. There
    was no supported recovery path. With the identity stable the rebuild simply *is* the stored
    build, so the write is an idempotent no-op with fresh provenance.
    """
    write_factor_panels(store, [_compute(store, panel)])
    write_panel_batch(
        store,
        dataclasses.replace(
            panel.batch(DAILY_DATASET), fetched_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ),
        year=YEAR,
    )

    rebuilt = _compute(store, panel)
    write_factor_panels(store, [rebuilt])

    (stored,) = load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    assert stored.manifest_id == rebuilt.manifest.manifest_id


def test_two_builds_over_different_cross_sections_do_not_share_an_identity(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The manifest recorded `subject_count` and not the set, so two builds over disjoint
    two-name cross sections were byte-identical as identities -- and writing the second replaced
    the first's observations under the same `manifest_id`, with nothing stored able to say which
    build produced which numbers.

    The reordered pair is the other half and is not decoration: an identity that moved for a
    shuffled argument list would fail the second half of the contract, because `compute_factor`
    produces one independent observation per subject and shuffling changes no answer.
    """
    first = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))
    disjoint = _compute(store, panel, subjects=("600000.SH", "600519.SH"))
    reordered = _compute(store, panel, subjects=("000002.SZ", "000001.SZ"))

    assert first.manifest.subject_count == disjoint.manifest.subject_count
    assert first.manifest.manifest_id != disjoint.manifest.manifest_id
    assert first.manifest.manifest_id == reordered.manifest.manifest_id
    assert first.values() == reordered.values()


def test_two_builds_over_different_universes_of_one_size_do_not_share_an_identity(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The sharper half of the same defect. The universe decides `not_in_universe`, which decides
    whether a security is scored at all -- so one cross section under two disjoint universes of
    equal size produced a full set of values and an empty one, under one `manifest_id`."""
    scored = _compute(
        store,
        panel,
        subjects=("000001.SZ", "000002.SZ"),
        universe=frozenset({"000001.SZ", "000002.SZ"}),
    )
    excluded = _compute(
        store,
        panel,
        subjects=("000001.SZ", "000002.SZ"),
        universe=frozenset({"600000.SH", "600519.SH"}),
    )

    assert scored.manifest.universe_count == excluded.manifest.universe_count
    assert dict(scored.coverage_census())["computed"] == 2
    assert dict(excluded.coverage_census())["not_in_universe"] == 2
    assert scored.values() != excluded.values()
    assert scored.manifest.manifest_id != excluded.manifest.manifest_id


_IDENTITY_EXEMPT_ARGUMENTS: Final[dict[str, str]] = {
    "store": (
        "a handle. Its *content* reaches the identity through each input's "
        "partition_content_hash, which test_the_manifest_names_the_partition_it_read checks "
        "against the catalog"
    ),
    "built_at": (
        "the wall clock, deliberately out of the content address so a rebuild of an unchanged "
        "factor reproduces its ID; recorded as the partition's fetched_at instead"
    ),
    "requirements": (
        "decides whether a read is permitted rather than what it returns -- a difference that "
        "mattered blocks the read. The part that does decide the answers is `years`, which "
        "arrives in the identity as manifest.inputs; "
        "test_the_years_a_requirement_names_reach_the_identity covers it"
    ),
}
"""Every `compute_factor` argument that does **not** have to move `manifest_id`, with why.

The other half of the audit below. An exemption is a claim, so each one is written out rather
than left as a name in a set: "this argument does not decide the answers" is the sentence that
was wrong about `subjects` and `universe`, and writing it down is what makes it reviewable.
"""


_OTHER_DEFINITION: Final[FactorDefinition] = _probe(
    key="probe_other_definition", lookback_sessions=3, max_window_sessions=3
)

_STUB_EVALUATORS: Final[dict[str, Any]] = {
    REVERSAL_1D.qualified_key: lambda window: 1.0,
    _OTHER_DEFINITION.qualified_key: lambda window: 1.0,
}
"""One evaluator table both sides of the determinant audit are built with.

`_OTHER_DEFINITION` is a probe the shipped table has no evaluator for, which is why a substitution
is needed at all; making it the *baseline's* table too is what keeps the audit from proving the
evaluator swap instead of the argument under test. See the audit's own docstring."""


_DETERMINANT_CASES: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("definition", {"definition": _OTHER_DEFINITION}),
    ("as_of", {"as_of": EARLY_WINDOW}),
    ("subjects", {"subjects": ("000001.SZ", "000002.SZ")}),
    ("universe", {"universe": frozenset({"000001.SZ"})}),
    ("code_commit", {"code_commit": "0000000"}),
    ("date_timezone", {"date_timezone": "UTC"}),
    ("evaluators", {"evaluators": {REVERSAL_1D.qualified_key: lambda window: 0.5}}),
)
"""Every `compute_factor` argument that **must** move `manifest_id`, and one way to move it.

**`evaluators` moved out of the exemption table in `V2-P3-019`, and the move is the point rather
than tidying.** Its written exemption was "a substitution seam whose production value is this
module's own `FACTOR_EVALUATORS`, which `code_commit` stands for. A callable cannot be canonically
hashed" -- every clause of which is still true about the *callable* and none of which was ever
true about its **output**. `FactorBuildManifest.observation_digest` addresses the answers, so an
evaluator that computes different numbers from the same rows now moves `manifest_id`, and the
exemption was the last place in this audit where "decides the answers" and "reaches the identity"
came apart. The substitution here is a constant, so every observation's value moves at once; the
sharper version -- the shipped formula with its sign flipped, holding every coverage code and
every window fixed -- is
`test_the_manifest_addresses_the_answers_and_moves_when_one_of_them_moves`."""


@pytest.mark.parametrize(("argument", "overrides"), _DETERMINANT_CASES)
def test_every_determinant_of_this_build_moves_the_manifest_id(
    store: PanelStore, panel: GeneratedPanel, argument: str, overrides: dict[str, Any]
) -> None:
    """One `compute_factor` argument at a time, against the baseline build.

    Varying the fields a *model declares* cannot show that the model declares everything that
    decides the output; this varies the **function's own inputs** instead, which is the only
    place the missing determinant could have been seen.

    **The baseline takes the same substituted evaluators as the varied build**, and since
    `V2-P3-019` that is load-bearing rather than tidy. It used to build the baseline with the
    shipped table and the varied one with the stubs -- harmless while `evaluators` reached no
    identity, and *vacuous* the moment `observation_digest` put the answers inside `manifest_id`:
    every case would then have moved the ID through the evaluator swap alone, so all seven would
    pass against a manifest that had stopped covering the argument each is named after. One
    substitution, both sides, and the `evaluators` row varies it deliberately on top.
    """
    baseline = _compute(store, panel, evaluators=_STUB_EVALUATORS)
    varied = _compute(store, panel, **{"evaluators": _STUB_EVALUATORS, **overrides})

    assert varied.manifest.manifest_id != baseline.manifest.manifest_id, argument


TWO_YEAR_DATASET = "probe_two_years"
TWO_YEAR_AS_OF = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)
TWO_YEAR_SESSIONS = {
    2025: (date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31)),
    2026: (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
}


def _two_year_store(
    tmp_path: Path, sessions: Mapping[int, tuple[date, ...]] = TWO_YEAR_SESSIONS
) -> PanelStore:
    """A three-session-per-year probe partition, so a window can be asked to span a year end.

    The fixture panel is one year by construction, and the year boundary is where the `years` a
    caller names stops being an implementation detail: a window wider than the sessions in one
    partition needs the previous year in `requirement.years` or it reads nothing at all.
    """
    store = PanelStore(tmp_path / "two_year")
    for year, days in sessions.items():
        written = datetime(year, 12, 31, 12, 0, tzinfo=UTC)
        instants = tuple(datetime(d.year, d.month, d.day, 7, 0, tzinfo=UTC) for d in days)
        published = tuple(datetime(d.year, d.month, d.day, 8, 30, tzinfo=UTC) for d in days)
        write_panel_batch(
            store,
            ColumnarPanelBatch(
                provider_id="synthetic",
                dataset=TWO_YEAR_DATASET,
                kind="probe",
                as_of=written,
                fetched_at=written,
                status="success",
                subjects=("000001.SZ",) * len(days),
                timeline=TimelineColumns(
                    event_time=instants,
                    available_time=published,
                    ingested_time=published,
                    revision_time=published,
                ),
                columns=(PanelColumn("score", "float", tuple(float(i) for i in range(len(days)))),),
            ),
            year=year,
        )
    return store


TWO_YEAR_ABANDONED_AS_OF = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
"""Half a year after the newest session either partition can show. The cross-year sibling of the
172-day build `V2-P3-002`'s C1 was raised against, and the direction that has to stay refused."""

TWO_YEAR_ABANDONED_SESSIONS = {
    2025: TWO_YEAR_SESSIONS[2025],
    2026: (*TWO_YEAR_SESSIONS[2026], date(2026, 12, 28), date(2026, 12, 29)),
}
"""The same probe with two December rows that have not published at `TWO_YEAR_ABANDONED_AS_OF`.

They are what makes `not_yet_knowable` fire, so the engine takes the *filtered* read rather than
being refused by the partition-level bound -- which is the arrangement that let the original
172-day build through, reported as `coverage="computed"`."""


def _two_year_requirement(
    *years: int,
    as_of: datetime = TWO_YEAR_AS_OF,
    max_staleness: timedelta = INPUT_STALENESS_BOUND,
) -> ReadinessRequirement:
    """A cross-year requirement with a **freshness** bound, not a window-width one.

    `INPUT_STALENESS_BOUND` is five days and the window this requirement serves spans ten, which
    is the whole point: `max_staleness` bounds how old the newest thing the read can see is, and
    `PanelStore.read_visible_at` decides it over every year named here rather than over each
    partition in turn. Waiving it is not an option -- `compute_factor` refuses a waived bound --
    so if the bound had to cover the window's span, a January momentum factor naming the previous
    year would need a six-month bound and C1 would be open again.
    """
    return ReadinessRequirement(
        dataset=TWO_YEAR_DATASET,
        as_of=as_of,
        years=years,
        required_dates=None,
        required_subjects=None,
        required_fields=("subject", "score"),
        max_staleness=max_staleness,
    )


def test_the_years_a_requirement_names_reach_the_identity(tmp_path: Path) -> None:
    """`requirements` is exempted from the audit above as "decides whether a read is permitted",
    and the part of it that decides the *answers* is `years`.

    It reaches the identity through `manifest.inputs`, one entry per `(dataset, year)` read, so
    the exemption is bounded rather than blanket. Asserted on a pair whose **values are
    identical** -- a two-session window takes the same two sessions either way -- so what moves
    the ID is the set of partitions read and nothing else.
    """
    store = _two_year_store(tmp_path)
    definition = _probe(
        key="probe_across_a_year_end",
        lookback_sessions=2,
        max_window_sessions=2,
        dataset=TWO_YEAR_DATASET,
        column="score",
    )
    evaluators = {
        definition.qualified_key: lambda window: window.series(TWO_YEAR_DATASET, "score")[-1]
    }

    def build(*years: int) -> FactorPanel:
        return compute_factor(
            store,
            definition,
            as_of=TWO_YEAR_AS_OF,
            subjects=("000001.SZ",),
            universe=frozenset({"000001.SZ"}),
            requirements={TWO_YEAR_DATASET: _two_year_requirement(*years)},
            code_commit=COMMIT,
            built_at=BUILT_AT,
            evaluators=evaluators,
        )

    one_year = build(2026)
    two_years = build(2025, 2026)

    assert one_year.values() == two_years.values()
    assert {item.year for item in one_year.manifest.inputs} == {2026}
    assert {item.year for item in two_years.manifest.inputs} == {2025, 2026}
    assert two_years.manifest.manifest_id != one_year.manifest.manifest_id


def test_a_window_that_spans_a_year_end_needs_the_earlier_year_named_and_says_so(
    tmp_path: Path,
) -> None:
    """The cross-year form of the same hole, and the one with a measured cost.

    A 120-session window evaluated in January needs the previous year in `requirement.years`;
    with only the current year named, the visible panel is a handful of sessions and *every*
    security is `insufficient_history` -- which used to be a successful build that
    `write_factor_panels` stored. Here it is four sessions against a five-session window: refused
    with the years in the message, and answered as soon as the earlier year is named.
    """
    store = _two_year_store(tmp_path)
    definition = _probe(
        key="probe_needs_both_years",
        lookback_sessions=5,
        max_window_sessions=10,
        dataset=TWO_YEAR_DATASET,
        column="score",
    )
    evaluators = {
        definition.qualified_key: lambda window: window.series(TWO_YEAR_DATASET, "score")[0]
    }

    def build(*years: int) -> FactorPanel:
        return compute_factor(
            store,
            definition,
            as_of=TWO_YEAR_AS_OF,
            subjects=("000001.SZ",),
            universe=frozenset({"000001.SZ"}),
            requirements={TWO_YEAR_DATASET: _two_year_requirement(*years)},
            code_commit=COMMIT,
            built_at=BUILT_AT,
            evaluators=evaluators,
        )

    with pytest.raises(FactorEngineError, match=r"holds 3") as raised:
        build(2026)

    assert "[2026]" in str(raised.value)
    assert "earlier year named too" in str(raised.value)
    assert dict(build(2025, 2026).coverage_census())["computed"] == 1


def test_a_declared_freshness_bound_survives_the_cross_year_window_it_has_to_allow(
    tmp_path: Path,
) -> None:
    """The gap the P3 merge exposed, in both directions, at the plane that pays for it.

    The two fixes above are in tension and neither branch could see it alone. `V2-P3-002`'s C1
    made `compute_factor` refuse a waived `max_staleness` and made `read_visible_at` re-decide
    the bound over the rows it returns. `V2-P3-003`'s cross-year probe proved a window spanning
    a year end has to name the earlier year. Put together, the earlier partition's own reach is
    a look-back window behind `as_of` by construction -- so a bound decided per partition
    refuses every cross-year build, and the only bound wide enough to permit one is wide enough
    to re-admit the 172-day build C1 exists for.

    `max_staleness` bounds how old the newest input the read can see is, and
    `evaluate_readiness` has always decided that over every year the requirement names; the
    re-check now does the same. So both directions hold at once, and both are asserted here
    because a fix for either alone is indistinguishable from the bug in the other:

    - A five-day bound, ten sessions of window, a January `as_of` and the previous year named:
      **computed**, over a window whose first session is in the *earlier* partition
      (`input_session_first` is 2025-12-30, five sessions back from 2026-01-07). That is the
      assertion that would still hold if the bound were merely wide -- so it is paired with the
      refusal below rather than standing alone.
    - The same five-day bound over the same two years at an `as_of` half a year later, where
      December rows are still unpublished so the *filtered* read is genuinely taken: **refused**,
      naming `stale` and the reach the whole answer got to.
    """
    definition = _probe(
        key="probe_cross_year_freshness",
        lookback_sessions=5,
        max_window_sessions=10,
        dataset=TWO_YEAR_DATASET,
        column="score",
    )
    evaluators = {
        definition.qualified_key: lambda window: window.series(TWO_YEAR_DATASET, "score")[0]
    }

    def build(store: PanelStore, requirement: ReadinessRequirement) -> FactorPanel:
        return compute_factor(
            store,
            definition,
            as_of=requirement.as_of,
            subjects=("000001.SZ",),
            universe=frozenset({"000001.SZ"}),
            requirements={TWO_YEAR_DATASET: requirement},
            code_commit=COMMIT,
            built_at=BUILT_AT,
            evaluators=evaluators,
        )

    answered = build(_two_year_store(tmp_path / "fresh"), _two_year_requirement(2025, 2026))

    assert dict(answered.coverage_census())["computed"] == 1
    assert {item.year for item in answered.manifest.inputs} == {2025, 2026}
    (observation,) = answered.observations
    assert observation.input_session_first == date(2025, 12, 30)
    assert observation.input_session_last == date(2026, 1, 7)
    assert answered.values() == {"000001.SZ": 1.0}

    abandoned = _two_year_store(tmp_path / "abandoned", TWO_YEAR_ABANDONED_SESSIONS)
    with pytest.raises(
        FactorEngineError, match=r"restricted to the rows its requested years return reaches"
    ) as raised:
        build(
            abandoned,
            _two_year_requirement(2025, 2026, as_of=TWO_YEAR_ABANDONED_AS_OF),
        )

    assert "['not_yet_knowable', 'stale']" in str(raised.value)
    assert "2026-01-07" in str(raised.value)


def test_every_determinant_of_this_build_is_either_in_the_identity_or_exempted_by_name() -> None:
    """The audit, and the reason the table above cannot go stale.

    `compute_factor` has eight mandatory arguments and two optional ones, and the defect this
    exists for was two of them deciding the answers without reaching the identity. A test that
    varied a hand-written list of arguments would have passed just as happily while a ninth
    arrived -- which is the shape of drift `panel build`'s `_audit_written_partitions` and
    `_refuse_table_drift` were both written to close.

    So the parameters are read off the live signature and partitioned: every one is either in the
    "moves the ID" table or in `_IDENTITY_EXEMPT_ARGUMENTS` with a written reason. A new argument
    fails here until somebody classifies it.
    """
    parameters = set(inspect.signature(compute_factor).parameters)
    moved = {argument for argument, _ in _DETERMINANT_CASES}

    assert moved | set(_IDENTITY_EXEMPT_ARGUMENTS) == parameters
    assert not (moved & set(_IDENTITY_EXEMPT_ARGUMENTS))
    assert all(len(reason) > 40 for reason in _IDENTITY_EXEMPT_ARGUMENTS.values())


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
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
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
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
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
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
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
    stored = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert {reference.dataset for reference in references} == {OBSERVATIONS, MANIFESTS}
    assert sorted(stored, key=lambda item: item.subject) == sorted(
        result.observations, key=lambda item: item.subject
    )
    assert all(isinstance(item, FactorObservation) for item in stored)
    assert isinstance(result.manifest, FactorBuildManifest)


def test_the_stored_manifest_reassembles_into_the_build_it_was_filed_under(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`load_factor_manifests`, and the reason it exists rather than being a convenience.

    A stored build may not be dropped, so a caller adding an `as_of` to a year has to know what
    the year already holds -- and before this read existed the only way to know was to remember.
    That was the whole of the recovery path from a refused write.

    The reassembled build is asserted to be the *same* build, `manifest_id` included, which is
    what makes it usable as an answer: the function recomputes the identity from the stored rows
    and refuses a partition where the two disagree, so a decoder that dropped a field would fail
    here rather than hand back a build nobody ran.
    """
    result = _compute(store, panel)
    write_factor_panels(store, [result])

    (stored,) = load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert stored == result.manifest
    assert stored.manifest_id == result.manifest.manifest_id
    assert stored.subject_digest == result.manifest.subject_digest
    assert stored.direction == REVERSAL_1D.direction
    assert stored.max_window_sessions == REVERSAL_1D.max_window_sessions


def test_a_second_as_of_written_alongside_the_first_keeps_both(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A partition is replaced whole, so both cross sections have to reach the store in one
    call -- and once they have, the filtered read gives each `as_of` its own answer: reading at
    `MID_WINDOW` returns the earlier build's rows and not the later one's, because the later
    one was not knowable then."""
    early = _compute(store, panel, as_of=EARLY_WINDOW)
    late = _compute(store, panel)

    write_factor_panels(store, [early, late])
    at_mid = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    at_early = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=EARLY_WINDOW)

    assert {item.as_of for item in at_mid} == {EARLY_WINDOW, MID_WINDOW}
    assert {item.as_of for item in at_early} == {EARLY_WINDOW}
    assert len(at_mid) == 2 * len(panel.securities)


def test_a_later_call_adds_an_as_of_and_the_guard_still_refuses_a_restatement(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`V2-P4-071`: a second call **adds** an instant, and the guard keeps its own case.

    ## What this asserted before, and why it changed

    It required `write_factor_panels(store, [late])` -- one of two stored builds, re-supplied on
    its own -- to be refused, because the whole-partition replace would have dropped `early`. The
    refusal was real and it was the cost of a missing primitive rather than a rule anybody wanted:
    the only way to keep two instants in a year was to hand both to every write, forever.

    `panel_ingest.carry_stored_rows_forward` reads the partition's stored rows back and puts them
    in front of the arriving batch, so that call now keeps both and recomputes nothing. The
    product consequence is `tests/integration/test_shortlist_workflow.py`'s whole reason for
    existing: yesterday's cross section survives today's build, so two days' shortlists can be
    compared.

    ## What the guard still refuses

    A **restatement** -- the same `as_of` under a different `--code-commit`, which mints a
    different `manifest_id` and answers a question the year already has an answer to. That build
    collides on `event_time`, the stored one is therefore not carried, and
    `_refuse_to_drop_a_stored_build` refuses by name. The guard is unchanged; what changed is that
    it now audits the merge instead of instructing the caller to rebuild the year. The two tests
    below drive the rest of its surface: `supersedes` repairing a narrowed rebuild, and a
    `supersedes` naming nothing being a typo.
    """
    early = _compute(store, panel, as_of=EARLY_WINDOW)
    late = _compute(store, panel)
    write_factor_panels(store, [early, late])

    # The write this test used to require a refusal for. It keeps both.
    write_factor_panels(store, [late])
    assert {
        item.manifest_id
        for item in load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    } == {early.manifest.manifest_id, late.manifest.manifest_id}
    assert len(
        load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    ) == 2 * len(panel.securities)

    restated = _compute(store, panel, code_commit="9876543210fedcba")
    assert restated.manifest.manifest_id != late.manifest.manifest_id
    with pytest.raises(FactorEngineError, match="would drop"):
        write_factor_panels(store, [restated])
    assert len(
        load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    ) == 2 * len(panel.securities)


def test_a_rebuild_that_says_which_build_it_replaces_is_allowed_to_drop_it(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """ "A rebuild that supersedes an earlier build must name it" was the rule and there was no
    way to name one: the only route past the guard was to re-supply the superseded build, which
    is the opposite of superseding it.

    Here the narrowed rebuild answers the same `as_of` over a smaller cross section, so its
    `manifest_id` differs -- which it now does because the cross section is in the identity --
    and naming the old one is what lets the write through. The stored partition afterwards holds
    the new build and only the new build.
    """
    original = _compute(store, panel)
    write_factor_panels(store, [original])
    narrowed = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))

    with pytest.raises(FactorEngineError, match="load_factor_manifests"):
        write_factor_panels(store, [narrowed])
    write_factor_panels(store, [narrowed], supersedes=(original.manifest.manifest_id,))

    stored = load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    observations = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert [item.manifest_id for item in stored] == [narrowed.manifest.manifest_id]
    assert {item.subject for item in observations} == {"000001.SZ", "000002.SZ"}


def test_superseding_a_build_the_partition_does_not_hold_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A waiver that matches nothing is a typo, and a typo that is ignored turns the guard off
    for the write it arrived with -- which is this repository's standing rule about waivers
    (`_refuse_unexplained_thin_sessions`' halts, `ReadinessRequirement`'s four checks)."""
    write_factor_panels(store, [_compute(store, panel)])
    narrowed = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))

    with pytest.raises(FactorEngineError, match="which no partition this write touches holds"):
        write_factor_panels(store, [narrowed], supersedes=("fmn_never_stored",))


def test_two_builds_of_one_factor_at_one_as_of_are_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Two answers to one cross-section question would store two rows for every
    `(subject, as_of)` and leave a reader to choose between them -- and the drop guard cannot
    catch it, because nothing is dropped."""
    first = _compute(store, panel)
    second = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))

    with pytest.raises(FactorEngineError, match="more than one build of"):
        write_factor_panels(store, [first, second])


def test_dropping_a_security_is_refused_by_the_build_guard_rather_than_a_second_one(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Why there is one guard and not two, asserted as the pair of facts that decide it.

    An observation partition's subjects are securities, and a securities-level guard is wrong in
    both directions: it permits a write that drops a whole `as_of` while keeping the same names,
    and it refuses a rebuild that legitimately narrows the cross section -- which
    `test_a_rebuild_that_says_which_build_it_replaces_is_allowed_to_drop_it` is exactly. So the
    stored build list is the only guard, and it is complete for observations too, because
    `manifest_id` covers `subject_digest`: a write that drops a security necessarily changes a
    build's identity, which is what this asserts.
    """
    write_factor_panels(store, [_compute(store, panel)])
    narrowed = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))

    with pytest.raises(FactorEngineError, match=MANIFESTS) as raised:
        write_factor_panels(store, [narrowed])

    assert OBSERVATIONS not in str(raised.value)
    assert "supersedes" in str(raised.value)


def _holed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a hole in the merge, on the **observation** plane and nowhere else (`V2-P4-073`).

    `appended_to_the_stored_year`'s claim is that a `retain` rule with a hole in it -- "one that
    mis-reads `build_column`, or that drops a build it meant to keep" -- produces exactly the
    refusal it produced before, naming the builds that went missing. The probe that measured it
    false is this: the caller passes an `identity_columns` that over-matches, so every stored row
    whose *subject* appears in the arriving batch is treated as displaced and is not carried.

    Expressed through the function's own parameters rather than by rewriting its body, so what is
    under test is the audit and not a re-implementation of the rule. The manifest plane is left
    exactly as the writer built it, which is what makes the two partitions disagree: the manifest
    partition ends up holding both builds and the observation partition holding one.
    """
    real = panel_factors.appended_to_the_stored_year

    def holed(
        store: PanelStore,
        batch: ColumnarPanelBatch,
        year: int,
        *,
        build_column: str,
        identity_columns: Sequence[str],
        superseded: Collection[str],
    ) -> object:
        return real(
            store,
            batch,
            year,
            build_column=build_column,
            identity_columns=(
                identity_columns if build_column == SUBJECT_COLUMN_NAME else (SUBJECT_COLUMN_NAME,)
            ),
            superseded=superseded,
        )

    monkeypatch.setattr(panel_factors, "appended_to_the_stored_year", holed)


def test_a_merge_that_loses_a_stored_build_is_refused_at_write_time_and_names_it(
    store: PanelStore, panel: GeneratedPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-073`: the drop guard used to audit the manifest merge and only the manifest merge.

    `write_factor_panels` ran `_refuse_to_drop_a_stored_build` for `kind == FACTOR_MANIFEST_KIND`
    and for nothing else, so the observation merge was unaudited. Measured with the probe above:
    the second build **succeeded, exit 0, silently**; the manifest partition held both builds and
    the observation partition had lost day one's entire eight-row cross section. It surfaced only
    on the next read, from `_refuse_rows_that_are_not_the_answers_their_manifest_addresses`.

    That exemption was argued from "a write carrying every stored build carries every stored
    security **by construction**", which was true while the arriving batch *was* the whole
    partition and both datasets came out of one `panels` sequence. `V2-P4-071` ended that: the two
    partitions are assembled by independent `appended_to_the_stored_year` calls, so one can lose a
    build while the other does not.
    """
    early = _compute(store, panel, as_of=EARLY_WINDOW)
    write_factor_panels(store, [early])
    _holed(monkeypatch)

    with pytest.raises(FactorEngineError, match="would drop") as raised:
        write_factor_panels(store, [_compute(store, panel)])

    assert early.manifest.manifest_id in str(raised.value)
    assert OBSERVATIONS in str(raised.value)


def test_the_refused_merge_leaves_the_partition_exactly_as_it_found_it(
    store: PanelStore, panel: GeneratedPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal changes nothing at all -- `write_factor_panels`' "every guard runs before the
    first write", inherited by this one because the audit runs where the old guard runs.

    Asserted through the read rather than through a row count: the state the old arrangement left
    was a manifest partition and an observation partition that disagreed, and the read is what
    reports that. A store this refusal had half-written would come back with a `FactorEngineError`
    of its own here.
    """
    early = _compute(store, panel, as_of=EARLY_WINDOW)
    write_factor_panels(store, [early])
    _holed(monkeypatch)

    with pytest.raises(FactorEngineError):
        write_factor_panels(store, [_compute(store, panel)])

    monkeypatch.undo()
    observations = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    assert {item.subject for item in observations} == set(panel.securities)
    assert [
        item.manifest_id
        for item in load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    ] == [early.manifest.manifest_id]


def test_a_merge_that_loses_nothing_is_not_refused_by_the_new_audit(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The direction a fail-closed audit reaches for on its own, pinned against it.

    Three writes that legitimately drop nothing, or drop only what they named: a first write into
    an empty year, a second instant appended beside the first, and a rebuild that supersedes.
    `V2-P4-071`'s whole product consequence is the middle one, and an audit that refused it would
    have taken the append back out while reporting a fix.
    """
    early = _compute(store, panel, as_of=EARLY_WINDOW)
    write_factor_panels(store, [early])
    late = _compute(store, panel)
    write_factor_panels(store, [late])
    narrowed = _compute(store, panel, subjects=("000001.SZ", "000002.SZ"))
    write_factor_panels(store, [narrowed], supersedes=(late.manifest.manifest_id,))

    assert {
        item.manifest_id
        for item in load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    } == {early.manifest.manifest_id, narrowed.manifest.manifest_id}


def test_each_factor_writes_its_own_partitions_and_does_not_disturb_another_factors(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The memory argument, checked as behaviour rather than as a docstring.

    With one shared observation dataset, a partition was a *year of every factor* -- 17 factors
    times 244 as_ofs times 5,534 names is 22,955,032 observations that all had to be alive at
    once, three times over. Per-factor datasets take the unit of work back to one factor-year,
    and the observable form of that is this: writing the second factor neither refuses because
    of the first nor rewrites it.
    """
    second = _probe(key="probe_second_factor", lookback_sessions=2, max_window_sessions=2)
    write_factor_panels(store, [_compute(store, panel)])
    before = store.read_coverage(OBSERVATIONS, YEAR)

    write_factor_panels(
        store,
        [
            _compute(
                store,
                panel,
                definition=second,
                evaluators={
                    second.qualified_key: lambda window: window.series("daily", "close")[-1]
                },
            )
        ],
    )
    after = store.read_coverage(OBSERVATIONS, YEAR)
    other = store.read_coverage(factor_observation_dataset(second), YEAR)

    assert before is not None and after is not None and other is not None
    assert after.partition_content_hash == before.partition_content_hash
    assert after.fetched_at == before.fetched_at
    assert other.row_count == len(panel.securities)
    assert len(load_factor_observations(store, second, years=(YEAR,), as_of=MID_WINDOW)) == len(
        panel.securities
    )


def test_reading_back_takes_only_the_partition_of_the_factor_it_was_asked_for(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The read-side half of the same split. An earlier version narrowed one shared partition
    with a SQL equality on `factor_id`; the partition is now per factor, so a read of one factor
    does not open another one's file at all -- and asking for a factor nobody has computed is a
    missing partition rather than an empty scan."""
    write_factor_panels(store, [_compute(store, panel)])
    uncomputed = _probe(key="probe_never_computed", lookback_sessions=2, max_window_sessions=2)

    mine = load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert len(mine) == len(panel.securities)
    assert {item.factor_id for item in mine} == {REVERSAL_1D.factor_id}
    assert store.read_coverage(factor_observation_dataset(uncomputed), YEAR) is None
    for read in (load_factor_observations, load_factor_manifests):
        with pytest.raises(FactorEngineError, match="partition_missing"):
            read(store, uncomputed, years=(YEAR,), as_of=MID_WINDOW)


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
    coverage = store.read_coverage(OBSERVATIONS, YEAR)

    assert rewritten.content_hash == observations.content_hash
    assert coverage is not None
    assert coverage.fetched_at == datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def test_the_write_path_refuses_an_observation_that_went_round_the_constructor(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`domain/factor.py::validate_factor_observation`'s second call site, exercised where it
    matters: at the boundary between an in-memory object and a Parquet column.

    A `__post_init__` is a method and a frozen `slots=True` dataclass is still subclassable, so
    the constructor's rules are one `def __post_init__: pass` away from being off. This is the
    check that stops such a row becoming a stored value -- the same move `panel/catalog.py` made
    for its own records and argued at length.
    """
    result = _compute(store, panel)

    class Unchecked(FactorObservation):
        def __post_init__(self) -> None:
            return None

    smuggled = Unchecked(
        subject="000001.SZ",
        as_of=MID_WINDOW,
        value=float("inf"),
        coverage="computed",
        factor_id=REVERSAL_1D.factor_id,
        manifest_id=result.manifest.manifest_id,
        input_row_count=2,
        input_session_first=date(2026, 1, 8),
        input_session_last=date(2026, 1, 9),
    )
    poisoned = replace(result, observations=(smuggled, *result.observations[1:]))

    with pytest.raises(FactorError, match="non-finite"):
        write_factor_panels(store, [poisoned])
    assert store.read_coverage(OBSERVATIONS, YEAR) is None


def test_the_stored_observation_partition_passes_the_stores_own_readiness_contract(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The factor plane is the panel plane: the partition the engine writes is assessed by the
    same rule table as `daily`, with the same coverage record behind it. A derived dataset that
    could not clear its own readiness would be a second storage format wearing the first one's
    directory layout."""
    write_factor_panels(store, [_compute(store, panel)])

    readiness = store.assess_readiness(
        factor_observation_requirement(REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    )

    assert readiness.state == "ready"
    assert readiness.issues == ()
    assert readiness.checks_waived == ("required_dates", "required_subjects", "max_staleness")
    assert readiness.row_count == len(panel.securities)


# --- the seal, and the placeholder that makes it constructible (`V2-P3-019`) ----------------------


def test_no_observation_of_a_computed_panel_carries_the_unsealed_placeholder(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`compute_factor`'s one provisional value must not survive the call that creates it.

    `FactorBuildManifest.observation_digest` is a field, so the manifest cannot exist until the
    cross section does -- and every observation carries the `manifest_id` that field moves. One of
    the two has to be provisional for the length of the function, and the placeholder is the half
    that cannot reach the address, because the digest is over `(subject, coverage, value)` and
    never mentions an identity.

    Asserted against the module constant rather than against the string, so renaming the constant
    cannot quietly retire this check -- and asserted over *every* row rather than the first,
    because the re-stamp is a comprehension and a comprehension can be wrong for one branch of the
    classifier: the fixture's cross section carries `computed`, `not_in_universe` and
    `insufficient_history` rows, and the three come out of different `return` statements.
    """
    built = _compute(store, panel, universe=frozenset(panel.securities[:-1]))
    stamped = {observation.manifest_id for observation in built.observations}

    assert stamped == {built.manifest.manifest_id}
    assert _UNSEALED_MANIFEST_ID not in stamped
    assert len({observation.coverage for observation in built.observations}) > 1


def test_the_manifest_addresses_the_answers_and_moves_when_one_of_them_moves(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The field the whole of `V2-P3-019` rests on, at the layer that mints it.

    Two builds over the same partitions with the same parameters and *different answers* used to
    share a `manifest_id`; the substitution seam `compute_factor` already has for tests is what
    makes that reachable without editing a file. The evaluator is the only thing that differs, so
    every other determinant -- the cross section, the universe, the inputs, the commit -- is held
    fixed and the identity still has to move.

    `code_commit` would also have moved it and is not what is being measured: the point is that
    the *numbers* reach the address, not that a second determinant exists.
    """
    honest = _compute(store, panel)
    negated = _compute(
        store,
        panel,
        evaluators={REVERSAL_1D.qualified_key: lambda window: -_reversal(window)},
    )

    assert honest.manifest.observation_digest != negated.manifest.observation_digest
    assert honest.manifest.manifest_id != negated.manifest.manifest_id
    assert honest.manifest.subject_digest == negated.manifest.subject_digest
    assert honest.manifest.inputs == negated.manifest.inputs


def _reversal(window: Any) -> float:
    """`reversal_1d`'s own arithmetic over the fixture's closes, so the negation above is a
    changed *answer* rather than a changed shape: same coverage codes, same windows, opposite
    sign."""
    closes = window.series(DAILY_DATASET, "close")
    return float(closes[-1] / closes[-2] - 1.0)


def test_a_manifest_describing_a_build_whose_rows_are_gone_is_refused(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The direction the loader cannot reach through a tampered file on a one-build store.

    `_refuse_rows_that_are_not_the_answers_their_manifest_addresses` checks orphaned rows first,
    so on a store holding a single build any tamper that empties the observation side also
    orphans nothing and is caught by the row-count check one layer down instead. The rule still
    has to hold -- a partition can hold two builds and lose one -- so it is exercised directly,
    with the same inputs the loader would hand it.

    Through the real helper rather than a re-implementation: a test that restated the comparison
    would pass while the loader called something else.
    """
    built = _compute(store, panel)

    with pytest.raises(FactorEngineError, match="is missing every observation of build"):
        _refuse_rows_that_are_not_the_answers_their_manifest_addresses(
            (),
            dataset=OBSERVATIONS,
            build_of=lambda row: row.manifest_id,
            addressed={built.manifest.manifest_id: built.manifest.observation_digest},
            digest_of=observation_digest,
        )
