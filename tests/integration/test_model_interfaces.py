"""The model chain, reached from where a user stands (`V2-P4-021`).

## What was measured before this file existed

`V2-P4-010` through `V2-P4-017` are eight issues of contracts: an `AlphaModel` protocol, a
versioned feature matrix, a walk-forward split with purge and embargo, two baselines, a
content-addressed artifact and a prediction store. At `694f822` **none of it was reachable from
any shipped surface**, which is the P3 phase acceptance's own root cause for nine Criticals,
reproduced by `V2-P4-032`/`033` on the ranking chain and again here:

    $ grep -rln "CliRunner\\|TestClient\\|OpenAlphaSDK" \\
        tests/unit/backtest/test_alpha_baseline.py \\
        tests/unit/backtest/test_walk_forward.py \\
        tests/integration/test_feature_matrix_reads.py
    (nothing)

`openalpha --help` listed ten commands and no `model`; no HTTP route's path contained `model` or
`prediction`; `OpenAlphaSDK` had no method that fitted anything. And `storage/predictions.py` --
the store Story S32 marks 不可省 -- was **not in the composition root**, on the deliberate ground
that nothing could fill it until a face above both contracts handed it a batch.

So every assertion in this file starts at `CliRunner`, `TestClient` or `OpenAlphaSDK`. A test that
imported `openalpha_cn.model_view` and called it directly would pass on a tree where the three
faces do not exist, which is the state this file was written to make impossible.

## The corpus, and why every number in it is chosen rather than incidental

One generated panel -- eight securities, ten sessions, 2026-01-05 through 2026-01-16 -- and one
raw factor build per session from the second onward. Five properties are load-bearing and each was
measured before it was relied on:

- **The horizon is `1d`, and it has to be.** A label window opens on the session *after* the
  prediction day and closes `horizon` sessions later, and `WalkForwardFold.purged` removes every
  training candidate whose label had not closed when the fold was first asked. On a ten-session
  panel a `5d` horizon purges **every** training example of every fold and `walk_forward_folds`
  refuses the schedule outright. `1d` leaves a training span behind each block: fold one trains on
  15 examples and fold two on 30, and the purge is visible in the gap.
- **The first session carries no build.** `reversal_1d/v1` declares `lookback_sessions=2`, so a
  build stamped at the close of the panel's first session sees one session and `compute_factor`
  refuses the *request* by name rather than answering `insufficient_history` per security.
- **Closes move, so the targets do not tie.** `daily.close_moves_between_sessions` makes each
  security's close `10 + index + 0.5 * session`, so a one-session forward return is
  `0.5 / (10 + index + 0.5 * k)` -- distinct per security and decreasing in the index. A flat panel
  would make every point `degenerate_returns` and every fold unmeasured.
- **The feature ordering is perturbed on a three-session cycle**, because a fixture whose feature
  ordering is constant against a target ordering that is also constant produces the same rank IC
  on every test day, a dispersion of exactly zero and `rank_icir: null` on every fold -- so the
  face's rendering of a real `rank_icir` would be untested. `_feature_value` swaps two securities
  on one session in three and two further apart on the next, which on the seven names that carry a
  value is Spearman `1.000`, `0.964` and `0.857`. The two folds then differ:
  `0.9107 / 12.0208` against `0.9821 / 38.8909`, measured.
- **`SECURITIES[-1]` is in no build's `subjects`.** It is listed on every session and carries no
  value for the declared column, so `rankable` leaves it out of the population and
  `CrossSectionalRankModel.predict` abstains on it by name. That is what makes `scored_ratio`
  `28/32` rather than `1.0`, and it is what the two refusal tests drive one flag apart. It is
  *also* the security the shapeless panel halts on 2026-01-09, so it is the one name that is both
  abstained on by the model and excluded by the labeller -- two different disclosures about one
  row, which the answer reports in two different places.

## The distinction this file exists to protect, twice

`V2-P4-023`/`033` established that a **blocked** answer must not look like an **empty** one, and
held it with two runs off one store whose `measurement` bodies are identical and whose verdicts
are not. The same pair is here on both model faces, against `--min-scored-ratio` -- the floor
under the one statistic `V2-P4-014` says exists to make two models comparable, because abstaining
on the hard names is otherwise a free way to win.

And `V2-P4-017`'s own honesty has to survive being rendered: a `forward` standing says this store
held the bytes before the outcome existed and says nothing a third party could check, so
`test_a_forward_standing_is_rendered_with_what_it_does_not_prove` holds the answer to that
sentence rather than to a badge.

## The clock, and why three tests build their own SDK

The two faces that read a real wall clock cannot produce a `forward` standing on a 2026-01 fixture
panel run today -- the probe measured exactly that: `predicted_at` lands in August and the record
stands `backfill`, correctly. So the standings are driven through `OpenAlphaSDK(clock=...)` and
through `create_app(clock=...)`, which is the seam `V2-P0B-008` built for `decision_id` parity and
the same one `FilePredictionStore` is constructed with by `build_storage`.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    ADJ_FACTOR_DATASET,
    EXCHANGE,
    SECURITIES,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn import cli
from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.model_view import ModelPanelUnreadableError, ModelRequestError
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

SUBJECTS: Final[tuple[str, ...]] = SECURITIES[:-1]
UNVALUED: Final[str] = SECURITIES[-1]
"""Listed on every session, in no build's `subjects`, so the model abstains on it by name."""

SESSIONS: Final[tuple[date, ...]] = (
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
    date(2026, 1, 12),
    date(2026, 1, 13),
    date(2026, 1, 14),
    date(2026, 1, 15),
    date(2026, 1, 16),
)
"""The ten sessions the generated panel prices, ascending. Asserted against the panel below."""

BUILD_SESSIONS: Final[tuple[date, ...]] = SESSIONS[1:]
TRAINING_SESSIONS: Final[tuple[date, ...]] = SESSIONS[1:8]
"""The seven prediction days a `1d` label can both be built at and closed inside this panel."""

PREDICT_SESSION: Final[date] = SESSIONS[9]
"""2026-01-16, the day `model daily-run` predicts about. Its outcome closes on 2026-01-20."""

PREDICTION_DAYS: Final[list[str]] = [day.isoformat() for day in TRAINING_SESSIONS]

SWAP: Final[dict[int, tuple[int, int]]] = {1: (0, 1), 2: (0, 2)}
"""Which two securities trade places on a session, keyed by that session's index modulo three.

See this module's docstring. The two distances are what give the two folds different statistics:
one adjacent transposition among seven names is Spearman `0.964`, one across two places is
`0.857`, and an unswapped session is `1.000`.
"""


def _build_instant(session: date) -> datetime:
    """17:00 Asia/Shanghai on `session`, which is after that session's 16:30 publication.

    So a build stamped here is *about* that session: `feature_matrix._session_for` resolves it to
    that session rather than to the one before, which is `V2-P4-077`'s two clocks on the side
    where they agree. It is also 2026-01-<n> in Asia/Shanghai, so the prediction day this face
    derives is the same date -- the two derivations agreeing here is what makes the fixture
    readable, and `test_a_prediction_day_is_the_instants_zone_date` is where they are separated.
    """
    return datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)


READ_AT: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""The instant every panel read in an evaluation is made at -- 12:00 Shanghai on the Saturday
after the panel's last session, so every label's window has closed and is readable."""

OUTCOME_KNOWN_AT: Final[str] = "2026-01-20T07:00:00+00:00"
"""15:00 Asia/Shanghai on 2026-01-20, the close of the window a 2026-01-16 prediction opens.

