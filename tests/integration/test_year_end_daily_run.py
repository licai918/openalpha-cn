"""A routine late-December daily run, on all three faces (`V2-P4-088`).

`V2-P4-080` named a class and fixed one instance: a domain error **designed to be a verdict**,
raised outside every guard, laundered by both product faces into `exit 5` and a bare `500`.
`V2-P4-084` was the same defect one seam over. This is the third, on the seam `V2-P4-017` and
`V2-P4-021` built -- and unlike the first two it is not reached by an unusual corpus. It is
reached by running the model on the last trading day of the year.

## The two paths, and why only one of them was guarded

`model_view._LabelInputs.window` wraps `build_label_window` and turns a calendar-horizon fault
into a named `blocked` with a remedy. That is the **training** side. The **prediction** side runs
the identical computation inside the store::

    predictions.put -> prediction_record_for -> outcome_known_at_for -> build_label_window

and `run_daily` called `put` **after its only `try` block had closed**. `put` raises
`CalendarHorizonError`, whose mro includes `ValueError`; the REST route catches `ModelViewError`
and `PredictionStoreError`, and the CLI's only net is `_panel_command`.

**Which path a run takes is not a coin toss.** `daily_request` refuses a `predict_at` whose date
is not strictly after `end`, so the prediction day is always later than every training day: the
guarded path can never see the furthest-reaching window and the unguarded one always does. The
condition is simply that the prediction day's outcome window exits past the calendar's last
session -- the last `horizon.sessions + 1` sessions of any year-keyed partition.

Measured on this file's own corpus against a `run_daily` `fadf72d` had character for character --
all three faces, the command line included once it is given the clock this file gives it::

    openalpha model daily-run      -> exit 5: "`model daily-run` did not finish: it raised an
        unhandled CalendarHorizonError. This is a defect in the command, not a verdict about the
        panel ... The exception's own message is withheld"
    POST /api/v1/models/daily-run  -> 500, text/plain, "Internal Server Error"
    OpenAlphaSDK.run_daily_model   -> CalendarHorizonError, unenveloped:
        "the first session after 2026-12-31 is outside the SZSE calendar's published range
         2026-01-01..2026-12-31; an unpublished date is not a holiday, so this is a block,
         not a False"

`MODEL_HTTP_STATUS`' `internal_error` row says "Nothing in this module raises it". It did. And the
command line's own withholding rule made it the worst of the three: `exit 5` says "a defect in the
command", and the sentence that would have named the session is suppressed on purpose, because an
unanticipated exception can carry whatever the frame it escaped was holding.

## Why no fixture could reach this before

`panel_fixtures` has always generated a calendar covering the **whole** partition year and priced
ten sessions of it in January. Ten sessions in the middle of a year are never within a horizon of
the calendar's last session, so no generated panel could put a prediction day where its outcome
window runs off the end -- whatever shape was requested. That is `V2-P4-080`'s own generalisation
for the third time: a fixture hides a wall by never walking up to it. `V2-P4-088` gave
`generate_panel` a `window`, and this file prices the whole of 2026 so the last session it holds
is the last session the calendar publishes.

## What each test here separates

The refusal has to be about the **boundary** rather than about this corpus, so the same panel is
driven three ways: predicting about the last session (refused), predicting about a session two
before it whose window still closes inside the year (answered), and training through the
second-to-last session, which puts the *guarded* path on the same fault. The last of those is
what shows the two paths now give one sentence rather than two verdicts.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    ADJ_FACTOR_DATASET,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    EXCHANGE,
    LAST_DAY,
    PRICE_LIMIT_DATASET,
    SECURITIES,
    STOCK_BASIC_DATASET,
    SUSPENSION_DATASET,
    TRADING_CALENDAR_DATASET,
    WINDOW_FIRST,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.api.app import MODEL_HTTP_STATUS, create_app
from openalpha_cn.cli import MODEL_EXIT, app
from openalpha_cn.model_view import ModelRunBlockedError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorPanel,
    compute_factor,
    write_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER
from openalpha_cn.sdk import OpenAlphaSDK

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "e" * 64
HORIZON: Final[str] = "1d"

SUBJECTS: Final[tuple[str, ...]] = SECURITIES[:-1]
"""`SECURITIES[-1]` is listed and carries no value, so the model abstains on it by name --
`test_model_interfaces.py`'s arrangement, kept so the two corpora differ only in their window."""

