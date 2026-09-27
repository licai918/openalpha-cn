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
from datetime import UTC, datetime
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
from openalpha_cn.panel.store import PanelStore
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

    def __init__(self, filings: Sequence[tuple[str, str, str, str, str, float]]) -> None:
        self.filings = tuple(filings)
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
                        [code, code, "SSE", "主板", "L", "20260102", None] for code in REGISTERED
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
            chosen.reverse()
            assert "offset" not in params and "limit" not in params, params
            return _envelope(
                dataset,
                [_item(dataset, f) for f in chosen[:CAP]],
                more=len(chosen) > CAP,
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


def _install(monkeypatch: pytest.MonkeyPatch, market: Market) -> Market:
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "_panel_transport", lambda: market)
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
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
    assert f"window {YEAR}01 ended before" in swept.output
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
