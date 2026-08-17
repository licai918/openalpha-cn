"""CLI/REST/SDK equivalence for the factor experiment face (`V2-P3-015`).

`V2-P1-016` established this file's shape for the panel plane and `V2-P0B-010` for outcome
validation: one question, driven through every face, answered identically. What this file adds is
the two things those two could not have, because the failures they were written before had not
been measured yet.

## 1. Equivalence is proved at a value the caller chose, never at a default

Task 39's finding, in full: an equivalence test that feeds *the same literal* to both faces proves
the two paths agree, and proves nothing about whether either of them carried the caller's value to
the judgement. It was found by measuring -- `sdk.py` hardcoded `exchange` and 1,815 tests stayed
green.

So `test_every_declared_run_parameter_reaches_the_answer_on_all_three_faces` varies **each of the
nineteen declared run parameters alone** and requires two things of every varied value at once:
that the answer moved away from the baseline, and that all three faces agree about what it moved
to. Splitting those two apart is not enough, and that is measured rather than argued -- an earlier
version of this file swept the parameters through the SDK alone and asserted the three-face
equality once at `BASELINE`, and a mutation replacing `retention_floor` with the literal `0.4` in
`cli.py` survived it. Task 39's shape had reappeared inside the test written to prevent it.

`--as-of` is the one parameter required *not* to move the answer, and that is a property rather
than an exemption: it is the instant the panel is read at, and the artifact's identity covers the
`as_of`s of the cross sections it found rather than the clock it looked at them from. Pinned by
`test_the_read_horizon_moves_no_number_it_only_decides_what_is_visible`.

## 2. Every key the faces render is held by the seal

`panel_view.py` renders 54 keys and 19 of them were never asserted; its line coverage was 100%.
This face renders none of its own -- `factor_view.experiment_view` ships the sealed document whole
-- so the per-key audit is `open_experiment`'s, applied to the body the faces actually hand out.
`test_every_key_the_three_faces_render_is_held_by_the_seal` walks every scalar leaf of the
response, perturbs exactly one at a time, and requires the result to fail to reopen.

## The frame

The generated eight-name panel, three stored tiers, and the **shipped** registry throughout: the
shipped `reversal_1d/v1`, its own evaluator, `cross_section_standard/v1` and
`industry_and_size/v1`. Nothing is substituted, which is what makes this the configuration an
operator would actually get -- including its consequence, which is measured rather than avoided:
both shipped derived specs declare `min_cross_section=100`, so on an eight-name market the
processed and neutralised tiers store a coverage code for every name and every attribution cell
reads `not_measured`. That is
`the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market`, and it is the honest
answer for this panel rather than a hole in this file --
`tests/integration/test_factor_run.py` is where a probe transform makes the acceptance
criterion's cell reachable and the declared floor is shown to decide it.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    DAILY_BASIC_DATASET,
    EXCHANGE,
    SECURITIES,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn.api.app import FACTOR_HTTP_STATUS, create_app
from openalpha_cn.backtest.factor_experiment import experiment_payload, open_experiment
from openalpha_cn.cli import ACCEPTANCE_MARKER, FACTOR_EXIT, UNMEASURED_WARNING, PanelExit, app
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.factor_view import (
    VIEW_SCHEMA_VERSION,
    FactorViewError,
    _board,
    _PanelInputs,
    experiment_view,
    factor_request,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    FACTOR_DEFINITIONS,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement
from openalpha_cn.panel_neutralization import (
    INDUSTRY_AND_SIZE,
    apply_factor_neutralization,
    load_industry_market_cap_cross_section,
    write_neutralized_factor_panels,
)
from openalpha_cn.sdk import OpenAlphaSDK

SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "industry.coverage_hole",
    "name_history.reform_prefixed_special_treatment",
)
"""Three shapes, and the third is there because a mutation survived without it.

Closes that move between sessions, so a one-day return is not zero; a second industry, so the
neutralisation's design is not one group; and a **special-treated name** from 2026-01-06, so the
bars this face prices are not all ordinary. Without the third,
`is_st=history.risk_warning_on(day) is not RiskWarning.none` could be replaced by `is_st=False`
and nothing went red -- see
`test_a_bar_this_face_prices_carries_the_stored_band_the_halt_state_and_the_warning`.
"""

SPECIAL_TREATED: Final[str] = SECURITIES[1]
"""`name_history.reform_prefixed_special_treatment` puts an `S*ST`-marked name on this one."""

STAR_BOARD: Final[str] = "688981.SH"
"""A STAR-board code in the generated universe: `_board` must not file it under `main`."""

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
"""The one shipped factor a ten-session panel can carry: `lookback_sessions=2`."""

CAP_BASE: Final[float] = 2_000_000.0
CAP_STEP: Final[float] = 750_000.0
"""The replacement `total_mv` grid. The fixture writes `1.0` on every row, which is a design with
no within-industry dispersion at all -- `degenerate_design` -- so the size regressor has to be
given a spread before a residual exists to report. `test_factor_neutralizations.py` replaces it
the same way and for the same reason."""

PREDICTION_DAYS: Final[tuple[date, ...]] = (date(2026, 1, 8), date(2026, 1, 9))
"""Two prediction days, which is `MINIMUM_IC_AS_OFS` and `MINIMUM_PORTFOLIO_PERIODS` exactly --
the smallest sample on which a mean, a sample standard deviation and one rebalance all exist.
Both sit inside the generated window, so each has stored sessions after it for its forward
return to be priced on."""

RUN_AS_OF: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""The instant the experiment is evaluated at: after every session the panel stores, so every
label window's exit is priced."""

BUILT_AT: Final[datetime] = datetime(2026, 1, 18, 2, 0, tzinfo=UTC)
COMMIT: Final[str] = "abcdef1234567"