MODEL_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
    PRICE_LIMIT_DATASET,
)
"""The seven a model run reads, written instead of all eleven.

A whole year of the generated panel is 259 sessions rather than ten, so the four datasets no
`_LabelInputs` read touches -- `namechange`, `index_weight`, `index_member_all`, `income` -- are
left out. `write_generated_panel` refuses `daily` without `suspend_d` while the halt guard is on,
so the pair travels together.
"""

DECEMBER: Final[tuple[date, ...]] = (
    date(2026, 12, 18),
    date(2026, 12, 21),
    date(2026, 12, 22),
    date(2026, 12, 23),
    date(2026, 12, 24),
    date(2026, 12, 25),
    date(2026, 12, 28),
    date(2026, 12, 29),
    date(2026, 12, 30),
    date(2026, 12, 31),
)
"""The last ten sessions of the generated 2026 calendar, ascending. Asserted against the panel.

Ten because the fit needs a training span and the interesting arithmetic is all in the last three:
2026-12-31 is the calendar's last published session, so a `1d` window opened on the 30th or the
31st cannot close, and one opened on the 29th closes exactly on the 31st.
"""

PREDICT_SESSION: Final[date] = DECEMBER[-1]
"""2026-12-31. Its outcome window opens on "the first session after 2026-12-31", which the SZSE
calendar for 2026 does not have -- and `CalendarHorizonError` refuses to invent."""

TRAINING: Final[tuple[date, ...]] = DECEMBER[:-2]
"""2026-12-18..2026-12-29: every one of these closes its window on or before 2026-12-31.

Two sessions short of the prediction day rather than one, and that gap is the whole point: it
puts the **training** side entirely inside the calendar while the prediction day sits outside it,
so a run over this range can only fail on the path that was unguarded.
"""

CONTROL_PREDICT_SESSION: Final[date] = DECEMBER[-3]
CONTROL_TRAINING: Final[tuple[date, ...]] = DECEMBER[:-4]
"""One session earlier on both ends: 2026-12-29 enters on the 30th and exits on the 31st, which
the calendar does publish. The same corpus, the same command, an answer."""

CLOCK: Final[datetime] = datetime(2026, 12, 31, 10, 0, tzinfo=UTC)
"""17:00 in the panel's own frame on the day predicted about, and all three faces read it.

Not a stylistic choice. `PredictionBatch` refuses a batch produced before the features it read
were readable, so a face reading the real wall clock refuses this corpus at the *fit* -- for a
correct and unrelated reason -- and never reaches the store. The SDK and `create_app` take a
clock by construction (`V2-P0B-008`'s seam); the CLI is monkeypatched at `cli._panel_clock`,
which is `test_model_interfaces.py::test_a_naive_predict_at_is_refused_with_the_field_it_names`'
own arrangement. It also makes this file independent of the date it is run on.
"""


def _build_instant(session: date) -> datetime:
    """17:00 Asia/Shanghai on `session`, after that session's 16:30 publication."""
    return datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)


