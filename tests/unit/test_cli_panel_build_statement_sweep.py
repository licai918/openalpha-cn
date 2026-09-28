"""The whole-market statement sweep writes what the per-security route writes (`V2-P6-002`).

`panel build --dataset income` without `--subject` used to be one request per security in the
stored registry -- 5,881 of them per dataset-year. It is now a sweep of Tushare's `*_vip`
endpoints: twelve announcement months per year for `income`, `balancesheet` and `cashflow`, four
report periods per period year for `fina_indicator`, each one request that must fit under the
endpoint's cap, and a month that does not is halved until it does. The
acceptance is that nothing downstream can tell the two routes apart: **the same partition rows
and the same `content_hash`**, driven here through the real CLI, provider, writers and store,
with only the HTTP call replaced.

The market below is built to make the equivalence hard rather than easy: the two routes serve
their rows in different orders, a security files twice on one day, another files an annual and
an interim on one day, one filing is stored as a version re-announced in the next year, most
months are empty, one month and one day reach the cap, and one security that files is not in
the registry at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

import duckdb
import pytest
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.financial_statements import (
    FINANCIAL_INDICATOR_DATASET,
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_statement_histories, load_upstream_defects
from openalpha_cn.providers import tushare

runner = CliRunner()

TOKEN: Final[str] = "sk-statement-sweep-token-must-not-leak-3307"
CLOCK: Final[datetime] = datetime(2026, 3, 20, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on 2026-03-20: after the revision below and after the 2025 annuals."""

YEAR: Final[int] = 2025
REGISTERED: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ", "600000.SH")
UNREGISTERED: Final[str] = "900001.SH"
"""Files in the whole market and is not in `stock_basic`. The per-security route never asks for
it, so the sweep must not store it either -- otherwise the two partitions differ by a security."""

FILLER: Final[str] = "900002.SH"
"""Another unregistered security, filing in every month and every report period the registered
three leave empty. A closed whole-market window that answers nothing is refused (the review of
`50c89ed`), and the live market has no empty closed month in 2015 or 2024; this keeps every window
of the frame non-empty while storing nothing, since neither route keeps an unregistered row."""

FILINGS: Final[tuple[tuple[str, str, str, str, str, float], ...]] = (
    # ts_code, end_date, ann_date, f_ann_date, update_flag, value
    ("000001.SZ", "20241231", "20250315", "20250315", "1", 11.0),
    ("000001.SZ", "20250331", "20250428", "20250428", "1", 12.0),
    ("000001.SZ", "20250630", "20250830", "20250830", "1", 13.0),
    # Stored as the version re-announced in the next year: f_ann_date > ann_date.
    ("000001.SZ", "20250930", "20251030", "20260105", "1", 14.0),
    # An annual and an interim announced on one day, and the interim served twice (a revision
    # pair): three rows for one security on one day.
    ("600000.SH", "20241231", "20250428", "20250428", "1", 21.0),
    ("600000.SH", "20250331", "20250428", "20250428", "0", 22.0),
    ("600000.SH", "20250331", "20250428", "20250428", "1", 22.5),
    ("000002.SZ", "20250331", "20250429", "20250429", "1", 31.0),
    ("000002.SZ", "20251231", "20260315", "20260315", "1", 32.0),
    (UNREGISTERED, "20250331", "20250428", "20250428", "1", 91.0),
    ("000001.SZ", "20251231", "20260120", "20260120", "1", 15.0),
    *(
        (FILLER, period, announced, announced, "1", 81.0)
        for period, announced in (
            ("20240331", "20240425"),
            ("20240630", "20240825"),
            ("20240930", "20241025"),
            # Late and revised reports of earlier periods, spread so that no report period
            # reaches `CAP`: a period has no finer window to halve into.
            ("20240930", "20250115"),
            ("20241231", "20250215"),
            ("20241231", "20250515"),
            ("20240630", "20250615"),
            ("20250630", "20250715"),
            ("20250630", "20250915"),
            ("20250930", "20251115"),
            ("20250930", "20251215"),
            ("20251231", "20260115"),
            ("20251231", "20260215"),
            ("20251231", "20260310"),
        )
    ),
)
"""April 2025 holds six rows, five of them on 28 April, so at a cap of six April is halved four
times before every window fits. The registered securities announce nothing in January, February,
May, June, July, September, November and December 2025; `FILLER` does."""

CAP: Final[int] = 6


def _fields(dataset: str) -> list[str]:
    keys = ["ts_code", "end_date", "ann_date"]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend(["f_ann_date", "update_flag"])
    return [*keys, *STATEMENT_DATA_COLUMNS[dataset]]


def _item(dataset: str, filing: tuple[str, str, str, str, str, float]) -> list[Any]:
    code, period, announced, first, flag, value = filing
    keys: list[Any] = [code, period, announced]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend([first, flag])
    return [*keys, *([value] * len(STATEMENT_DATA_COLUMNS[dataset]))]


def _envelope(dataset: str, items: Sequence[list[Any]], *, more: bool = False) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "",
        "data": {"fields": _fields(dataset), "items": list(items), "has_more": more},
    }


class Market:
    """One set of filings answered through both routes, in two different orders.

    The per-security endpoint filters one `ts_code` and serves newest period first; the `*_vip`
    endpoint serves the whole market in the reverse of `FILINGS`' order and withholds everything
    past `CAP`, saying so in `has_more`, as the live endpoints do. `income`'s and its two
    siblings' windows filter `ann_date`; `fina_indicator`'s per-security window and its `period`
    filter `end_date` -- the asymmetry `_financial_indicator_params` records.
    """

    def __init__(
        self,
        filings: Sequence[tuple[str, str, str, str, str, float]],
        *,
        listed: str = "20260102",
        cap: int = CAP,
    ) -> None:
        self.filings = tuple(filings)
        self.listed = listed
        self.cap = cap
        self.payloads: list[dict[str, Any]] = []

    def api_names(self) -> list[str]:
        return [str(entry["api_name"]) for entry in self.payloads]

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        api = str(payload["api_name"])
        params: Mapping[str, str] = payload["params"]
        if api == STOCK_BASIC_DATASET:
            return {
                "code": 0,
                "msg": "",
                "data": {
                    "fields": [
                        "ts_code",
                        "name",
                        "exchange",
                        "market",
                        "list_status",
                        "list_date",
                        "delist_date",
                    ],
                    "items": [
                        [code, code, "SSE", "主板", "L", self.listed, None] for code in REGISTERED
                    ],
                    "has_more": False,
                },
            }
        dataset = api.removesuffix("_vip")
        assert dataset in STATEMENT_DATA_COLUMNS, api
        if api.endswith("_vip"):
            if "period" in params:
                chosen = [f for f in self.filings if f[1] == params["period"]]
            else:
                chosen = [
                    f for f in self.filings if params["start_date"] <= f[2] <= params["end_date"]
                ]
            if "ts_code" in params:
                listed = set(str(params["ts_code"]).split(","))
                chosen = [f for f in chosen if f[0] in listed]
            chosen.reverse()
            assert "offset" not in params and "limit" not in params, params
            return _envelope(
                dataset,
                [_item(dataset, f) for f in chosen[: self.cap]],
                more=len(chosen) > self.cap,
            )
        column = 1 if dataset == FINANCIAL_INDICATOR_DATASET else 2
        chosen = [
            f
            for f in self.filings
            if f[0] == params["ts_code"] and params["start_date"] <= f[column] <= params["end_date"]
        ]
        chosen.sort(key=lambda f: f[1], reverse=True)
        return _envelope(dataset, [_item(dataset, f) for f in chosen])


