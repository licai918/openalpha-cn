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
import re
import shutil
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from cli_help import rendered_help
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
from openalpha_cn.model_view import (
    KNOWN_MODEL_VIEW_LIMITATIONS,
    ModelPanelUnreadableError,
    ModelRequestError,
)
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
from openalpha_cn.storage.predictions import PredictionStoreError

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
    if parameters.get("shelf_life_days") is not None:
        arguments += ["--shelf-life-days", str(parameters["shelf_life_days"])]
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


ONE_NAME_TWO_TYPES: Final[tuple[tuple[str, bool | int | float | str], ...]] = (
    ("audited", 1),
    ("audited", "a"),
)
"""`V2-P4-091`'s literal input: one name declared twice, with values of two different types.

The repetition is what every face must refuse -- `AlphaModelDeclaration` calls a repeated key "one
parameter stated twice, and the two can disagree" -- and the two *types* are what decided which
face refused it. A face that sorts whole `(name, value)` pairs reaches the values only when the
names tie, so this is the narrowest input that separates sorting by name from sorting by pair, and
it is a caller's mistake on every face rather than an exotic one: `--hyperparameter audited=1
--hyperparameter audited=a` is a typo a scheduler can make.
"""


@pytest.fixture
def served(runtime_dir: Path) -> Iterator[TestClient]:
    """The REST face with Starlette's own last-resort handler left in front of it.

    `test_partial_registry_faces._post`'s arrangement and its reason: the defect this fixture is
    for **is** the unenveloped `500`, so a client that re-raised the server's exception would hide
    the thing under test behind a traceback. `rest` above keeps the default, because every other
    test here wants a raised fault to be a loud one.
    """
    with TestClient(
        create_app(runtime_dir=runtime_dir, clock=lambda: FORWARD_CLOCK),
        raise_server_exceptions=False,
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("command", "route"),
    [("evaluate", "/api/v1/models/evaluate"), ("daily-run", "/api/v1/models/daily-run")],
)
def test_one_name_declared_twice_with_two_types_is_one_verdict_on_all_three_faces(
    runtime_dir: Path, served: TestClient, command: str, route: str
) -> None:
    """`V2-P4-091`: sixteen bad inputs agreed across the faces and this one did not.

    `cli._model_hyperparameters` sorted by name; `ModelRunApiRequest.declared_hyperparameters`
    sorted whole `(name, value)` tuples. On two hyperparameters sharing a name the pair sort falls
    through to the values and compares `1 < "a"`, which is a `TypeError` -- raised while the
    route's arguments are still being evaluated, so it lands outside `except (ModelViewError,
    PredictionStoreError)` and Starlette answers `500 text/plain "Internal Server Error"`. Not the
    `{"detail": {...}}` envelope, so a client branching on `isinstance(detail, dict)` -- the
    branch `MODEL_HTTP_STATUS`' own docstring tells it to take -- gets nothing at all.

    A caller error reported as a service fault pages an operator and trips a retry, which is why
    this is the divergence that matters rather than a cosmetic one. Both run routes are driven
    because both evaluate the same property.
    """
    declared = {
        **(BASELINE if command == "evaluate" else DAILY),
        "hyperparameters": ONE_NAME_TWO_TYPES,
    }

    code, out = _cli(runtime_dir, command, declared)
    assert code == 3, out
    assert "not strictly increasing" in out

    response = served.post(route, json=_rest_body(declared))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["reason"] == "bad_request"
    assert "not strictly increasing" in detail["message"]

    sdk = OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: FORWARD_CLOCK)
    call = sdk.evaluate_model if command == "evaluate" else sdk.run_daily_model
    with pytest.raises(ModelRequestError, match="not strictly increasing"):
        call(**_sdk_arguments(declared))


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


def test_a_stale_daily_run_abstains_on_every_security_and_is_refused_by_the_coverage_floor(
    daily_runtime_dir: Path,
) -> None:
    """Story S35 at a surface: `stale 模型显式弃权`, end to end through the command line.

    One flag apart, on one store, with the same declaration and the same stored cross section.
    Under a shelf life of zero days the fit stands further past its training cutoff than it is
    allowed to and **every** security carries `ABSTAIN_STALE_MODEL` instead of a score -- including
    the ones the model had a number for a line above, which is what makes this a policy rather than
    a coincidence about coverage.

    The refusal comes from `--min-scored-ratio` and not from the shelf life, which is
    `an_expired_run_is_refused_only_by_the_coverage_floor_the_caller_declared` driven: the same
    expired run under a floor of `0.0` exits `0`. Both are asserted, because a bar asserted in one
    direction is a constant.

    **The prediction is registered either way.** Story S32 is about a prediction being persisted
    before its outcome is known, which is unconditional -- and a batch of stated refusals is
    exactly the kind of answer that has to be on the record rather than absent from it.
    """
    stale = {**DAILY, "shelf_life_days": 0, "minimum_scored_ratio": 0.5}
    fresh_code, fresh_out = _cli(daily_runtime_dir, "daily-run", DAILY)
    stale_code, stale_out = _cli(daily_runtime_dir, "daily-run", stale)
    admitted_code, admitted_out = _cli(
        daily_runtime_dir, "daily-run", {**stale, "minimum_scored_ratio": 0.0}
    )

    assert fresh_code == 0, fresh_out
    fresh = json.loads(fresh_out)
    expired = json.loads(stale_out)
    admitted = json.loads(admitted_out)

    assert any(row["score"] is not None for row in fresh["prediction"]["predictions"])
    assert [row["ts_code"] for row in expired["prediction"]["predictions"]] == sorted(SECURITIES)
    assert {row["abstention"] for row in expired["prediction"]["predictions"]} == {
        "this fit's training cutoff stands further behind this cross section than its shelf life"
    }
    assert all(row["score"] is None for row in expired["prediction"]["predictions"])

    assert expired["measurement"]["scored_ratio"] == 0.0
    assert expired["declaration"]["shelf_life_days"] == 0
    assert fresh["declaration"]["shelf_life_days"] is None
    assert stale_code == 1 and expired["admitted"] is None
    assert admitted_code == 0 and admitted["admitted"] == []
    assert expired["prediction"]["record_id"], "a refused run still registered its prediction"


