"""`panel build`'s eight non-price targets, driven end to end through `CliRunner`.

The companion to `tests/integration/test_cli_panel.py`, and it exists as a separate module for
one reason: that one's scripted transport answers a whole-market cross section per session, and
these eight targets ask completely different questions -- one announcement year, one index-month,
one taxonomy vintage, one `(l1_code, is_new)` slice, one `(security, year)` window. Folding both
frames into one transport would make every assertion in either module depend on the other's
fixture shape.

Nothing here touches the network. `cli._panel_transport` is replaced, so the real
`TushareProvider` decodes, point-in-time filters and projects, and the real `panel_ingest`
writers store -- the seam is the HTTP call and nothing above it, exactly as in the sibling
module. What the live endpoint actually serves for these datasets is measured in `tests/e2e/`.

## Why the frame is 2025 and the clock is 2026

Three of these targets derive their request window from `as_of`: `namechange` and the three
announcement-year statement endpoints take its Asia/Shanghai *year*, and `index_weight` takes its
*month*. A frame inside the current year would leave most of those windows unpublished at the
clock, so the point-in-time filter would empty them and every test here would be asserting
against `no_data`. A finished year with the clock just past it exercises the windows the way a
backfill does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET, INDEX_WEIGHT_INDEX_CODES
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
    SW2014_TAXONOMY,
    SW2021_TAXONOMY,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel.store import PanelStore

runner = CliRunner()

SECRET_TOKEN = "sk-extra-targets-token-must-not-leak-88214"
"""Distinct from every other module's, so a leak assertion here cannot pass because some other
test happened to scrub a different string."""

EXTRA_YEAR: int = 2025
EXTRA_CLOCK: datetime = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2026-01-08 -- eight days after the frame year ended."""

SECURITIES: tuple[str, ...] = ("000001.SZ", "600000.SH")

L1_CODES: tuple[str, ...] = ("801010.SI", "801030.SI")
"""Two level-one industries rather than the live corpus's 31, so a membership sweep here is four
requests. The count is not load-bearing anywhere: `build_industry_tree` checks the parent chain,
the level names and the identifiers, and never how many industries a vintage has."""

REGISTRY_FIELDS = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]
NAMECHANGE_FIELDS = ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]
INDEX_WEIGHT_FIELDS = ["index_code", "con_code", "trade_date", "weight"]
INDEX_CLASSIFY_FIELDS = [
    "index_code",
    "industry_name",
    "level",
    "industry_code",
    "is_pub",
    "parent_code",
    "src",
]
INDEX_MEMBER_FIELDS = ["ts_code", "l1_code", "l2_code", "l3_code", "in_date", "out_date"]


def _statement_fields(dataset: str) -> list[str]:
    """One statement endpoint's response shape, taken from the domain's own column list.

    Written out of `STATEMENT_DATA_COLUMNS` rather than copied, so a column added there fails
    `_check_panel_projection` against this transport instead of being silently absent from a
    fixture that still passes.
    """
    keys = ["ts_code", "end_date", "ann_date"]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend(["f_ann_date", "update_flag"])
    return [*keys, *STATEMENT_DATA_COLUMNS[dataset]]


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _response(
    fields: Sequence[str], items: Sequence[Sequence[Any]], *, has_more: bool = False
) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(fields),
            "items": [list(item) for item in items],
            "has_more": has_more,
        },
    }


# --- what each endpoint answers ---------------------------------------------------------------

PUBLICATION_DAY: int = 28
"""The day of the month `index_weight` publishes on in this frame.

The live endpoint publishes on each month's last open session, which is a calendar fact this
transport has no calendar for. What matters to the code under test is that the publication is
inside the requested month window and is knowable at the month-end `as_of` the CLI derives, and
the 28th is both in every month of every year.
"""

STATEMENT_PERIODS: tuple[tuple[str, str], ...] = (
    ("0331", "0428"),
    ("0630", "0830"),
    ("0930", "1030"),
)
"""The three interim reports of a period year, as `(period end, announcement)` month-days.

The annual is deliberately not here: it is announced the *following* spring, which is the whole
asymmetry `fina_indicator`'s request window creates and which `_annual_filing` supplies.
"""


def _annual_filing(period_year: int) -> tuple[str, str]:
    """The annual report of `period_year`: period end 31 December, announced 15 March after it."""
    return (f"{period_year}1231", f"{period_year + 1}0315")


