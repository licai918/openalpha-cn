"""One partial registry, three surfaces, and the sentence each of them reaches a user with.

`V2-P4-060` taught the **factor** face that a registry read can refuse with a statement about the
stored registry's shape rather than about a partition: `StockUniverseError` for an orphan delisting
row, `PanelBatchError` for a lifecycle year the read was told to cover and does not. Both are facts
about data, so both belong under `panel_unreadable`, and `factor_view._REGISTRY_FAULTS` is where
that was written down.

`V2-P4-070` is the same store meeting the **shortlist** face, which reads the same registry through
`shortlist_view`'s own four-member list and catches neither. Measured on the fixture below, before
the fix::

    factor build                 -> exit 1, "999999.SZ has a delisting row and no listing row ..."
    shortlist run                -> exit 5, "did not finish: it raised an unhandled
                                    StockUniverseError ... The exception's own message is withheld"
    POST /api/v1/shortlists/run  -> 500, text/plain, "Internal Server Error"

One broken partition, filed as a verdict about the panel on one channel and as a defect in the
command on two others. `internal_error` is the row whose whole meaning is "nothing was judged and
the remedy is a bug report" (`cli.PanelExit`), and `docs/api/http.md` says the same of `500` -- so
the two shipped sentences were false for as long as this path could reach them, and the user was
told to report a bug when the remedy was to finish the interrupted registry backfill.

## Why this file drives surfaces rather than `shortlist_view`

The unit pin that existed -- `set(SHORTLIST_PANEL_FAULTS) == set(FACTOR_PANEL_FAULTS)` -- was true
throughout, because `V2-P4-060` widened the factor face at the *read* (`_read_registry`'s `faults=`
argument) and not at the module constant the test compared. A constant-to-constant assertion cannot
see that, which is why the taxonomy pin in `tests/unit/test_shortlist_view.py` is now on the read
seams and why the product statement is here, where an exit code and a status code are observable.

## The store

An interrupted registry backfill, which is the shape `write_stock_universe`'s own docstring names:
the two-phase write filed the newest lifecycle year and never reached the years beneath it, so a
security that listed in 2010 and died in 2026 has its delisting row in the store and its listing
row nowhere. `stock_universe_from_panel_rows` refuses that, correctly, as a partial read --
`tests/integration/test_cli_factor_universe_scope.py::interrupted_backfill` is the same state built
the same way, and the residue it names ("widening cannot invent partitions that were never
written") is exactly what keeps this reachable.

The factor partition is written before the registry is truncated, because the shortlist face reads
the components first and a store with no factor rows would be refused two steps earlier for an
unrelated reason.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import panel_fixtures
import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import SHORTLIST_HTTP_STATUS, create_app
from openalpha_cn.cli import FACTOR_EXIT, SHORTLIST_EXIT, PanelExit, app
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import split_panel_batch_by_year, write_panel_batch
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER

runner = CliRunner()

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""After the panel's last session closed, and the instant every face here asks about."""

ORPHAN: Final[str] = "999999.SZ"
"""The security whose listing row the interrupted backfill never wrote."""

ORPHAN_LISTED: Final[date] = date(2010, 5, 6)
"""Sixteen lifecycle years below the one the store holds -- which is what puts its listing row in
a partition that does not exist rather than in the one that does."""

ORPHAN_DELISTED: Final[date] = date(2026, 1, 5)
"""Knowable before `AS_OF`. A termination the read cannot yet see is filtered out and the store
stops being partial, which is a fixture that proves nothing."""

REFUSAL: Final[str] = "has a delisting row and no listing row"
"""`stock_universe_from_panel_rows`' own sentence, and the one thing a user needs off any of the
three channels: which security, and that this is a partial read rather than a market."""


def _midnight_shanghai(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC) - timedelta(hours=8)