def test_a_declared_shelf_life_is_one_answer_on_all_three_faces(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`--shelf-life-days 0`, `{"shelf_life_days": 0}` and `shelf_life_days=0` are one question.

    The evaluation face rather than the daily one, because a walk-forward schedule expires every
    fold at once and the comparison is then over the whole `folds` array. `artifact_id` is asserted
    equal across the three **and** unchanged from the un-expired run: the shelf life is a property
    of the ask, so it reaches no artifact field and cannot move a fit's address.
    """
    expired = {**BASELINE, "shelf_life_days": 0}
    code, out = _cli(runtime_dir, "evaluate", expired)
    assert code == 0, out
    from_cli = json.loads(out)
    from_rest = rest.post("/api/v1/models/evaluate", json=_rest_body(expired)).json()
    from_sdk = OpenAlphaSDK(runtime_dir=runtime_dir).evaluate_model(**_sdk_arguments(expired))
    fresh = json.loads(_cli(runtime_dir, "evaluate", BASELINE)[1])

    assert from_cli["declaration"] == from_rest["declaration"]
    assert from_cli["declaration"]["shelf_life_days"] == 0
    assert from_cli["measurement"] == from_rest["measurement"]
    assert from_cli["measurement"]["scored_ratio"] == 0.0
    assert from_sdk.scored_ratio == 0.0
    assert all(fold["mean_rank_ic"] is None for fold in from_cli["folds"])
    assert [fold["artifact_id"] for fold in from_cli["folds"]] == [
        fold["artifact_id"] for fold in fresh["folds"]
    ], "the fit is the same fit; only the reading of it changed"
    assert any(fold["mean_rank_ic"] is not None for fold in fresh["folds"])


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


def test_a_training_range_reaching_the_prediction_day_is_purged_rather_than_refused(
    daily_runtime_dir: Path, tmp_path: Path, runtime_dir: Path
) -> None:
    """`V2-P4-095`: the labelling read refused a range the purge was about to discard.

    The panel prices ten sessions and the run predicts about the last of them. `--end` at the
    session *before* it -- the most natural thing to type, and one this command's own contract
    invites -- opened a `1d` window exiting on 2026-01-19, a session the panel does not hold, and
    the whole run died at `exit 1` reading price bars for it. The wall was exactly the horizon:
    a caller had to pull `--end` back `horizon + 1` sessions, and no message, flag or limitations
    entry said so.

    **What makes it a defect rather than a limitation is that the answer was never in doubt.**
    `daily-run` promises *"the training set is every labelled example whose outcome window had
    already closed at `--predict-at`; nothing that had not is offered to the fit"*, and
    2026-01-15's window closes on 2026-01-19, three days after the instant this run predicts
    about. It was always going to be purged. So the two `--end` values one session apart are
    asserted to produce the **same artifact**, not merely two successes: the user was losing
    nothing but the guessing, and an assertion that only checked `exit 0` would pass on a fix
    that quietly trained on a different set.

    **Both directions of the section filter are pinned here and nowhere else, which is why a
    second test asserting "the purge removes nothing" was written and deleted.** Whether
    `trainable_at` removes anything after the filter is not observable from any face -- a filter
    that kept 2026-01-15 and let the purge drop it reports the identical `day_count` -- so that
    test asserted a formula (`days x securities`) rather than the property, and the formula was
    wrong: the fixture labels 54 examples over 7 days and 8 names, not 56, because two of them
    carry no outcome. What separates the two directions is *this* test: a looser filter reaches
    for 2026-01-19's bars again and the run dies, and a stricter one trains on six days and the
    artifact stops matching.
    """
    root = tmp_path / "purged"
    root.mkdir()
    shutil.copytree(runtime_dir / "panel", root / "panel")
    reaching = {**DAILY, "end": SESSIONS[8]}
    assert reaching["end"] == date(2026, 1, 15), "the session before the one predicted about"
    assert reaching["end"] > DAILY["end"], "this range has to reach further than the safe one"

    code, out = _cli(root, "daily-run", reaching)
    assert code == 0, out
    reached = json.loads(out)

    with TestClient(create_app(runtime_dir=root, clock=lambda: FORWARD_CLOCK)) as client:
        response = client.post("/api/v1/models/daily-run", json=_rest_body(reaching))
    assert response.status_code == 200, response.text

    sdk = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=lambda: FORWARD_CLOCK)
    pulled_back = sdk.run_daily_model(**_sdk_arguments(DAILY))
    reaching_sdk = sdk.run_daily_model(**_sdk_arguments(reaching))

    assert reached["training"]["day_count"] == len(TRAINING_SESSIONS) == 7
    assert reached["training"]["end"] == SESSIONS[8].isoformat()
    assert (
        reaching_sdk.record.batch.artifact.artifact_id
        == pulled_back.record.batch.artifact.artifact_id
    ), "pulling --end back a session was never a different fit, only a different guess"
    assert reaching_sdk.record.record_id == pulled_back.record.record_id


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
    assert json.loads(listed_out) == {"record_ids": [], "predictions": []}

    terminal_code, terminal_out = _cli_predictions(daily_runtime_dir, json_output=False)
    assert terminal_code == 0
    assert terminal_out == "", "an empty register prints no header to hang a row on"


def _cli_prediction(runtime_dir: Path, record_id: str) -> tuple[int, str]:
    result = CliRunner().invoke(
        app, ["model", "prediction", record_id, "--runtime-dir", str(runtime_dir)]
    )
    return result.exit_code, result.output


def _cli_predictions(runtime_dir: Path, *, json_output: bool = True) -> tuple[int, str]:
    arguments = ["model", "predictions", "--runtime-dir", str(runtime_dir)]
    result = CliRunner().invoke(app, arguments + (["--json"] if json_output else []))
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
        assert listed.json()["record_ids"] == [record_id]
        assert [row["record_id"] for row in listed.json()["predictions"]] == [record_id]

        held = client.get(f"/api/v1/predictions/{record_id}")
        assert held.status_code == 200
        assert held.json()["record_id"] == record_id
        assert held.json()["standing"] == "forward"
        assert held.json() == {**body["prediction"], "limitations": body["limitations"]}


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


def _register_then(daily_runtime_dir: Path, damage: Any) -> str:
    """Register one prediction, apply `damage` to the bytes on disk, and return its address."""
    _sdk, result = _sdk_daily(daily_runtime_dir, lambda: FORWARD_CLOCK)
    record_id = result.record.record_id
    document = daily_runtime_dir / "predictions" / f"{record_id}.json"
    document.write_text(damage(document.read_text(encoding="utf-8")), encoding="utf-8")
    return record_id


_DAMAGE: Final[dict[str, Any]] = {
    "a write that stopped half way": lambda whole: whole[: len(whole) // 2],
    "a schema_version this build cannot read": lambda whole: whole.replace(
        "alpha-prediction-record/v1", "alpha-prediction-record/v2", 1
    ),
    "a field that is no longer the type it was filed as": lambda whole: json.dumps(
        {**json.loads(whole), "batch": {**json.loads(whole)["batch"], "scored": "not a list"}}
    ),
}
"""Three documents `read_versioned` cannot turn back into a record, one per fault it raises.

Truncation is `V2-P4-096`'s own -- a power cut or a full disk between `write_text` and the
flush behind `replace` -- and the other two are the same seam's other two exits, which the
measurement found arriving exactly as badly: `UnknownSchemaVersionError` and pydantic's
`ValidationError`, both `exit 5` / bare `500` / unenveloped before this issue.
"""


@pytest.mark.parametrize("described", sorted(_DAMAGE))
def test_a_stored_prediction_that_cannot_be_parsed_is_refused_by_name_on_every_face(
    tmp_path: Path, runtime_dir: Path, described: str
) -> None:
    """`V2-P4-096`: the fourth instance of `V2-P4-080`'s class, on Story S32's own store.

    The contrast is the diagnosis. A document *edited* on disk was already handled perfectly --
    `get` re-derives the address and refuses a document that no longer matches its filename, at
    `exit 1` with the recomputed address in the sentence. A document that cannot be **parsed**
    never reached that check, because it fails one line earlier inside `read_versioned`: the
    store checked the address and not the parse.

    Driven from all three faces because that is where the defect was visible. Nothing in
    `model_view` or the routes changed to close it -- `FilePredictionStore.get` converts the
    faults `read_versioned` names, and the `PredictionStoreError` arm every face already had
    does the rest.
    """
    root = tmp_path / "damaged"
    root.mkdir()
    shutil.copytree(runtime_dir / "panel", root / "panel")
    record_id = _register_then(root, _DAMAGE[described])

    code, out = _cli_prediction(root, record_id)
    assert code == 1, out
    assert record_id in out
    assert "could not be read back as a prediction" in out
    assert "unhandled" not in out

    with TestClient(create_app(runtime_dir=root), raise_server_exceptions=False) as client:
        response = client.get(f"/api/v1/predictions/{record_id}")
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["reason"] == "not_held"
        assert "could not be read back as a prediction" in response.json()["detail"]["message"]

    with pytest.raises(PredictionStoreError, match="could not be read back as a prediction"):
        OpenAlphaSDK(runtime_dir=root).held_prediction(record_id)


def test_a_prediction_that_cannot_be_parsed_refuses_the_run_that_would_re_register_it(
    tmp_path: Path, runtime_dir: Path
) -> None:
    """The store's **other** reader, which is the reason the fix is not at the call site.

    `put` reads through `get` twice -- once for `supersedes`, and once when a document is already
    filed under the address a write derived -- so an unreadable document does not only refuse
    `openalpha model prediction`, it refuses the daily run that would produce the same prediction
    again. Measured before the fix: `JSONDecodeError` straight out of `put`, which `run_daily`
    calls outside every arm that could name it.

    A named refusal on this path rather than an overwrite: `put` writes only where nothing is
    held, and a document that cannot be parsed is still a document that is held.

    Driven through the SDK rather than the command line because the address has to be the *same*
    one: `predicted_at` reaches it, so a second run under a different clock derives a different
    address and never meets the damaged document at all. That is what the first draft of this
    test did, and it passed while proving nothing.
    """
    root = tmp_path / "reregister"
    root.mkdir()
    shutil.copytree(runtime_dir / "panel", root / "panel")
    record_id = _register_then(root, _DAMAGE["a write that stopped half way"])

    unreadable = "could not be read back as a prediction"
    with pytest.raises(PredictionStoreError, match=unreadable) as raised:
        _sdk_daily(root, lambda: FORWARD_CLOCK)

    assert record_id in str(raised.value)


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


# --- 7. what the model chain's product acceptance measured (V2-P4-097..100) ----------------------

TREES: Final[dict[str, Any]] = {
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
"""The same one column through the other shipped family. See `test_the_other_shipped_family_...`,
which measured that these four hyperparameters fit on this corpus."""


def _fold_column(terminal: str) -> list[str]:
    """The last column of each fold row in a terminal evaluation, which is the fit."""
    return [
        line.rsplit("  ", 1)[-1].strip()
        for line in terminal.splitlines()
        if line.startswith("2026-01-")
    ]


def test_the_terminal_evaluation_prints_the_coefficient_its_headline_cannot_move(
    runtime_dir: Path,
) -> None:
    """`V2-P4-097`: the one number that responds to the fit was the one the default face dropped.

    Measured on this corpus: the two folds are fitted on 15 and 30 examples and learn `-0.9107`
    and `-0.9464`, and every column the terminal printed -- block, coverage, `mean_rank_ic`,
    `rank_icir`, reach -- came from `evaluation_rows`, which omitted `folds[].parameters`
    entirely. `V2-P4-014` measured that a leak shows in the coefficient, so the default face was
    the one place it could not be seen.
    """
    _code, body = _cli(runtime_dir, "evaluate", BASELINE)
    folds = json.loads(body)["folds"]
    assert [item["value"] for fold in folds for item in fold["parameters"]] == [
        pytest.approx(-0.9107142857142855),
        pytest.approx(-0.9464285714285712),
    ], "the corpus is expected to fit two folds to two different coefficients"

    _terminal_code, terminal = _cli(runtime_dir, "evaluate", {**BASELINE, "json_output": False})
    assert _fold_column(terminal) == [
        "reversal_1d/v1@raw=-0.9107",
        "reversal_1d/v1@raw=-0.9464",
    ], terminal
    header = next(line for line in terminal.splitlines() if line.startswith("block"))
    assert header.endswith("fit"), header


def test_an_ensemble_too_wide_to_print_is_counted_rather_than_truncated(
    runtime_dir: Path,
) -> None:
    """The other shipped family encodes its whole ensemble in `parameters`, and a terminal cannot.

    `alpha_tree._encode` emits two entries per node under keys like `t000.n000.edge`, which on
    this corpus is 40 and 56 of them. Printing a column-keyed coefficient table and printing a
    tree are not one rendering, so the rule is the artifact's own rather than the family's: the
    entries whose key is a declared feature column are printed by name and everything else is
    counted. A branch on `family` here would be the `if`/`elif` `MODEL_FAMILIES` exists to avoid.
    """
    _code, body = _cli(runtime_dir, "evaluate", TREES)
    assert [len(fold["parameters"]) for fold in json.loads(body)["folds"]] == [40, 56]

    _terminal_code, terminal = _cli(runtime_dir, "evaluate", {**TREES, "json_output": False})
    assert _fold_column(terminal) == [
        "40 parameter(s), none on a declared column",
        "56 parameter(s), none on a declared column",
    ], terminal


def test_a_rank_evaluation_says_its_statistics_see_only_the_ordering_its_fit_induces(
    runtime_dir: Path,
) -> None:
    """`V2-P4-097`'s second half: the invariance is a fact about *this* answer, not a footnote.

    Sweeping `--embargo-sessions` moves the training set and leaves `mean_rank_ic` identical to
    twelve decimals, because `CrossSectionalRankModel` scores `c.rank(x)` and a rank correlation
    is invariant to every positive monotone transform of the score. Over **one** declared column
    that leaves the coefficient's sign as the only thing the headline can see.

    It is declared as a boundary *and* said by the run, and the two are different statements: the
    registry entry is true of the family, and this key is true of this run's own column count --
    which is why the count is rendered into the sentence rather than described in it.
    """
    _code, body = _cli(runtime_dir, "evaluate", BASELINE)
    invariances = json.loads(body)["invariances"]

    assert [item["code"] for item in invariances] == [
        "a_rank_statistic_sees_only_the_ordering_this_fit_induces"
    ]
    detail = str(invariances[0]["detail"])
    assert "over this run's 1 declared column(s)" in detail
    assert "the sign of its coefficient and nothing else" in detail

    _terminal_code, terminal = _cli(runtime_dir, "evaluate", {**BASELINE, "json_output": False})
    assert "invariance a_rank_statistic_sees_only_the_ordering_this_fit_induces" in terminal
    assert "over this run's 1 declared column(s)" in terminal


def test_the_other_family_claims_no_such_invariance(runtime_dir: Path) -> None:
    """The falsifier, and the reason this key is a list rather than a sentence on every answer.

    A boosted ensemble over one column is a step function of it, not a monotone transform of it,
    so the statistic really does see the fit: measured on this corpus the tree's `mean_rank_ic` is
    `0.9274` where the rank baseline's is `0.9107` on the same fold, out of the same column.
    """
    _code, body = _cli(runtime_dir, "evaluate", TREES)
    answer = json.loads(body)

    assert answer["invariances"] == []
    assert answer["folds"][0]["mean_rank_ic"] == pytest.approx(0.9274260335029674)

    _rank_code, rank_body = _cli(runtime_dir, "evaluate", BASELINE)
    assert json.loads(rank_body)["folds"][0]["mean_rank_ic"] == pytest.approx(0.9107142857142855)


def test_both_model_terminal_faces_say_how_many_limitations_the_body_carries(
    runtime_dir: Path, daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-099`'s fourth account: `evaluate`'s terminal carried none of the named boundaries.

    `daily-run`'s terminal prints the standing pair and `evaluate`'s printed nothing at all, and
    the asymmetry was not a decision. What a terminal may not do is print fifteen paragraphs, so
    both faces name the count and the flag that hands them over -- falsifiable, because the count
    is the registry's own length, where "see the documentation" would not be.
    """
    expected = (
        f"{len(KNOWN_MODEL_VIEW_LIMITATIONS)} named boundary(ies) on what this answer means; "
        "read them with --json"
    )
    _code, terminal = _cli(runtime_dir, "evaluate", {**BASELINE, "json_output": False})
    assert expected in terminal

    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    _daily_code, daily = _cli(daily_runtime_dir, "daily-run", {**DAILY, "json_output": False})
    assert expected in daily


CUSTODY_CLOCKS: Final[tuple[datetime, ...]] = tuple(
    datetime(2026, 1, 16, hour, 0, tzinfo=UTC) for hour in (10, 11, 12, 13, 14)
)
"""Five custody instants an hour apart, all before `OUTCOME_KNOWN_AT`, so all five stand forward.

Five records rather than two because two can agree on an order by accident. Measured on this
corpus, the five addresses sort into the order `[3rd, 1st, 4th, 5th, 2nd]` -- the record created
third sorts first and the one created second sorts last -- which is the shape `V2-P4-098` found on
a real register and is asserted below rather than assumed.
"""


def _register(root: Path, clocks: Sequence[datetime]) -> tuple[str, ...]:
    """One daily run per clock, in order, and the addresses they were filed under.

    Every run declares the identical day; they get different addresses because `predicted_at`
    reaches the content address and each run is stamped from its own clock. That is
    `V2-P4-100`'s duplicate seen from the other side, and it is what makes five records out of
    one command.
    """
    return tuple(
        OpenAlphaSDK(runtime_dir=root, clock=lambda clock=clock: clock)  # type: ignore[misc]
        .run_daily_model(**_sdk_arguments(DAILY))
        .record.record_id
        for clock in clocks
    )


def test_the_register_lists_what_it_holds_in_custody_order_and_not_by_content_hash(
    daily_runtime_dir: Path,
) -> None:
    """`V2-P4-098`'s first account: the register could not answer what it exists to answer.

    The user's stated need is to show later that they committed first, and the listing was
    `sorted(list_ids())` -- a sort over content digests, which is *uncorrelated* with time and
    therefore actively misleading rather than merely unhelpful. Measured on these five records,
    the one created **last** sorts first and the one created **first** sorts last: the maximum
    inversion five records admit. (It was the third that sorted first until `V2-P5-062` made
    `_pearson` exactly rounded, which moved the predicted values and so the digests over them.
    The two interpreters in this repository's matrix disagreed about these five ids before that
    change and agree on them after it, which is the sharper half of what that issue fixed: a
    content address is not supposed to be a property of the interpreter that computed it.)

    The order is the **custody stamp** and not `predicted_at`, and the choice is the same one
    `standing` rests on: `predicted_at` is whatever the caller passed to `predict` and this
    repository cannot check it, while `recorded_at` is the one instant a caller does not set.
    Ordering a register by a field its subjects choose would be the register agreeing to be told.
    """
    made = _register(daily_runtime_dir, CUSTODY_CLOCKS)

    assert len(set(made)) == len(CUSTODY_CLOCKS), "five clocks must file five records"
    by_address = sorted(made)
    assert by_address != list(made), "this corpus is expected to misorder under a digest sort"
    assert by_address[0] == made[4], "the record created last is expected to sort first"
    assert by_address[-1] == made[0], "the record created first is expected to sort last"

    held = OpenAlphaSDK(runtime_dir=daily_runtime_dir).held_predictions()
    assert tuple(record.record_id for record in held) == made

    _code, listed = _cli_predictions(daily_runtime_dir)
    assert json.loads(listed)["record_ids"] == list(made)

    with TestClient(create_app(runtime_dir=daily_runtime_dir)) as client:
        response = client.get("/api/v1/predictions")
    assert response.status_code == 200
    assert response.json()["record_ids"] == list(made)


def test_a_listed_prediction_says_what_it_is_without_being_opened(
    daily_runtime_dir: Path,
) -> None:
    """The other half: a listing of bare addresses has no date, no model and no standing on it.

    A register whose index answers only "these twenty-four digests exist" makes every question a
    user actually has -- which of these was about last Tuesday, which are `forward`, which came
    out of the declaration I was running in March -- a loop of `model prediction` calls. So the
    row carries what a reader needs to choose *which* body to open, and the standing travels with
    both of `PREDICTION_STANDING_MEANINGS`' sentences here exactly as it does on a body, because
    a `forward` in a table reads as an attestation just as fast as a `forward` in a document.
    """
    made = _register(daily_runtime_dir, CUSTODY_CLOCKS[:2])

    _code, listed = _cli_predictions(daily_runtime_dir)
    rows = json.loads(listed)["predictions"]

    assert [row["record_id"] for row in rows] == list(made)
    assert [row["recorded_at"] for row in rows] == [
        clock.isoformat() for clock in CUSTODY_CLOCKS[:2]
    ]
    assert {row["standing"] for row in rows} == {"forward"}
    assert {row["as_of"] for row in rows} == {_build_instant(PREDICT_SESSION).isoformat()}
    assert {row["outcome_known_at"] for row in rows} == {OUTCOME_KNOWN_AT}
    assert {row["model_name"] for row in rows} == {"reversal-rank"}
    assert {row["horizon"] for row in rows} == {HORIZON}
    assert {(row["scored_count"], row["offered_count"]) for row in rows} == {
        (DAILY_SCORED, DAILY_OFFERED)
    }
    for row in rows:
        assert "held these bytes before the instant" in str(row["standing_proves"])
        assert "nothing here defends against whoever owns the disk" in str(
            row["standing_does_not_prove"]
        )

    _terminal_code, terminal = _cli_predictions(daily_runtime_dir, json_output=False)
    assert terminal.splitlines()[0].split() == [
        "recorded_at",
        "as_of",
        "standing",
        "horizon",
        "scored",
        "model",
        "record_id",
    ]
    assert [line.split()[-1] for line in terminal.splitlines()[1:3]] == list(made)
    # Once per *standing*, not once per row: two rows of one standing get one legend, which is
    # the one place `PREDICTION_STANDING_MEANINGS`' "the sentences travel in the body" has to
    # bend, because two paragraphs against every line of a long table is a table nobody reads.
    assert terminal.count("forward means") == 1
    assert terminal.count("and does not prove") == 1


def test_the_register_orders_by_the_stamp_the_caller_does_not_set(
    daily_runtime_dir: Path,
) -> None:
    """`recorded_at` and not `predicted_at`, driven where the two disagree.

    On every ordinary run the two instants are equal -- one clock stamps both, which is
    `no_face_here_can_produce_an_unwitnessed_record_because_one_clock_stamps_both_instants` -- so
    a register sorted on either would look identical and a mutation between them would survive.
    Here one record is written through a clock that advances between the two readings: it claims
    the **earliest** production instant of the four and reaches custody **last**.

    Sorted on `predicted_at` it would lead the register; sorted on custody it trails it. Custody
    is the honest key, and for the reason `standing` rests on: `predicted_at` is whatever the
    caller passed to `predict` and nothing here can check it, so a register ordered on it is a
    register that agrees to be told what order it is in.
    """
    settled = _register(daily_runtime_dir, CUSTODY_CLOCKS[1:4])

    reading = {"count": 0}

    def clock() -> datetime:
        """The earliest production instant of the four, then a custody stamp after the deadline.

        Armed after the container is built rather than counted from zero, `test_a_batch_this_
        store_received_late_is_unwitnessed_and_says_which_half_failed`'s reason: `build_storage`
        reads the clock itself to recover interrupted batches.
        """
        reading["count"] += 1
        return CUSTODY_CLOCKS[0] if reading["count"] == 1 else LATE_CLOCK

    sdk = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=clock)
    reading["count"] = 0
    slow = sdk.run_daily_model(**_sdk_arguments(DAILY)).record

    assert slow.standing == "unwitnessed"
    assert slow.batch.predicted_at == CUSTODY_CLOCKS[0]
    assert slow.recorded_at == LATE_CLOCK

    held = OpenAlphaSDK(runtime_dir=daily_runtime_dir).held_predictions()
    assert [record.record_id for record in held] == [*settled, slow.record_id]
    assert sorted(held, key=lambda record: record.batch.predicted_at)[0].record_id == (
        slow.record_id
    ), "the slow record claims the earliest production instant of the four"


