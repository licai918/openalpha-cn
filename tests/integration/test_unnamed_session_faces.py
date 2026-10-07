"""One session no name was on file for, three surfaces, and the sentence each reaches a user with.

`V2-P4-070` taught both faces that the **registry** read can refuse with a statement about stored
data rather than about a partition, and put those refusals under `panel_unreadable`. `V2-P4-080` is
the same shape one dataset over, and it does not go through a read at all.

`MarketBar.is_st` is `history.risk_warning_on(session) is not RiskWarning.none`, written out at
`shortlist_view._bars_on` and at `factor_view._PanelInputs.market_bar`.
`NameHistory.risk_warning_on` delegates to `record_on`, which raises `NameHistoryHorizonError`
for a day before the history's first record -- deliberately, because "an unrecorded name is
unknown rather than equal to the earliest one on file". **Both call sites sat outside every
`_read` guard**, so neither `_PANEL_FAULTS` nor `_REGISTRY_FAULTS` ever saw it. Measured on the
store below, before the fix::

    shortlist run                -> exit 5, "did not finish: it raised an unhandled
                                    NameHistoryHorizonError ... The exception's own message is
                                    withheld"
    POST /api/v1/shortlists/run  -> 500, text/plain, "Internal Server Error"
    factor run                   -> exit 5, the same sentence with the same type name

## Why this needs no exotic corpus

`load_name_histories(store, years=request.years, ...)` is scoped to announcement years, so the
corpus a run sees holds only those years' announcements. Any security whose only rename in the
requested year is **announced before the priced session and effective after it** -- the ordinary
two-clock rename `domain/name_history.py` models on purpose, and 8,016 of that module's 14,166
measured rows separate the two clocks -- has `first_effective_date > session`. Nothing in the read
backfills the name it traded under before that: the announcement that established it is in a year
partition this run did not ask for.

## The fixture blindness this was hiding behind

`panel_fixtures._name_records` gave **every** security a baseline record effective at `LISTED_ON`,
three days before the window's first session, so `record_on` answered on every session of every
generated panel and this refusal was unreachable offline on both faces. That baseline is a
flattery: it makes `namechange` look better-formed than the corpus it stands for.
`name_history.effect_after_every_priced_session` is the shape that drops it for one security, and
it is what this file asks for.

## Why the remedy is not `is_st=False`

Defaulting is exactly what `record_on` refuses to do. What it costs was measured rather than
asserted, and the first answer was wrong: `MarketBar.is_st` is read at exactly one site --
`backtest/execution._price_band` -- and only when the published band is absent, which neither face
can produce, since both build every bar through `published_limit_fields(limit)` and build none
when the limit is missing. So a fabricated `False` moves no verdict on either face today. What it
does is record, on a field whose contract is "the risk-warning state of the name in effect", an
assertion the corpus does not support and no reader of the bar can tell from a measured one --
one caller away from the derivation `KNOWN_CROSS_SECTION_LIMITATIONS.
an_absent_published_band_is_refused_here_and_derived_by_the_execution_policy` says the two planes
already disagree about. Both faces refuse and name the security instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    EXCHANGE,
    SECURITIES,
    UNNAMED_SESSION_EFFECTIVE_FROM,
    UNNAMED_SESSION_SECURITY_INDEX,
    YEAR,
    generate_panel,
    write_generated_panel,
)
from test_factor_interfaces import BASELINE as FACTOR_BASELINE
from test_factor_interfaces import store_three_tiers
from typer.testing import CliRunner

from openalpha_cn.api.app import SHORTLIST_HTTP_STATUS, create_app
from openalpha_cn.cli import FACTOR_EXIT, SHORTLIST_EXIT, PanelExit, app
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement, load_name_histories
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import ShortlistPanelUnreadableError

runner = CliRunner()

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""After the panel's last session closed, and the instant every face here asks about."""