@pytest.fixture(autouse=True)
def _small_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cap of six, so April has to be halved; see `test_tushare_statement_periods._cap`."""
    for dataset, sweep in list(tushare._TUSHARE_SWEEPS_BY_NAME.items()):
        monkeypatch.setitem(
            tushare._TUSHARE_SWEEPS_BY_NAME,
            dataset,
            sweep.model_copy(update={"max_rows_per_response": CAP}),
        )


def _install(monkeypatch: pytest.MonkeyPatch, market: Market, *, clock: datetime = CLOCK) -> Market:
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: market)
    monkeypatch.setattr(cli, "_panel_clock", lambda: clock)
    return market


def _build(root: Path, *arguments: str) -> Any:
    return runner.invoke(app, ["panel", "build", "--runtime-dir", str(root), *arguments])


def _by_subject(*targets: str, years: Sequence[str] = ("--year", str(YEAR))) -> list[str]:
    arguments = [*years]
    for target in targets:
        arguments.extend(["--dataset", target])
    for code in REGISTERED:
        arguments.extend(["--subject", code])
    return arguments


def _swept(*targets: str, years: Sequence[str] = ("--year", str(YEAR))) -> list[str]:
    arguments = [*years, "--dataset", STOCK_BASIC_DATASET]
    for target in targets:
        arguments.extend(["--dataset", target])
    return arguments


def _stored(root: Path, dataset: str, year: int) -> tuple[str, list[tuple[object, ...]]]:
    """The catalog's content hash and every stored row, in stored order."""
    coverage = PanelStore(root / "panel").read_coverage(dataset, year)
    assert coverage is not None, (dataset, year)
    path = root / "panel" / dataset / str(year) / "data.parquet"
    with duckdb.connect() as connection:
        rows = connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    return coverage.partition_content_hash or "", rows


# --- the acceptance ------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", ["income", "balancesheet", "cashflow"])
def test_the_sweep_and_the_per_security_route_write_the_same_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dataset: str
) -> None:
    market = _install(monkeypatch, Market(FILINGS))

    by_subject = _build(tmp_path / "subject", *_by_subject(dataset))
    subject_calls = market.api_names()
    market.payloads.clear()
    swept = _build(tmp_path / "sweep", *_swept(dataset))

    assert by_subject.exit_code == PanelExit.ok, by_subject.output
    assert swept.exit_code == PanelExit.ok, swept.output
    subject_hash, subject_rows = _stored(tmp_path / "subject", dataset, YEAR)
    sweep_hash, sweep_rows = _stored(tmp_path / "sweep", dataset, YEAR)
    assert sweep_rows == subject_rows
    assert sweep_hash == subject_hash
    # Every row the registered securities announced in 2025, the revision included.
    assert len(sweep_rows) == 8
    assert UNREGISTERED not in {row[0] for row in sweep_rows}
    # And the routes really were different routes.
    assert subject_calls == [dataset] * len(REGISTERED)
    assert set(market.api_names()) == {STOCK_BASIC_DATASET, f"{dataset}_vip"}