Entry is the next trading day after the 16th (Monday the 19th) and a `1d` window exits one session
later. Measured off the calendar rather than assumed; the daily-run tests below assert it.
"""

FORWARD_CLOCK: Final[datetime] = datetime(2026, 1, 16, 10, 0, tzinfo=UTC)
"""Before `OUTCOME_KNOWN_AT`, so a batch produced and held here stands `forward`."""

LATE_CLOCK: Final[datetime] = datetime(2026, 1, 21, 2, 0, tzinfo=UTC)
"""After it, so a batch produced *and* held here is a `backfill`."""

HORIZON: Final[str] = "1d"

OFFERED: Final[int] = 32
SCORED: Final[int] = 28
"""Four securities offered across the folds' eight test-day rows carry no score, all of them
`UNVALUED`. Measured, not chosen: 2 folds x 2 days x 8 listed names offered, one abstention each."""

DAILY_OFFERED: Final[int] = 8
DAILY_SCORED: Final[int] = 7


def _feature_value(subject: str, session: date) -> float:
    """The declared column's stored value, perturbed on a three-session cycle.

    See `SWAP` and this module's docstring: a constant ordering against a constant target ordering
    gives one rank IC on every day, a dispersion of zero and `rank_icir: null` everywhere, so the
    face's rendering of a real `rank_icir` would never be exercised.
    """
    index = SUBJECTS.index(subject)
    swap = SWAP.get(SESSIONS.index(session) % 3)
    if swap is not None and index in swap:
        index = swap[0] + swap[1] - index
    return (index + 1) / 100.0


BASELINE: Final[dict[str, Any]] = {
    "features": ({"factor": REVERSAL.qualified_key, "tier": "raw"},),
    "name": "reversal-rank",
    "family": "cross_sectional_rank",
    "horizon": HORIZON,
    "seed": 7,
    "start": TRAINING_SESSIONS[0],
    "end": TRAINING_SESSIONS[-1],
    "as_of": READ_AT,
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "folds": 2,
    "test_days_per_fold": 2,
    "embargo_sessions": 0,
    "minimum_scored_ratio": 0.0,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}
"""One evaluation, in the vocabulary all three faces take. See `_cli`, `_rest_body`, `_sdk`."""

DAILY: Final[dict[str, Any]] = {
    key: value
    for key, value in BASELINE.items()
    if key not in {"folds", "test_days_per_fold", "embargo_sessions"}
} | {"predict_at": _build_instant(PREDICT_SESSION)}
"""One daily run. `predict_at` is the stored cross section the prediction is about; `as_of` is the
instant the *labels* behind the fit are read at, and they are two clocks on purpose."""


def _build(store: PanelStore, panel: GeneratedPanel, session: date) -> FactorPanel:
    """One raw cross section at `session`'s own 17:00, through the real engine.

    The evaluator seam is `compute_factor`'s own documented one and is substituted rather than the
    factor arithmetic driven, `test_feature_matrix_reads.py`'s reason: what is under test is which
    market a stored cross section is offered to and what a fit makes of it, not what `reversal_1d`
    computes.
    """
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
            REVERSAL.qualified_key: lambda context: _feature_value(context.subject, session)
        },
    )


def _write_corpus(root: Path, *, datasets: tuple[str, ...] | None = None) -> Path:
    store = PanelStore(root / "panel")
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    assert panel.sessions == SESSIONS, "the generated panel is not the ten sessions assumed here"
    write_generated_panel(store, panel, datasets=datasets)
    write_factor_panels(store, [_build(store, panel, session) for session in BUILD_SESSIONS])
    return root


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One panel and nine raw cross sections, one per session. See this module's docstring."""
    return _write_corpus(tmp_path_factory.mktemp("model-faces"))