BUILD_AT: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on the newest session, so the cross section this run resolves is priced on
2026-01-16 -- a session inside the window and therefore one the walled security has no name on."""

UNNAMED: Final[str] = SECURITIES[UNNAMED_SESSION_SECURITY_INDEX]
"""The security whose whole rename corpus takes effect after the window."""

SHAPE: Final[str] = "name_history.effect_after_every_priced_session"

SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions", SHAPE)
"""The moving closes are the control's requirement rather than this shape's: with a flat grid
every one-session reversal is the same number and the funnel answers `degenerate_scores`, which
would refuse the control below for a reason that has nothing to do with a name."""

WHOLE_SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)

REFUSAL: Final[str] = (
    f"is before {UNNAMED}'s first known name, which takes effect "
    f"{UNNAMED_SESSION_EFFECTIVE_FROM.isoformat()}"
)
"""`NameHistory.record_on`'s own sentence, and the one thing a user needs off any of the three
channels: which security, and that the corpus does not reach the session rather than that the
command is broken."""


def _write(runtime: Path, *, shapes: tuple[str, ...]) -> None:
    """One generated panel and one raw factor partition holding one cross section."""
    store = PanelStore(runtime / "panel")
    panel = generate_panel(shapes=shapes)
    write_generated_panel(store, panel)
    calendar = panel.calendar()
    built = compute_factor(
        store,
        REVERSAL,
        as_of=BUILD_AT,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                calendar, years=(YEAR,), as_of=BUILD_AT, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=BUILD_AT,
        evaluators={
            REVERSAL.qualified_key: (
                lambda context: (SECURITIES.index(context.subject) + 1) / 100.0
            )
        },
    )
    write_factor_panels(store, (built,))


@pytest.fixture(scope="module")
def walled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    runtime = tmp_path_factory.mktemp("unnamed-session-walled")
    _write(runtime, shapes=SHAPES)
    return runtime


@pytest.fixture(scope="module")
def whole(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The control: the same panel with the baseline record `_name_records` gives every name."""
    runtime = tmp_path_factory.mktemp("unnamed-session-whole")
    _write(runtime, shapes=WHOLE_SHAPES)
    return runtime


@pytest.fixture(scope="module")
def walled_tiers(tmp_path_factory: pytest.TempPathFactory) -> Path:
    runtime = tmp_path_factory.mktemp("unnamed-session-factor")
    store_three_tiers(runtime, shapes=SHAPES)
    return runtime