def test_fina_indicator_sweeps_report_periods_into_the_same_announcement_years(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Period years 2024 and 2025 file into announcement years 2025 and 2026: an annual announced
    the spring after its period, beside an interim announced the same day."""
    market = _install(monkeypatch, Market(FILINGS))
    span = ("--start", str(YEAR - 1), "--end", str(YEAR))

    by_subject = _build(tmp_path / "subject", *_by_subject(FINANCIAL_INDICATOR_DATASET, years=span))
    market.payloads.clear()
    swept = _build(tmp_path / "sweep", *_swept(FINANCIAL_INDICATOR_DATASET, years=span))

    assert by_subject.exit_code == PanelExit.ok, by_subject.output
    assert swept.exit_code == PanelExit.ok, swept.output
    for year in (YEAR, YEAR + 1):
        assert _stored(tmp_path / "sweep", FINANCIAL_INDICATOR_DATASET, year) == _stored(
            tmp_path / "subject", FINANCIAL_INDICATOR_DATASET, year
        )
    periods = [
        str(entry["params"]["period"])
        for entry in market.payloads
        if entry["api_name"] == "fina_indicator_vip"
    ]
    assert sorted(set(periods)) == [
        f"{year}{end}" for year in (YEAR - 1, YEAR) for end in ("0331", "0630", "0930", "1231")
    ]


# --- route selection -----------------------------------------------------------------------------


def test_subject_still_goes_one_security_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(monkeypatch, Market(FILINGS))

    result = _build(
        tmp_path, "--year", str(YEAR), "--dataset", INCOME_DATASET, "--subject", REGISTERED[0]
    )

    assert result.exit_code == PanelExit.ok, result.output
    assert market.api_names() == [INCOME_DATASET]
    assert [str(entry["params"]["ts_code"]) for entry in market.payloads] == [REGISTERED[0]]


def test_the_sweep_asks_each_month_once_and_halves_the_busy_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(monkeypatch, Market(FILINGS))

    result = _build(tmp_path, *_swept(INCOME_DATASET))

    assert result.exit_code == PanelExit.ok, result.output
    windows = [
        (str(entry["params"]["start_date"]), str(entry["params"]["end_date"]))
        for entry in market.payloads
        if entry["api_name"] == "income_vip"
    ]
    first_asked: dict[str, tuple[str, str]] = {}
    for window in windows:
        first_asked.setdefault(window[0][:6], window)
    # Each month is first asked whole, in order.
    assert list(first_asked) == [f"{YEAR}{month:02d}" for month in range(1, 13)]
    assert all(start.endswith("01") for start, _ in first_asked.values())
    # April is six rows against a cap of six: refused whole and halved until 28 April (five
    # rows) stands alone. Every other month is one request.
    april = [window for window in windows if window[0][:6] == f"{YEAR}04"]
    assert april == [
        (f"{YEAR}0401", f"{YEAR}0430"),
        (f"{YEAR}0401", f"{YEAR}0415"),
        (f"{YEAR}0416", f"{YEAR}0430"),
        (f"{YEAR}0416", f"{YEAR}0423"),
        (f"{YEAR}0424", f"{YEAR}0430"),
        (f"{YEAR}0424", f"{YEAR}0427"),
        (f"{YEAR}0428", f"{YEAR}0430"),
        (f"{YEAR}0428", f"{YEAR}0429"),
        (f"{YEAR}0428", f"{YEAR}0428"),
        (f"{YEAR}0429", f"{YEAR}0429"),
        (f"{YEAR}0430", f"{YEAR}0430"),
    ]
    assert len(windows) == 11 + len(april)
    assert f"BUDGET {INCOME_DATASET} year={YEAR} 12 windows" in result.stderr
    assert TOKEN not in result.output


# --- empty windows and an empty year ------------------------------------------------------------


def test_a_year_in_which_nothing_was_announced_is_refused_by_both_routes_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty year is a fetch that did not work, refused before it reaches the writer on either
    route and with the same exit code; the sweep refuses at its first closed empty month."""
    _install(monkeypatch, Market(()))

    swept = _build(tmp_path / "sweep", *_swept(INCOME_DATASET))
    by_subject = _build(tmp_path / "subject", *_by_subject(INCOME_DATASET))

    assert by_subject.exit_code == PanelExit.unhealthy
    assert swept.exit_code == PanelExit.unhealthy
    assert f"window {YEAR}01 had ended" in swept.output
    assert PanelStore(tmp_path / "sweep" / "panel").registered_years(INCOME_DATASET) == ()


def test_a_year_whose_only_filings_are_outside_the_registry_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, Market([f for f in FILINGS if f[0] == UNREGISTERED]))

    result = _build(tmp_path, *_swept(INCOME_DATASET))

    assert result.exit_code == PanelExit.unhealthy
    assert PanelStore(tmp_path / "panel").registered_years(INCOME_DATASET) == ()


def test_the_sweep_still_needs_the_registry_it_filters_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(monkeypatch, Market(FILINGS))

    result = _build(tmp_path, "--year", str(YEAR), "--dataset", INCOME_DATASET)

    assert result.exit_code == PanelExit.unhealthy
    assert "--dataset stock_basic" in result.output
    assert market.payloads == []


# --- an empty whole-market window (the review of 50c89ed) ----------------------------------------


class FlakyMarket(Market):
    """`Market`, except that the first request for each window in `empty_once` answers nothing."""

    def __init__(
        self, filings: Sequence[tuple[str, str, str, str, str, float]], empty_once: set[str]
    ) -> None:
        super().__init__(filings)
        self.empty_once = set(empty_once)

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        params: Mapping[str, str] = payload["params"]
        window = str(params.get("period") or params.get("start_date", ""))
        if str(payload["api_name"]).endswith("_vip") and window in self.empty_once:
            self.empty_once.discard(window)
            self.payloads.append(payload)
            return _envelope(str(payload["api_name"]).removesuffix("_vip"), [])
        return super().post(payload)


def _asked(market: Market, api: str, key: str, value: str) -> int:
    return sum(
        1
        for entry in market.payloads
        if entry["api_name"] == api and str(entry["params"].get(key)) == value
    )


def test_an_empty_closed_month_is_refused_after_one_re_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """June 2025 closed long before the clock and the whole market answers nothing for it, twice.
    That is not a quiet month -- none of 2015's or 2024's 72 live months held fewer than 30 stored
    rows -- it is a month of filings the partition would silently lack."""
    market = _install(monkeypatch, Market([f for f in FILINGS if not f[2].startswith(f"{YEAR}06")]))

    result = _build(tmp_path, *_swept(INCOME_DATASET))

    assert result.exit_code == PanelExit.unhealthy
    assert f"{INCOME_DATASET} year={YEAR}" in result.output
    assert f"window {YEAR}06" in result.output
    assert _asked(market, "income_vip", "start_date", f"{YEAR}0601") == 2
    assert PanelStore(tmp_path / "panel").registered_years(INCOME_DATASET) == ()


def test_a_transient_empty_answer_is_re_requested_once_and_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(monkeypatch, FlakyMarket(FILINGS, {f"{YEAR}0601"}))

    result = _build(tmp_path, *_swept(INCOME_DATASET))

    assert result.exit_code == PanelExit.ok, result.output
    assert _asked(market, "income_vip", "start_date", f"{YEAR}0601") == 2
    assert "1 empty closed window(s) re-requested" in result.stderr


def test_an_empty_closed_report_period_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(monkeypatch, Market([f for f in FILINGS if f[1] != f"{YEAR - 1}0630"]))

    result = _build(
        tmp_path,
        *_swept(FINANCIAL_INDICATOR_DATASET, years=("--start", str(YEAR - 1), "--end", str(YEAR))),
    )

    assert result.exit_code == PanelExit.unhealthy
    assert f"window {YEAR - 1}0630" in result.output
    assert _asked(market, "fina_indicator_vip", "period", f"{YEAR - 1}0630") == 2