@pytest.fixture(scope="module")
def unadjusted_runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same corpus with **no `adj_factor` partition at all**.

    `V2-P4-078`'s shape on this face: a panel a factor build is perfectly happy with, and a model
    run is not, because a label is a return between two sessions. The remedy has to name the
    command, not the partition.
    """
    root = tmp_path_factory.mktemp("model-faces-unadjusted")
    store = PanelStore(root / "panel")
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    kept = tuple(name for name in panel.batches if name != ADJ_FACTOR_DATASET)
    write_generated_panel(store, panel, datasets=kept)
    write_factor_panels(store, [_build(store, panel, session) for session in BUILD_SESSIONS])
    return root


def _cli(runtime_dir: Path, command: str, parameters: dict[str, Any]) -> tuple[int, str]:
    """One `openalpha model <command>` invocation over the shared parameter dict."""
    arguments = [
        "model",
        command,
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
        "--as-of",
        parameters["as_of"].isoformat(),
        "--exchange",
        str(parameters["exchange"]),
        "--min-scored-ratio",
        str(parameters["minimum_scored_ratio"]),
    ]
    if parameters.get("json_output", True):
        arguments.append("--json")
    for feature in parameters["features"]:
        token = f"{feature['factor']}@{feature['tier']}"
        if feature.get("transform"):
            token += f":{feature['transform']}"
        arguments += ["--feature", token]
    for year in parameters["years"]:
        arguments += ["--year", str(year)]
    for flag in ("code_commit", "config_digest", "feature_version"):
        value = parameters.get(flag)
        if value is not None:
            arguments += [f"--{flag.replace('_', '-')}", str(value)]
    for name, value in parameters.get("hyperparameters", ()):
        arguments += ["--hyperparameter", f"{name}={value}"]
    if command == "evaluate":
        arguments += [
            "--folds",
            str(parameters["folds"]),
            "--test-days-per-fold",
            str(parameters["test_days_per_fold"]),
            "--embargo-sessions",
            str(parameters["embargo_sessions"]),
        ]
    else:
        arguments += ["--predict-at", parameters["predict_at"].isoformat()]
    result = CliRunner().invoke(app, arguments)
    return result.exit_code, result.output


def _rest_body(parameters: dict[str, Any]) -> dict[str, Any]:
    body = {
        key: value
        for key, value in parameters.items()
        if key
        not in {
            "as_of",
            "predict_at",
            "years",
            "features",
            "start",
            "end",
            "hyperparameters",
            "json_output",
        }
    }
    body["as_of"] = parameters["as_of"].isoformat()
    body["start"] = parameters["start"].isoformat()
    body["end"] = parameters["end"].isoformat()
    body["years"] = list(parameters["years"])
    body["features"] = [dict(feature) for feature in parameters["features"]]
    body["hyperparameters"] = [
        {"name": name, "value": value} for name, value in parameters.get("hyperparameters", ())
    ]
    if "predict_at" in parameters:
        body["predict_at"] = parameters["predict_at"].isoformat()
    return body


def _sdk_arguments(parameters: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in parameters.items() if key not in {"json_output"}}


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime_dir, clock=lambda: FORWARD_CLOCK)) as client:
        yield client


@pytest.fixture
def daily_runtime_dir(tmp_path: Path, runtime_dir: Path) -> Path:
    """A private copy of the corpus, so a registering test cannot see another's writes.

    The panel is copied and the `predictions` directory is not, which is what lets
    `test_an_evaluation_registers_nothing` and the idempotence test each start from an empty
    register.
    """
    root = tmp_path / "daily"
    root.mkdir()
    shutil.copytree(runtime_dir / "panel", root / "panel")
    return root


# --- 1. end to end, from a stored factor tier to a walk-forward evaluation -----------------------


def test_a_stored_factor_tier_reaches_a_walk_forward_evaluation_through_the_command_line(
    runtime_dir: Path,
) -> None:
    """The dead end this issue exists to close, driven end to end in one command.

    Before it, `build_feature_matrix`, `labelled_panel`, `walk_forward_folds` and
    `evaluate_walk_forward` were four functions no shipped surface called.

    Every number is measured off the corpus rather than chosen: seven prediction days survive the
    `1d` horizon on a ten-session panel, two folds of two test days tile the tail, and the two
    folds' training sets are 15 and 30 examples -- the gap is the purge, which removes the day
    whose outcome had not closed when each fold was first asked.
    """
    code, out = _cli(runtime_dir, "evaluate", BASELINE)
    assert code == 0, out
    answer = json.loads(out)

    assert answer["is_blocked"] is False
    assert answer["schedule"]["prediction_days"] == PREDICTION_DAYS
    assert [fold["first_test_day"] for fold in answer["folds"]] == ["2026-01-09", "2026-01-13"]
    assert [fold["coverage"] for fold in answer["folds"]] == ["measured", "measured"]
    assert [fold["training_example_count"] for fold in answer["folds"]] == [15, 30]
    assert answer["measurement"] == {
        "prediction_day_count": 7,
        "fold_count": 2,
        "measured_fold_count": 2,
        "offered_count": OFFERED,
        "scored_count": SCORED,
        "scored_ratio": SCORED / OFFERED,
    }
    assert answer["admitted"] == [fold["artifact_id"] for fold in answer["folds"]]
    assert len(set(answer["admitted"])) == 2, "two folds must produce two artifact addresses"


def test_each_fold_reports_the_five_statistics_the_baseline_measures(runtime_dir: Path) -> None:
    """`V2-P4-014`'s five, per fold, and the two folds must not be the same numbers.

    A fixture on which every fold reported one mean and one dispersion could not tell a face that
    renders each fold from one that renders the first twice, which is why `_feature_value`
    perturbs on a three-session cycle rather than on alternate sessions.
    """
    _code, out = _cli(runtime_dir, "evaluate", BASELINE)
    first, second = json.loads(out)["folds"]

    assert first["mean_rank_ic"] == pytest.approx(0.910714, abs=1e-6)
    assert first["stdev_rank_ic"] == pytest.approx(0.075761, abs=1e-6)
    assert first["rank_icir"] == pytest.approx(12.0208, abs=1e-4)
    assert [point["rank_ic"] for point in first["points"]] == [
        pytest.approx(0.964286, abs=1e-6),
        pytest.approx(0.857143, abs=1e-6),
    ]

    assert second["mean_rank_ic"] == pytest.approx(0.982143, abs=1e-6)
    assert second["rank_icir"] == pytest.approx(38.8909, abs=1e-4)
    assert first["mean_rank_ic"] != second["mean_rank_ic"]
    assert first["rank_icir"] != second["rank_icir"]


def test_a_security_the_labeller_could_not_price_leaves_visibly(runtime_dir: Path) -> None:
    """A row that drops out of the fit says so, with the labeller's own sentence.

    `walk_forward.PanelExclusion` carries `OutcomeLabel.refusal_summary` verbatim, and this face
    puts it on the answer rather than reporting a training count nobody can reconcile. The halt
    is the shapeless panel's own, not one this fixture arranged.
    """
    _code, out = _cli(runtime_dir, "evaluate", BASELINE)
    excluded = json.loads(out)["excluded"]

    assert [item["ts_code"] for item in excluded] == [UNVALUED, UNVALUED]
    assert [item["prediction_day"] for item in excluded] == ["2026-01-07", "2026-01-08"]
    assert "halted_session on 2026-01-09" in excluded[0]["reason"]


# --- 2. blocked is not empty --------------------------------------------------------------------


def test_a_refused_evaluation_and_an_admitted_one_are_two_different_answers(
    runtime_dir: Path,
) -> None:
    """One store, one command line, **one flag apart** -- and the two answers are distinguishable.

    The model abstains on one of the eight listed names because it carries no value for the
    declared column, so `scored_ratio` is `28/32`. Under a declared floor of `0.9` the run is
    refused and names `scored_ratio_below_floor` with both sides of the comparison; under `0.0`
    the identical measurement is admitted with the two artifact addresses it stands behind.

    The assertion on `measurement` is what pins that nothing but the declared bar separates them.
    """
    refused_code, refused_out = _cli(
        runtime_dir, "evaluate", {**BASELINE, "minimum_scored_ratio": 0.9}
    )
    admitted_code, admitted_out = _cli(
        runtime_dir, "evaluate", {**BASELINE, "minimum_scored_ratio": 0.0}
    )
    refused = json.loads(refused_out)
    admitted = json.loads(admitted_out)

    assert refused_code == 1
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None
    assert [block["code"] for block in refused["blocks"]] == ["scored_ratio_below_floor"]
    block = refused["blocks"][0]
    assert block["measured"] == SCORED / OFFERED
    assert block["required"] == 0.9
    assert "28 of the 32 securities" in block["detail"]
    assert "0.8750" in block["detail"] and "0.9000" in block["detail"]

    assert admitted_code == 0
    assert admitted["is_blocked"] is False
    assert admitted["admitted"] is not None
    assert admitted["blocks"] == []

    assert refused["measurement"] == admitted["measurement"]
    assert refused["folds"] == admitted["folds"]


def test_the_human_readable_evaluation_says_refused_in_words_rather_than_printing_a_table(
    runtime_dir: Path,
) -> None:
    """Without `--json`, the verdict is the **first** line and is a word, not a silent table.

    A reader who has to infer "refused" from a table of folds is the same reader the `null`
    versus list distinction exists for, one channel over -- and a refused evaluation still prints
    every fold, because "here are the numbers, and here is why you may not act on them" is the
    answer.
    """
    code, out = _cli(
        runtime_dir,
        "evaluate",
        {**BASELINE, "minimum_scored_ratio": 0.9, "json_output": False},
    )
    lines = out.splitlines()

    assert code == 1
    assert lines[0] == "verdict    REFUSED by ['scored_ratio_below_floor']"
    assert "reversal-rank (cross_sectional_rank, 1d)" in out
    assert "2026-01-09 measured" in out
    assert "2026-01-13 measured" in out
    assert "scored_ratio_below_floor: 28 of the 32" in out


def test_the_rest_face_answers_a_refused_evaluation_with_409_and_the_bar_it_missed(
    rest: TestClient,
) -> None:
    """`409` plus a verdict body, never `200` with a null-looking one.

    `POST /api/v1/shortlists/run`'s arrangement, which the product acceptance named the standard
    for the whole repository: the status code is the deliverable and the refusal still carries
    every fold, the measurement each was read against, and both sides of the bar.
    """
    refused = rest.post(
        "/api/v1/models/evaluate", json=_rest_body({**BASELINE, "minimum_scored_ratio": 0.9})
    )
    assert refused.status_code == 409
    body = refused.json()
    assert "detail" not in body
    assert body["is_blocked"] is True
    assert body["admitted"] is None
    assert [block["code"] for block in body["blocks"]] == ["scored_ratio_below_floor"]

    admitted = rest.post("/api/v1/models/evaluate", json=_rest_body(BASELINE))
    assert admitted.status_code == 200
    assert admitted.json()["is_blocked"] is False
    assert admitted.json()["admitted"] is not None
    assert admitted.json()["measurement"] == body["measurement"]


def test_an_unmeasured_statistic_is_never_rendered_as_a_zero(runtime_dir: Path) -> None:
    """A fold too short to summarise reports `null` beside the code that says why.

    Two folds of **one** test day each: `MINIMUM_FOLD_DAYS` is 2, so neither fold can produce a
    dispersion and both come back `insufficient_as_ofs` with three `null` statistics -- while
    every per-day `rank_ic` is still a number. A face that filled the summary with `0.0` would be
    reporting a measurement nobody took, which is the defect `FoldEvaluation` refuses at the
    contract and a renderer is the one place that refusal cannot be re-checked.

    The terminal rendering is asserted beside the JSON because that is the channel where a `0.00`
    is most tempting.
    """
    single = {**BASELINE, "folds": 2, "test_days_per_fold": 1}
    code, out = _cli(runtime_dir, "evaluate", single)
    assert code == 0, out
    folds = json.loads(out)["folds"]

    assert [fold["coverage"] for fold in folds] == ["insufficient_as_ofs"] * 2
    for fold in folds:
        assert fold["mean_rank_ic"] is None
        assert fold["stdev_rank_ic"] is None
        assert fold["rank_icir"] is None
        assert fold["measured_count"] == 1
        assert [point["rank_ic"] for point in fold["points"]] != [None]

    _code, terminal = _cli(runtime_dir, "evaluate", {**single, "json_output": False})
    rows = [line for line in terminal.splitlines() if line.startswith("2026-01-")]
    assert len(rows) == 2
    for row in rows:
        assert row.count("not measured") == 2, row
        assert "0.0000" not in row, row


# --- 3. the three faces answer one question -----------------------------------------------------


def test_the_three_faces_answer_one_evaluation_from_one_declaration(
    runtime_dir: Path, rest: TestClient
) -> None:
    """One store, one declaration -- the CLI, HTTP and the SDK cannot fit three models.

    `artifact_id` is the whole comparison: it is `stable_model_id` over the declaration, the
    feature list, the training cutoff, the example count and the learned parameters, so two faces
    agreeing on it agree about everything that reached the fit.
    """
    code, out = _cli(runtime_dir, "evaluate", BASELINE)
    assert code == 0, out
    from_cli = json.loads(out)
    from_rest = rest.post("/api/v1/models/evaluate", json=_rest_body(BASELINE)).json()
    from_sdk = OpenAlphaSDK(runtime_dir=runtime_dir).evaluate_model(**_sdk_arguments(BASELINE))

    assert [fold["artifact_id"] for fold in from_cli["folds"]] == [
        fold["artifact_id"] for fold in from_rest["folds"]
    ]
    assert [fold["artifact_id"] for fold in from_cli["folds"]] == [
        evaluation.artifact.artifact_id for evaluation in from_sdk.folds
    ]
    assert from_cli["declaration"] == from_rest["declaration"]
    assert from_cli["measurement"] == from_rest["measurement"]
    assert from_sdk.scored_ratio == from_cli["measurement"]["scored_ratio"]


def test_a_boolean_hyperparameter_is_one_declaration_on_all_three_faces(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`--hyperparameter x=true` and `{"name": "x", "value": true}` are the same declaration.

    `AlphaModelDeclaration.hyperparameters` reaches the artifact's content address, so a command
    line that parsed `true` as the **string** `"true"` while a JSON body carried the boolean would
    give one declaration two addresses -- `V2-P4-046`'s equivalence claim broken on the one field
    where it is invisible, because both spellings validate. `cli._model_scalar` tries bool first
    for exactly this reason.

    The tree family is used because it is the one with hyperparameters to declare; the value here
    is not one of its own, which is deliberate -- what is under test is the parser, and a declared
    scalar reaches the address whatever the implementation does with it.
    """
    declared = {**BASELINE, "hyperparameters": (("audited", "true"),)}
    code, out = _cli(runtime_dir, "evaluate", declared)
    assert code == 0, out
    from_cli = json.loads(out)

    body = _rest_body(declared)
    body["hyperparameters"] = [{"name": "audited", "value": True}]
    from_rest = rest.post("/api/v1/models/evaluate", json=body).json()

    assert from_cli["declaration"]["hyperparameters"] == [{"name": "audited", "value": True}]
    assert [fold["artifact_id"] for fold in from_cli["folds"]] == [
        fold["artifact_id"] for fold in from_rest["folds"]
    ]


