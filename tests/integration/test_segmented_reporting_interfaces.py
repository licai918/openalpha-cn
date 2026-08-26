"""`V2-P5-009` on the product surfaces: `openalpha validation segmented` and the SDK.

The rule four acceptances taught this repository: a policy only reachable by importing modules
by hand is not delivered. So every claim below starts at an `OpenAlphaSDK` or a `CliRunner` over
a real runtime directory -- real migrations, a real `ResearchEngine` cycle, real
`OutcomeValidator` results in the real `SQLiteValidationStore`, and the segmented report read
back out of that store by signal ID.

## The corpus, and the one number the whole row turns on

Three subjects give three signals; each is validated over three windows, so the store holds nine
`ValidationResult` rows under three `signal_id`s, each signal on its own three windows. A
fourth subject is the equal-weight baseline and is validated over **all nine of those windows**,
which is what lets the benchmark pair -- `_paired_difference` matches on the multiset of
observation windows and refuses anything that is not a one-to-one correspondence.

The plan cuts those three signals two ways -- `industry` into `banks` and `tech`, and
`market_regime` into `bull` and `bear`. **Two axes over three signals is four buckets, not
two**, and with the baseline and its paired difference the family holds six hypotheses. That
ratio is the row: an implementation reporting each axis as its own family would produce two
corrections of two, and `test_the_cli_prints_one_family_across_both_axes_and_not_one_per_axis`
counts six in one.

`--family-size 4` is therefore refused and `--family-size 6` is accepted, and the refusal names
both numbers. That pair is the second question this file asks, and an implementation that
recovered the family size by counting axes would answer the first one to both.

## What is deliberately *not* derived here

Not one label in the plan is computed. `domain/daily_prices.py` carries `total_mv`, `circ_mv`
and `turnover_rate` and none of it is reachable from a `ValidationResult`, which names a
`signal_id` and no security at all. The plan file below is what a caller would check into a
repository beside the study, and `test_a_signal_with_no_label_on_a_declared_axis_is_refused_by_
name` is what happens when it is incomplete -- a refusal, never an `unknown` bucket.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from typer.testing import CliRunner

from openalpha_cn.backtest.segmented_reporting import (
    MARKET_REGIME_AXIS,
    SegmentationPlan,
    SegmentedReportingError,
)
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.sdk import OpenAlphaSDK

BENCHMARK: Final[float] = 0.0625
COST: Final[float] = 0.0078125

SUBJECTS: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ", "000004.SZ")
BASELINE_SUBJECT: Final[str] = "000005.SZ"

PRICES: Final[dict[str, tuple[tuple[float, float], ...]]] = {
    "000001.SZ": ((100.0, 125.0), (100.0, 112.5), (100.0, 137.5)),
    "000002.SZ": ((100.0, 112.5), (100.0, 100.0), (100.0, 118.75)),
    "000004.SZ": ((100.0, 125.0), (100.0, 118.75), (100.0, 106.25)),
    BASELINE_SUBJECT: ((100.0, 106.25), (100.0, 103.125), (100.0, 109.375)),
}
"""Every quotient is exact in binary, so every realised return below is a dyadic rational."""

INDEPENDENT: Final[str] = "independent-or-positively-dependent"

SEGMENT_BUCKETS: Final[int] = 4
"""`industry` into two and `market_regime` into two -- two axes, four buckets."""

FAMILY: Final[int] = 6
"""The four buckets plus the baseline row plus its paired difference."""


def _evidence(subject: str, frozen_now: datetime) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now,
            ingested_time=frozen_now,
            revision_time=frozen_now,
        ),
        source_id="segmented.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up for the segmented-reporting corpus.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.0, "pct_change": 10.0, "board_count": 1},
            "quality_flags": [],
        },
    )


def _run(sdk: OpenAlphaSDK, subject: str, frozen_now: datetime) -> ResearchRunResult:
    research = sdk.run_research(
        ResearchRunRequest(
            run_id=f"segmented-{subject}",
            mode="backtest",
            subject=subject,
            as_of=frozen_now,
            evidence=(_evidence(subject, frozen_now),),
            code_commit="0123456789abcdef",
            config_digest="c" * 64,
            random_seed=7,
        )
    )
    assert research.decision.final_action == "watch", (
        "the corpus needs a held position; a flat decision realises 0.0 and every bucket "
        "would carry the same net return"
    )
    return research


def _windows(index: int) -> tuple[int, ...]:
    """The three window offsets signal `index` was validated over.

    Each signal gets its **own** three windows -- `0,1,2`, then `10,11,12`, then `20,21,22` --
    rather than all three sharing one set. Two reasons, and the second is the one that bit:
    three signals researched at different times is the realistic shape, and a `ValidationResult`
    is content-addressed, so validating the *same* window at the same prices twice produces the
    same `validation_id` and the store keeps one row. A baseline built by repeating an
    observation therefore silently holds three rows where the fixture meant nine.
    """
    return tuple(index * 10 + offset for offset in range(3))


def _seed(tmp_path: Path, frozen_now: datetime) -> tuple[OpenAlphaSDK, dict[str, str]]:
    """Four signals written through the shipped faces: nine strategy rows and nine baseline rows.

    The baseline is validated over **every one of the nine windows the strategy used**, which is
    what makes the comparison pairable: `_paired_difference` matches on the multiset of
    `(observation_start, observation_end)` and refuses anything else.
    """
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "runtime")
    signals: dict[str, str] = {}

    for index, subject in enumerate(SUBJECTS):
        research = _run(sdk, subject, frozen_now)
        signals[subject] = research.signal.signal_id
        for offset, (start_price, end_price) in zip(_windows(index), PRICES[subject], strict=True):
            _validate(sdk, research, frozen_now, offset, start_price, end_price)

    baseline = _run(sdk, BASELINE_SUBJECT, frozen_now)
    signals[BASELINE_SUBJECT] = baseline.signal.signal_id
    for index in range(len(SUBJECTS)):
        for position, offset in enumerate(_windows(index)):
            start_price, end_price = PRICES[BASELINE_SUBJECT][position]
            _validate(sdk, baseline, frozen_now, offset, start_price, end_price)

    assert len(set(signals.values())) == 4
    assert len(sdk.list_validations_by_signal(signals[BASELINE_SUBJECT])) == 9, (
        "the baseline must cover every window the strategy did, or it cannot be paired"
    )
    return sdk, signals


def _validate(
    sdk: OpenAlphaSDK,
    research: ResearchRunResult,
    frozen_now: datetime,
    offset: int,
    start_price: float,
    end_price: float,
) -> None:
    sdk.validate_outcome(
        research=research,
        observation=OutcomeObservation(
            observation_start=frozen_now + timedelta(days=offset),
            observation_end=frozen_now + timedelta(days=offset + 5),
            start_price=start_price,
            end_price=end_price,
            benchmark_return=BENCHMARK,
            transaction_cost=COST,
            data_quality_notes=("Synthetic outcome.",),
        ),
    )


def _plan_payload(signals: dict[str, str], *, complete: bool = True) -> dict[str, Any]:
    """The segmentation plan a caller would check in beside the study.

    `complete=False` drops the last strategy signal from the `industry` axis, which is the
    incomplete-plan arm: a signal with no label on a declared axis is neither in a bucket nor
    honestly out of one.
    """
    industry = {
        signals[SUBJECTS[0]]: "banks",
        signals[SUBJECTS[1]]: "banks",
        signals[SUBJECTS[2]]: "tech",
    }
    if not complete:
        industry.pop(signals[SUBJECTS[2]])
    return {
        "axes": [
            {
                "axis_id": "industry",
                "definition": "CSRC level-1 industry on the prediction day",
                "source": "the caller's own mapping, fixed before the run",
                "labels": industry,
            },
            {
                "axis_id": MARKET_REGIME_AXIS,
                "definition": "sign of the CSI300 60-session trend on the prediction day",
                "source": "the caller's own classifier, fixed before the run",
                "labels": {
                    signals[SUBJECTS[0]]: "bull",
                    signals[SUBJECTS[1]]: "bear",
                    signals[SUBJECTS[2]]: "bear",
                },
            },
        ],
        "benchmarks": [
            {
                "benchmark_id": "equal-weight",
                "kind": "equal-weight-baseline",
                "definition": "equal weights over the same universe and the same windows",
                "signal_id": signals[BASELINE_SUBJECT],
            }
        ],
    }


def _plan_file(tmp_path: Path, signals: dict[str, str], *, complete: bool = True) -> Path:
    path = tmp_path / "segments.json"
    path.write_text(json.dumps(_plan_payload(signals, complete=complete)), encoding="utf-8")
    return path


def _cli(*arguments: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(arguments))
    return result.exit_code, result.output


def _segmented(
    tmp_path: Path,
    signals: dict[str, str],
    *,
    family_size: int = FAMILY,
    complete: bool = True,
    extra: tuple[str, ...] = (),
) -> tuple[int, str]:
    plan = _plan_file(tmp_path, signals, complete=complete)
    arguments = ["validation", "segmented"]
    for subject in SUBJECTS:
        arguments.extend(["--signal", signals[subject]])
    arguments.extend(
        [
            "--plan",
            str(plan),
            "--family-size",
            str(family_size),
            "--dependence",
            INDEPENDENT,
            "--false-discovery-rate",
            "0.5",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            *extra,
        ]
    )
    return _cli(*arguments)


# --- the SDK face -----------------------------------------------------------------------------


def test_the_sdk_cuts_stored_outcomes_every_declared_way_and_tests_them_in_one_family(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Two axes over three signals is four buckets, and one family holds all six rows."""
    sdk, signals = _seed(tmp_path, frozen_now)
    plan = SegmentationPlan.model_validate(_plan_payload(signals))

    report = sdk.segmented_outcomes(
        signal_ids=tuple(signals[subject] for subject in SUBJECTS),
        plan=plan,
        declared_family_size=FAMILY,
        false_discovery_rate=0.5,
        dependence=INDEPENDENT,
    )

    assert [axis.axis_id for axis in report.axes] == ["industry", MARKET_REGIME_AXIS]
    assert report.segment_hypotheses == SEGMENT_BUCKETS
    assert report.benchmark_hypotheses == 2
    assert report.statistics.multiple_testing.family_size == FAMILY
    assert report.statistics.multiple_testing.reported_hypotheses == FAMILY