def _build(store: PanelStore, panel: GeneratedPanel, session: date) -> FactorPanel:
    """One raw cross section at `session`'s own 17:00, through the real engine."""
    instant = _build_instant(session)
    return compute_factor(
        store,
        REVERSAL,
        as_of=instant,
        subjects=SUBJECTS,
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=instant,
        evaluators={
            REVERSAL.qualified_key: lambda context: (SUBJECTS.index(context.subject) + 1) / 100.0
        },
    )


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The whole of 2026 priced, with a raw cross section on each of its last ten sessions."""
    root = tmp_path_factory.mktemp("year-end")
    store = PanelStore(root / "panel")
    panel = generate_panel(
        shapes=("daily.close_moves_between_sessions",), window=(WINDOW_FIRST, LAST_DAY)
    )
    assert panel.sessions[-len(DECEMBER) :] == DECEMBER, (
        "the generated panel does not end on the ten sessions this file assumes"
    )
    assert panel.sessions[-1] == panel.calendar().horizon.last_date, (
        "the last priced session must be the last session the calendar publishes, or this corpus "
        "reaches no boundary at all"
    )
    write_generated_panel(store, panel, datasets=MODEL_DATASETS)
    write_factor_panels(store, [_build(store, panel, session) for session in DECEMBER])
    return root


def _parameters(*, training: tuple[date, ...], predict: date) -> dict[str, Any]:
    return {
        "features": ({"factor": REVERSAL.qualified_key, "tier": "raw"},),
        "name": "reversal-rank",
        "family": "cross_sectional_rank",
        "horizon": HORIZON,
        "seed": 7,
        "start": training[0],
        "end": training[-1],
        "predict_at": _build_instant(predict),
        "as_of": _READ_AT,
        "years": (YEAR,),
        "exchange": EXCHANGE,
        "minimum_scored_ratio": 0.0,
        "code_commit": COMMIT,
        "config_digest": CONFIG_DIGEST,
    }


_READ_AT: Final[datetime] = datetime(2027, 1, 1, 4, 0, tzinfo=UTC)
"""12:00 Asia/Shanghai on the day after the panel's last session -- `GeneratedPanel.as_of` for
this window, so every stored row is readable and none of them is not yet knowable."""


def _cli(runtime_dir: Path, parameters: dict[str, Any]) -> tuple[int, str]:
    arguments = [
        "model",
        "daily-run",
        "--runtime-dir",
        str(runtime_dir),
        "--name",
        str(parameters["name"]),
        "--family",
        str(parameters["family"]),
        "--horizon",
        str(parameters["horizon"]),
        "--seed",
        str(parameters["seed"]),
        "--start",
        parameters["start"].isoformat(),
        "--end",
        parameters["end"].isoformat(),
        "--predict-at",
        parameters["predict_at"].isoformat(),
        "--as-of",
        parameters["as_of"].isoformat(),
        "--exchange",
        str(parameters["exchange"]),
        "--min-scored-ratio",
        str(parameters["minimum_scored_ratio"]),
        "--code-commit",
        str(parameters["code_commit"]),
        "--config-digest",
        str(parameters["config_digest"]),
        "--year",
        str(YEAR),
        "--feature",
        f"{REVERSAL.qualified_key}@raw",
        "--json",
    ]
    result = CliRunner().invoke(app, arguments)
    return result.exit_code, result.output


def _body(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "features": [dict(feature) for feature in parameters["features"]],
        "name": parameters["name"],
        "family": parameters["family"],
        "horizon": parameters["horizon"],
        "seed": parameters["seed"],
        "start": parameters["start"].isoformat(),
        "end": parameters["end"].isoformat(),
        "predict_at": parameters["predict_at"].isoformat(),
        "as_of": parameters["as_of"].isoformat(),
        "years": list(parameters["years"]),
        "exchange": parameters["exchange"],
        "minimum_scored_ratio": parameters["minimum_scored_ratio"],
        "code_commit": parameters["code_commit"],
        "config_digest": parameters["config_digest"],
    }


@pytest.fixture
def served(runtime_dir: Path) -> Iterator[TestClient]:
    """`raise_server_exceptions=False`, because the defect here **was** the unenveloped `500`."""
    with TestClient(
        create_app(runtime_dir=runtime_dir, clock=lambda: CLOCK), raise_server_exceptions=False
    ) as client:
        yield client


REFUSED: Final[str] = "the outcome window for a prediction at 2026-12-31T09:00:00+00:00"
"""The subject of the refusal, held to the instant the run was **asked about**.

`batch.as_of` and not `predicted_at`: the two are different instants here on purpose (see `CLOCK`)
and a message naming the second would tell an operator when the process ran rather than which
session it could not place. A mutant swapping them survived every other assertion in this file.
"""

REMEDY: Final[str] = "`openalpha panel build --dataset trade_cal --year 2027`"
"""The command the refusal has to name, spelled once here so three faces are held to one string.

`_unbuilt_dataset_remedy`'s rule: the year is the one after the calendar's own last session,
because that is where a window opened on its last few sessions lands. The prose remedy this
replaced -- "build the calendar over the year the window ends in" -- left an operator to work out
both the dataset name and the year, at three in the morning on the 31st of December.
"""


def test_a_year_end_daily_run_is_blocked_by_name_on_the_command_line(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit `1` with the calendar named, rather than `5` with the exception withheld."""
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    code, out = _cli(runtime_dir, _parameters(training=TRAINING, predict=PREDICT_SESSION))

    assert code == int(MODEL_EXIT["blocked"]), out
    assert REFUSED in out
    assert "the first session after 2026-12-31 is outside" in out
    assert REMEDY in out