class ExtraTargetTransport:
    """A `TushareTransport` answering the eight targets this module drives, recording payloads.

    Every branch honours the *request* -- the announcement-year window, the index-month window,
    the taxonomy vintage, the `(l1_code, is_new)` pair, the `ts_code` and the report-period year
    -- rather than answering one canned response, because half the tests here are about whether
    the CLI built the right request in the first place. A canned answer would make "did `--year`
    reach the window, or did the clock?" unobservable, which is
    `test_cli_panel.py::YearAwareCalendarTransport`'s reason for existing one dataset over.
    """

    def __init__(
        self,
        *,
        weight_gap_months: frozenset[int] = frozenset(),
        superseded: bool = True,
        filing_securities: frozenset[str] | None = None,
        publishes_weights: bool = True,
        classifies: bool = True,
        assigns: bool = True,
        assigned_securities: tuple[str, ...] = SECURITIES,
        vintage_override: str | None = None,
    ) -> None:
        self._assigned_securities = assigned_securities
        self.payloads: list[dict[str, Any]] = []
        self._weight_gap_months = weight_gap_months
        self._superseded = superseded
        self._filing_securities = filing_securities
        self._publishes_weights = publishes_weights
        self._classifies = classifies
        self._assigns = assigns
        self._vintage_override = vintage_override

    # -- helpers -------------------------------------------------------------------------------

    def requests_for(self, dataset: str) -> list[Mapping[str, str]]:
        """Every `params` mapping this transport was asked for one dataset, in order."""
        return [entry["params"] for entry in self.payloads if str(entry["api_name"]) == dataset]

    def _statement_rows(self, dataset: str, params: Mapping[str, str]) -> list[list[Any]]:
        code = str(params["ts_code"])
        if self._filing_securities is not None and code not in self._filing_securities:
            return []
        start = int(str(params["start_date"])[:4])
        values = [1.0 * (index + 1) for index in range(len(STATEMENT_DATA_COLUMNS[dataset]))]
        filings = [
            # The previous period year's annual, which is announced inside `start` and is what
            # makes an announcement year genuinely span two report-period years rather than
            # merely be documented as doing so.
            _annual_filing(start - 1),
            *((f"{start}{end}", f"{start}{ann}") for end, ann in STATEMENT_PERIODS),
            _annual_filing(start),
        ]
        rows: list[list[Any]] = []
        for period, announced in filings:
            # `fina_indicator`'s window filters the report period; the other three filter the
            # announcement. Answering both from one list is what makes the two request shapes
            # distinguishable here rather than being two names for one query.
            inside = (
                period[:4] == str(start)
                if dataset == FINANCIAL_INDICATOR_DATASET
                else announced[:4] == str(start)
            )
            if not inside:
                continue
            keys: list[Any] = [code, period, announced]
            if dataset != FINANCIAL_INDICATOR_DATASET:
                keys.extend([announced, "1"])
            rows.append([*keys, *values])
        return rows

    # -- the transport -------------------------------------------------------------------------

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api_name = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api_name == STOCK_BASIC_DATASET:
            return _response(
                REGISTRY_FIELDS,
                [[code, code, "SSE", "主板", "L", "20260102", None] for code in SECURITIES],
            )
        if api_name == NAMECHANGE_DATASET:
            year = str(params["start_date"])[:4]
            return _response(
                NAMECHANGE_FIELDS,
                [
                    [code, f"ST{code[:6]}", f"{year}0620", None, f"{year}0610", "改名"]
                    for code in SECURITIES
                ],
            )
        if api_name == INDEX_WEIGHT_DATASET:
            month = int(str(params["start_date"])[4:6])
            if month in self._weight_gap_months or not self._publishes_weights:
                return _response(INDEX_WEIGHT_FIELDS, [])
            day = _compact(date(int(str(params["start_date"])[:4]), month, PUBLICATION_DAY))
            return _response(
                INDEX_WEIGHT_FIELDS,
                [
                    [params["index_code"], SECURITIES[0], day, 60.0],
                    [params["index_code"], SECURITIES[1], day, 40.0],
                ],
            )
        if api_name == INDUSTRY_TREE_DATASET:
            if not self._classifies:
                return _response(INDEX_CLASSIFY_FIELDS, [])
            return _response(
                INDEX_CLASSIFY_FIELDS,
                _tree_items(self._vintage_override or str(params["src"])),
            )
        if api_name == INDUSTRY_MEMBERSHIP_DATASET:
            return _response(INDEX_MEMBER_FIELDS, self._membership_items(params))
        if api_name in STATEMENT_DATA_COLUMNS:
            return _response(_statement_fields(api_name), self._statement_rows(api_name, params))
        raise AssertionError(f"the CLI asked for an unscripted dataset: {api_name}")

    def _membership_items(self, params: Mapping[str, str]) -> list[list[Any]]:
        level_one = str(params["l1_code"])
        if not self._assigns:
            return []
        if str(params["is_new"]) == "N":
            if not self._superseded:
                return []
            # A closed assignment: the split writes an opening row in 2021 and a closing row in
            # 2022, which is the shape `_industry_membership_panel_rows` exists to produce.
            return [
                [
                    code,
                    level_one,
                    f"{level_one[:5]}1.SI",
                    f"{level_one[:5]}2.SI",
                    "20211220",
                    "20220109",
                ]
                for code in self._assigned_securities
            ]
        return [
            [code, level_one, f"{level_one[:5]}1.SI", f"{level_one[:5]}2.SI", "20220110", ""]
            for code in self._assigned_securities
        ]