def test_the_sdk_pairs_a_baseline_measured_over_the_same_windows(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Both arms went through the same `OutcomeValidator`, so the difference is a real one."""
    sdk, signals = _seed(tmp_path, frozen_now)

    report = sdk.segmented_outcomes(
        signal_ids=tuple(signals[subject] for subject in SUBJECTS),
        plan=SegmentationPlan.model_validate(_plan_payload(signals)),
        declared_family_size=FAMILY,
        false_discovery_rate=0.5,
        dependence=INDEPENDENT,
    )
    comparison = report.benchmarks[0]

    assert comparison.kind == "equal-weight-baseline"
    assert comparison.comparison_absence_reason is None
    assert comparison.difference is not None
    assert comparison.difference.sample_size == 9
    assert report.statistics.verdict_for(comparison.difference_cohort_id or "") is not None


def test_a_signal_with_no_label_on_a_declared_axis_is_refused_by_name(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Never an `unknown` bucket -- that would invent a segment and publish statistics for it."""
    sdk, signals = _seed(tmp_path, frozen_now)

    with pytest.raises(SegmentedReportingError) as error:
        sdk.segmented_outcomes(
            signal_ids=tuple(signals[subject] for subject in SUBJECTS),
            plan=SegmentationPlan.model_validate(_plan_payload(signals, complete=False)),
            declared_family_size=FAMILY,
            false_discovery_rate=0.5,
            dependence=INDEPENDENT,
        )

    assert signals[SUBJECTS[2]] in str(error.value)
    assert "would invent a segment" in str(error.value)


def test_a_signal_with_nothing_stored_is_refused_rather_than_dropped(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Dropping it would silently shrink the family the caller declared."""
    sdk, signals = _seed(tmp_path, frozen_now)
    payload = _plan_payload(signals)
    payload["axes"][0]["labels"]["sig_absent"] = "banks"
    payload["axes"][1]["labels"]["sig_absent"] = "bull"

    with pytest.raises(SegmentedReportingError) as error:
        sdk.segmented_outcomes(
            signal_ids=(*(signals[subject] for subject in SUBJECTS), "sig_absent"),
            plan=SegmentationPlan.model_validate(payload),
            declared_family_size=FAMILY + 2,
            false_discovery_rate=0.5,
            dependence=INDEPENDENT,
        )

    assert "sig_absent" in str(error.value)


# --- the CLI face, and the family size that decides every verdict ------------------------------


def test_the_cli_prints_one_family_across_both_axes_and_not_one_per_axis(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The row's whole point, on the surface a reader actually looks at."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    code, output = _segmented(tmp_path, signals)

    assert code == PanelExit.ok, output
    assert "ONE family of 6 across 2 axis/axes" in output
    assert "4 segment bucket(s) + 2 benchmark row(s)" in output
    assert "axis industry -- CSRC level-1 industry on the prediction day" in output
    assert f"axis {MARKET_REGIME_AXIS} --" in output
    assert output.count("family:") == 1


def test_the_cli_refuses_a_family_declared_before_the_cut_and_names_both_numbers(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`--family-size 4` counts the buckets and forgets the baseline rows; 6 is the answer."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    code, output = _segmented(tmp_path, signals, family_size=SEGMENT_BUCKETS)

    assert code == PanelExit.bad_request, output
    assert "4" in output
    assert "6" in output
    assert "multiplies the hypotheses tested" in output


def test_the_cli_json_carries_the_capability_of_every_bucket(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """A q-value that could never have been small reads as resolution, not as evidence."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    code, output = _segmented(tmp_path, signals, extra=("--json",))

    assert code == PanelExit.ok, output
    payload = json.loads(output)
    assert payload["declared_family_size"] == FAMILY
    assert payload["family"]["family_size"] == FAMILY
    assert payload["family"]["reported_hypotheses"] == FAMILY
    assert payload["segment_hypotheses"] == SEGMENT_BUCKETS

    for axis in payload["axes"]:
        assert axis["definition"]
        assert axis["source"]
        for segment in axis["segments"]:
            capability = segment["capability"]
            assert capability["most_permissive_critical_value"] > 0
            assert isinstance(capability["can_ever_reject"], bool)
            assert capability["reason"]

    assert payload["regime_coverage"]["declared"] is True
    assert payload["regime_coverage"]["spans_multiple_regimes"] is True
    assert {limitation["code"] for limitation in payload["limitations"]}


def test_the_cli_prints_the_regime_coverage_it_measured_rather_than_the_folds_it_ran(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`V2-P5-009` asks for a multi-regime walk-forward; whether it is one is a measurement."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    code, output = _segmented(tmp_path, signals)

    assert code == PanelExit.ok, output
    assert "regimes: 2 of 2 declared regime(s) reached" in output


def test_a_single_regime_plan_says_so_on_the_command_line(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """One regime is not multiple regimes, however many folds produced it."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    payload = _plan_payload(signals)
    payload["axes"][1]["labels"] = dict.fromkeys(payload["axes"][1]["labels"], "bull")
    plan = tmp_path / "one-regime.json"
    plan.write_text(json.dumps(payload), encoding="utf-8")

    arguments = ["validation", "segmented"]
    for subject in SUBJECTS:
        arguments.extend(["--signal", signals[subject]])
    arguments.extend(
        [
            "--plan",
            str(plan),
            "--family-size",
            "5",
            "--dependence",
            INDEPENDENT,
            "--false-discovery-rate",
            "0.5",
            "--runtime-dir",
            str(tmp_path / "runtime"),
        ]
    )
    code, output = _cli(*arguments)

    assert code == PanelExit.ok, output
    assert "cannot support a claim of regime robustness" in output


def test_a_plan_that_is_not_a_plan_is_refused_before_any_store_is_opened(
    tmp_path: Path, frozen_now: datetime
) -> None:
    _sdk, signals = _seed(tmp_path, frozen_now)
    plan = tmp_path / "broken.json"
    plan.write_text('{"axes": []}', encoding="utf-8")

    code, output = _cli(
        "validation",
        "segmented",
        "--signal",
        signals[SUBJECTS[0]],
        "--plan",
        str(plan),
        "--family-size",
        "2",
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == PanelExit.bad_request, output
    assert "--plan is not a segmentation plan" in output


def test_a_missing_plan_file_is_refused_by_path(tmp_path: Path, frozen_now: datetime) -> None:
    _sdk, signals = _seed(tmp_path, frozen_now)

    code, output = _cli(
        "validation",
        "segmented",
        "--signal",
        signals[SUBJECTS[0]],
        "--plan",
        str(tmp_path / "absent.json"),
        "--family-size",
        "2",
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == PanelExit.bad_request, output
    assert "--plan could not be read" in output


def test_a_dependence_that_is_not_one_of_the_two_is_refused(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """It decides the correction and has no default, `validation statistics`' rule exactly."""
    _sdk, signals = _seed(tmp_path, frozen_now)
    code, output = _segmented(tmp_path, signals, extra=())
    assert code == PanelExit.ok, output

    plan = _plan_file(tmp_path, signals)
    code, output = _cli(
        "validation",
        "segmented",
        "--signal",
        signals[SUBJECTS[0]],
        "--plan",
        str(plan),
        "--family-size",
        "6",
        "--dependence",
        "whatever-rejects-most",
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == PanelExit.bad_request, output
    assert "--dependence must be" in output


def test_the_sdk_and_the_cli_serve_one_segmented_report(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`--json` emits `segmented_report_view`'s bytes and not a second shape that agrees today."""
    sdk, signals = _seed(tmp_path, frozen_now)
    report = sdk.segmented_outcomes(
        signal_ids=tuple(signals[subject] for subject in SUBJECTS),
        plan=SegmentationPlan.model_validate(_plan_payload(signals)),
        declared_family_size=FAMILY,
        false_discovery_rate=0.5,
        dependence=INDEPENDENT,
    )
    code, output = _segmented(tmp_path, signals, extra=("--json",))

    assert code == PanelExit.ok, output
    assert json.loads(output) == json.loads(
        json.dumps(sdk.segmented_report_view(report), ensure_ascii=False, sort_keys=True)
    )