BASELINE: Final[dict[str, Any]] = {
    "factor": "reversal_1d/v1",
    "transform": "cross_section_standard/v1",
    "neutralization": "industry_and_size/v1",
    "start": PREDICTION_DAYS[0],
    "end": PREDICTION_DAYS[-1],
    "as_of": RUN_AS_OF,
    "exchange": EXCHANGE,
    "horizon": "1d",
    "ic_method": "spearman",
    "min_securities": 4,
    "min_as_ofs": 2,
    "group_count": 2,
    "min_securities_per_group": 2,
    "position_capital": Decimal("100000"),
    "min_periods": 2,
    "participation_cap": Decimal("0.01"),
    "min_rebalances": 1,
    "redundancy_threshold": 0.8,
    "retention_floor": 0.4,
    "code_commit": COMMIT,
}
"""Every declared run parameter, once, so the three faces are driven from one dict rather than
from three literal argument lists that could drift apart -- which is the drift this whole file is
about, arriving in the file itself."""

VARIATIONS: Final[dict[str, Any]] = {
    "factor": "return_on_equity_ttm/v1",
    "transform": "cross_section_standard/v1",
    "neutralization": "industry_and_size/v1",
    "start": date(2026, 1, 9),
    "end": date(2026, 1, 8),
    "exchange": "SSE",
    "horizon": "2d",
    "ic_method": "pearson",
    "min_securities": 9,
    "min_as_ofs": 3,
    "group_count": 4,
    "min_securities_per_group": 5,
    "position_capital": Decimal("250000"),
    "min_periods": 3,
    "participation_cap": Decimal("0.5"),
    "min_rebalances": 2,
    "redundancy_threshold": 0.5,
    "retention_floor": 0.9,
    "code_commit": "0f1e2d3c4b5a6978",
}
"""A second value for every parameter of `BASELINE` except `as_of`, which is exempt by proof
rather than by omission -- see this module's docstring.

`transform` and `neutralization` map to themselves and are the two entries that are **not** a
variation, because this build declares exactly one of each: `FACTOR_TRANSFORMS` and
`FACTOR_NEUTRALIZATIONS` are one-element registries, so there is no second legal value to
send. `test_every_declared_run_parameter_reaches_the_answer` names them rather than skipping
them, so a second declared transform arrives with a variation missing rather than silently
untested.

`start` and `end` are swapped so the range runs backwards, which is `bad_request`; `factor` and
`exchange` name a factor and a calendar this store does not hold, which is `panel_unreadable`.
Two different rows of both channel tables, reached by three parameters, is the point of those
choices.
"""

LATER_PREDICTION_DAY: Final[date] = date(2026, 1, 14)
"""A third prediction day, outside `BASELINE`'s range and still inside the generated window.

The blocked cases are built on it rather than by leaving a tier unwritten, because the shape that
matters is the one a real store has: `panel_neutralization` writes its residuals at **one** later
instant for a whole year, so the partition exists and the in-range read comes back **empty**. A
fixture with no neutralised partition at all would be `panel_unreadable` instead -- a different
row of both tables, and not the one this issue is about.
"""

_TOKEN: Final[str] = "tushare-token-that-must-never-be-echoed-0123456789"

RENDERED_LEAF_COUNT: Final[int] = 309
"""How many scalar leaves the sealed document a face hands out actually has, on `BASELINE`.

Pinned rather than bounded, and the reason is `panel_view.py`'s measurement: 54 rendered keys with
100% line coverage, 19 of them never asserted. A `>=` bound here would let a key vanish from the
transport and the per-key audit would go on passing on a narrower document. An exact count makes
a key gained or lost a failure that says so -- and every one of these 309 is separately falsified
by `test_every_key_the_three_faces_render_is_held_by_the_seal`.
"""


def _panel() -> GeneratedPanel:
    """The generated panel with a market capitalisation that actually varies."""
    built = generate_panel(shapes=SHAPES)
    batch = built.batch(DAILY_BASIC_DATASET)
    caps = tuple(CAP_BASE + CAP_STEP * SECURITIES.index(str(item)) for item in batch.subjects)
    columns = tuple(
        PanelColumn(column.name, column.kind, caps) if column.name == "total_mv" else column
        for column in batch.columns
    )
    return dataclasses.replace(
        built,
        batches={**built.batches, DAILY_BASIC_DATASET: dataclasses.replace(batch, columns=columns)},
    )


def store_three_tiers(
    runtime_dir: Path,
    *,
    prediction_days: Sequence[date] = PREDICTION_DAYS,
    neutralized_days: Sequence[date] | None = None,
    evaluator: Callable[[Any], float | None] | None = None,
    transform: Any = CROSS_SECTION_STANDARD,
    neutralization: Any = INDUSTRY_AND_SIZE,
) -> PanelStore:
    """Write one generated panel and one factor's three stored tiers into `runtime_dir/panel`.

    The tiers are built by the real `compute_factor`, `apply_factor_transform` and
    `apply_factor_neutralization` and written by the real three writers, so every write-time guard
    runs. Two things are substituted and each is named:

    - **`evaluator`**, `compute_factor`'s own documented substitution seam. `None` uses the
      shipped table, which is what every test in this file does.
    - **the neutralisation's second cross section is re-stamped at the panel's own instant.** It
      is read off the real store through the real `load_industry_market_cap_cross_section`, at an
      `as_of` after the whole panel, and then stamped with the prediction day's instant --
      because that loader reads `daily_basic` through `read_if_ready` and therefore cannot be
      called at a mid-window `as_of` at all. That is not a defect in this fixture: it is
      `the_three_tiers_must_have_been_built_at_the_same_instants` itself, and
      `test_the_neutralisation_loader_really_refuses_the_mid_window_as_of_this_fixture_works_around`
      drives the refusal this fixture works around so the claim is measured rather than asserted
      here in prose.

    `neutralized_days` defaults to `prediction_days`; naming a subset is how the blocked case is
    built -- a raw tier the neutralised tier does not cover.
    """
    panel = _panel()
    store = PanelStore(runtime_dir / "panel")
    write_generated_panel(store, panel)
    calendar = panel.calendar()
    residual_days = prediction_days if neutralized_days is None else neutralized_days
    raws, processed, neutralized = [], [], []
    for day in prediction_days:
        as_of = datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)
        built = compute_factor(
            store,
            REVERSAL,
            as_of=as_of,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements={
                "daily": daily_requirement(
                    calendar, years=(YEAR,), as_of=as_of, max_staleness=timedelta(days=30)
                )
            },
            code_commit=COMMIT,
            built_at=as_of,
            evaluators=None if evaluator is None else {REVERSAL.qualified_key: evaluator},
        )
        raws.append(built)
        transformed = apply_factor_transform(built, transform, code_commit=COMMIT, built_at=as_of)
        processed.append(transformed)
        if day not in residual_days:
            continue
        section = load_industry_market_cap_cross_section(
            store,
            neutralization,
            subjects=panel.securities,
            day=day,
            as_of=RUN_AS_OF,
            calendar=calendar,
            membership_years=(YEAR,),
            max_staleness=None,
        )
        neutralized.append(
            apply_factor_neutralization(
                transformed,
                neutralization,
                dataclasses.replace(section, as_of=as_of),
                code_commit=COMMIT,
                built_at=as_of,
            )
        )
    write_factor_panels(store, raws)
    write_processed_factor_panels(store, processed)
    if neutralized:
        write_neutralized_factor_panels(store, neutralized)
    return store