# --- 4. the daily run, and the store V2-P4-017 could not fill ------------------------------------


def _sdk_daily(root: Path, clock: object, parameters: dict[str, Any] | None = None) -> Any:
    sdk = OpenAlphaSDK(runtime_dir=root, clock=clock)  # type: ignore[arg-type]
    return sdk, sdk.run_daily_model(**_sdk_arguments(parameters or DAILY))


def test_a_daily_run_registers_a_prediction_the_store_hands_back(
    daily_runtime_dir: Path,
) -> None:
    """Story S32, from a face, for the first time in this repository.

    `V2-P4-017` shipped `FilePredictionStore` and deliberately left it out of the composition
    root, because two `lint-imports` contracts stand between a `PredictionBatch` producer and
    `openalpha_cn.storage` and nothing could hand it a batch. This is the run that can.

    The deadline is the calendar's, not this test's: a prediction about 2026-01-16 enters on
    Monday the 19th and a `1d` window exits one session later, so the outcome becomes knowable at
    that session's 15:00 Shanghai close.
    """
    sdk, result = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)

    assert sdk.list_predictions() == (result.record.record_id,)
    assert result.outcome == "created"
    assert result.record.outcome_known_at.isoformat() == OUTCOME_KNOWN_AT
    assert result.record.batch.as_of == _build_instant(PREDICT_SESSION)
    assert result.offered_count == DAILY_OFFERED
    assert result.scored_count == DAILY_SCORED
    assert result.training_day_count == len(TRAINING_SESSIONS)

    held = sdk.held_prediction(result.record.record_id)
    assert held.record_id == result.record.record_id
    assert held.batch.artifact.artifact_id == result.record.batch.artifact.artifact_id


def test_a_forward_standing_is_rendered_with_what_it_does_not_prove(
    daily_runtime_dir: Path,
) -> None:
    """`V2-P4-017`'s honesty has to survive being rendered, and this is where it could be lost.

    That contract is unusually plain: `predicted_at` is whatever the caller passed to `predict`
    and nothing here can check it, and *"none of this defends against whoever owns the disk"*. A
    face that printed `"standing": "forward"` and stopped would turn a local-first, single-user
    bookkeeping fact into what reads like a third-party attestation.

    So the two sentences travel **in the body**, which is what a caller pastes into a report, and
    they say the unverifiable half by name.
    """
    _sdk, result = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)
    view = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=lambda: FORWARD_CLOCK).daily_view(
        result
    )
    prediction = view["prediction"]
    assert isinstance(prediction, dict)

    assert prediction["standing"] == "forward"
    assert "held these bytes before the instant the outcome became knowable" in str(
        prediction["standing_proves"]
    )
    does_not = str(prediction["standing_does_not_prove"])
    assert "predicted_at is whatever the caller passed to predict" in does_not
    assert "nothing here defends against whoever owns the disk" in does_not
    assert "a timestamp somebody else controls" in does_not


def test_a_batch_this_store_received_late_is_unwitnessed_and_says_which_half_failed(
    daily_runtime_dir: Path,
) -> None:
    """The middle standing, driven from a face, and the reason it cannot collapse into either.

    A clock that advances between the batch being produced and the store taking custody is a slow
    disk, not a lie -- and `V2-P4-017` refuses to file it as either neighbour: as `forward` it
    would be evidence it is not, and as `backfill` it would accuse a caller whose only fault may
    have been the disk.

    Driven by reading one clock twice, which is exactly the shape the faces have: `run_daily`
    stamps `predicted_at` from the clock and `FilePredictionStore` stamps `recorded_at` from its
    own, and the two are the same clock read at two instants.
    """
    reading = {"count": 0}

    def clock() -> datetime:
        """`FORWARD_CLOCK` once the SDK is built, then `LATE_CLOCK` for every later reading.

        `build_storage` reads the clock itself, to recover interrupted batches, so the sequence
        is armed *after* the container exists rather than counted from zero -- which is the kind
        of coupling a list of instants hides until somebody adds a store.
        """
        reading["count"] += 1
        return FORWARD_CLOCK if reading["count"] == 1 else LATE_CLOCK

    sdk = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=clock)
    reading["count"] = 0
    result = sdk.run_daily_model(**_sdk_arguments(DAILY))

    assert result.record.standing == "unwitnessed"
    assert result.record.batch.predicted_at == FORWARD_CLOCK
    assert result.record.recorded_at == LATE_CLOCK
    view = sdk.daily_view(result)
    prediction = view["prediction"]
    assert isinstance(prediction, dict)
    assert "uncorroborated" in str(prediction["standing_does_not_prove"])


def test_a_batch_produced_after_its_outcome_printed_is_a_backfill(
    daily_runtime_dir: Path,
) -> None:
    """The third standing, and the one an ordinary wall clock produces on a historical panel.

    Measured rather than assumed: run today against a 2026-01 fixture, `predicted_at` lands after
    the outcome became knowable and the record stands `backfill` -- correctly, and that is why the
    two tests above build their own clock rather than trusting the process's.
    """
    _sdk, result = _sdk_daily(daily_runtime_dir, lambda: LATE_CLOCK)

    assert result.record.standing == "backfill"
    assert result.record.supersedes is None


def test_the_daily_run_fills_the_manifest_slot_three_issues_left_open(
    daily_runtime_dir: Path,
) -> None:
    """`RunManifest.alpha_model_versions`, filled for the first time.

    `V2-P4-010` declared the slot and named `V2-P4-016` for it; that issue measured that
    `run_cycle` has no `AlphaModel` on its path and passed it on; `V2-P4-017` measured the same
    thing from the store side and left it *"still nobody's"*. A daily run is the first thing in
    this repository that holds a fitted artifact and a run's identity at once.

    The three other component planes stay empty and each is a statement rather than a gap: no
    agent ran, no vendor model was called, and the only prompt in this repository is a string
    literal `code_commit` already pins.
    """
    sdk, result = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)
    manifest = result.manifest

    assert manifest.mode == "daily"
    assert [(ref.name, ref.artifact_id) for ref in manifest.alpha_model_versions] == [
        ("reversal-rank", result.record.batch.artifact.artifact_id)
    ]
    assert manifest.agent_versions == ()
    assert manifest.model_versions == ()
    assert manifest.prompt_versions == ()
    assert manifest.random_seed == DAILY["seed"]
    assert manifest.run_id == f"daily-{result.record.record_id}"
    assert sdk.repository.get_run(manifest.run_id) is not None
    assert sdk.repository.list_runs(mode="daily") == (manifest,)


def test_re_running_an_identical_day_registers_nothing_new_on_either_store(
    daily_runtime_dir: Path,
) -> None:
    """Idempotence, and it has to hold on **both** stores or the second one raises.

    `FilePredictionStore.put` never writes where something is held and reports `unchanged`; the
    run repository raises `DuplicateRecordError` on a repeated `run_id`. Deriving the `run_id`
    from the prediction's own content address is what makes the two agree -- a re-run that
    reproduces a prediction reproduces its address, finds the manifest already filed, and reports
    `unchanged` rather than failing halfway through.
    """
    sdk, first = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)
    second = sdk.run_daily_model(**_sdk_arguments(DAILY))

    assert first.outcome == "created" and first.manifest_outcome == "created"
    assert second.outcome == "unchanged" and second.manifest_outcome == "unchanged"
    assert second.record.record_id == first.record.record_id
    assert second.record.recorded_at == first.record.recorded_at
    assert sdk.list_predictions() == (first.record.record_id,)