def test_windows_after_the_clock_are_not_asked_and_an_open_empty_month_is_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 2026-03-20, April 2026 onwards has not begun and is never asked for. March has begun
    and not ended, so a whole market that has announced nothing in it yet is a fact about the
    clock, not a lost month -- asked once and not refused."""
    market = _install(
        monkeypatch, Market([f for f in FILINGS if not f[2].startswith(f"{YEAR + 1}03")])
    )

    result = _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", str(YEAR + 1))))

    assert result.exit_code == PanelExit.ok, result.output
    starts = [
        str(entry["params"]["start_date"])
        for entry in market.payloads
        if entry["api_name"] == "income_vip"
    ]
    assert starts == [f"{YEAR + 1}0101", f"{YEAR + 1}0201", f"{YEAR + 1}0301"]
    assert f"BUDGET {INCOME_DATASET} year={YEAR + 1} 3 windows" in result.stderr


# --- a report period is refused empty only after its statutory deadline ---------------------------


def _noon(day: str) -> datetime:
    """12:00 Asia/Shanghai on a `YYYYMMDD` day, as the UTC instant the fake clock returns."""
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 4, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("period", "clock", "listed", "refused"),
    [
        # Q3: due by 31 October. Ended, not yet due: an empty market is a timetable.
        (f"{YEAR}0930", f"{YEAR}1015", f"{YEAR}0102", False),
        (f"{YEAR}0930", f"{YEAR}1101", f"{YEAR}0102", True),
        # Annual: due by 30 April of the following year.
        (f"{YEAR}1231", f"{YEAR + 1}0331", f"{YEAR + 1}0102", False),
        (f"{YEAR}1231", f"{YEAR + 1}0501", f"{YEAR + 1}0102", True),
    ],
)
def test_an_empty_report_period_is_refused_only_after_its_disclosure_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    period: str,
    clock: str,
    listed: str,
    refused: bool,
) -> None:
    """The period's own end is the wrong trigger: every period is empty for days after it, and a
    refusal there would fail the daily update for weeks after each quarter end."""
    market = _install(
        monkeypatch,
        Market([f for f in FILINGS if f[1] != period], listed=listed),
        clock=_noon(clock),
    )

    result = _build(tmp_path, *_swept(FINANCIAL_INDICATOR_DATASET, years=("--year", str(YEAR))))

    asked = _asked(market, "fina_indicator_vip", "period", period)
    if refused:
        assert result.exit_code == PanelExit.unhealthy
        assert f"window {period}" in result.output
        assert asked == 2
    else:
        assert result.exit_code == PanelExit.ok, result.output
        assert asked == 1


# --- a capped report period is re-fetched in registry chunks (fix round 3) ------------------------


def test_a_capped_report_period_stores_what_an_uncapped_answer_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At a cap of three, 20250331 (five rows) and three other periods reach the cap; each is
    re-fetched as chunks of the stored registry's codes, halving a chunk that is itself capped.
    The partitions must be the ones a cap of six, where every period fits in one answer, stores."""
    span = ("--start", str(YEAR - 1), "--end", str(YEAR))
    wide = _install(monkeypatch, Market(FILINGS))
    uncapped = _build(tmp_path / "uncapped", *_swept(FINANCIAL_INDICATOR_DATASET, years=span))
    uncapped_requests = sum(1 for entry in wide.payloads if entry["api_name"].endswith("_vip"))

    sweep = tushare._TUSHARE_SWEEPS_BY_NAME[FINANCIAL_INDICATOR_DATASET]
    monkeypatch.setitem(
        tushare._TUSHARE_SWEEPS_BY_NAME,
        FINANCIAL_INDICATOR_DATASET,
        sweep.model_copy(update={"max_rows_per_response": 3}),
    )
    narrow = _install(monkeypatch, Market(FILINGS, cap=3))
    capped = _build(tmp_path / "capped", *_swept(FINANCIAL_INDICATOR_DATASET, years=span))

    assert uncapped.exit_code == PanelExit.ok, uncapped.output
    assert capped.exit_code == PanelExit.ok, capped.output
    for year in (YEAR, YEAR + 1):
        assert _stored(tmp_path / "capped", FINANCIAL_INDICATOR_DATASET, year) == _stored(
            tmp_path / "uncapped", FINANCIAL_INDICATOR_DATASET, year
        )
    chunked = [
        str(entry["params"]["ts_code"])
        for entry in narrow.payloads
        if entry["api_name"] == "fina_indicator_vip" and "ts_code" in entry["params"]
    ]
    assert chunked, "no period was re-fetched in chunks"
    assert all(set(chunk.split(",")) <= set(REGISTERED) for chunk in chunked)
    capped_requests = sum(1 for entry in narrow.payloads if entry["api_name"].endswith("_vip"))
    assert capped_requests > uncapped_requests == 8
    # The SWEPT lines report the requests the sweep actually made, chunks included.
    assert f"SWEPT {FINANCIAL_INDICATOR_DATASET} period-year={YEAR}" in capped.stderr
    swept_requests = sum(
        int(line.split(" requests")[0].rsplit(" ", 1)[1])
        for line in capped.stderr.splitlines()
        if line.startswith("SWEPT")
    )
    assert swept_requests == capped_requests


@pytest.mark.parametrize("dataset", ["income", "balancesheet", "cashflow"])
def test_a_capped_single_day_stores_what_an_uncapped_answer_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dataset: str
) -> None:
    """At a cap of four, 28 April 2025 (five rows) cannot be narrowed by date and is re-fetched as
    chunks of the stored registry's codes -- one chunk is itself capped and halved. The partition
    must be the one a cap of six, where the day fits in one answer, stores."""
    wide = _install(monkeypatch, Market(FILINGS))
    uncapped = _build(tmp_path / "uncapped", *_swept(dataset))
    assert not any("ts_code" in entry["params"] for entry in wide.payloads)

    sweep = tushare._TUSHARE_SWEEPS_BY_NAME[dataset]
    monkeypatch.setitem(
        tushare._TUSHARE_SWEEPS_BY_NAME,
        dataset,
        sweep.model_copy(update={"max_rows_per_response": 4}),
    )
    narrow = _install(monkeypatch, Market(FILINGS, cap=4))
    capped = _build(tmp_path / "capped", *_swept(dataset))

    assert uncapped.exit_code == PanelExit.ok, uncapped.output
    assert capped.exit_code == PanelExit.ok, capped.output
    assert _stored(tmp_path / "capped", dataset, YEAR) == _stored(
        tmp_path / "uncapped", dataset, YEAR
    )
    chunked = [entry["params"] for entry in narrow.payloads if "ts_code" in entry["params"]]
    assert {(entry["start_date"], entry["end_date"]) for entry in chunked} == {
        (f"{YEAR}0428", f"{YEAR}0428")
    }
    # 900001.SH files only on 28 April; in the capped build only the day's capped witness shows
    # it. Both builds count the same two unregistered securities (it and the filler), store none.
    assert "2 securities outside the stored registry" in uncapped.stderr
    assert "2 securities outside the stored registry" in capped.stderr


# --- V2-P6-018: an announcement year with no filing yet ----------------------------------------

JANUARY_2: Final[datetime] = datetime(2026, 1, 2, 4, 0, tzinfo=UTC)
JANUARY_FILINGS: Final = (*FILINGS, (FILLER, "20250930", "20251020", "20251020", "1", 81.0))
"""`FILINGS` with October 2025 filled: its only filing there is stored as a version re-announced on
5 January 2026, not yet knowable on 2 January, and a closed empty month is refused."""
"""12:00 Asia/Shanghai on 2 January 2026: nothing in `FILINGS` is announced in 2026 yet -- the
first is `FILLER` on 15 January. The stored record has three such first sessions in 2014-2026."""