@pytest.fixture
def runtime_dir(tmp_path: Path) -> Path:
    store_three_tiers(tmp_path)
    return tmp_path


def _resolved(parameters: dict[str, Any]) -> Any:
    """One parameter dict through the same resolver the three faces use."""
    return factor_request(**parameters)


def _sdk(runtime_dir: Path) -> OpenAlphaSDK:
    return OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: BUILT_AT)


def _rest(runtime_dir: Path) -> TestClient:
    return TestClient(create_app(runtime_dir=runtime_dir, clock=lambda: BUILT_AT))


def _json_body(parameters: dict[str, Any]) -> dict[str, Any]:
    """One parameter dict as the HTTP body, with no key invented and none dropped."""
    return {
        name: (
            value.isoformat()
            if isinstance(value, datetime | date)
            else str(value)
            if isinstance(value, Decimal)
            else value
        )
        for name, value in parameters.items()
    }


def _cli_arguments(runtime_dir: Path, parameters: dict[str, Any]) -> list[str]:
    """One parameter dict as the command line, keyed off the dict rather than hand-written."""
    flags = ["factor", "run", "--runtime-dir", str(runtime_dir), "--json"]
    for name, value in parameters.items():
        flags.append("--" + name.replace("_", "-"))
        flags.append(value.isoformat() if isinstance(value, datetime | date) else str(value))
    return flags