def test_the_http_face_answers_a_verdict_rather_than_five_hundred(served: TestClient) -> None:
    """`409` with `{"detail": {"reason": "blocked", ...}}`, not `500 text/plain`.

    Both halves are asserted. A client told `500` retries and pages somebody; a client told `409`
    with a `detail` object reads the remedy off it, which is the branch `MODEL_HTTP_STATUS`' own
    docstring tells it to take.
    """
    response = served.post(
        "/api/v1/models/daily-run",
        json=_body(_parameters(training=TRAINING, predict=PREDICT_SESSION)),
    )

    assert response.status_code == MODEL_HTTP_STATUS["blocked"], response.text
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["reason"] == "blocked"
    assert REFUSED in detail["message"]
    assert "the first session after 2026-12-31 is outside" in detail["message"]
    assert REMEDY in detail["message"]


def test_the_sdk_raises_the_named_refusal_rather_than_the_domain_exception(
    runtime_dir: Path,
) -> None:
    """`ModelRunBlockedError`, not the `CalendarHorizonError` the store propagated.

    The in-process face is where the laundering was easiest to miss: a caller who wrote
    `except ModelViewError` around `run_daily_model` caught nothing, and `CalendarHorizonError`
    is a `ValueError`, so the ones who wrote `except ValueError` swallowed a block as a bad
    request.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: CLOCK)
    # Held before and after rather than "the register is empty": the control test below shares
    # this module-scoped store and registers a prediction into it, so an emptiness assertion
    # would pass only while this test happened to sort first. That is the order dependence
    # `V2-P4-089` is about, and writing one into its sibling issue's file would be a poor joke.
    held = sdk.list_predictions()

    with pytest.raises(ModelRunBlockedError) as captured:
        sdk.run_daily_model(**_parameters(training=TRAINING, predict=PREDICT_SESSION))

    assert REFUSED in str(captured.value)
    assert "the first session after 2026-12-31 is outside" in str(captured.value)

    assert sdk.list_predictions() == held, (
        "the run was refused before the store took custody, so nothing new is registered -- which "
        "is the opposite of a floor refusal, where the prediction is filed and the verdict is "
        "about whether it may be acted on"
    )


def test_the_same_corpus_two_sessions_earlier_is_answered(runtime_dir: Path) -> None:
    """The control. The refusal is about the calendar's edge, not about this panel.

    2026-12-29 enters on the 30th and exits on the 31st, which the calendar publishes, so the
    identical command over the identical store registers a prediction. Without this, every
    assertion above would also pass on a corpus that was simply unusable.
    """
    result = OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: CLOCK).run_daily_model(
        **_parameters(training=CONTROL_TRAINING, predict=CONTROL_PREDICT_SESSION)
    )

    assert result.record.batch.as_of == _build_instant(CONTROL_PREDICT_SESSION)
    assert result.record.outcome_known_at == datetime(2026, 12, 31, 7, 0, tzinfo=UTC)
    assert result.outcome == "created"
    assert result.is_blocked is False


def test_the_guarded_path_on_the_same_fault_gives_the_same_sentence(runtime_dir: Path) -> None:
    """Training through 2026-12-30 puts `_LabelInputs.window` on the fault instead of the store.

    Both paths build the same window through the same `build_label_window`, so they now raise
    through one `_outcome_window_refusal` and one `_OUTCOME_WINDOW_FAULTS`. Before this issue the
    training side already answered `blocked` and the prediction side answered `500`, which is the
    shape that makes a defect invisible: the well-lit path was correct.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: CLOCK)

    with pytest.raises(ModelRunBlockedError) as captured:
        sdk.run_daily_model(**_parameters(training=DECEMBER[:-1], predict=PREDICT_SESSION))

    message = str(captured.value)
    assert "a prediction at 2026-12-30T09:00:00+00:00" in message, (
        "the guarded path refuses at the training day whose window cannot close, one session "
        "before the prediction day the unguarded path refuses at"
    )
    assert "+1 sessions from 2026-12-31 is outside" in message
    assert REMEDY in message


def test_no_year_end_refusal_names_the_store_on_disk(
    runtime_dir: Path, served: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disclosure rule, on the sentence this issue added.

    A refusal a browser can reach may not print the operator's filesystem layout, and a new
    message is exactly where that leaks back in.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: CLOCK)
    parameters = _parameters(training=TRAINING, predict=PREDICT_SESSION)

    _code, out = _cli(runtime_dir, parameters)
    response = served.post("/api/v1/models/daily-run", json=_body(parameters))

    assert str(runtime_dir) not in response.text
    assert PANEL_STORE_PLACEHOLDER not in out, (
        "the command line may name the store it read, and does so by printing the real path; the "
        "placeholder is the HTTP face's substitution and has no business on this one"
    )
    assert str(runtime_dir) not in response.json()["detail"]["message"]