def test_a_stored_prediction_resolves_back_to_the_declaration_it_was_fitted_under(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-098`'s second account, and the finding's own premise is half wrong.

    *"A record read a year later says 'reversal-rank predicted these sixty numbers' and cannot say
    what reversal-rank was."* The **document** can: `PredictionRecord` carries the whole
    `AlphaModelArtifact` by value, which carries the declaration, the resolved `feature_version`,
    the feature columns, the code commit, the seed, the hyperparameters, the training cutoff, the
    example count and the fitted coefficients. It was `prediction_view` that threw all of it away
    and rendered `model_name` and `artifact_id`, so no face could resolve `mdl_...` -- and none
    needs to, because nothing has to be looked up.

    What is genuinely absent is the training **range** and the instant the fit read the panel;
    see `a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel`.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    _run_code, run_out = _cli(daily_runtime_dir, "daily-run", DAILY)
    answer = json.loads(run_out)
    record_id = answer["prediction"]["record_id"]

    _code, held_out = _cli_prediction(daily_runtime_dir, record_id)
    model = json.loads(held_out)["model"]

    assert model == {
        "artifact_id": answer["prediction"]["artifact_id"],
        "code_commit": COMMIT,
        "family": "cross_sectional_rank",
        "feature_ids": ["reversal_1d/v1@raw"],
        "feature_version": "feat_3b3122f39322527699a2cabc",
        "hyperparameters": [],
        "name": "reversal-rank",
        "parameters": [
            {"feature_id": "reversal_1d/v1@raw", "value": pytest.approx(-0.9438775510204079)}
        ],
        "seed": 7,
        "training_cutoff": "2026-01-16T07:00:00+00:00",
        "training_example_count": 54,
    }

    with TestClient(create_app(runtime_dir=daily_runtime_dir)) as client:
        response = client.get(f"/api/v1/predictions/{record_id}")
    assert response.status_code == 200
    assert response.json()["model"] == json.loads(held_out)["model"]


EARLY_DAILY: Final[dict[str, Any]] = {
    **DAILY,
    "start": SESSIONS[1],
    "end": SESSIONS[2],
    "predict_at": _build_instant(SESSIONS[3]),
}
"""A daily run about 2026-01-08, whose outcome closes long before this corpus's reading `as_of`."""

EARLY_CLOCK: Final[datetime] = datetime(2026, 1, 8, 10, 0, tzinfo=UTC)
"""Before that outcome closed, so the record stands `forward` while the panel was read after."""


def test_a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel(
    daily_runtime_dir: Path,
) -> None:
    """`V2-P4-098`'s sharpest account, reproduced on the fixture: honesty stops at the record.

    Measured here -- the outcome of a 2026-01-08 prediction becomes knowable at 2026-01-12T07:00Z,
    this run's clock stamps both instants at 2026-01-08T10:00Z so the record stands `forward`, and
    every panel read behind the fit was made at 2026-01-17T04:00Z, **five days after** the answer
    printed. The standing is correct about what it claims and says nothing whatever about that.

    The reading instant is deliberately **not** added to the record, and
    `a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel` carries the argument.
    What is added is at the faces: the terminal rendering of a daily run prints the reading
    instant beside the deadline, because that is the one face holding both numbers at once.
    """
    sdk = OpenAlphaSDK(runtime_dir=daily_runtime_dir, clock=lambda: EARLY_CLOCK)
    result = sdk.run_daily_model(**_sdk_arguments(EARLY_DAILY))
    view = sdk.daily_view(result)
    training = view["training"]
    assert isinstance(training, dict)

    assert result.record.standing == "forward"
    assert result.record.outcome_known_at.isoformat() == "2026-01-12T07:00:00+00:00"
    assert datetime.fromisoformat(str(training["as_of"])) > result.record.outcome_known_at

    _code, held_out = _cli_prediction(daily_runtime_dir, result.record.record_id)
    assert READ_AT.isoformat() not in held_out, (
        "the stored document is expected to carry no field naming the instant its fit read"
    )

    codes = {item["code"] for item in json.loads(held_out)["limitations"]}
    assert "a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel" in codes


def test_the_daily_terminal_prints_the_instant_the_panel_was_read_beside_the_deadline(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half of `V2-P4-098`'s third account that a face *can* answer.

    The stored record cannot carry the reading instant, and the argument for not putting it there
    is on the limitation. But a daily run's own terminal answer is holding the deadline and the
    reading instant at the same moment, and it printed only one of them -- so a reader watching a
    scheduled job go past could not see the contradiction even where it was visible.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    code, out = _cli(daily_runtime_dir, "daily-run", {**DAILY, "json_output": False})

    assert code == 0, out
    labels = [line.split("  ")[0] for line in out.splitlines()]
    assert labels.index("panel read at") == labels.index("outcome_known_at") + 1
    assert f"panel read at       {READ_AT.isoformat()}" in out


def test_a_re_run_of_one_day_through_the_command_line_files_a_second_record(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-100`'s second account, and it is a defect in a claim rather than in the store.

    `model daily-run --help` said re-running an identical day was `unchanged` on both stores. It
    cannot be through this face: `predicted_at` is the process clock's reading, it reaches
    `record_id` through the batch, and a scheduled job retrying an hour after a transient failure
    therefore leaves two records and two manifests for one prediction day.

    The clock is monkeypatched to a value this test moves *between* the two invocations, which is
    the production shape rather than a convenience: within one run both instants come from one
    reading, and between two runs the wall clock has moved. `test_re_running_an_identical_day_
    registers_nothing_new_on_either_store` reaches `unchanged` from the SDK with a *fixed* clock,
    and the contrast is the finding.
    """
    now = {"value": FORWARD_CLOCK}
    monkeypatch.setattr(cli, "_panel_clock", lambda: now["value"])

    first_code, first_out = _cli(daily_runtime_dir, "daily-run", DAILY)
    now["value"] = FORWARD_CLOCK + timedelta(hours=1)
    second_code, second_out = _cli(daily_runtime_dir, "daily-run", DAILY)

    assert (first_code, second_code) == (0, 0), (first_out, second_out)
    first, second = json.loads(first_out), json.loads(second_out)

    assert first["write_outcome"] == second["write_outcome"] == "created"
    assert first["prediction"]["record_id"] != second["prediction"]["record_id"]
    assert first["run_id"] != second["run_id"]
    assert first["prediction"]["as_of"] == second["prediction"]["as_of"]
    assert first["prediction"]["artifact_id"] == second["prediction"]["artifact_id"]

    _listed_code, listed = _cli_predictions(daily_runtime_dir)
    assert json.loads(listed)["record_ids"] == [
        first["prediction"]["record_id"],
        second["prediction"]["record_id"],
    ], "two records for one prediction day, oldest custody first"

    rendered = rendered_help(*DAILY_HELP)
    assert "every invocation of this command files a new record" in re.sub(r"\s+", " ", rendered)
    assert (
        "a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_address"
        in re.sub(r"\s+", "", rendered)
    ), "a limitation code is one token and rich breaks it across lines; join rather than collapse"


DAILY_HELP: Final[tuple[str, ...]] = ("model", "daily-run")


def test_no_shipped_face_stamps_the_two_instants_from_two_clock_readings(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-099`'s third account: a third of the standing vocabulary is unreachable in practice.

    `unwitnessed` describes a batch stamped in time that reached the store late. Every shipped
    face hands `predicted_at` and the store's clock the *same* callable, so the two instants come
    out equal and the window the standing describes is the duration of one `put`. Measured here
    on the command line with a fixed clock, which is what makes the equality visible rather than
    merely likely: a real clock would differ in the microseconds and prove nothing about which
    reading each instant came from.

    Not repaired. `V2-P4-017` argues the standing may not be collapsed into either neighbour, and
    a contract that could not express a slow disk would be wrong the first time this store lives
    somewhere a write can block.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    _code, out = _cli(daily_runtime_dir, "daily-run", DAILY)
    prediction = json.loads(out)["prediction"]

    assert prediction["predicted_at"] == prediction["recorded_at"] == FORWARD_CLOCK.isoformat()
    assert prediction["standing"] == "forward"

    codes = {item["code"] for item in json.loads(out)["limitations"]}
    assert (
        "no_face_here_can_produce_an_unwitnessed_record_because_one_clock_stamps_both_instants"
        in codes
    )


def test_no_shipped_face_can_name_the_record_a_backfill_supersedes(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-099`'s second account, brought up into the registry a caller actually reads.

    A backfill is rendered with *"a backfill naming no earlier record corrects nothing"* and no
    face carries a flag, a field or a parameter that could name one. `domain/prediction_record.py`
    has known that since `V2-P4-093`; what it did not have was a way to reach a body, and this
    registry is what a caller pastes into a report.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: LATE_CLOCK)
    _code, out = _cli(daily_runtime_dir, "daily-run", DAILY)
    answer = json.loads(out)

    assert answer["prediction"]["standing"] == "backfill"
    assert answer["prediction"]["supersedes"] is None
    assert "corrects nothing" in answer["prediction"]["standing_does_not_prove"]

    collapsed = re.sub(r"\s+", " ", rendered_help(*DAILY_HELP))
    assert "--supersedes" not in collapsed
    codes = {item["code"] for item in answer["limitations"]}
    assert "the_supersedes_edge_is_unreachable_from_every_face_this_module_serves" in codes


def test_a_factor_built_over_fewer_subjects_is_still_offered_the_whole_registry(
    daily_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P4-100`'s first account, at the scale this corpus can carry it.

    `feature_matrix.py` says *"the rows are the universe"*: the cross section is the stored
    registry's listed set, not the subjects a factor build named. This corpus builds
    `reversal_1d/v1` over seven of its eight listed securities, and the eighth is offered to the
    model on every prediction day and abstained on by name -- one name out of eight here, and
    32,742 security-days out of 33,090 on the real panel that acceptance ran, which is why no
    meaningful `--min-scored-ratio` was reachable there.
    """
    monkeypatch.setattr(cli, "_panel_clock", lambda: FORWARD_CLOCK)
    _code, out = _cli(daily_runtime_dir, "daily-run", DAILY)
    answer = json.loads(out)

    assert len(SUBJECTS) == len(SECURITIES) - 1
    assert answer["measurement"]["offered_count"] == len(SECURITIES)
    assert answer["measurement"]["scored_count"] == len(SUBJECTS)
    assert [
        row["ts_code"] for row in answer["prediction"]["predictions"] if row["score"] is None
    ] == [UNVALUED]

    codes = {item["code"] for item in answer["limitations"]}
    assert "a_subject_narrowed_factor_build_does_not_narrow_the_market_this_face_labels" in codes


def test_the_horizon_wall_names_the_horizon_and_the_prediction_day_that_reached_it(
    runtime_dir: Path,
) -> None:
    """`V2-P4-099`'s first account: one sentence for every horizon, naming neither flag.

    Measured on this corpus, `--horizon 2d`, `3d`, `5d` and `8d` all produce the **identical**
    refusal about 2026-01-19's 16:30 publication, because the first unpublished session a window
    reaches is the same one whatever the horizon; `1d` clears it. So the wall is a joint function
    of the declared horizon and the last prediction day in the range, and the refusal named
    neither -- against the same face's schedule refusal, which is exemplary: *"this panel's 7
    prediction day(s) cannot carry the declared schedule of 2 fold(s) of 2 test day(s)"*.

    The remedy names **both** flags because either one moves the wall, which is the whole finding.

    The two sentences now differ in the half that matters and agree in the half that is a fact
    about the panel: both refusals are still about 2026-01-19, because that is the first
    unpublished session either window touches -- and they name *different prediction days*,
    because a `5d` window already overshoots from the range's first day while a `2d` one only
    overshoots from its last. Measured, not chosen: a reader of the `5d` sentence learns that
    shortening the range will not help them, which the old sentence could not have told them.
    """
    refusals = {
        horizon: _cli(runtime_dir, "evaluate", {**BASELINE, "horizon": horizon})
        for horizon in ("2d", "5d")
    }
    assert {code for code, _out in refusals.values()} == {1}
    assert refusals["2d"][1] != refusals["5d"][1], (
        "two horizons must no longer produce one identical sentence"
    )

    for _horizon, (_code, out) in refusals.items():
        assert "daily cannot be read for 2026-01-19" in out
        assert "that session had not published yet" in out
        assert "a shorter --horizon, or a --start/--end range that stops earlier" in out
        # `V2-P4-100`'s fourth account, pinned here: lengthening the horizon on a mid-year panel
        # does *not* reach `V2-P4-088`'s calendar-horizon refusal. The calendar is built to the
        # end of its year and reaches every window this range can ask for, so the price plane
        # answers first. `tests/integration/test_year_end_daily_run.py` is what reaches the other.
        assert "cannot be built on the SZSE calendar" not in out

    assert (
        "This run reached it because the 2d outcome window for the prediction day 2026-01-14 "
        "opens on 2026-01-15 and exits on 2026-01-19" in refusals["2d"][1]
    ), refusals["2d"][1]
    assert (
        "This run reached it because the 5d outcome window for the prediction day 2026-01-09 "
        "opens on 2026-01-12 and exits on 2026-01-19" in refusals["5d"][1]
    ), refusals["5d"][1]
    # The remedy is conditioned on the reason rather than asserted: every read behind a label
    # window comes through the same `except`, and "shorten your horizon" cannot repair a
    # partition whose file is gone.
    assert "Where that session has simply not published yet" in refusals["2d"][1]