def test_a_refused_daily_run_still_registered_its_prediction(daily_runtime_dir: Path) -> None:
    """Exit `1` says the answer may not be acted on. It does not say nothing was stored.

    Story S32 is about a prediction being persisted **before its outcome is known**, which is
    unconditional; the coverage floor is about whether the answer may be published, which is not.
    `run_shortlist` stores a blocked shortlist for the same reason, and getting this backwards
    would make a scheduled job that missed its bar leave no record of what it had predicted.

    The `record_id` is on the refused body, so the two facts arrive together.
    """
    code, out = _cli(daily_runtime_dir, "daily-run", {**DAILY, "minimum_scored_ratio": 0.9})
    answer = json.loads(out)

    assert code == 1
    assert answer["is_blocked"] is True
    assert answer["admitted"] is None
    assert [block["code"] for block in answer["blocks"]] == ["scored_ratio_below_floor"]
    assert answer["measurement"] == {
        "offered_count": DAILY_OFFERED,
        "scored_count": DAILY_SCORED,
        "scored_ratio": DAILY_SCORED / DAILY_OFFERED,
    }
    assert answer["write_outcome"] == "created"

    record_id = answer["prediction"]["record_id"]
    held_code, held_out = _cli_prediction(daily_runtime_dir, record_id)
    assert held_code == 0, held_out
    assert json.loads(held_out)["record_id"] == record_id


def test_an_admitted_daily_run_is_the_same_measurement_one_flag_apart(
    daily_runtime_dir: Path,
) -> None:
    """The other half of the pair, so neither half can pass on a fixture that differs by accident.

    `admitted` is `null` on a refusal and the list of scored securities on an answer -- two
    different answers, on one store, one flag apart.
    """
    refused_code, refused_out = _cli(
        daily_runtime_dir, "daily-run", {**DAILY, "minimum_scored_ratio": 0.9}
    )
    admitted_code, admitted_out = _cli(
        daily_runtime_dir, "daily-run", {**DAILY, "minimum_scored_ratio": 0.0}
    )
    refused = json.loads(refused_out)
    admitted = json.loads(admitted_out)

    assert refused_code == 1 and admitted_code == 0
    assert refused["admitted"] is None
    assert admitted["admitted"] == [
        item["ts_code"]
        for item in admitted["prediction"]["predictions"]
        if item["score"] is not None
    ]
    assert UNVALUED not in admitted["admitted"]
    assert refused["measurement"] == admitted["measurement"]


def test_the_security_the_model_declined_says_so_rather_than_vanishing(
    daily_runtime_dir: Path,
) -> None:
    """`V2-P4-011`'s *scored or abstained, never absent*, at a surface.

    The registered batch answers about every security the cross section carried; the one with no
    value for the declared column carries a stated reason instead of a number, and the reason is
    `alpha_baseline.ABSTAIN_INCOMPLETE_FEATURES` verbatim rather than a code this face invented.
    """
    code, out = _cli(daily_runtime_dir, "daily-run", DAILY)
    assert code == 0, out
    rows = json.loads(out)["prediction"]["predictions"]

    assert [row["ts_code"] for row in rows] == sorted(SECURITIES)
    declined = [row for row in rows if row["score"] is None]
    assert [row["ts_code"] for row in declined] == [UNVALUED]
    assert declined[0]["abstention"] == (
        "this security carries no value for at least one declared feature"
    )


def test_the_daily_fit_consumed_only_outcomes_that_had_already_closed(
    daily_runtime_dir: Path,
) -> None:
    """`trainable_at`, visible at a surface: the artifact's cutoff is at or before the instant it
    predicts about.

    That is `PredictionBatch`'s own floor read forward -- it refuses `as_of < training_cutoff` --
    and the equality case is admitted deliberately, because training through last night's close
    and predicting as of it is what a daily model does. Here the last training label closes at
    2026-01-16's 15:00 Shanghai and the prediction stands at 17:00 the same day.
    """
    _sdk, result = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)
    artifact = result.record.batch.artifact

    assert artifact.training_cutoff <= result.record.batch.as_of
    assert artifact.training_cutoff.isoformat() == "2026-01-16T07:00:00+00:00"
    assert result.training_example_count == artifact.training_example_count


def test_an_evaluation_registers_nothing(daily_runtime_dir: Path) -> None:
    """The register stays empty after an evaluation, and that is a decision rather than a gap.

    `evaluate_fold` dates every batch `predicted_at = section.as_of`, an instant in the past, so
    every record an evaluation could store would stand `unwitnessed` -- claimed in time, received
    late. Filling Story S32's register with backtests would bury the `forward` rows it exists for.
    """
    code, out = _cli(daily_runtime_dir, "evaluate", BASELINE)
    assert code == 0, out

    assert OpenAlphaSDK(runtime_dir=daily_runtime_dir).list_predictions() == ()
    listed_code, listed_out = _cli_predictions(daily_runtime_dir)
    assert listed_code == 0
    assert json.loads(listed_out) == {"record_ids": []}


def _cli_prediction(runtime_dir: Path, record_id: str) -> tuple[int, str]:
    result = CliRunner().invoke(
        app, ["model", "prediction", record_id, "--runtime-dir", str(runtime_dir)]
    )
    return result.exit_code, result.output


def _cli_predictions(runtime_dir: Path) -> tuple[int, str]:
    result = CliRunner().invoke(
        app, ["model", "predictions", "--runtime-dir", str(runtime_dir), "--json"]
    )
    return result.exit_code, result.output


def test_the_rest_face_registers_and_hands_back_one_prediction(
    daily_runtime_dir: Path,
) -> None:
    """`POST /api/v1/models/daily-run` then `GET /api/v1/predictions/{record_id}`.

    The service's own clock stamps both instants, so the standing is a reading of one clock a
    caller never reaches -- which is the entire mechanism behind `PredictionRecord.standing` and
    the reason no request field carries either timestamp.
    """
    with TestClient(
        create_app(runtime_dir=daily_runtime_dir, clock=lambda: FORWARD_CLOCK)
    ) as client:
        registered = client.post("/api/v1/models/daily-run", json=_rest_body(DAILY))
        assert registered.status_code == 200, registered.text
        body = registered.json()
        record_id = body["prediction"]["record_id"]

        listed = client.get("/api/v1/predictions")
        assert listed.json() == {"record_ids": [record_id]}

        held = client.get(f"/api/v1/predictions/{record_id}")
        assert held.status_code == 200
        assert held.json()["record_id"] == record_id
        assert held.json()["standing"] == "forward"
        assert held.json() == body["prediction"]


def test_a_prediction_address_that_is_not_one_and_one_nothing_is_held_under_are_two_answers(
    daily_runtime_dir: Path,
) -> None:
    """`bad_request` and `not_held` stay apart, on both channels.

    "That is not an address" and "nothing is filed under that address" have different remedies --
    fix the question, or run the model -- and one `404` covering both is the collapse
    `ShortlistNotHeldError` was split out to prevent one plane over.
    """
    with TestClient(create_app(runtime_dir=daily_runtime_dir)) as client:
        malformed = client.get("/api/v1/predictions/not-an-address")
        assert malformed.status_code == 422
        assert malformed.json()["detail"]["reason"] == "bad_request"

        unheld = client.get(f"/api/v1/predictions/prd_{'0' * 24}")
        assert unheld.status_code == 404
        assert unheld.json()["detail"]["reason"] == "not_held"

    assert _cli_prediction(daily_runtime_dir, "not-an-address")[0] == 3
    assert _cli_prediction(daily_runtime_dir, f"prd_{'0' * 24}")[0] == 1


# --- 5. the request, refused where it should be -------------------------------------------------