def _tree_items(taxonomy: str) -> list[list[Any]]:
    """One vintage's tree: two L1 industries, each with an L2 and an L3 beneath it.

    The parent chain is real because `build_industry_tree` refuses a broken one, and a broken
    chain is exactly what a partial read of the tree partition looks like -- so a fixture that
    faked it would make the loader's strongest check unreachable from here.
    """
    items: list[list[Any]] = []
    for position, level_one in enumerate(L1_CODES, start=1):
        root = f"{position}10000"
        items.append([level_one, f"industry{position}", "L1", root, "1", "0", taxonomy])
        items.append(
            [
                f"{level_one[:5]}1.SI",
                f"industry{position}-2",
                "L2",
                f"{root[:2]}1000",
                "1",
                root,
                taxonomy,
            ]
        )
        items.append(
            [
                f"{level_one[:5]}2.SI",
                f"industry{position}-3",
                "L3",
                f"{root[:2]}1100",
                "1",
                f"{root[:2]}1000",
                taxonomy,
            ]
        )
    return items


@pytest.fixture
def extra_transport(monkeypatch: pytest.MonkeyPatch) -> ExtraTargetTransport:
    return _install(monkeypatch, ExtraTargetTransport())


def _install(
    monkeypatch: pytest.MonkeyPatch, transport: ExtraTargetTransport
) -> ExtraTargetTransport:
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: transport)
    monkeypatch.setattr(cli, "_panel_clock", lambda: EXTRA_CLOCK)
    return transport


def build(runtime_dir: Path, *targets: str, extra: Sequence[str] = ()) -> Any:
    arguments = ["panel", "build", "--runtime-dir", str(runtime_dir), "--year", str(EXTRA_YEAR)]
    for target in targets:
        arguments.extend(["--dataset", target])
    arguments.extend(extra)
    return runner.invoke(app, arguments)


def _partitions(result: Any) -> dict[str, list[int]]:
    payload = json.loads(result.stdout)
    landed: dict[str, list[int]] = {}
    for entry in payload["partitions"]:
        landed.setdefault(str(entry["dataset"]), []).append(int(entry["year"]))
    return {name: sorted(years) for name, years in landed.items()}


EVERY_EXTRA_TARGET: tuple[str, ...] = (
    STOCK_BASIC_DATASET,
    NAMECHANGE_DATASET,
    INDEX_WEIGHT_DATASET,
    INCOME_DATASET,
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    INDUSTRY_TREE_DATASET,
    INDUSTRY_MEMBERSHIP_DATASET,
    FINANCIAL_INDICATOR_DATASET,
)


# --- the load-bearing one ---------------------------------------------------------------------