@pytest.fixture(scope="module")
def whole_tiers(tmp_path_factory: pytest.TempPathFactory) -> Path:
    runtime = tmp_path_factory.mktemp("unnamed-session-factor-whole")
    store_three_tiers(runtime, shapes=WHOLE_SHAPES)
    return runtime


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

    `raise_server_exceptions=False` for `test_partial_registry_faces._post`'s reason: with the
    default, `TestClient` re-raises the unhandled exception inside the test and the status code
    the caller would have received is never observed.
    """
    with TestClient(create_app(runtime_dir=runtime), raise_server_exceptions=False) as client:
        return client.post("/api/v1/shortlists/run", json=_shortlist_body())


def _factor_run(runtime: Path) -> Any:
    arguments = ["factor", "run", "--runtime-dir", str(runtime), "--json"]
    for key, value in FACTOR_BASELINE.items():
        rendered = value.isoformat() if hasattr(value, "isoformat") else str(value)
        arguments.extend((f"--{key.replace('_', '-')}", rendered))
    return runner.invoke(app, arguments)


# --- the shortlist face ---------------------------------------------------------------------


def test_the_shortlist_cli_calls_an_unnamed_session_a_verdict_about_the_panel(
    walled: Path,
) -> None:
    """`exit 1`, not `exit 5`, and the refusal's own sentence rather than a withheld one.

    `internal_error` is the row whose whole meaning is "nothing was judged and the remedy is a bug
    report" (`cli.PanelExit`). The remedy here is a rename corpus that reaches the session, which
    is data, so this is `unhealthy` with the sentence that says which security and why.
    """
    result = _shortlist_run(walled)

    assert result.exit_code == SHORTLIST_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert "unhandled" not in result.stderr
    assert UNNAMED in result.stderr
    assert REFUSAL in result.stderr
    assert "namechange" in result.stderr


def test_the_http_face_answers_a_verdict_rather_than_five_hundred(walled: Path) -> None:
    """Asserted against `SHORTLIST_HTTP_STATUS` rather than a literal, because the claim is that
    this situation is `panel_unreadable` -- and a caller branching on `detail.reason` has to find
    it there rather than on a `text/plain` body Starlette wrote."""
    response = _post(walled)

    assert response.status_code == SHORTLIST_HTTP_STATUS["panel_unreadable"]
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert REFUSAL in response.json()["detail"]["message"]


def test_the_response_body_still_does_not_name_the_store_it_read(walled: Path) -> None:
    """`_read`'s arrangement, which a refusal raised beside it rather than through it inherits or
    defeats: the local message names the store and the body says `<panel store>` instead."""
    response = _post(walled)

    assert str(walled) not in response.text
    assert PANEL_STORE_PLACEHOLDER in response.json()["detail"]["message"]


def test_the_sdk_raises_the_named_refusal_rather_than_the_domain_exception(walled: Path) -> None:
    """The in-process face, which is the one that could have kept the raw exception.

    `V2-P4-033`'s three faces resolve through one function, so a `NameHistoryHorizonError` here
    would mean the CLI and the route were catching something the SDK's own callers cannot.
    """
    sdk = OpenAlphaSDK(runtime_dir=walled)

    with pytest.raises(ShortlistPanelUnreadableError, match=REFUSAL):
        sdk.run_shortlist(
            components=({"factor": "reversal_1d/v1", "weight": 1.0},),
            tier="raw",
            shortlist_size=2,
            position_capital="100000",
            as_of=AS_OF,
            years=(YEAR,),
            exchange=EXCHANGE,
            horizon="5d",
            minimum_tradable_ratio=0.0,
            minimum_researched_ratio=0.0,
            maximum_ranking_age_days=3650,
            code_commit=COMMIT,
            config_digest=CONFIG_DIGEST,
        )


def test_a_corpus_that_reaches_the_session_is_still_screened_rather_than_refused(
    whole: Path,
) -> None:
    """The control, and it is not decoration: without it every assertion above is satisfied by a
    face that refuses everything, which is the shape a fail-closed fix reaches for on its own."""
    result = _shortlist_run(whole)

    assert result.exit_code == PanelExit.ok, result.output + result.stderr
    assert _post(whole).status_code == SHORTLIST_HTTP_STATUS["answered"]


# --- the factor face, which has the same call and the same exposure ---------------------------


def test_the_factor_face_answers_the_same_store_shape_the_same_way(walled_tiers: Path) -> None:
    """`factor_view._PanelInputs.market_bar` reaches `risk_warning_on` for every priced pair.

    The same exit code as the shortlist face, carrying the same sentence. Two faces that answered
    one corpus differently is exactly what `V2-P4-070` was, one dataset over.
    """
    result = _factor_run(walled_tiers)

    assert result.exit_code == FACTOR_EXIT["panel_unreadable"], result.stderr
    assert result.exit_code == PanelExit.unhealthy
    assert "unhandled" not in result.stderr
    assert UNNAMED in result.stderr
    assert REFUSAL in result.stderr


def test_the_factor_face_still_runs_a_corpus_that_reaches_every_priced_session(
    whole_tiers: Path,
) -> None:
    """The factor face's own control, on the three tiers built over the shapeless corpus."""
    assert _factor_run(whole_tiers).exit_code == PanelExit.ok


# --- what the fixture had to learn before any of this was reachable ---------------------------


def test_the_generated_corpus_really_leaves_a_priced_session_with_no_name_on_file(
    walled: Path, whole: Path
) -> None:
    """The shape is measured off the stored partition rather than trusted from the shape id.

    Both halves: the walled store's security has no record in effect on any session in the
    window, and the control's has one from before the window's first. Without the second, the
    refusals above could be a face that learned to say no to every generated panel.
    """
    walled_names = load_name_histories(
        PanelStore(walled / "panel"), years=(YEAR,), as_of=AS_OF, max_staleness=None
    )
    whole_names = load_name_histories(
        PanelStore(whole / "panel"), years=(YEAR,), as_of=AS_OF, max_staleness=None
    )

    assert walled_names[UNNAMED].first_effective_date == UNNAMED_SESSION_EFFECTIVE_FROM
    assert AS_OF.date() < UNNAMED_SESSION_EFFECTIVE_FROM
    assert whole_names[UNNAMED].first_effective_date < AS_OF.date()