def test_a_declared_feature_version_that_is_not_this_recipe_is_refused_on_every_face(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`require_declared_features`' first caller, and its refusal reaches all three surfaces.

    `V2-P4-012` built that check and could not call it; `V2-P4-014` was named as its first caller
    and structurally could not be, because `backtest-no-numeric-stack-or-panel-plane` forbids
    `openalpha_cn.feature_matrix` to that whole package. The caller is a composition holding a
    declaration and a matrix, and this is it.

    The refusal is `bad_request` on every face, because no amount of building repairs a
    declaration that names a recipe it was not fitted on.
    """
    wrong = {**BASELINE, "feature_version": f"feat_{'0' * 24}"}
    code, out = _cli(runtime_dir, "evaluate", wrong)
    assert code == 3
    assert "declares feature_version" in out
    assert "is offered a matrix built to" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(wrong))
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"

    with pytest.raises(ModelRequestError, match="declares feature_version"):
        OpenAlphaSDK(runtime_dir=runtime_dir).evaluate_model(**_sdk_arguments(wrong))


def test_an_omitted_feature_version_resolves_and_the_answer_says_it_was_resolved(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`--code-commit`'s arrangement on the one field a caller cannot compute by hand.

    A caller cannot type a `feat_` digest, so a face that required one would be unusable; a face
    that never accepted one would leave `AlphaModelDeclaration.feature_version` decorative. What
    the resolved form does *not* prove is that anybody intended that recipe -- a mistyped
    `--feature` produces a different, self-consistent digest -- so the answer records **which of
    the two it was** rather than leaving a reader to infer it from the command line.
    """
    code, out = _cli(runtime_dir, "evaluate", BASELINE)
    assert code == 0, out
    resolved = json.loads(out)["declaration"]

    assert resolved["feature_version"].startswith("feat_")
    assert resolved["feature_version_source"] == "resolved"

    declared = {**BASELINE, "feature_version": resolved["feature_version"]}
    from_rest = rest.post("/api/v1/models/evaluate", json=_rest_body(declared))
    assert from_rest.status_code == 200
    assert from_rest.json()["declaration"]["feature_version_source"] == "declared"
    assert from_rest.json()["declaration"]["feature_version"] == resolved["feature_version"]


@pytest.mark.parametrize("field", ["code_commit", "config_digest"])
def test_an_explicitly_empty_provenance_field_is_refused_on_every_face(
    runtime_dir: Path, rest: TestClient, field: str
) -> None:
    """`V2-P4-046`, which measured `code_commit=""` publishing on one face and refused on two.

    Both flags default to `None` -- *unset* -- rather than to `""`, so there is a value the parser
    can hand back that means "the caller typed an empty one". With an empty-string default there
    is not, and `value or None` resolves it server-side: the same literal was a `422` over HTTP
    and a published answer on the command line.
    """
    empty = {**BASELINE, field: ""}
    code, out = _cli(runtime_dir, "evaluate", empty)
    assert code == 3, out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(empty))
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"

    with pytest.raises(ModelRequestError):
        OpenAlphaSDK(runtime_dir=runtime_dir).evaluate_model(**_sdk_arguments(empty))


@pytest.mark.parametrize("field", ["code_commit", "config_digest"])
def test_an_omitted_provenance_field_still_resolves_from_the_process(
    runtime_dir: Path, rest: TestClient, field: str
) -> None:
    """The other half of the pair: omitted is not the same as empty, and still answers."""
    omitted = {**BASELINE, field: None}
    code, out = _cli(runtime_dir, "evaluate", omitted)
    assert code == 0, out
    assert json.loads(out)["is_blocked"] is False

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(omitted))
    assert response.status_code == 200


def test_a_factor_no_registry_declares_is_a_bad_request_rather_than_an_empty_evaluation(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`bad_request`: exit 3 and `422`, with the keys this build knows in the message.

    No amount of building fixes a mistyped factor, and a caller who typed a key needs the keys
    back rather than nineteen content addresses -- `shortlist_view._resolve_factor`'s measured
    wording, restated on this face because the two refusals are two functions.
    """
    mistyped = {**BASELINE, "features": ({"factor": "no_such_factor/v1", "tier": "raw"},)}
    code, out = _cli(runtime_dir, "evaluate", mistyped)
    assert code == 3
    assert "reversal_1d/v1" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(mistyped))
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"
    assert "reversal_1d/v1" in response.json()["detail"]["message"]


def test_a_family_no_implementation_answers_to_is_refused_with_the_two_that_exist(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`MODEL_FAMILIES` is a table, so a third implementation with no row is refused by name.

    An `if`/`elif` chain would have fallen through to whichever branch it happened to end on,
    which is `SHORTLIST_EXIT`'s own argument for a table one plane over.
    """
    unknown = {**BASELINE, "family": "lightgbm"}
    code, out = _cli(runtime_dir, "evaluate", unknown)
    assert code == 3
    assert "boosted_rank_trees" in out and "cross_sectional_rank" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(unknown))
    assert response.status_code == 422
    assert "cross_sectional_rank" in response.json()["detail"]["message"]


def test_a_neutralized_feature_is_refused_by_name_with_the_issue_that_owns_the_boundary(
    runtime_dir: Path, rest: TestClient
) -> None:
    """The tier this face cannot read, refused at request time rather than as a fit on nothing.

    `openalpha factor build --tier neutralized` refuses every instant before its year's last
    stored session (`V2-P4-026`), so a neutralized column is empty at every instant a walk-forward
    asks about. Letting that arrive as a blocked panel would send a caller to rebuild a partition
    that cannot be built at those instants at all.
    """
    neutralized = {
        **BASELINE,
        "features": (
            {
                "factor": REVERSAL.qualified_key,
                "tier": "neutralized",
                "transform": "cross_section_standard/v1",
                "neutralization": "industry_market_cap/v1",
            },
        ),
    }
    code, out = _cli(runtime_dir, "evaluate", neutralized)
    assert code == 3
    assert "V2-P4-026" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(neutralized))
    assert response.status_code == 422
    assert "V2-P4-026" in response.json()["detail"]["message"]


def test_a_reading_as_of_before_the_range_it_reads_is_refused_rather_than_answered_short(
    runtime_dir: Path, rest: TestClient
) -> None:
    """The two clocks, and the one arrangement of them that cannot answer.

    An outcome is not knowable at the instant it is predicted about, so the labels behind every
    fold are read at one later `as_of`. A run that read them at or before its own last prediction
    day would find no closed window at all and would report a panel fault for a request fault.
    """
    early = {**BASELINE, "as_of": datetime(2026, 1, 10, 4, 0, tzinfo=UTC)}
    code, out = _cli(runtime_dir, "evaluate", early)
    assert code == 3
    assert "before the last prediction day it asks about" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(early))
    assert response.status_code == 422


def test_a_prediction_instant_inside_the_training_range_is_refused(runtime_dir: Path) -> None:
    """A daily run predicts about a day that has no outcome yet, so it must be after the range.

    Without this the fit would be offered the very day it is predicting about, which is the
    leakage `PredictionBatch`'s floor refuses one contract down -- and the refusal there names an
    instant comparison rather than the flag a caller has to change.
    """
    inside = {**DAILY, "predict_at": _build_instant(TRAINING_SESSIONS[-1])}
    code, out = _cli(runtime_dir, "daily-run", inside)
    assert code == 3
    assert "is not after the last training day it names" in out