def test_every_new_target_writes_a_partition_through_the_real_writers(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """If a complete, well-formed fetch of these eight could not be stored, every refusal test
    below would pass for the wrong reason.

    This is the state three separate acceptance passes reported as missing: `providers/tushare.py`
    declared fifteen datasets, `panel_ingest` had twelve writers, and `panel build` offered five
    targets -- so eight datasets P2's gates and P3's whole factor stack are specified against
    could be fetched, written and read back by this repository and could not be *built* by it.
    """
    result = build(tmp_path, *EVERY_EXTRA_TARGET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert _partitions(result) == {
        # `--year 2025` was asked for and the registry lands in its lifecycle year, which is
        # 2026 for both securities in this frame. That is the exemption, not a defect.
        STOCK_BASIC_DATASET: [2026],
        NAMECHANGE_DATASET: [EXTRA_YEAR],
        INDEX_WEIGHT_DATASET: [EXTRA_YEAR],
        INCOME_DATASET: [EXTRA_YEAR],
        BALANCE_SHEET_DATASET: [EXTRA_YEAR],
        CASH_FLOW_DATASET: [EXTRA_YEAR],
        # The vintages, not `--year`: SW2014's nodes are dated 2014-02-21 and SW2021's
        # 2021-12-13, because the endpoint carries no date column of its own.
        INDUSTRY_TREE_DATASET: [2014, 2021],
        # Membership *event* years: the opening half of a closed assignment lands in 2021 and
        # the closing half in 2022, and the open assignments land in 2022.
        INDUSTRY_MEMBERSHIP_DATASET: [2021, 2022],
        # Announcement years derived from period year 2025: its three interims were announced
        # in 2025 and its annual on 2026-03-15, which is past this build's clock and dropped.
        FINANCIAL_INDICATOR_DATASET: [EXTRA_YEAR],
    }
    assert SECRET_TOKEN not in result.output


def test_the_token_reaches_the_transport_and_appears_in_no_output(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """Both halves, on the eight targets that did not exist when the sibling module pinned it for
    the other five. The CLI never reads `TUSHARE_TOKEN` itself -- `TushareProvider` does -- so the
    token must reach the transport and must appear nowhere in this command's own output."""
    human = build(tmp_path, *EVERY_EXTRA_TARGET)

    assert human.exit_code == PanelExit.ok
    assert SECRET_TOKEN not in human.output
    assert {entry["token"] for entry in extra_transport.payloads} == {SECRET_TOKEN}


# --- the request each target builds -------------------------------------------------------------


def test_namechange_asks_for_the_year_it_was_given_at_an_instant_that_can_see_it(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The window comes from `--year` and the `as_of` comes from the end of that year.

    Both halves matter and only together. `_namechange_params` derives the window from `as_of`'s
    Asia/Shanghai year, so an instant taken from the wrong year fetches the wrong corpus; and the
    rows are clocked at their announcement, so `_year_as_of`'s 1 January -- which is right for
    `trade_cal`, whose rows are all available from the start of their year -- would fetch the
    right window and then have every row in it dropped by the point-in-time filter.
    """
    result = build(tmp_path, NAMECHANGE_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert extra_transport.requests_for(NAMECHANGE_DATASET) == [
        {"start_date": f"{EXTRA_YEAR}0101", "end_date": f"{EXTRA_YEAR}1231"}
    ]
    assert _partitions(result) == {NAMECHANGE_DATASET: [EXTRA_YEAR]}


def test_index_weight_asks_every_month_of_the_year_for_every_index(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """One request is one index for one calendar month, so a year is 36 of them and they all have
    to land in **one** write: `PanelStore` replaces a partition whole and its key has no index
    dimension, so a per-index loop would leave the year holding whichever index went last."""
    result = build(tmp_path, INDEX_WEIGHT_DATASET, extra=["--json"])

    requested = extra_transport.requests_for(INDEX_WEIGHT_DATASET)
    assert result.exit_code == PanelExit.ok
    assert len(requested) == 12 * len(INDEX_WEIGHT_INDEX_CODES)
    assert {str(entry["index_code"]) for entry in requested} == set(INDEX_WEIGHT_INDEX_CODES)
    assert {str(entry["start_date"])[4:6] for entry in requested} == {
        f"{month:02d}" for month in range(1, 13)
    }
    stored = PanelStore(tmp_path / "panel")
    coverage = stored.read_coverage(INDEX_WEIGHT_DATASET, EXTRA_YEAR)
    assert coverage is not None
    assert set(coverage.subjects) == set(INDEX_WEIGHT_INDEX_CODES)


def test_index_weight_refuses_a_month_that_lies_between_two_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hole in the month sequence is refused at write time rather than left to the read.

    `domain/index_membership.py::build_index_membership` does refuse it -- on every load, which is
    the residue a write-time census cannot close -- but only once someone loads the partition. So
    without this the build reports success and the panel is unreadable from then on, which is the
    shape `V2-P1-013` calls an empty success one layer up.
    """
    _install(monkeypatch, ExtraTargetTransport(weight_gap_months=frozenset({6})))

    result = build(tmp_path, INDEX_WEIGHT_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "between two publications" in result.output
    assert PanelStore(tmp_path / "panel").registered_years(INDEX_WEIGHT_DATASET) == ()


def test_index_weight_accepts_a_gap_at_either_end_of_the_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An index that had not launched at the start of the year, and a current year that has not
    finished publishing, are horizons rather than holes -- `000852.SH` begins in 2014 and August
    2026 had not reached its last open session when this was measured. Only an interior gap is a
    fault, so the refusal above must not fire on these."""
    _install(monkeypatch, ExtraTargetTransport(weight_gap_months=frozenset({1, 2, 11, 12})))

    result = build(tmp_path, INDEX_WEIGHT_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert _partitions(result) == {INDEX_WEIGHT_DATASET: [EXTRA_YEAR]}


def test_index_weight_refuses_a_year_in_which_no_index_published_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A year entirely before the first index's launch has no partition to write rather than an
    empty one. Reached without this guard, `merge_panel_batches` would refuse an empty list with
    "needs at least one batch" -- a true sentence about a list, several layers from the year that
    produced it."""
    _install(monkeypatch, ExtraTargetTransport(publishes_weights=False))

    result = build(tmp_path, INDEX_WEIGHT_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "no partition to write rather than an empty one" in result.output
    assert PanelStore(tmp_path / "panel").registered_years(INDEX_WEIGHT_DATASET) == ()


def test_index_weight_only_asks_for_the_months_that_have_begun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial current year is the ordinary case, and the loop is bounded by the clock rather
    than by the calendar: a month that has not begun is not asked for at all, and the months that
    have but whose last open session has not arrived answer `no_data`, which is a horizon at the
    end of the year rather than a hole in it. Both halves are what
    `_month_end_as_of` returning `None` and `_build_index_weights`' end-gap tolerance are for."""
    transport = _install(monkeypatch, ExtraTargetTransport())
    monkeypatch.setattr(cli, "_panel_clock", lambda: datetime(2025, 5, 20, 4, 0, tzinfo=UTC))

    result = build(tmp_path, INDEX_WEIGHT_DATASET, extra=["--json"])

    requested = transport.requests_for(INDEX_WEIGHT_DATASET)
    assert result.exit_code == PanelExit.ok
    # January to May: the five months that had begun on 2025-05-20. May's own window is clamped
    # at the clock, and its publication (the 28th in this frame) has not happened, so it serves
    # nothing -- a trailing gap, which is legitimate.
    assert {str(entry["start_date"])[4:6] for entry in requested} == {"01", "02", "03", "04", "05"}
    assert len(requested) == 5 * len(INDEX_WEIGHT_INDEX_CODES)
    assert _partitions(result) == {INDEX_WEIGHT_DATASET: [EXTRA_YEAR]}


def test_a_year_that_has_not_begun_is_refused_before_any_request(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """`_build_sessions`' refusal at the other end of the same question. Without it, `--year 2030`
    would have its window clamped to the clock, fetch *this* year's announcements, store them as
    this year's partition and be caught only by the misfiled-year audit -- which would report a
    fetch fault for what is a plain fact about the calendar."""
    result = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            NAMECHANGE_DATASET,
            "--year",
            "2030",
            "--runtime-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == PanelExit.bad_request
    assert "had not begun" in result.output
    assert extra_transport.payloads == []


def test_the_industry_tree_is_filed_by_vintage_and_not_by_the_year_that_was_asked_for(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """`index_classify` carries no date column at all, so `providers/tushare.py` dates every node
    at its vintage's effective day. Both measured vintages are fetched: refusing the endpoint's
    own SW2014 default is what `_index_classify_params` is for, and having refused the default,
    the remaining set is closed and two requests long."""
    result = build(tmp_path, INDUSTRY_TREE_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert [str(entry["src"]) for entry in extra_transport.requests_for(INDUSTRY_TREE_DATASET)] == [
        SW2014_TAXONOMY,
        SW2021_TAXONOMY,
    ]
    assert _partitions(result) == {INDUSTRY_TREE_DATASET: [2014, 2021]}


def test_a_vintage_that_serves_no_node_is_refused_rather_than_stored_as_an_empty_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty tree would read as a taxonomy with no industries in it, and `index_member_all`'s
    whole fetch plan is built out of that tree's L1 nodes -- so an empty one is a sweep of zero
    slices reported as a success."""
    _install(monkeypatch, ExtraTargetTransport(classifies=False))

    result = build(tmp_path, INDUSTRY_TREE_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "served no node for vintage" in result.output
    assert PanelStore(tmp_path / "panel").registered_years(INDUSTRY_TREE_DATASET) == ()


def test_an_endpoint_that_answers_every_vintage_with_sw2014_cannot_slice_the_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree build that succeeded and still left the membership sweep with no codes to slice by.

    The endpoint's own default vintage is SW2014 while every `index_member_all` row is SW2021,
    which is what `_index_classify_params` refuses to default for; this is the same disagreement
    one layer up, where the slices are chosen. The transport answers SW2014 nodes to *both*
    vintage requests, so the tree build reports success -- and because a node's partition year is
    derived from its own `src`, both writes land in **2014** and the 2021 partition never exists.

    The refusal therefore arrives as "the SW2021 tree could not be read", not as "the 2021
    partition holds another vintage": on this data path the second is unreachable, because a 2021
    partition can only ever hold SW2021 rows. `_stored_level_one_codes` keeps a branch for it
    anyway, so that `trees.get(...)` returning `None` is a stated refusal rather than a
    `KeyError` several frames from the cause -- and that branch is defensive by construction, not
    covered by this test.

    What is pinned here is the property that matters: the sweep is refused by name rather than
    silently slicing SW2021 memberships by SW2014's 28 codes, which would drop three whole
    industries and look like a complete corpus.
    """
    _install(monkeypatch, ExtraTargetTransport(vintage_override=SW2014_TAXONOMY))

    tree = build(tmp_path, INDUSTRY_TREE_DATASET, extra=["--json"])
    assert tree.exit_code == PanelExit.ok
    assert _partitions(tree) == {INDUSTRY_TREE_DATASET: [2014, 2014]}

    result = build(tmp_path, INDUSTRY_MEMBERSHIP_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert SW2021_TAXONOMY in result.output
    assert "--dataset index_classify" in result.output


def test_a_membership_sweep_that_served_no_row_at_all_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct from the current-snapshot refusal below it: there, the corpus is complete-looking
    and has no history; here there is no corpus, and an empty one would read as a market in which
    nothing has ever been classified."""
    _install(monkeypatch, ExtraTargetTransport(assigns=False))

    assert build(tmp_path, INDUSTRY_TREE_DATASET).exit_code == PanelExit.ok
    result = build(tmp_path, INDUSTRY_MEMBERSHIP_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "nothing has ever been classified" in result.output


def test_a_resweep_that_would_drop_a_security_from_a_stored_event_year_is_a_data_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`panel_ingest._refuse_to_drop_stored_subjects` raising inside the **span** phase.

    Two things are pinned at once. That the guard reaches the span phase at all -- it is the
    writer's, and the span phase is a second build path that had to learn `_PANEL_WRITE_REFUSALS`
    for itself. And that the refusal is `unhealthy` with the writer's own message visible rather
    than `internal_error` with it withheld, which is the classification defect `SuspensionError`
    booked for the price path and which a new build phase could quietly reintroduce.
    """
    _install(monkeypatch, ExtraTargetTransport())
    assert build(tmp_path, INDUSTRY_TREE_DATASET, INDUSTRY_MEMBERSHIP_DATASET).exit_code == (
        PanelExit.ok
    )

    _install(monkeypatch, ExtraTargetTransport(assigned_securities=SECURITIES[:1]))
    result = build(tmp_path, INDUSTRY_MEMBERSHIP_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert result.exit_code != PanelExit.internal_error
    assert "the panel refused this build" in result.output
    assert "would drop" in result.output
    assert SECURITIES[1] in result.output
    assert "defect in the command" not in result.output


def test_the_membership_sweep_slices_by_the_stored_trees_level_one_codes(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The `l1_code` slices come from the stored SW2021 tree rather than from a literal in
    `cli.py`, so they cannot go stale when Shenwan adds an industry -- and cannot go stale
    *silently*, which is what a missing code would do: an unfetched slice is not an error, it is
    simply a corpus with an industry missing. Both membership states are asked for, because the
    endpoint's default hides the superseded assignments behind a tidy one-row-per-security
    answer with no truncation flag."""
    result = build(tmp_path, INDUSTRY_TREE_DATASET, INDUSTRY_MEMBERSHIP_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert [
        (str(entry["l1_code"]), str(entry["is_new"]))
        for entry in extra_transport.requests_for(INDUSTRY_MEMBERSHIP_DATASET)
    ] == [(code, state) for code in L1_CODES for state in ("Y", "N")]


def test_the_membership_sweep_is_refused_before_it_starts_when_no_tree_is_stored(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """`_stored_calendar`'s shape one dataset over: the request needs something the panel already
    holds, so a store without it is refused after zero round trips and the message names the
    command that fixes it."""
    result = build(tmp_path, INDUSTRY_MEMBERSHIP_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "--dataset index_classify" in result.output
    assert extra_transport.requests_for(INDUSTRY_MEMBERSHIP_DATASET) == []


def test_a_membership_sweep_with_no_superseded_row_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one shape `write_industry_memberships`' own subject guard says it cannot see.

    A current-only corpus carries **every** security, so no subject goes missing and the write
    succeeds; what is absent is the history, and the panel then reads as a market in which nobody
    has ever been reclassified. The live endpoint's default request produces exactly that, which
    is why `_index_member_all_params` refuses to default `is_new` -- and this is the second half
    of the same argument, at the layer that can see the whole sweep rather than one request.
    """
    _install(monkeypatch, ExtraTargetTransport(superseded=False))

    assert build(tmp_path, INDUSTRY_TREE_DATASET).exit_code == PanelExit.ok
    result = build(tmp_path, INDUSTRY_MEMBERSHIP_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "current snapshot alone" in result.output
    assert PanelStore(tmp_path / "panel").registered_years(INDUSTRY_MEMBERSHIP_DATASET) == ()


# --- the statement targets ----------------------------------------------------------------------


def test_a_statement_target_fetches_one_request_per_security_in_the_stored_registry(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """`ts_code` is mandatory on all four endpoints and a comma-joined list answers zero rows on
    three of them, so there is no cross-section fetch and the securities have to come from
    somewhere. They come from `stock_basic`, which is why it runs first in the same invocation."""
    result = build(tmp_path, STOCK_BASIC_DATASET, INCOME_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok, result.stderr
    assert [str(entry["ts_code"]) for entry in extra_transport.requests_for(INCOME_DATASET)] == [
        *SECURITIES
    ]
    assert {
        (str(entry["start_date"]), str(entry["end_date"]))
        for entry in extra_transport.requests_for(INCOME_DATASET)
    } == {(f"{EXTRA_YEAR}0101", f"{EXTRA_YEAR}1231")}


def test_a_statement_target_is_refused_before_it_starts_when_no_registry_is_stored(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    result = build(tmp_path, INCOME_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "--dataset stock_basic" in result.output
    assert extra_transport.requests_for(INCOME_DATASET) == []


def test_subject_narrows_the_sweep_to_the_securities_that_were_named(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The lever that turns 5,881 requests into one. It also means the registry is not read at
    all, which is what makes `--subject` usable on a store that has no `stock_basic` in it."""
    result = build(tmp_path, INCOME_DATASET, extra=["--subject", SECURITIES[0], "--json"])

    assert result.exit_code == PanelExit.ok
    assert [str(entry["ts_code"]) for entry in extra_transport.requests_for(INCOME_DATASET)] == [
        SECURITIES[0]
    ]


def test_subject_is_refused_for_a_target_whose_partition_is_the_whole_market(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """Refused rather than ignored. A flag the command silently drops is indistinguishable from
    one the caller never passed -- `_build_years`' finding about `--year` -- and here the
    silently-ignored version would also have looked like it worked."""
    result = build(tmp_path, NAMECHANGE_DATASET, extra=["--subject", SECURITIES[0]])

    assert result.exit_code == PanelExit.bad_request
    assert NAMECHANGE_DATASET in result.output
    assert extra_transport.payloads == []


def test_a_sweep_in_which_no_security_filed_is_refused_rather_than_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One security with nothing to report is ordinary -- `000013.SZ` served no `income` row for
    the 2024 window -- and every security with nothing to report is a fetch that did not work.
    Without this the empty list reaches `merge_panel_batches` and is refused as "needs at least
    one batch", a true sentence about a list several layers from the fetch that produced it."""
    _install(monkeypatch, ExtraTargetTransport(filing_securities=frozenset()))

    assert build(tmp_path, STOCK_BASIC_DATASET).exit_code == PanelExit.ok
    result = build(tmp_path, INCOME_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "none of the 2 securities served a filing" in result.output


def test_the_statement_sweep_states_its_size_before_it_makes_a_request(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """A budget line on stderr, before the first round trip.

    This target turned the command's unit of cost from minutes into hours: one request per
    security is 5,881 of them for one dataset-year against the live registry, and `--start 2015
    --end 2026` over the four endpoints is ~282,000. The only honest moment to say so is before
    the fetch rather than in an `eta` that appears after ten minutes -- and it has to be stderr,
    because `--json` promises a parseable stdout.
    """
    result = build(tmp_path, STOCK_BASIC_DATASET, INCOME_DATASET, extra=["--json"])

    assert result.exit_code == PanelExit.ok
    assert f"BUDGET {INCOME_DATASET} year={EXTRA_YEAR} 2 requests" in result.stderr
    assert "BUDGET" not in result.stdout
    json.loads(result.stdout)


# --- fina_indicator, the target whose partitions straddle its requests -------------------------


def test_fina_indicator_accumulates_its_period_years_into_one_write(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The finding that made `PANEL_BUILD_SPAN_TARGETS` exist.

    Its window filters the report period and its rows are filed by announcement date, so an
    announcement year is assembled from at least two period years: the annual of *A-1* plus the
    interims of *A*. A per-year loop would write announcement year 2025 from period year 2024
    (the annual) and then replace it from period year 2025 (the interims) -- more rows, the same
    securities, and nothing in `panel_ingest._refuse_to_drop_stored_subjects` able to see it.

    Asserted on the row count rather than only on the years, because the years alone would look
    identical under the defect: what a per-year loop loses is the annual report inside a year
    that still exists.
    """
    result = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            STOCK_BASIC_DATASET,
            "--dataset",
            FINANCIAL_INDICATOR_DATASET,
            "--start",
            str(EXTRA_YEAR - 1),
            "--end",
            str(EXTRA_YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == PanelExit.ok
    payload = json.loads(result.stdout)
    assert payload["span"]["targets"] == [FINANCIAL_INDICATOR_DATASET]
    landed = {
        int(entry["year"]): int(entry["row_count"])
        for entry in payload["span"]["partitions"]
        if entry["dataset"] == FINANCIAL_INDICATOR_DATASET
    }
    # 2024: three interims of period year 2024. 2025: the annual of 2024 announced 2025-03-15
    # *plus* the three interims of 2025 -- the row that a per-year loop destroys.
    assert landed == {EXTRA_YEAR - 1: 3 * len(SECURITIES), EXTRA_YEAR: 4 * len(SECURITIES)}
    assert [
        str(entry["start_date"])[:4]
        for entry in extra_transport.requests_for(FINANCIAL_INDICATOR_DATASET)
    ] == [str(EXTRA_YEAR - 1)] * len(SECURITIES) + [str(EXTRA_YEAR)] * len(SECURITIES)


def test_a_narrower_fina_indicator_span_is_refused_rather_than_shrinking_a_stored_year(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The half of the same defect that accumulation cannot close, because it happens across
    invocations. After the two-period-year build above, `--year 2025` alone reproduces only that
    period year's interims for announcement year 2025 and would drop the 2024 annual -- fewer
    rows, the same securities, and therefore invisible to the subject guard."""
    wide = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            STOCK_BASIC_DATASET,
            "--dataset",
            FINANCIAL_INDICATOR_DATASET,
            "--start",
            str(EXTRA_YEAR - 1),
            "--end",
            str(EXTRA_YEAR),
            "--runtime-dir",
            str(tmp_path),
        ],
    )
    assert wide.exit_code == PanelExit.ok

    result = build(tmp_path, FINANCIAL_INDICATOR_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "fewer rows than they hold" in result.output
    stored = PanelStore(tmp_path / "panel")
    coverage = stored.read_coverage(FINANCIAL_INDICATOR_DATASET, EXTRA_YEAR)
    assert coverage is not None
    assert coverage.row_count == 4 * len(SECURITIES)


def test_a_span_target_runs_once_however_many_years_the_invocation_names(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """`index_classify` and `index_member_all` take no date filter at all, so running them inside
    the year loop would re-fetch the same corpus once per year and write the same partitions each
    time. Three years, two vintage requests."""
    result = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--dataset",
            INDUSTRY_TREE_DATASET,
            "--start",
            str(EXTRA_YEAR - 2),
            "--end",
            str(EXTRA_YEAR),
            "--runtime-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == PanelExit.ok
    assert len(extra_transport.requests_for(INDUSTRY_TREE_DATASET)) == 2
    payload = json.loads(result.stdout)
    assert payload["years"] == [EXTRA_YEAR - 2, EXTRA_YEAR - 1, EXTRA_YEAR]
    assert sorted(entry["year"] for entry in payload["span"]["partitions"]) == [2014, 2021]


# --- resume -------------------------------------------------------------------------------------


def test_resume_skips_a_registered_statement_year_and_says_which_rule_it_used(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The weaker of `--resume`'s two rules, and the output says so on the line rather than only
    in a docstring: this one reads a registered partition and nothing else, because a statement
    dataset has no session census to compare against."""
    assert build(tmp_path, STOCK_BASIC_DATASET, INCOME_DATASET).exit_code == PanelExit.ok
    before = len(extra_transport.requests_for(INCOME_DATASET))

    result = build(tmp_path, INCOME_DATASET, extra=["--resume"])

    assert result.exit_code == PanelExit.ok
    assert len(extra_transport.requests_for(INCOME_DATASET)) == before
    assert "RESUMED income" in result.output
    assert "does not check which securities it holds" in result.output


def test_resume_cannot_tell_a_narrowed_statement_partition_from_a_whole_market_one(
    tmp_path: Path, extra_transport: ExtraTargetTransport
) -> None:
    """The disclosed limitation, measured rather than claimed.

    A partition an earlier `--subject` run wrote is registered, so `--resume` skips it and the
    year stays narrow. This test exists so that the sentence in `_resumable_targets`' docstring
    is a finding with a reproduction rather than a caveat nobody checked -- and so that anyone who
    later strengthens the rule has a failing test telling them the disclosure is now stale.

    The remedy is always available and is asserted here too: re-running without `--resume` fetches
    the whole registry, and `panel_ingest._refuse_to_drop_stored_subjects` does not object because
    a wider batch drops nothing.
    """
    assert build(tmp_path, STOCK_BASIC_DATASET).exit_code == PanelExit.ok
    assert (
        build(tmp_path, INCOME_DATASET, extra=["--subject", SECURITIES[0]]).exit_code
        == PanelExit.ok
    )
    stored = PanelStore(tmp_path / "panel")
    narrow = stored.read_coverage(INCOME_DATASET, EXTRA_YEAR)
    assert narrow is not None and set(narrow.subjects) == {SECURITIES[0]}

    resumed = build(tmp_path, INCOME_DATASET, extra=["--resume", "--json"])
    after_resume = stored.read_coverage(INCOME_DATASET, EXTRA_YEAR)
    assert resumed.exit_code == PanelExit.ok
    assert after_resume is not None and set(after_resume.subjects) == {SECURITIES[0]}

    rebuilt = build(tmp_path, INCOME_DATASET, extra=["--json"])
    widened = stored.read_coverage(INCOME_DATASET, EXTRA_YEAR)
    assert rebuilt.exit_code == PanelExit.ok
    assert widened is not None and set(widened.subjects) == set(SECURITIES)