def _registry() -> ColumnarPanelBatch:
    """The whole registry the backfill was fetching: eight listings, and one security's pair."""
    codes = [*SECURITIES, ORPHAN, ORPHAN]
    events = ["listing"] * len(SECURITIES) + ["listing", "delisting"]
    days = [panel_fixtures.LISTED_ON] * len(SECURITIES) + [ORPHAN_LISTED, ORPHAN_DELISTED]
    clocks = tuple(_midnight_shanghai(day) for day in days)
    return ColumnarPanelBatch(
        provider_id="tushare",
        dataset=STOCK_BASIC_DATASET,
        kind=STOCK_BASIC_DATASET,
        as_of=panel_fixtures.AS_OF,
        fetched_at=panel_fixtures.AS_OF,
        status="success",
        subjects=tuple(codes),
        timeline=TimelineColumns(
            event_time=clocks, available_time=clocks, ingested_time=clocks, revision_time=clocks
        ),
        columns=(
            PanelColumn("lifecycle_event", "string", tuple(events)),
            PanelColumn("lifecycle_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("exchange", "string", tuple(EXCHANGE for _ in codes)),
        ),
    )


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """`(whole, partial)`: one panel and one real `factor build`, under two registry vintages.

    Module-scoped and copied per test for `test_cli_factor_universe_scope.py::markets`' reason --
    the panel generation and the factor build are the expensive half and no test here mutates a
    store it did not copy.

    Built through `openalpha factor build` rather than through `compute_factor` with an injected
    evaluator, because the *order* is the fixture's whole content: the factor partition was
    written while the registry was whole, and the registry became partial afterwards. A store
    whose factor rows were injected could not have been in that order.

    `daily.close_moves_between_sessions` is asked for so the shipped `reversal_1d/v1` produces
    eight different numbers. Without it every close is flat, every one-session reversal is the
    same, and the funnel answers `degenerate_scores` -- which refuses the control below for a
    reason that has nothing to do with a registry.

    The **whole** registry is that control, and it is not decoration: without it every assertion
    here is satisfied by a face that refuses everything, which is the shape a fail-closed fix
    reaches for on its own.
    """
    root = tmp_path_factory.mktemp("partial-registry")
    whole = root / "whole"
    write_generated_panel(
        PanelStore(whole / "panel"), generate_panel(shapes=("daily.close_moves_between_sessions",))
    )
    built = _factor_build(whole)
    assert built.exit_code == PanelExit.ok, built.stderr

    partial = root / "partial"
    shutil.copytree(whole, partial)
    # The interrupted two-phase write: only the newest lifecycle year reaches the store, so the
    # orphan's listing row -- which belongs to 2010 -- is in no partition at all.
    for year, yearly in split_panel_batch_by_year(_registry()):
        if year == YEAR:
            write_panel_batch(PanelStore(partial / "panel"), yearly, year=year)

    yield whole, partial


@pytest.fixture
def partial(stores: tuple[Path, Path], tmp_path: Path) -> Path:
    runtime = tmp_path / "partial"
    shutil.copytree(stores[1], runtime)
    return runtime


@pytest.fixture
def whole(stores: tuple[Path, Path], tmp_path: Path) -> Path:
    runtime = tmp_path / "whole"
    shutil.copytree(stores[0], runtime)
    return runtime


def _factor_build(runtime: Path) -> Any:
    return runner.invoke(
        app,
        [
            "factor", "build",
            "--factor", "reversal_1d/v1",
            "--tier", "raw",
            "--as-of", AS_OF.isoformat(),
            "--max-staleness-days", "30",
            "--runtime-dir", str(runtime),
            "--exchange", EXCHANGE,
            "--year", str(YEAR),
            "--json",
        ],
    )  # fmt: skip


def _shortlist_run(runtime: Path) -> Any:
    return runner.invoke(
        app,
        [
            "shortlist", "run",
            "--runtime-dir", str(runtime),
            "--tier", "raw",
            "--shortlist-size", "2",
            "--position-capital", "100000",
            "--as-of", AS_OF.isoformat(),
            "--exchange", EXCHANGE,
            "--horizon", "5d",
            "--min-tradable-ratio", "0.0",
            "--min-researched-ratio", "0.0",
            "--max-ranking-age-days", "3650",
            "--code-commit", COMMIT,
            "--config-digest", CONFIG_DIGEST,
            "--component", "reversal_1d/v1=1.0",
            "--year", str(YEAR),
            "--json",
        ],
    )  # fmt: skip


def _shortlist_body() -> dict[str, Any]:
    return {
        "components": [{"factor": "reversal_1d/v1", "weight": 1.0}],
        "tier": "raw",
        "shortlist_size": 2,
        "position_capital": "100000",
        "as_of": AS_OF.isoformat(),
        "years": [YEAR],
        "exchange": EXCHANGE,
        "horizon": "5d",
        "minimum_tradable_ratio": 0.0,
        "minimum_researched_ratio": 0.0,
        "maximum_ranking_age_days": 3650,
        "code_commit": COMMIT,
        "config_digest": CONFIG_DIGEST,
    }


def _post(runtime: Path) -> Any:
    """The route's answer as a caller over the wire sees it.

    `raise_server_exceptions=False` because the defect this file was written for *is* the
    unhandled exception: with the default, `TestClient` re-raises it inside the test and the
    status code the caller would have received is never observed.
    """
    with TestClient(create_app(runtime_dir=runtime), raise_server_exceptions=False) as client:
        return client.post("/api/v1/shortlists/run", json=_shortlist_body())


def test_the_factor_face_calls_a_partial_registry_a_verdict_about_the_panel(
    partial: Path,
) -> None:
    """`V2-P4-060`'s half, re-driven here so the comparison below has both sides on one store."""
    result = _factor_build(partial)

    assert result.exit_code == FACTOR_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert REFUSAL in result.stderr
    assert "unhandled" not in result.stderr


def test_the_shortlist_face_answers_the_same_store_the_same_way(partial: Path) -> None:
    """The other face of one question, which is what makes the equality worth pinning.

    Not "some non-zero exit": the *same* exit as the factor face, carrying the *same* sentence.
    An `internal_error` here would tell a user holding an interrupted backfill that the command
    is defective and that nothing was checked -- and would withhold the one sentence that says
    what to do, on the correct grounds that an unanticipated frame can be holding the credential.
    The withholding is right; being unanticipated is what was wrong.
    """
    shortlist = _shortlist_run(partial)

    assert shortlist.exit_code == SHORTLIST_EXIT["panel_unreadable"], shortlist.stderr
    assert shortlist.exit_code == _factor_build(partial).exit_code
    assert REFUSAL in shortlist.stderr
    assert "unhandled" not in shortlist.stderr
    assert ORPHAN in shortlist.stderr


def test_the_http_face_answers_a_verdict_rather_than_five_hundred(partial: Path) -> None:
    """`500` is the row that means "nothing was judged; report a bug", and this was not that.

    Asserted against the table rather than against a literal, because the claim is that this
    situation is `panel_unreadable` -- and a caller branching on `detail.reason` has to find it
    there rather than on a `text/plain` body Starlette wrote.
    """
    response = _post(partial)

    assert response.status_code == SHORTLIST_HTTP_STATUS["panel_unreadable"]
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert REFUSAL in response.json()["detail"]["message"]


def test_the_response_body_still_does_not_name_the_store_it_read(partial: Path) -> None:
    """The disclosable half of the same message, which the widening must not have skipped.

    `_read`'s whole arrangement is that the local message names the store and the response body
    does not: the CLI is inside the process that owns the store, while a body hands that path to
    whoever could reach the port. A refusal newly routed through it inherits that or defeats it.
    """
    response = _post(partial)

    assert str(partial) not in response.text
    assert PANEL_STORE_PLACEHOLDER in response.json()["detail"]["message"]


def test_a_whole_registry_is_still_screened_rather_than_refused(whole: Path) -> None:
    """The control. Both faces answer `0` on the same store with its registry intact, so the
    refusals above are about the partition and not about a face that learned to say no."""
    assert _factor_build(whole).exit_code == PanelExit.ok
    shortlist = _shortlist_run(whole)
    assert shortlist.exit_code == PanelExit.ok, shortlist.output + shortlist.stderr
    assert _post(whole).status_code == SHORTLIST_HTTP_STATUS["answered"]