def _no_filing_yet(root: Path, dataset: str, year: int) -> None:
    """The year is stored, empty, and says so: zero rows, a coverage record of zero rows observed
    at 2 January, and readers, the doctor and the gate that answer from it without refusing."""
    store = PanelStore(root / "panel")
    coverage = store.read_coverage(dataset, year)
    assert coverage is not None and coverage.row_count == 0
    assert coverage.subjects == () and coverage.dates == ()
    assert coverage.as_of == JANUARY_2 and coverage.last_event_time == JANUARY_2
    assert _stored(root, dataset, year)[1] == []
    for command in (["panel", "doctor"], ["data-check"]):
        checked = runner.invoke(
            app,
            [
                *command,
                "--runtime-dir",
                str(root),
                "--year",
                str(year),
                "--dataset",
                dataset,
                "--as-of",
                JANUARY_2.isoformat(),
                "--no-calendar",
            ],
        )
        assert checked.exit_code == 0, (command, checked.output)


@pytest.mark.parametrize("dataset", ["income", "balancesheet", "cashflow"])
def test_on_2_january_an_announcement_year_with_no_filing_yet_is_recorded_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dataset: str
) -> None:
    """Before `V2-P6-018` the sweep refused the year -- "none of the 1 month windows served a
    filing by a security in the stored registry" -- and a statement-reading configuration could
    not be run on a year's first session. The year is now admissible while no statutory deadline
    in it has passed (30 April), recorded empty, and read as "no filing yet": the histories answer
    from 2025, and the doctor and the gate clear the empty year."""
    _install(monkeypatch, Market(JANUARY_FILINGS), clock=JANUARY_2)
    seeded = _build(tmp_path, *_swept(dataset, years=("--year", "2025")))
    assert seeded.exit_code == PanelExit.ok, seeded.output

    result = _build(tmp_path, *_swept(dataset, years=("--year", "2026")))

    assert result.exit_code == PanelExit.ok, result.output
    assert f"EMPTY {dataset} year=2026" in result.stderr
    _no_filing_yet(tmp_path, dataset, 2026)
    histories = load_statement_histories(
        PanelStore(tmp_path / "panel"),
        dataset=dataset,
        years=(2025, 2026),
        as_of=JANUARY_2,
        max_staleness=None,
    )
    assert set(histories) == {"000001.SZ", "000002.SZ", "600000.SH"}