def test_a_year_the_panel_never_held_is_refused_by_name_rather_than_answered_empty(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`blocked`: exit 1 and `409` -- never `200` with no folds."""
    unheld = {**BASELINE, "years": (YEAR - 3,)}
    code, out = _cli(runtime_dir, "evaluate", unheld)
    assert code == 1, out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(unheld))
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] in {"blocked", "panel_unreadable"}


def test_a_panel_short_of_the_adjustment_factors_names_the_command_that_builds_them(
    unadjusted_runtime_dir: Path,
) -> None:
    """`V2-P4-078` on this face: the refusal has to name the command, not only the partition.

    `adj_factor` is the prerequisite that separates a model run from a shortlist run -- a label is
    a *return between two sessions*, so `label_outcome` requires an adjustment series -- and it is
    exactly the kind of partition a caller who has been running `factor build --tier raw` will not
    have. A message naming the dataset and nothing else leaves them to find `PANEL_BUILD_TARGETS`
    themselves, which is the bar `panel_view.NO_CALENDAR_REMEDY` set.
    """
    code, out = _cli(unadjusted_runtime_dir, "evaluate", BASELINE)

    assert code == 1, out
    assert "openalpha panel build --dataset adj_factor --year" in out


def test_no_model_response_names_the_store_on_disk(runtime_dir: Path, rest: TestClient) -> None:
    """A refusal echoing the runtime directory answers a question about the deployment.

    `panel_view.PANEL_STORE_PLACEHOLDER`'s rule, unchanged. The status assertion is what stops
    this passing vacuously on a `404`: the route has to exist and have refused.
    """
    response = rest.post(
        "/api/v1/models/evaluate", json=_rest_body({**BASELINE, "years": (YEAR - 3,)})
    )

    assert response.status_code == 409
    assert str(runtime_dir) not in response.text
    assert PANEL_STORE_PLACEHOLDER in response.json()["detail"]["message"]


def test_the_answer_carries_the_limitations_a_reader_needs_beside_the_numbers(
    runtime_dir: Path,
) -> None:
    """Nine named boundaries, in the body rather than in documentation.

    `openalpha factor list --json`'s `run_limitations` arrangement and its reason: a caller pastes
    the body into a report, and a boundary that lives only in a docstring is one nobody who reads
    the report will meet.
    """
    _code, out = _cli(runtime_dir, "evaluate", BASELINE)
    limitations = json.loads(out)["limitations"]

    codes = {item["code"] for item in limitations}
    assert "the_scored_ratio_floor_is_a_coverage_bar_and_never_a_quality_one" in codes
    assert "no_hyperparameter_is_selected_by_anything_on_this_face" in codes
    assert all(item["detail"] for item in limitations)


def test_the_evaluation_reports_which_instant_each_fold_was_cut_from(runtime_dir: Path) -> None:
    """`cross_section_as_of` on every point, because a stored build may be older than the day.

    A prediction day is the zone date of the instant a cross section was **built** at, and the
    build may be stale; the per-point `as_of` is what says which one answered. A face that
    reported only the day would make a fortnight-old cross section indistinguishable from a fresh
    one.
    """
    _code, out = _cli(runtime_dir, "evaluate", BASELINE)
    points = [point for fold in json.loads(out)["folds"] for point in fold["points"]]

    assert [point["prediction_day"] for point in points] == [
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
    ]
    for point in points:
        instant = datetime.fromisoformat(point["as_of"])
        assert instant == _build_instant(date.fromisoformat(point["prediction_day"]))


def test_the_daily_terminal_rendering_says_the_standing_and_what_it_does_not_prove(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal channel carries both sentences too, and `V2-P4-017`'s bar is why.

    A reader of a terminal is the reader most likely to take a one-word standing at face value,
    and the JSON body's two keys do not reach them. `monkeypatch` on the CLI's own clock is what
    makes a `forward` standing reachable from a command line on a historical panel -- the
    established seam in `test_cli_panel.py` and its siblings.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    code, out = _cli(daily_runtime_dir, "daily-run", {**DAILY, "json_output": False})

    assert code == 0, out
    assert out.splitlines()[0].startswith("verdict             REGISTERED")
    assert "standing            forward" in out
    assert "and does not prove  that the batch was produced when it says it was" in out
    assert "outcome_known_at    2026-01-20T07:00:00+00:00" in out


# --- 6. what the first mutation round found nothing driving -------------------------------------


def test_a_ratio_exactly_on_the_declared_floor_is_admitted(runtime_dir: Path) -> None:
    """`>=` and not `>`, and the boundary is where a coverage bar is most often argued about.

    A mutant making the floor exclusive survived the whole suite, because every other test
    declares a floor the measurement is strictly above or strictly below. The inclusive reading
    is the right one and it is not arbitrary: a caller who reads `scored_ratio: 0.875` off one
    run and declares `0.875` on the next is asking for "at least what I saw", and a face that
    refused it would be refusing the very answer it had just published.
    """
    exact = {**BASELINE, "minimum_scored_ratio": SCORED / OFFERED}
    code, out = _cli(runtime_dir, "evaluate", exact)
    answer = json.loads(out)

    assert code == 0, out
    assert answer["measurement"]["scored_ratio"] == exact["minimum_scored_ratio"]
    assert answer["is_blocked"] is False
    assert answer["blocks"] == []


def test_a_floor_no_answer_could_meet_is_refused_as_a_request_rather_than_as_a_verdict(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`minimum_scored_ratio` outside `[0, 1]` is `bad_request`, not a permanent refusal.

    The failure this closes is a caller who typed a percentage: `--min-scored-ratio 90` is a bar
    no cross section can clear, and answering it with `refused` would report a fact about the
    model for a fault in the request -- exit 1 and a `409` both mean "the panel and this model
    are the problem", and the remedy here is a different number.
    """
    for floor in (90.0, -0.5):
        code, out = _cli(runtime_dir, "evaluate", {**BASELINE, "minimum_scored_ratio": floor})
        assert code == 3, out
        assert "outside [0, 1]" in out

    response = rest.post(
        "/api/v1/models/evaluate", json=_rest_body({**BASELINE, "minimum_scored_ratio": 90.0})
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"


def test_a_range_the_panel_holds_no_build_in_is_refused_by_name(
    runtime_dir: Path, rest: TestClient
) -> None:
    """A **year** the store never held and a **range** inside a year it did are two refusals.

    The first fails at the partition read; the second reads the partition, finds nine stored cross
    sections and none of them inside the declared days. A mutant that dropped the second check
    survived, because the only "nothing to answer with" test in this file was the first kind --
    and the two have different remedies: build the year, or build the days.
    """
    empty = {
        **BASELINE,
        "start": date(2026, 2, 2),
        "end": date(2026, 2, 6),
        "as_of": datetime(2026, 2, 10, 4, 0, tzinfo=UTC),
    }
    code, out = _cli(runtime_dir, "evaluate", empty)
    assert code == 1, out
    assert "no stored cross section" in out
    assert "2026-02-02" in out and "2026-02-06" in out
    assert "openalpha factor build" in out

    response = rest.post("/api/v1/models/evaluate", json=_rest_body(empty))
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "blocked"


def test_a_panel_read_refusal_names_no_path_over_http(unadjusted_runtime_dir: Path) -> None:
    """The **panel read** path's `disclosable`, which the year-that-was-never-held case misses.

    `test_no_model_response_names_the_store_on_disk` drives a refusal raised while resolving the
    stored instants, which has a `disclosable` of its own; a mutant that made `_read` -- the seam
    every calendar, registry, bar, band, halt and adjustment read goes through -- echo the store's
    absolute path survived it. This is that seam, reached by a panel that holds no `adj_factor`.
    """
    with TestClient(create_app(runtime_dir=unadjusted_runtime_dir)) as client:
        response = client.post("/api/v1/models/evaluate", json=_rest_body(BASELINE))

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    message = response.json()["detail"]["message"]
    assert PANEL_STORE_PLACEHOLDER in message
    assert str(unadjusted_runtime_dir) not in response.text
    assert "openalpha panel build --dataset adj_factor --year" in message


def test_the_other_shipped_family_is_fitted_by_the_other_implementation(
    runtime_dir: Path,
) -> None:
    """`--family boosted_rank_trees` goes through `alpha_tree.py` and not through the baseline.

    A mutant pointing the tree row of `MODEL_FAMILIES` at `CrossSectionalRankModel` survived every
    test, because nothing drove the second family. That row is what `V2-P4-015` shipped, and a
    table whose second entry is never exercised is an `if`/`elif` chain with extra ceremony.

    The two artifacts must differ: `family` reaches the declaration and the declaration reaches
    the address, so a mis-keyed table would produce the *rank baseline's* coefficients under a
    declaration claiming the tree's family -- which validates, and is a lie about which arithmetic
    ran.
    """
    trees = {
        **BASELINE,
        "name": "reversal-trees",
        "family": "boosted_rank_trees",
        "hyperparameters": (
            ("learning_rate", "0.1"),
            ("max_depth", "2"),
            ("min_leaf_securities", "3"),
            ("tree_count", "4"),
        ),
    }
    code, out = _cli(runtime_dir, "evaluate", trees)
    assert code == 0, out
    answer = json.loads(out)

    assert answer["declaration"]["family"] == "boosted_rank_trees"
    assert [item["name"] for item in answer["declaration"]["hyperparameters"]] == [
        "learning_rate",
        "max_depth",
        "min_leaf_securities",
        "tree_count",
    ]
    _rank_code, rank_out = _cli(runtime_dir, "evaluate", BASELINE)
    assert [fold["artifact_id"] for fold in answer["folds"]] != [
        fold["artifact_id"] for fold in json.loads(rank_out)["folds"]
    ]
    assert [fold["parameters"] for fold in answer["folds"]] != [
        fold["parameters"] for fold in json.loads(rank_out)["folds"]
    ]


def test_two_hyperparameters_declared_out_of_order_are_one_declaration_on_both_faces(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`AlphaModelDeclaration` refuses an unsorted tuple, so both faces sort before they build one.

    A mutant removing either sort survived, because every other test declares at most one
    hyperparameter. The order a caller typed is not a claim -- a repeated **name** is, and that
    contract still refuses it -- so sorting costs nothing and buys one address per declaration.
    Unsorted, the contract would refuse a legal request on one face and accept it on the other
    depending on the order the flags happened to be typed in.
    """
    unsorted = {**BASELINE, "hyperparameters": (("zeta", "1"), ("alpha", "2"))}
    code, out = _cli(runtime_dir, "evaluate", unsorted)
    assert code == 0, out
    from_cli = json.loads(out)

    body = _rest_body(unsorted)
    body["hyperparameters"] = [{"name": "zeta", "value": 1}, {"name": "alpha", "value": 2}]
    response = rest.post("/api/v1/models/evaluate", json=body)
    assert response.status_code == 200, response.text

    assert [item["name"] for item in from_cli["declaration"]["hyperparameters"]] == [
        "alpha",
        "zeta",
    ]
    assert from_cli["declaration"] == response.json()["declaration"]
    assert [fold["artifact_id"] for fold in from_cli["folds"]] == [
        fold["artifact_id"] for fold in response.json()["folds"]
    ]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("reversal_1d/v1", "is not `<factor>@<tier>`"),
        ("reversal_1d/v1@", "names no tier"),
        ("reversal_1d/v1@raw:a:b:c", "spec(s) after its tier"),
    ],
)
def test_a_malformed_feature_token_is_refused_before_any_store_is_opened(
    runtime_dir: Path, token: str, expected: str
) -> None:
    """Three ways a `--feature` can be half a column, and three different sentences.

    The grammar is `feature_matrix.FeatureColumn.feature_id`'s read backwards: `@` between the
    factor and the tier, `:` between the tier and each spec. A token with no `@` names a factor
    and no tier, which has no default because `raw` and `processed` carry different numbers for
    the same security at the same instant.

    **The first case is the one the first draft of this test could not reach**, and a mutant
    found it: `_cli` composes `f"{factor}@{tier}"`, so passing an empty tier produced
    `reversal_1d/v1@` -- which *has* the separator and is refused one function later, by the tier
    resolver. The separator check went untested while a test named after it passed. Each token
    here is handed to the parser verbatim.
    """
    result = CliRunner().invoke(
        app,
        [
            "model",
            "evaluate",
            "--runtime-dir",
            str(runtime_dir),
            "--feature",
            token,
            "--name",
            "reversal-rank",
            "--family",
            "cross_sectional_rank",
            "--horizon",
            HORIZON,
            "--seed",
            "7",
            "--start",
            TRAINING_SESSIONS[0].isoformat(),
            "--end",
            TRAINING_SESSIONS[-1].isoformat(),
            "--year",
            str(YEAR),
            "--folds",
            "2",
            "--test-days-per-fold",
            "2",
            "--embargo-sessions",
            "0",
            "--min-scored-ratio",
            "0.0",
        ],
    )

    assert result.exit_code == 3, result.output
    assert expected in result.output


def test_a_naive_predict_at_is_refused_with_the_field_it_names(runtime_dir: Path) -> None:
    """A naive instant reaches `daily_request`'s own refusal, which is the only copy of the rule.

    The CLI parsed `--predict-at` and checked its offset too; a mutant deleting that check
    survived, and deleting it for real was the answer rather than testing a second copy -- the
    contract below refuses the same value by name. What the command line still owns is the parse:
    a token that is not an instant never becomes one for `_aware` to inspect.
    """
    naive = {**DAILY, "predict_at": datetime(2026, 1, 16, 9, 0)}
    code, out = _cli(runtime_dir, "daily-run", naive)
    assert code == 3, out
    assert "carries no UTC offset" in out

    unparseable = dict(DAILY)
    result = CliRunner().invoke(
        app,
        [
            "model",
            "daily-run",
            "--runtime-dir",
            str(runtime_dir),
            "--predict-at",
            "the-sixteenth",
            "--feature",
            f"{REVERSAL.qualified_key}@raw",
            "--name",
            str(unparseable["name"]),
            "--family",
            str(unparseable["family"]),
            "--horizon",
            HORIZON,
            "--seed",
            "7",
            "--start",
            unparseable["start"].isoformat(),
            "--end",
            unparseable["end"].isoformat(),
            "--year",
            str(YEAR),
            "--min-scored-ratio",
            "0.0",
        ],
    )
    assert result.exit_code == 3
    assert "expects an ISO-8601 instant with an offset" in result.output


def test_a_daily_fit_leaves_out_the_label_that_had_not_closed_yet(
    daily_runtime_dir: Path,
) -> None:
    """`trainable_at` at a surface, on the only instant where it removes something.

    A mutant replacing the purge with `panel.examples` survived every other test in this file,
    and the fixture is why rather than the assertion: predicting about 2026-01-16 comes after
    **every** training label has closed, so the two answers coincide. Predicting about the 15th
    does not -- 2026-01-14's window exits on the 16th, which is after 17:00 on the 15th -- so the
    fit consulting it would be a fit that consumed an outcome nobody could have known.

    Both halves are asserted against the same store one flag apart, because "one fewer day" alone
    passes on a face that dropped the *first* day instead of the last.
    """
    sdk = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=lambda: FORWARD_CLOCK)
    late = sdk.run_daily_model(**_sdk_arguments(DAILY))
    early = sdk.run_daily_model(
        **{**_sdk_arguments(DAILY), "predict_at": _build_instant(SESSIONS[8])}
    )

    assert late.training_day_count == len(TRAINING_SESSIONS) == 7
    assert early.training_day_count == 6
    assert early.record.batch.artifact.training_cutoff < late.record.batch.artifact.training_cutoff
    assert early.record.batch.artifact.training_cutoff <= early.record.batch.as_of
    assert early.record.batch.artifact.artifact_id != late.record.batch.artifact.artifact_id


def test_a_partition_whose_file_is_gone_is_refused_without_naming_it(
    tmp_path: Path, runtime_dir: Path
) -> None:
    """The one refusal on this face that really does interpolate a filesystem path.

    A mutant replacing `_read`'s `_without_store_path` with the raw error survived two rounds,
    and the reason was the fixture rather than the guard: a year the store never held, an empty
    range and a missing partition all produce messages that name a **dataset** and never a path,
    so the substitution was invisible. `panel_view._without_store_path`'s own docstring names the
    shape that is not invisible -- *"a `PanelStorageError` about a registered partition whose
    Parquet file is gone interpolates that file's path into its own detail"* -- and this drives
    it: the catalog still has the row, the bytes are not there, and DuckDB says so with an
    absolute path in hand.

    Measured before it was relied on: without the guard the `409` body carries the temporary
    directory's absolute path, which answers a question about the deployment to whoever can reach
    the port.

    **Which face sees which message was also measured, and it is not the one this test first
    assumed.** A first draft asserted the *command line* prints the path-bearing form, on the
    reasoning that the CLI runs inside the process that owns the store. It does not:
    `cli._model_fail` hands `_panel_fail` the `disclosable` message, exactly as
    `cli._shortlist_fail` does, so the only caller that ever reads the local form is one holding
    the exception -- an in-process SDK caller. Both halves are asserted below, because a
    `disclosable` that equalled the local message would pass any test that only looked at one.

    **What survives the guard is the partition's path *inside* the store, and that is deliberate.**
    The first draft of this test asserted `"data.parquet" not in response.text` and went red on a
    body reading `<panel-store>/adj_factor/2026/data.parquet`. The assertion was wrong, not the
    code: `PANEL_STORE_PLACEHOLDER`'s stated guarantee is that *"the store's location is
    configuration of the process that holds it"*, and a relative partition path is identical on
    every installation -- it is the actionable half, naming which partition to rebuild. Asserting
    its presence rather than its absence is what keeps a future "sanitise harder" from silently
    turning an actionable refusal into an unactionable one.
    """
    root = tmp_path / "corrupt"
    root.mkdir()
    shutil.copytree(runtime_dir / "panel", root / "panel")
    partition = root / "panel" / ADJ_FACTOR_DATASET / str(YEAR) / "data.parquet"
    assert partition.is_file(), "the corpus is expected to hold a registered adj_factor partition"
    partition.unlink()

    with TestClient(create_app(runtime_dir=root)) as client:
        response = client.post("/api/v1/models/evaluate", json=_rest_body(BASELINE))

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "panel_unreadable"
    assert "partition_file_missing" in detail["message"]
    assert PANEL_STORE_PLACEHOLDER in detail["message"]
    assert str(root) not in response.text
    assert (
        f"{PANEL_STORE_PLACEHOLDER}/{ADJ_FACTOR_DATASET}/{YEAR}/data.parquet" in detail["message"]
    )

    code, out = _cli(root, "evaluate", BASELINE)
    assert code == 1
    assert str(root) not in out

    with pytest.raises(ModelPanelUnreadableError) as raised:
        OpenAlphaSDK(runtime_dir=root).evaluate_model(**_sdk_arguments(BASELINE))
    assert str(root / "panel") in str(raised.value)
    assert str(root) not in raised.value.disclosable