def sdk_answer(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """The SDK's answer, or the fault it raised, in one comparable shape."""
    sdk = _sdk(runtime_dir)
    try:
        record, write = sdk.run_factor_experiment(**parameters)
    except FactorViewError as error:
        # Two messages, because the split is the design: `disclosable` may cross a process
        # boundary and `str(error)` names the store, which an in-process caller configured.
        return {"reason": error.reason, "message": error.disclosable, "local": str(error)}
    return experiment_view(record, write=write)


def rest_answer(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """The REST face's answer, or the fault it enveloped, in the same shape."""
    response = _rest(runtime_dir).post("/api/v1/factors/run", json=_json_body(parameters))
    if response.status_code == FACTOR_HTTP_STATUS["answered"]:
        return dict(response.json())
    detail = response.json()["detail"]
    return {
        "reason": detail["reason"],
        "message": detail["message"],
        "status": response.status_code,
    }


def cli_answer(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """The CLI's answer, or the fault it exited on, in the same shape."""
    result = CliRunner().invoke(app, _cli_arguments(runtime_dir, parameters))
    if result.exit_code == int(PanelExit.ok):
        return dict(json.loads(result.stdout))
    return {"exit_code": result.exit_code, "stderr": result.stderr}


def assert_three_faces_agree(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """Drive one parameter dict through all three faces and require one answer. Return the SDK's.

    Written as a helper because it is used at **twenty** different parameter values rather than at
    one, which is the whole of this file's answer to Task 39: a face that hardcoded any single
    parameter would agree with the others only at the value it hardcoded, so an equivalence
    asserted once -- at whatever the test happened to send -- proves nothing about the other
    eighteen. `test_every_declared_run_parameter_reaches_the_answer_on_all_three_faces` calls this
    at every varied value in `VARIATIONS`, and a mutation that hardcoded `retention_floor` in
    `cli.py` survived the version of this file that did not.

    `write` is excluded from the comparison and is the one key that legitimately differs: the
    first face to run creates the document and the other two find it already held. That is
    `refuse_a_restated_experiment`'s admitted direction arriving at the store, and it is *itself*
    an equality proof -- a single differing byte would have been refused rather than reported
    `unchanged`.

    A refusal is compared in each channel's own vocabulary, because that is what a channel has:
    the same `reason`, the same disclosable message, the status code its own table gives that
    reason and the exit code its own table gives it.
    """
    first = sdk_answer(runtime_dir, parameters)
    second = rest_answer(runtime_dir, parameters)
    third = cli_answer(runtime_dir, parameters)

    if "document" in first:
        without_write = {key: value for key, value in first.items() if key != "write"}
        assert {key: value for key, value in second.items() if key != "write"} == without_write
        assert {key: value for key, value in third.items() if key != "write"} == without_write
        assert (second["write"], third["write"]) == ("unchanged", "unchanged")
        return first
    reason = first["reason"]
    assert second["reason"] == reason
    assert second["message"] == first["message"]
    assert second["status"] == FACTOR_HTTP_STATUS[reason]
    assert third["exit_code"] == int(FACTOR_EXIT[reason])
    # The CLI is inside the process that owns the store, so it prints the *local* message; the
    # HTTP body gets `disclosable`. Comparing each against the one its own channel is entitled to
    # is what keeps this equality from quietly requiring the two to be the same string.
    assert first["local"] in third["stderr"]
    return first


# --- one request, three faces, one answer ------------------------------------------------------


def test_the_three_faces_seal_one_experiment_from_one_request(runtime_dir: Path) -> None:
    """The whole point of the file: one document, three ways in.

    Driven at `BASELINE`, every value of which is stated in this module rather than defaulted by
    any face -- see this module's docstring on Task 39. The three answers are compared for
    **equality of the whole body**, not of a summary of it: `experiment_view`'s envelope carries
    the sealed document, so equality here is equality of every number in the report.

    The `write` key is the one that legitimately differs and it is asserted rather than excluded:
    the first face to run creates the document and the other two find it already held, which is
    `refuse_a_restated_experiment`'s "an identical answer is admitted" arriving at the store. That
    the second and third runs are `unchanged` is the proof that all three produced byte-identical
    payloads -- a single differing byte would have been refused instead.
    """
    first = sdk_answer(runtime_dir, BASELINE)
    second = rest_answer(runtime_dir, BASELINE)
    third = cli_answer(runtime_dir, BASELINE)

    assert first["write"] == "created"
    assert second["write"] == "unchanged"
    assert third["write"] == "unchanged"
    assert first["schema_version"] == VIEW_SCHEMA_VERSION
    assert {key: value for key, value in second.items() if key != "write"} == {
        key: value for key, value in first.items() if key != "write"
    }
    assert {key: value for key, value in third.items() if key != "write"} == {
        key: value for key, value in first.items() if key != "write"
    }
    # The document is really an experiment, and it really came off this panel.
    document = first["document"]
    assert document["artifact"]["spec"]["ic"]["definition"]["key"] == "reversal_1d"
    assert [row["tier"] for row in document["artifact"]["tiers"]] == [
        "raw",
        "processed",
        "neutralized",
    ]
    assert len(document["artifact"]["attributions"]) == 6


def test_the_raw_tier_is_measured_off_the_stored_panel_and_the_derived_tiers_are_not(
    runtime_dir: Path,
) -> None:
    """The shipped configuration's own answer on an eight-name market, as a number.

    The raw tier is `measured` with a mean rank IC of exactly `-1.0`: the generated grid moves
    every close by the same half yuan a session, so a one-session return is `0.5 / previous
    close` and orders the cross section exactly inversely to the next session's return.
    `reversal_1d` declares `lower_is_better`, so its oriented IC is that ordering negated -- and
    `-1.0` is what a factor that anti-predicts perfectly looks like.

    Both derived tiers are `insufficient_as_ofs` and every cell is `not_measured`, because
    `CROSS_SECTION_STANDARD` and `INDUSTRY_AND_SIZE` both declare `min_cross_section=100` and this
    market has eight names. Asserted as an exact expectation rather than left implicit, because it
    is the measurement behind
    `the_shipped_transform_and_neutralisation_floors_exceed_a_thin_market` -- and because a face
    that quietly reported a *measured* processed tier here would be one that had lowered somebody
    else's floor.
    """
    document = sdk_answer(runtime_dir, BASELINE)["document"]
    tiers = {row["tier"]: row for row in document["artifact"]["tiers"]}

    assert tiers["raw"]["ic"]["coverage"] == "measured"
    assert tiers["raw"]["ic"]["mean_ic"] == -1.0
    assert tiers["processed"]["ic"]["coverage"] == "insufficient_as_ofs"
    assert tiers["neutralized"]["ic"]["coverage"] == "insufficient_as_ofs"
    assert {cell["verdict"] for cell in document["artifact"]["attributions"]} == {"not_measured"}


# --- the parameter really reaches the judgement -------------------------------------------------


def test_the_sweep_below_covers_every_declared_run_parameter() -> None:
    """The sweep is a table, and this is the run of it.

    A parameter added to the face without a row in `VARIATIONS` would leave the sweep silently
    one short -- the exact shape `test_the_registry_table_is_every_known_registry_in_the_source_
    tree` exists to stop one plane over. `as_of` is the one deliberate absence and is named here
    rather than skipped inside the sweep, because "this parameter must **not** move the answer" is
    a different claim with its own test.

    `BASELINE` is itself checked against the face's own signature, so a nineteenth parameter added
    to `factor_request` arrives here as a failure rather than as an untested option.
    """
    declared = set(inspect.signature(factor_request).parameters) - {
        "factors",
        "transforms",
        "neutralizations",
    }
    assert set(BASELINE) == declared
    assert set(VARIATIONS) | {"as_of"} == set(BASELINE)
    assert "as_of" not in VARIATIONS


def test_every_declared_run_parameter_reaches_the_answer_on_all_three_faces(
    runtime_dir: Path,
) -> None:
    """Vary one declared parameter alone; the answer must move, and all three faces must move.

    Task 39's antidote in the only form that works, and the form is the finding. An earlier
    version of this file did the two halves separately -- a per-parameter sweep through the SDK,
    and one three-face equality at `BASELINE` -- and a mutation that replaced `retention_floor`
    with the literal `0.4` in `cli.py` **survived it**: the sweep never asked the CLI, and the
    equality was asserted at exactly the value the mutant hardcoded. That is Task 39's shape
    reappearing inside the test written to prevent it.

    So every varied value goes through every face. Nineteen parameters, one loop, and each
    iteration asserts both halves at once: the answer moved away from the baseline, and the three
    faces agree about what it moved to. A face that hardcodes any one parameter now disagrees with
    the other two on that parameter's own iteration.

    `transform` and `neutralization` map to themselves because this build declares one of each, so
    there is no second legal value to send; the branch below states that rather than dropping
    them, so a second declared transform arrives here as a failure asking for a variation. The
    three faces are still driven at that value, because "the CLI hardcodes the only legal
    transform" is exactly as invisible as the mutation above.
    """
    baseline = assert_three_faces_agree(runtime_dir, BASELINE)

    unmoved = []
    for parameter, value in sorted(VARIATIONS.items()):
        varied = assert_three_faces_agree(runtime_dir, {**BASELINE, parameter: value})
        if value == BASELINE[parameter]:
            assert parameter in {"transform", "neutralization"}
            assert varied.get("experiment_id") == baseline["experiment_id"]
            continue
        if varied == baseline:
            unmoved.append(parameter)

    assert unmoved == [], f"{unmoved} did not reach the answer"


def test_the_three_faces_move_together_when_a_parameter_moves(runtime_dir: Path) -> None:
    """The other half: the three faces agree at a value that is nobody's default.

    Two parameters at once, both away from `BASELINE`: a `pearson` correlation instead of a
    `spearman` one, and a two-session forward window instead of a one-session one. Both move the
    raw tier's numbers, so an equality here is an equality about numbers this test chose rather
    than about numbers a face could have invented.
    """
    parameters = {**BASELINE, "ic_method": "pearson", "horizon": "2d"}
    first = sdk_answer(runtime_dir, parameters)
    second = rest_answer(runtime_dir, parameters)
    third = cli_answer(runtime_dir, parameters)

    baseline = sdk_answer(runtime_dir, BASELINE)
    assert first["experiment_id"] != baseline["experiment_id"]
    assert first["document"]["artifact"]["tiers"][0]["ic"]["method"] == "pearson"
    assert first["document"]["artifact"]["spec"]["horizon_sessions"] == 2
    assert {key: value for key, value in second.items() if key != "write"} == {
        key: value for key, value in first.items() if key != "write"
    }
    assert {key: value for key, value in third.items() if key != "write"} == {
        key: value for key, value in first.items() if key != "write"
    }


def test_the_range_bounds_the_sample_and_a_stored_cross_section_outside_it_is_not_in_it(
    tmp_path: Path,
) -> None:
    """`--start`/`--end` really select, and a third stored day proves it.

    The store holds three prediction days on all three tiers; the run asks about two. Without the
    range filter the experiment would silently be over three -- a *wider* sample than the caller
    requested, which is the direction nothing else in this file can see: every other fixture holds
    exactly the days it asks about, so dropping the filter changes nothing on them.

    The two-day experiment is required to be byte-identical to the one this file's own fixture
    produces, so "the range selected" and "the range happened to match" are told apart: the same
    `experiment_id`, the same `content_digest`, and the store reports `unchanged` because the
    document was already there.
    """
    store_three_tiers(tmp_path, prediction_days=(*PREDICTION_DAYS, LATER_PREDICTION_DAY))

    narrowed = sdk_answer(tmp_path, BASELINE)
    widened = sdk_answer(tmp_path, {**BASELINE, "end": LATER_PREDICTION_DAY})

    assert len(narrowed["document"]["artifact"]["tiers"][0]["ic"]["as_ofs"]) == 2
    assert len(widened["document"]["artifact"]["tiers"][0]["ic"]["as_ofs"]) == 3
    assert narrowed["experiment_id"] != widened["experiment_id"]
    sample = narrowed["document"]["artifact"]["tiers"][0]["ic"]["as_ofs"]
    assert [instant[:10] for instant in sample] == [day.isoformat() for day in PREDICTION_DAYS]


def test_the_read_horizon_moves_no_number_it_only_decides_what_is_visible(
    runtime_dir: Path,
) -> None:
    """`--as-of` is a read horizon, and two horizons that see the same rows give one experiment.

    The one parameter of the nineteen that must **not** move the answer while it succeeds, and it
    is a claim rather than an omission: `FactorExperimentSpec` covers the `as_of`s of the cross
    sections the run found (`as_of_digest`) and the builds it read them from, and deliberately not
    the clock it looked at them from -- because two runs of one declaration over one panel have to
    reproduce one identity or the identity cannot be used to detect drift.

    **What it does decide is whether the panel can answer at all**, and that half is pinned in the
    same test because the two are easy to confuse. `load_daily_bars` derives its `required_dates`
    from the calendar up to the `as_of` it is asked at, so a horizon a week later demands five
    sessions this panel never stored and comes back `date_gap` -- `panel_unreadable`, naming the
    first missing session. A face that answered a *shorter* experiment there would be reporting a
    factor over a sample the caller did not ask for.
    """
    same_horizon = {**BASELINE, "as_of": RUN_AS_OF + timedelta(hours=8)}
    beyond_the_panel = {**BASELINE, "as_of": RUN_AS_OF + timedelta(days=7)}

    first = sdk_answer(runtime_dir, BASELINE)
    second = sdk_answer(runtime_dir, same_horizon)
    third = sdk_answer(runtime_dir, beyond_the_panel)

    assert second["experiment_id"] == first["experiment_id"]
    assert second["content_digest"] == first["content_digest"]
    assert second["write"] == "unchanged"
    assert second["document"] == first["document"]
    assert third["reason"] == "panel_unreadable"
    assert "date_gap" in third["message"]


# --- blocked is not a 2xx, on any of the three faces --------------------------------------------


def test_a_range_whose_neutralised_tier_was_never_built_is_refused_the_same_way_on_all_three_faces(
    tmp_path: Path,
) -> None:
    """The issue's own question: what does a range that ends before the neutralisation do?

    This store holds a raw and a processed cross section at all three prediction days and a
    residual at the **latest** one alone -- which is the shape a real store has, because
    `load_industry_market_cap_cross_section` reads `daily_basic` through the unfiltered door and
    can therefore only be called at an instant at or after the last stored session, so a whole
    year's residuals are stamped at one late instant. The run asks about the two earlier days.

    The neutralised partition therefore **exists** and its rows are outside the range, so reading
    it back through `read_visible_at` comes back **empty rather than raising** -- the shape that
    makes this dangerous, because a face that shrugged would answer a three-tier report whose
    third row measured nothing, and that row is the one the acceptance criterion is decided on.

    All three faces refuse, in their own envelope and with the same message: `409` on HTTP, exit
    `1` on the command line, `FactorRunBlockedError` in process. The message names the instants
    that have no residual, because a caller told only "blocked" cannot act on it.
    """
    store_three_tiers(
        tmp_path,
        prediction_days=(*PREDICTION_DAYS, LATER_PREDICTION_DAY),
        neutralized_days=(LATER_PREDICTION_DAY,),
    )

    sdk = sdk_answer(tmp_path, BASELINE)
    rest = rest_answer(tmp_path, BASELINE)
    cli = cli_answer(tmp_path, BASELINE)

    assert sdk["reason"] == "blocked"
    assert "2026-01-08T09:00:00+00:00" in sdk["message"]
    assert "industry_and_size/v1" in sdk["message"]
    assert rest["status"] == FACTOR_HTTP_STATUS["blocked"] == 409
    assert rest["reason"] == "blocked"
    assert rest["message"] == sdk["message"] == sdk["local"]
    assert cli["exit_code"] == int(FACTOR_EXIT["blocked"]) == int(PanelExit.unhealthy) == 1
    assert sdk["message"] in cli["stderr"]


def test_a_refused_run_stores_nothing_at_all(tmp_path: Path) -> None:
    """A refusal is not a 2xx *and* it is not a write.

    `V2-P1-013`'s empty success has a second shape on a face that persists: an endpoint that
    refused and still left a document behind would let a later `GET` serve an experiment no run
    ever answered with. The store is asked directly, through the SDK's own listing.
    """
    store_three_tiers(
        tmp_path,
        prediction_days=(*PREDICTION_DAYS, LATER_PREDICTION_DAY),
        neutralized_days=(LATER_PREDICTION_DAY,),
    )

    answer = sdk_answer(tmp_path, BASELINE)

    assert answer["reason"] == "blocked"
    assert _sdk(tmp_path).list_factor_experiments() == ()
    assert _rest(tmp_path).get("/api/v1/factors/experiments").json() == {"experiment_ids": []}


def test_a_partially_built_neutralised_tier_names_the_missing_instant_rather_than_shrinking(
    tmp_path: Path,
) -> None:
    """Half a tier is refused too, and the refusal says which half.

    The sharper case: the residuals exist for the first prediction day and not the second. A face
    that intersected the three tiers' instants would answer a perfectly well-formed one-day
    experiment -- with `min_as_ofs=2` silently unmet on a sample the caller never asked for. The
    refusal names the missing instant instead.
    """
    store_three_tiers(
        tmp_path,
        prediction_days=(*PREDICTION_DAYS, LATER_PREDICTION_DAY),
        neutralized_days=(PREDICTION_DAYS[0], LATER_PREDICTION_DAY),
    )

    answer = sdk_answer(tmp_path, BASELINE)

    assert answer["reason"] == "blocked"
    assert "2026-01-09T09:00:00+00:00" in answer["message"]
    assert "2026-01-08T09:00:00+00:00" not in answer["message"]


def test_the_neutralisation_loader_really_refuses_the_mid_window_as_of_this_fixture_works_around(
    tmp_path: Path,
) -> None:
    """The fact the whole tier-schedule rule rests on, driven rather than quoted.

    `store_three_tiers` re-stamps the second cross section because
    `load_industry_market_cap_cross_section` cannot be called at a prediction day's own instant.
    That claim is load-bearing -- it is why a neutralised series inside a covered year does not
    exist -- so it is measured here: the same call, at the same day, at the prediction day's own
    `as_of`, refuses on `daily_basic`.
    """
    panel = _panel()
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)

    with pytest.raises(Exception, match="not_yet_knowable"):
        load_industry_market_cap_cross_section(
            store,
            INDUSTRY_AND_SIZE,
            subjects=panel.securities,
            day=PREDICTION_DAYS[0],
            as_of=datetime(2026, 1, 8, 9, 0, tzinfo=UTC),
            calendar=panel.calendar(),
            membership_years=(YEAR,),
            max_staleness=None,
        )


def test_a_request_that_cannot_be_put_is_a_different_row_on_every_face(runtime_dir: Path) -> None:
    """`bad_request` and the two panel rows are different rows, and all three faces keep them apart.

    A range that runs backwards cannot be put at all -- no build fixes it, so it is `422` and exit
    `3` -- while a factor this store has never held is a statement about the store, and is
    `panel_unreadable` rather than `blocked`: its observation *partition* is absent, which is one
    step earlier than a partition that exists and holds nothing in range. Collapsing any two of
    the three would tell a scheduled job to re-fetch for a typo, or to file a bug for a factor
    nobody has built yet.
    """
    backwards = {**BASELINE, "start": VARIATIONS["start"], "end": VARIATIONS["end"]}

    sdk = sdk_answer(runtime_dir, backwards)
    rest = rest_answer(runtime_dir, backwards)
    cli = cli_answer(runtime_dir, backwards)

    assert sdk["reason"] == "bad_request"
    assert rest["status"] == FACTOR_HTTP_STATUS["bad_request"] == 422
    assert cli["exit_code"] == int(FACTOR_EXIT["bad_request"]) == int(PanelExit.bad_request) == 3

    unbuilt = {**BASELINE, "factor": VARIATIONS["factor"]}
    unbuilt_answer = sdk_answer(runtime_dir, unbuilt)
    assert unbuilt_answer["reason"] == "panel_unreadable"
    assert "return_on_equity_ttm" in unbuilt_answer["message"]
    assert rest_answer(runtime_dir, unbuilt)["status"] == FACTOR_HTTP_STATUS["panel_unreadable"]
    assert cli_answer(runtime_dir, unbuilt)["exit_code"] == int(FACTOR_EXIT["panel_unreadable"])


def test_an_exchange_this_store_never_held_is_panel_unreadable_and_not_a_silent_answer(
    runtime_dir: Path,
) -> None:
    """The parameter Task 39 measured, on the face that measured it.

    `exchange` decides which stored calendar the label windows are cut against, so a face that
    hardcoded it would answer the same numbers whatever the caller sent. This store holds SZSE
    alone: `SSE` has to be a refusal, and the refusal has to name the exchange.
    """
    answer = sdk_answer(runtime_dir, {**BASELINE, "exchange": "SSE"})

    assert answer["reason"] == "panel_unreadable"
    assert "SSE" in answer["message"]
    assert rest_answer(runtime_dir, {**BASELINE, "exchange": "SSE"})["status"] == 409


# --- every rendered key is held by the seal -----------------------------------------------------


def _leaf_paths(node: object, prefix: tuple[str | int, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaf_paths(value, (*prefix, index))
    else:
        yield prefix


def _at(node: Any, path: Sequence[Any]) -> Any:
    for step in path:
        node = node[step]
    return node


def _perturb(value: object) -> object:
    """One scalar changed to a different scalar of a shape the contract would still admit."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, str):
        return value + "x"
    return "perturbed"


def test_every_key_the_three_faces_render_is_held_by_the_seal(runtime_dir: Path) -> None:
    """Walk every scalar leaf of the rendered body, perturb one, require a refusal.

    `V2-P3-014` installed this on the record; this is the same audit on **what the faces actually
    hand out**, which is the direction 100% line coverage is worth nothing in -- `panel_view.py`
    rendered 54 keys with full line coverage and 19 of them were never asserted.

    It is possible here at all because `experiment_view` ships the sealed document rather than a
    rendering of its own: every key of `document` is inside `content_digest`, so a perturbation
    anywhere in it makes `open_experiment` refuse. The envelope's own four keys are checked
    separately below, because the seal does not cover them -- which is exactly why they have to be
    checked separately rather than counted as covered.
    """
    body = sdk_answer(runtime_dir, BASELINE)
    document = body["document"]
    paths = list(_leaf_paths(document))

    assert len(paths) == RENDERED_LEAF_COUNT, (
        f"the rendered document now has {len(paths)} scalar leaves and this audit expected "
        f"{RENDERED_LEAF_COUNT}; a leaf gained or lost is a change to what the faces hand out"
    )
    survivors = []
    for path in paths:
        edited = json.loads(json.dumps(document))
        parent = _at(edited, path[:-1])
        parent[path[-1]] = _perturb(_at(document, path))
        payload = json.dumps(edited, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            open_experiment(payload)
        except Exception:
            continue
        survivors.append(path)

    assert survivors == [], f"{len(survivors)} rendered key(s) the seal does not hold: {survivors}"


def test_the_envelope_keys_are_each_separately_falsifiable(runtime_dir: Path) -> None:
    """The four keys the seal does not cover, held one at a time.

    `schema_version` is this envelope's own and is not any of the document's three;
    `experiment_id` and `content_digest` are projections a caller can recompute from `document`;
    `write` is the store's answer and is the one key that legitimately differs between two
    otherwise identical responses.
    """
    first = sdk_answer(runtime_dir, BASELINE)
    again = sdk_answer(runtime_dir, BASELINE)
    record = open_experiment(
        json.dumps(first["document"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

    assert set(first) == {
        "schema_version",
        "experiment_id",
        "content_digest",
        "write",
        "document",
    }
    assert first["schema_version"] == VIEW_SCHEMA_VERSION != record.artifact.schema_version
    assert first["experiment_id"] == record.experiment_id
    assert first["content_digest"] == record.content_digest
    assert (first["write"], again["write"]) == ("created", "unchanged")
    assert json.loads(experiment_payload(record)) == first["document"]


def test_the_stored_document_is_the_one_the_faces_returned(runtime_dir: Path) -> None:
    """What a face hands out and what the store keeps cannot come apart.

    Both `GET /api/v1/factors/experiments/{id}` and `OpenAlphaSDK.get_factor_experiment` reopen
    the held document through `open_experiment`, so this also proves the stored bytes still hash
    to their own seal after a round trip through the filesystem.
    """
    body = sdk_answer(runtime_dir, BASELINE)
    experiment_id = body["experiment_id"]

    served = _rest(runtime_dir).get(f"/api/v1/factors/experiments/{experiment_id}")
    reopened = _sdk(runtime_dir).get_factor_experiment(experiment_id)

    assert served.status_code == 200
    assert served.json()["document"] == body["document"]
    assert reopened is not None
    assert reopened.content_digest == body["content_digest"]
    assert _sdk(runtime_dir).list_factor_experiments() == (experiment_id,)
    assert _rest(runtime_dir).get("/api/v1/factors/experiments").json() == {
        "experiment_ids": [experiment_id]
    }


def test_an_unheld_experiment_is_a_404_and_a_malformed_key_is_not(runtime_dir: Path) -> None:
    """The only `404` this face has, and the row that is deliberately not one.

    A key that is not `stable_model_id`'s own output never named a resource in the first place --
    it is a filename rather than a content address -- so it is `bad_request`, and the store's
    shape check is what makes it impossible for a key to reach the filesystem unchecked.
    """
    client = _rest(runtime_dir)

    missing = client.get("/api/v1/factors/experiments/fxp_000000000000000000000000")
    malformed = client.get("/api/v1/factors/experiments/etc-passwd")

    assert missing.status_code == FACTOR_HTTP_STATUS["not_found"] == 404
    assert missing.json()["detail"]["reason"] == "not_found"
    assert malformed.status_code == FACTOR_HTTP_STATUS["bad_request"]
    assert _sdk(runtime_dir).get_factor_experiment("fxp_000000000000000000000000") is None


# --- the credential never travels ---------------------------------------------------------------


def test_no_factor_response_or_log_line_carries_a_token(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A real token in the environment, and nothing that reaches a caller mentions it.

    `V2-P1-016`'s standing requirement, on a face that reads a panel a provider fetched. The three
    channels are checked: the HTTP body of an answer, the HTTP body of a refusal (which is where a
    message from a lower layer would surface), and every log record emitted while both ran.

    The refusal is the one worth driving, because a `disclosable` message is built from a cause
    the panel plane raised -- and an unanticipated exception is precisely what
    `_panel_command`'s docstring says can carry whatever the frame it escaped was holding.
    """
    monkeypatch.setenv("TUSHARE_TOKEN", _TOKEN)
    client = _rest(runtime_dir)

    with caplog.at_level("DEBUG"):
        answered = client.post("/api/v1/factors/run", json=_json_body(BASELINE))
        refused = client.post(
            "/api/v1/factors/run", json=_json_body({**BASELINE, "exchange": "SSE"})
        )

    assert answered.status_code == 200
    assert refused.status_code == 409
    for text in (answered.text, refused.text, caplog.text):
        assert _TOKEN not in text
        assert "TUSHARE_TOKEN" not in text


def test_a_bar_this_face_prices_carries_the_stored_band_the_halt_state_and_the_warning(
    runtime_dir: Path,
) -> None:
    """The three things a `MarketBar` cannot be defaulted into, each read off the stored panel.

    `AShareExecutionPolicy` derives a limit band from `board` and `is_st` when a bar carries none,
    and `KNOWN_EXECUTION_LIMITATIONS` records that the derived rule is measurably wrong on the
    Beijing board and on ST names outside the main board. This face therefore never builds a bar
    without the exchange's own band -- which makes `board` and `is_st` unobservable through the
    *numbers*, and is exactly why they are asserted here directly rather than left to a spread to
    notice. Two mutations survived the rest of this file without this test: `_board` returning
    `"main"` for every code, and `is_st` hardcoded to `False`.

    `board` is not merely decorative even with a published band: it also decides the lot rule,
    which requires 200 shares on STAR and a multiple of 100 elsewhere.
    """
    inputs = _PanelInputs(PanelStore(runtime_dir / "panel"), _resolved(BASELINE))
    session = PREDICTION_DAYS[-1]

    special = inputs.market_bar(SPECIAL_TREATED, session)
    ordinary = inputs.market_bar(SECURITIES[0], session)
    star = inputs.market_bar(STAR_BOARD, session)

    assert special is not None and ordinary is not None and star is not None
    assert special.is_st and not ordinary.is_st
    assert (special.board, ordinary.board, star.board) == ("main", "main", "star")
    assert special.has_published_limits and ordinary.has_published_limits
    assert not special.suspended
    assert _board("000001.BJ") == "bse"
    assert _board("300750.SZ") == "growth"


def test_the_acceptance_row_is_marked_and_an_unmeasured_grid_is_warned_about(
    runtime_dir: Path,
) -> None:
    """The two renderings `V2-P3-019` added, on the fixture that produces the dangerous answer.

    This file's own configuration is the one the warning exists for. Both shipped derived specs
    declare `min_cross_section=100` and this market has eight names, so **every** attribution cell
    is `not_measured` -- and `test_the_raw_tier_is_measured_off_the_stored_panel_and_the_derived_
    tiers_are_not` already pins that as the honest answer. The danger is what it looks like:
    exit `0`, `200`, and no `removed` cell anywhere. A reader or a CI step that greps for `removed`
    and stops has concluded "this factor survived neutralisation" about two tiers that never
    computed a number.

    Four claims, and the exit code is one of them: the warning is a **warning**, not a fourth exit
    code, because the experiment did assemble and its record is worth keeping. See
    `factor_view.everything_is_unmeasured` for why, and
    `tests/integration/test_factor_run.py::
    test_a_grid_with_a_real_verdict_is_not_reported_as_unmeasured` for the other direction --
    without it, `everything_is_unmeasured` could return `True` unconditionally and this test would
    still pass.

    The marker is required on the acceptance step's rows and **absent everywhere else**, so a
    rendering that marked all six would fail rather than satisfy this vacuously.
    """
    runner = CliRunner()
    arguments = _cli_arguments(runtime_dir, BASELINE)
    plain = runner.invoke(app, [flag for flag in arguments if flag != "--json"])
    as_data = runner.invoke(app, arguments)

    assert plain.exit_code == int(PanelExit.ok) == 0
    assert as_data.exit_code == int(PanelExit.ok)
    marked = [line for line in plain.stdout.splitlines() if ACCEPTANCE_MARKER in line]
    assert len(marked) == 2
    assert all(line.startswith("processed->neutralized") for line in marked)
    assert "answer     processed->neutralized mean_ic=not_measured" in plain.stdout

    assert UNMEASURED_WARNING in plain.stderr
    assert UNMEASURED_WARNING in as_data.stderr
    # `--json` stdout stays exactly the sealed envelope, so a caller piping it still parses.
    assert json.loads(as_data.stdout)["schema_version"] == VIEW_SCHEMA_VERSION
    assert "WARNING" not in as_data.stdout


def test_a_refusal_body_names_no_filesystem_path(runtime_dir: Path) -> None:
    """The store's location is configuration of this process, not an answer about a factor.

    The in-process faces get the local message, which names the store, because they are inside
    the process that owns it; the HTTP body gets `disclosable`, which does not. Driven on the
    refusal whose cause interpolates a path -- an unreadable calendar -- so the substitution is
    exercised rather than described.
    """
    parameters = {**BASELINE, "exchange": "SSE"}

    local = sdk_answer(runtime_dir, parameters)
    over_http = rest_answer(runtime_dir, parameters)

    assert str(runtime_dir) not in over_http["message"]
    assert "this service's panel store" in over_http["message"]
    assert local["message"] == over_http["message"]
    assert not re.search(r"/(?:private/)?(?:var|tmp)/", over_http["message"])
    # And the in-process message really is the other one: the split is not a no-op.
    assert str(runtime_dir) in local["local"]
    assert "this service's panel store" not in local["local"]