def test_on_2_january_fina_indicators_announcement_year_with_no_report_yet_is_recorded_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fina_indicator` is swept by report period, so its build does not refuse: period year 2025
    files into announcement year 2025, and 2026 -- which the 2025 annuals will file into -- was
    simply not written, `partition_missing` to every reader. It is now recorded empty."""
    _install(monkeypatch, Market(JANUARY_FILINGS), clock=JANUARY_2)

    result = _build(tmp_path, *_swept(FINANCIAL_INDICATOR_DATASET, years=("--year", "2025")))

    assert result.exit_code == PanelExit.ok, result.output
    assert f"EMPTY {FINANCIAL_INDICATOR_DATASET} year=2026" in result.stderr
    _no_filing_yet(tmp_path, FINANCIAL_INDICATOR_DATASET, 2026)


def test_an_empty_year_is_as_fresh_as_its_observation_and_no_fresher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed empty on 20 January (nothing registered filed in 2026 yet). A reader on 25
    January with a twenty-day bound reads it -- the observation is five days old, though the year
    began twenty-four days before. A reader in March with a thirty-day bound is refused, rather
    than told that nothing has been filed since January."""
    filings = [f for f in JANUARY_FILINGS if f[2] < "20260101" or f[0] not in REGISTERED]
    _install(monkeypatch, Market(filings), clock=_noon("20260120"))
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2025"))).exit_code == 0
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2026"))).exit_code == 0
    store = PanelStore(tmp_path / "panel")

    fresh = load_statement_histories(
        store,
        dataset=INCOME_DATASET,
        years=(2025, 2026),
        as_of=_noon("20260125"),
        max_staleness=timedelta(days=20),
    )
    assert fresh
    with pytest.raises(PanelStorageError, match="stale"):
        load_statement_histories(
            store,
            dataset=INCOME_DATASET,
            years=(2025, 2026),
            as_of=_noon("20260302"),
            max_staleness=timedelta(days=30),
        )


@pytest.mark.parametrize(("clock", "admitted"), [("20260430", True), ("20260501", False)])
def test_an_announcement_year_may_be_empty_only_until_its_first_statutory_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: str, admitted: bool
) -> None:
    """Every month of 2026 answered -- by an unregistered filer -- and none by a registered one.
    On 30 April, the day the 2025 annual reports and the 2026 first quarters are due, that is
    still "not filed yet"; on 1 May it is a failed fetch and the build is refused."""
    filings = [f for f in FILINGS if f[2] < "20260101" or f[0] not in REGISTERED]
    filings.append((FILLER, "20251231", "20260420", "20260420", "1", 81.0))
    _install(monkeypatch, Market(filings), clock=_noon(clock))
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2025"))).exit_code == 0

    result = _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2026")))

    store = PanelStore(tmp_path / "panel")
    if admitted:
        assert result.exit_code == PanelExit.ok, result.output
        coverage = store.read_coverage(INCOME_DATASET, 2026)
        assert coverage is not None and coverage.row_count == 0
    else:
        assert result.exit_code == PanelExit.unhealthy
        assert "none of the 5 month windows served a filing" in result.output
        assert 2026 not in store.registered_years(INCOME_DATASET)


def test_an_empty_year_is_never_written_over_filed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty answer where rows are stored is a lost fetch, not a fact about the year."""
    from openalpha_cn.domain.financial_statements import FinancialStatementError
    from openalpha_cn.panel_ingest import write_empty_announcement_year

    _install(monkeypatch, Market(FILINGS), clock=_noon("20260320"))
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2026"))).exit_code == 0
    store = PanelStore(tmp_path / "panel")

    with pytest.raises(FinancialStatementError, match="already holds"):
        write_empty_announcement_year(
            store, dataset=INCOME_DATASET, year=2026, observed_at=_noon("20260320")
        )


# --- V2-P6-018: an incremental build re-sweeps the months that can still change ----------------


@pytest.mark.parametrize(
    ("last_build", "now", "expected"),
    [
        # Daily: the previous and the current month, plus Thursday's April and September.
        ("20251210", "20251211", ["202504", "202509", "202511", "202512"]),
        # The first run of a month reaches back to the month before the previous one.
        ("20251128", "20251201", ["202501", "202506", "202510", "202511", "202512"]),
        # A gap of more than a week: every weekday's group has passed, so the whole year.
        ("20250610", "20251209", [f"2025{month:02d}" for month in range(1, 13)]),
        # A holiday Monday: Tuesday's run catches up Monday's group as well as its own.
        ("20251205", "20251209", ["202501", "202502", "202506", "202507", "202511", "202512"]),
        # A weekend run re-sweeps the trailing months only.
        ("20251212", "20251213", ["202511", "202512"]),
    ],
)
def test_the_months_an_incremental_build_re_sweeps(
    last_build: str, now: str, expected: list[str]
) -> None:
    windows = tuple(f"2025{month:02d}" for month in range(1, 13))
    assert (
        list(cli.statement_resweep_windows(windows, last_build=_noon(last_build), now=_noon(now)))
        == expected
    )


LISTED_LATE: Final[str] = "300999.SZ"
"""Listed on 5 December 2025, after the seed build: its pre-listing filing is dated in May."""


class GrowingMarket(Market):
    """`Market` whose registry gains `LISTED_LATE` once `grown` is set, and whose answers to a
    comma-joined `ts_code` list come back empty while `hide_listed` is."""

    grown = False
    hide_listed = False

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (
            self.hide_listed
            and "ts_code" in payload["params"]
            and payload["api_name"].endswith("_vip")
        ):
            self.payloads.append(payload)
            return _envelope(str(payload["api_name"]).removesuffix("_vip"), [])
        answer = super().post(payload)
        if payload["api_name"] == STOCK_BASIC_DATASET and self.grown:
            answer["data"]["items"].append(
                [LISTED_LATE, LISTED_LATE, "SZSE", "创业板", "L", "20251205", None]
            )
        return answer


def _monthly_filings() -> list[tuple[str, str, str, str, str, float]]:
    """A registered filing in every month of 2025 (so a changed one can be placed anywhere),
    April's cap-reaching day kept from `FILINGS`."""
    rows = [f for f in FILINGS if f[2].startswith("2025")]
    rows.extend(
        ("000002.SZ", "20241231", f"2025{month:02d}20", f"2025{month:02d}20", "1", 40.0 + month)
        for month in range(1, 13)
    )
    rows.append((LISTED_LATE, "20241231", "20250520", "20250520", "1", 77.0))
    return rows


def _changed(
    filings: Sequence[tuple[str, str, str, str, str, float]], month: int
) -> list[tuple[str, str, str, str, str, float]]:
    """`filings` with 000002.SZ's filing announced in `month` restated in place (same key)."""
    marker = f"2025{month:02d}20"
    return [
        (*f[:5], f[5] + 1000.0) if f[0] == "000002.SZ" and f[2] == marker else f for f in filings
    ]


@pytest.mark.parametrize(("month", "caught"), [(11, True), (9, True), (6, False)])
def test_an_incremental_build_matches_a_full_one_unless_a_carried_month_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, month: int, caught: bool
) -> None:
    """Seeded on Wednesday 10 December 2025; on Thursday 11 December the upstream has restated a
    filing announced in `month` and the registry has gained a security whose filing is dated May.

    November is inside the trailing window and September is Thursday's rotation, so the change is
    re-swept and the incremental partition is hash-equal to a full build at the same `as_of`. June
    is carried that day -- the premise, stated rather than hidden: the incremental partition then
    still holds the old value until June's rotation day, and differs from the full build by it.
    The new listing's May filing is fetched either way, by the one listed-securities request."""
    market = _install(
        monkeypatch,
        GrowingMarket(_monthly_filings(), listed=f"{YEAR}0102"),
        clock=_noon("20251210"),
    )
    seeded = _build(tmp_path / "daily", *_swept(INCOME_DATASET), "--incremental")
    assert seeded.exit_code == PanelExit.ok, seeded.output

    market.filings = tuple(_changed(market.filings, month))
    market.grown = True
    market.payloads.clear()
    monkeypatch.setattr(cli, "_panel_clock", lambda: _noon("20251211"))
    daily = _build(tmp_path / "daily", *_swept(INCOME_DATASET), "--incremental")
    assert daily.exit_code == PanelExit.ok, daily.output
    full = _build(tmp_path / "full", *_swept(INCOME_DATASET))
    assert full.exit_code == PanelExit.ok, full.output

    daily_hash, daily_rows = _stored(tmp_path / "daily", INCOME_DATASET, YEAR)
    full_hash, _full_rows = _stored(tmp_path / "full", INCOME_DATASET, YEAR)
    assert any(row[0] == LISTED_LATE for row in daily_rows)
    assert (daily_hash == full_hash) is caught
    restated = [row for row in daily_rows if row[0] == "000002.SZ" and row[-1] > 1000.0]
    assert bool(restated) is caught


def test_an_incremental_build_asks_only_for_the_re_swept_months_and_the_new_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = _install(
        monkeypatch,
        GrowingMarket(_monthly_filings(), listed=f"{YEAR}0102"),
        clock=_noon("20251210"),
    )
    assert _build(tmp_path, *_swept(INCOME_DATASET), "--incremental").exit_code == 0
    market.grown = True
    market.payloads.clear()
    monkeypatch.setattr(cli, "_panel_clock", lambda: _noon("20251211"))

    result = _build(tmp_path, *_swept(INCOME_DATASET), "--incremental")

    assert result.exit_code == PanelExit.ok, result.output
    asked = [e["params"] for e in market.payloads if e["api_name"] == "income_vip"]
    months = sorted({str(p["start_date"])[:6] for p in asked if "ts_code" not in p})
    assert months == ["202504", "202509", "202511", "202512"]
    # April's stored census already reaches the cap, so the month is split without being asked.
    assert {"start_date": "20250401", "end_date": "20250430"} not in asked
    (listed,) = [p for p in asked if "ts_code" in p]
    assert listed == {"start_date": "20250101", "end_date": "20251231", "ts_code": LISTED_LATE}
    assert "INCREMENTAL income year=2025 re-sweeps 4 of 12" in result.stderr


# --- V2-P6-018, review round 5 ----------------------------------------------------------------


def _sessions(first: date, last: date, closed: set[date]) -> list[date]:
    days = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
    return [day for day in days if day.weekday() < 5 and day not in closed]


def _holidays(first: str, last: str) -> set[date]:
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return {start + timedelta(days=offset) for offset in range((end - start).days + 1)}


@pytest.mark.parametrize(
    ("closed", "missed"),
    [
        # 2023: Friday 29 September and Friday 6 October were both holidays.
        (_holidays("2023-09-29", "2023-10-06"), set()),
        # 2025: Wednesday 1 October to Wednesday 8 October, and a run missed on 20 October.
        (_holidays("2025-10-01", "2025-10-08"), {date(2025, 10, 20)}),
    ],
)
def test_every_month_is_re_swept_within_seven_days_of_every_build(
    closed: set[date], missed: set[date]
) -> None:
    """The bound the rotation guarantees, driven over the real holidays that broke the earlier
    one: when a build finishes, no month of the year has gone more than seven calendar days
    without a re-sweep -- whatever holidays or missed runs came before it."""
    year = min(closed).year
    runs = [
        day for day in _sessions(date(year, 9, 1), date(year, 11, 28), closed) if day not in missed
    ]
    last_swept = {month: runs[0] for month in range(1, 13)}  # the full build the daily began with
    for previous, day in pairwise(runs):
        opened = tuple(f"{year}{month:02d}" for month in range(1, day.month + 1))
        for window in cli.statement_resweep_windows(
            opened, last_build=_noon(previous.strftime("%Y%m%d")), now=_noon(day.strftime("%Y%m%d"))
        ):
            last_swept[int(window[4:])] = day
        stale = {
            month: (day - last_swept[month]).days
            for month in range(1, day.month + 1)
            if (day - last_swept[month]).days > 7
        }
        assert stale == {}, (day, stale)


def test_a_listed_answer_that_leaves_out_a_held_security_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The newly listed securities' answer replaces their stored rows. Empty for a security the
    partition holds -- asked twice -- it is a failed fetch, and writing it would drop that
    security's filings; refused by name, nothing written."""
    market = _install(
        monkeypatch,
        GrowingMarket(_monthly_filings(), listed=f"{YEAR}0102"),
        clock=_noon("20251210"),
    )
    market.grown = True
    assert _build(tmp_path, *_swept(INCOME_DATASET), "--incremental").exit_code == 0
    before = _stored(tmp_path, INCOME_DATASET, YEAR)
    assert any(row[0] == LISTED_LATE for row in before[1])
    market.hide_listed = True
    market.payloads.clear()
    monkeypatch.setattr(cli, "_panel_clock", lambda: _noon("20251211"))

    result = _build(tmp_path, *_swept(INCOME_DATASET), "--incremental")

    assert result.exit_code == PanelExit.unhealthy
    assert LISTED_LATE in result.output and "asked twice" in result.output
    assert sum(1 for e in market.payloads if "ts_code" in e["params"]) == 2
    assert _stored(tmp_path, INCOME_DATASET, YEAR) == before


def _moved(filings: Sequence[tuple[str, str, str, str, str, float]], announced: str) -> list:
    """`filings` with 000002.SZ's 2025 first-quarter report re-published on `announced` and the
    old version no longer served -- how `fina_indicator` publishes a correction."""
    return [
        (*f[:2], announced, announced, *f[4:]) if f[0] == "000002.SZ" and f[1] == "20250331" else f
        for f in filings
    ]


def test_a_fina_indicator_report_re_published_in_another_year_moves_with_its_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Announcement year 2025 loses the report and 2026 gains it, in one incremental build. The
    shrink is admitted because the same build writes the key into 2026, and the superseded row
    is kept whole in `superseded_fina_indicator`'s 2025 partition. Before, every daily build
    from then on was refused."""
    from openalpha_cn.panel_ingest import (
        SUPERSEDED_CONFIRMED_AT_COLUMN,
        SUPERSEDED_INDICATOR_DATASET,
    )

    market = _install(monkeypatch, Market(FILINGS), clock=CLOCK)
    arguments = _swept(FINANCIAL_INDICATOR_DATASET)
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    stored_2025 = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)[1]
    market.filings = tuple(_moved(market.filings, "20260318"))
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=1))

    result = _build(tmp_path, *arguments, "--incremental")

    assert result.exit_code == PanelExit.ok, result.output
    after_2025 = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)[1]
    after_2026 = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR + 1)[1]
    assert len(after_2025) == len(stored_2025) - 1
    assert any(row[0] == "000002.SZ" and row[5] == "2025-03-31" for row in after_2026)
    store = PanelStore(tmp_path / "panel")
    evidence = store.read_coverage(SUPERSEDED_INDICATOR_DATASET, YEAR)
    assert evidence is not None and evidence.subjects == ("000002.SZ",)
    names = [f.name for f in evidence.fields]
    (kept,) = _stored(tmp_path, SUPERSEDED_INDICATOR_DATASET, YEAR)[1]
    old = next(row for row in stored_2025 if row[0] == "000002.SZ" and row[5] == "2025-03-31")
    # Kept exactly as stored: every data column, and the stored stamp in original_ingested_time.
    assert kept[5 : len(old)] == old[5:]
    assert kept[names.index("original_ingested_time")] == old[names.index("ingested_time")]
    assert SUPERSEDED_CONFIRMED_AT_COLUMN in names
    # Indexed in upstream_defects under its own announcement day.
    (record,) = load_upstream_defects(store, years=(YEAR,), as_of=CLOCK + timedelta(days=2))
    assert (record.ts_code, record.trade_date, record.source_dataset, record.kind) == (
        "000002.SZ",
        date(2025, 4, 29),
        FINANCIAL_INDICATOR_DATASET,
        "superseded_after_publication",
    )


def test_a_fina_indicator_report_that_goes_nowhere_still_refuses_the_shrink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report no longer served and re-published nowhere is not a correction: the shrink is
    refused by name, as before, and nothing is written."""
    market = _install(monkeypatch, Market(FILINGS), clock=CLOCK)
    arguments = _swept(FINANCIAL_INDICATOR_DATASET)
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    before = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)
    market.filings = tuple(
        f for f in market.filings if not (f[0] == "000002.SZ" and f[1] == "20250331")
    )
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=1))

    result = _build(tmp_path, *arguments, "--incremental")

    assert result.exit_code == PanelExit.unhealthy
    assert "000002.SZ 2025-03-31" in result.output
    assert _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR) == before


@pytest.mark.parametrize(("clock", "readable"), [("20260430", True), ("20260501", False)])
def test_an_empty_year_is_unreadable_after_its_first_deadline_even_without_a_staleness_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: str, readable: bool
) -> None:
    """The writer's rule, applied at read time: recorded empty on 2 January, the year may be read
    as "no filing yet" through 30 April and not after, with `max_staleness=None`."""
    _install(monkeypatch, Market(JANUARY_FILINGS), clock=JANUARY_2)
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2025"))).exit_code == 0
    assert _build(tmp_path, *_swept(INCOME_DATASET, years=("--year", "2026"))).exit_code == 0

    def read() -> object:
        return load_statement_histories(
            PanelStore(tmp_path / "panel"),
            dataset=INCOME_DATASET,
            years=(2025, 2026),
            as_of=_noon(clock),
            max_staleness=None,
        )

    if readable:
        assert read()
    else:
        with pytest.raises(PanelStorageError, match="stale by rule"):
            read()


# --- V2-P6-018 on V2-P6-016: supersession at the version, carried and retired -----------------

SECOND_VERSION: Final = ("000002.SZ", "20250331", "20250601", "20250601", "1", 33.0)
"""A later version of 000002.SZ's 2025 first-quarter report, announced in 2025 as well."""


def _without(filings: Sequence[tuple[str, str, str, str, str, float]], announced: str) -> list:
    """`filings` with 000002.SZ's 2025 first-quarter version announced on `announced` no longer
    served."""
    return [
        f for f in filings if not (f[0] == "000002.SZ" and f[1] == "20250331" and f[2] == announced)
    ]


def _republished(announced: str) -> tuple[str, str, str, str, str, float]:
    return ("000002.SZ", "20250331", announced, announced, "1", 34.0)


def _two_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Market, list[str]]:
    market = _install(monkeypatch, Market((*FILINGS, SECOND_VERSION)), clock=CLOCK)
    arguments = _swept(FINANCIAL_INDICATOR_DATASET)
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=1))
    return market, arguments


def test_a_version_superseded_while_another_version_is_still_held_is_a_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Announcement year 2025 holds two versions of 000002.SZ's report (29 April and 1 June).
    The upstream stops serving the 29 April one and re-publishes the report in 2026. The key is
    still held in 2025 -- by the June version -- so a key-level rule saw nothing leave and the
    count guard refused the year; at the version, 29 April is superseded, kept, and indexed."""
    from openalpha_cn.panel_ingest import SUPERSEDED_INDICATOR_DATASET

    market, arguments = _two_versions(tmp_path, monkeypatch)
    market.filings = (*_without(market.filings, "20250429"), _republished("20260318"))

    result = _build(tmp_path, *arguments, "--incremental")

    assert result.exit_code == PanelExit.ok, result.output
    kept = _stored(tmp_path, SUPERSEDED_INDICATOR_DATASET, YEAR)[1]
    assert [(row[0], row[5], row[6]) for row in kept] == [("000002.SZ", "2025-03-31", "2025-04-29")]
    held = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)[1]
    assert ("000002.SZ", "2025-03-31", "2025-06-01") in {(r[0], r[5], r[6]) for r in held}


def test_a_lost_version_with_no_later_one_served_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The June version stops being served and nothing later replaces it -- the April one is
    earlier. That is not a correction, and the build is refused naming the version."""
    market, arguments = _two_versions(tmp_path, monkeypatch)
    before = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)
    market.filings = tuple(_without(market.filings, "20250601"))

    result = _build(tmp_path, *arguments, "--incremental")

    assert result.exit_code == PanelExit.unhealthy
    assert "000002.SZ 2025-03-31 2025-06-01" in result.output
    assert _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR) == before


def test_a_superseded_version_served_again_in_its_year_is_retired_with_its_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V2-P6-016's retirement rule for a withdrawn row, for a superseded version: the next day the
    upstream serves the 29 April version again (beside the 2026 one). 2025 stores it again, and
    both the kept copy and its `upstream_defects` record are gone -- neither can truthfully
    describe the store any more. A day in between that changes nothing carries both, re-stamped."""
    from openalpha_cn.panel_ingest import SUPERSEDED_INDICATOR_DATASET

    market = _install(monkeypatch, Market(FILINGS), clock=CLOCK)
    arguments = _swept(FINANCIAL_INDICATOR_DATASET)
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    original = market.filings
    market.filings = tuple(_moved(original, "20260318"))
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=1))
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    store = PanelStore(tmp_path / "panel")
    first = store.read_coverage(SUPERSEDED_INDICATOR_DATASET, YEAR)
    assert first is not None
    names = [field.name for field in first.fields]
    (first_row,) = _stored(tmp_path, SUPERSEDED_INDICATOR_DATASET, YEAR)[1]
    for command in (["panel", "doctor"], ["data-check"]):
        checked = runner.invoke(
            app,
            [
                *command,
                "--runtime-dir",
                str(tmp_path),
                "--year",
                str(YEAR),
                "--year",
                str(YEAR - 1),
                "--dataset",
                SUPERSEDED_INDICATOR_DATASET,
                "--as-of",
                (CLOCK + timedelta(days=1)).isoformat(),
                "--no-calendar",
            ],
        )
        assert checked.exit_code == 0, (command, checked.output)

    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=2))
    assert _build(tmp_path, *arguments, "--incremental").exit_code == 0
    carried = store.read_coverage(SUPERSEDED_INDICATOR_DATASET, YEAR)
    assert carried is not None and carried.row_count == 1
    # Carried per V2-P6-003: ingested_time re-stamped, the stored stamp kept in its own column.
    (carried_row,) = _stored(tmp_path, SUPERSEDED_INDICATOR_DATASET, YEAR)[1]
    ingested, kept_stamp = names.index("ingested_time"), names.index("original_ingested_time")
    assert carried_row[ingested] > first_row[ingested]
    assert carried_row[kept_stamp] == first_row[kept_stamp]
    assert len(load_upstream_defects(store, years=(YEAR,), as_of=CLOCK + timedelta(days=3))) == 1

    market.filings = (*original, _republished("20260318"))
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK + timedelta(days=3))
    result = _build(tmp_path, *arguments, "--incremental")

    assert result.exit_code == PanelExit.ok, result.output
    assert YEAR not in store.registered_years(SUPERSEDED_INDICATOR_DATASET)
    assert YEAR not in store.registered_years("upstream_defects")
    held = _stored(tmp_path, FINANCIAL_INDICATOR_DATASET, YEAR)[1]
    assert ("000002.SZ", "2025-03-31", "2025-04-29") in {(r[0], r[5], r[6]) for r in held}
