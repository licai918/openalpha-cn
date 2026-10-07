"""`V2-P5-007`/`V2-P5-008` on the product surfaces: `openalpha validation statistics` and the SDK.

The rule this repository learned four separate acceptances' worth of times: a policy that is only
reachable by importing modules by hand is not delivered. So every claim below starts at an
`OpenAlphaSDK` or a `CliRunner` over a real runtime directory -- real migrations, a real
`ResearchEngine` cycle, real `OutcomeValidator` results written into the real
`SQLiteValidationStore`, and the report read back out of that store by signal ID.

## The corpus, and why its numbers are the unit corpus's numbers

Two subjects give two signals, and each signal is validated over three windows, so the store holds
six `ValidationResult` rows under two `signal_id`s. The prices are chosen so every realised return
is a dyadic rational -- `125/100`, `112.5/100`, `137.5/100` for the first signal and `112.5/100`,
`100/100`, `118.75/100` for the second -- against the same `2**-4` benchmark and `2**-7` cost that
`tests/unit/backtest/test_validation.py` uses. Those are exactly the returns
`tests/unit/backtest/test_outcome_statistics.py` builds by hand, so the two files predict the same
figures from two different directions: the unit corpus constructs `ValidationResult`s directly,
this one makes the shipped validator produce them.

A third subject is validated over exactly **one** window, and only the human-face test uses it.
That row is the named absence on the surface a reader actually looks at: five columns, a sample
count of one, `not tested` where the q-value would be, and the reason printed under the table
rather than truncated into a cell.

**One thing the corpus illustrates on purpose.** Validating one decision over three overlapping
windows is about as dependent as three observations get, and both the bootstrap and the sign-flip
test resample them as though they were independent draws. That is
`the_observations_are_resampled_as_though_they_were_independent_draws` in the flesh, and the
report prints the limitation beside the numbers rather than leaving a reader to notice.

## What the two faces are asked, and what would pass without the second question

The same request twice with one number changed: `--family-size 2` (the family is complete) and
`--family-size 8` (six cohorts were tested and are not reported). The first face rejects one
hypothesis, the second rejects none, and **not one measured return moves between them**. An
implementation that recovered the family size by counting the rows it had would give the first
answer to both questions, which is the whole of `V2-P5-007`'s second half.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from typer.testing import CliRunner

from openalpha_cn.backtest.outcome_statistics import OutcomeStatisticsError
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.sdk import OpenAlphaSDK

BENCHMARK: Final[float] = 0.0625
COST: Final[float] = 0.0078125

ALPHA_SUBJECT: Final[str] = "000001.SZ"
BETA_SUBJECT: Final[str] = "000002.SZ"
SOLITARY_SUBJECT: Final[str] = "000004.SZ"

ALPHA_PRICES: Final[tuple[tuple[float, float], ...]] = (
    (100.0, 125.0),
    (100.0, 112.5),
    (100.0, 137.5),
)
"""Realised `0.25`, `0.125`, `0.375`: each quotient is exact in binary."""

BETA_PRICES: Final[tuple[tuple[float, float], ...]] = (
    (100.0, 112.5),
    (100.0, 100.0),
    (100.0, 118.75),
)
"""Realised `0.125`, `0.0`, `0.1875` -- one flat window among three, where `alpha` has none."""

ALPHA_GROSS: Final[float] = 0.1875
ALPHA_DRAG: Final[float] = -0.0078125
ALPHA_NET: Final[float] = 0.1796875
ALPHA_P_VALUE: Final[float] = 0.25
BETA_P_VALUE: Final[float] = 0.75

RATE: Final[float] = 0.5
"""`2**-1`, so `1 * 0.5 / 2` is `0.25` -- `alpha`'s p-value exactly, on BH's inclusive line."""

INDEPENDENT: Final[str] = "independent-or-positively-dependent"


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
        source_id="statistics.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up for the outcome-statistics corpus.",
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
            run_id=f"statistics-{subject}",
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
        "the corpus needs a held position; a flat decision realises 0.0 and every cohort "
        "would carry the same net return"
    )
    return research


def _seed(tmp_path: Path, frozen_now: datetime) -> tuple[OpenAlphaSDK, str, str]:
    """Two signals, three validated windows apiece, all written through the shipped faces."""
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "runtime")
    signals: list[str] = []
    for subject, prices in ((ALPHA_SUBJECT, ALPHA_PRICES), (BETA_SUBJECT, BETA_PRICES)):
        research = _run(sdk, subject, frozen_now)
        signals.append(research.signal.signal_id)
        for offset, (start_price, end_price) in enumerate(prices):
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
    alpha, beta = signals
    assert alpha != beta
    assert len(sdk.list_validations_by_signal(alpha)) == 3
    return sdk, alpha, beta


def _seed_one_window(sdk: OpenAlphaSDK, frozen_now: datetime) -> str:
    """A third signal validated over exactly one window: the named-absence arm on the CLI."""
    research = _run(sdk, SOLITARY_SUBJECT, frozen_now)
    sdk.validate_outcome(
        research=research,
        observation=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(days=5),
            start_price=100.0,
            end_price=125.0,
            benchmark_return=BENCHMARK,
            transaction_cost=COST,
            data_quality_notes=("Synthetic outcome.",),
        ),
    )
    return research.signal.signal_id


def _cli(*arguments: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(arguments))
    return result.exit_code, result.output


def _row(payload: dict[str, Any], cohort_id: str) -> dict[str, Any]:
    return next(row for row in payload["cohorts"] if row["cohort_id"] == cohort_id)


# --- the SDK face -----------------------------------------------------------------------------


def test_the_sdk_reports_gross_drag_net_and_the_residual_off_real_stored_results(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`V2-P5-008`'s four columns, computed over results the shipped validator wrote.

    The identity `gross + drag == net` is asserted with `==` rather than `approx` because every
    input is dyadic all the way from `end_price / start_price`, and it is only worth asserting
    because the three columns are three independent means: a `net` derived from the other two
    would satisfy it for any arithmetic whatever.
    """
    sdk, alpha, _beta = _seed(tmp_path, frozen_now)

    report = sdk.outcome_statistics(
        signal_ids=(alpha,),
        family_size=1,
        false_discovery_rate=RATE,
        dependence=INDEPENDENT,
    )
    cohort = report.cohorts[0]

    assert cohort.sample_size == 3
    assert cohort.gross_active_return == ALPHA_GROSS
    assert cohort.cost_drag == ALPHA_DRAG
    assert cohort.net_active_return == ALPHA_NET
    assert cohort.gross_active_return + cohort.cost_drag == cohort.net_active_return
    assert cohort.unexplained_return == ALPHA_GROSS


def test_the_sdk_refuses_a_signal_with_nothing_stored_by_name(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Dropping it would shrink the family the caller declared, silently.

    "No outcomes were recorded for this signal" and "this signal was never asked about" are
    different facts with different remedies, and a cohort quietly missing from the report makes
    them the same one.
    """
    sdk, alpha, _beta = _seed(tmp_path, frozen_now)

    with pytest.raises(OutcomeStatisticsError) as refusal:
        sdk.outcome_statistics(
            signal_ids=(alpha, "sig_nothing_here"),
            family_size=2,
            false_discovery_rate=RATE,
            dependence=INDEPENDENT,
        )

    assert "sig_nothing_here" in str(refusal.value)


# --- the CLI face, and the family size that decides the verdict --------------------------------


def test_the_cli_prints_gross_drag_and_net_side_by_side_with_the_sample_count(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The human rendering, which is the only face most readers of this number will use.

    `-0.007812` and not `-0.007813`: the cost is `2**-7` exactly, so six decimal places lands on
    an exact tie and Python rounds it half-to-even. The literal here is the one the command
    prints rather than the one arithmetic-by-hand suggests, which is the whole reason a
    rendering gets its own assertion instead of being trusted to follow the model.
    """
    _sdk, alpha, beta = _seed(tmp_path, frozen_now)

    code, output = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--signal",
        beta,
        "--family-size",
        "2",
        "--false-discovery-rate",
        str(RATE),
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == int(PanelExit.ok), output
    assert "gross" in output and "drag" in output and "net" in output
    assert "+0.187500" in output
    assert "-0.007812" in output
    assert "+0.179688" in output
    assert "2 hypotheses tested, 2 reported, 0 withheld" in output
    assert "1 discoveries" in output
    assert "percentile bootstrap, 95% over 1000 resamples from seed 0" in output


def test_the_declared_family_size_decides_the_verdict_through_the_cli(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`V2-P5-007`'s second half, driven end to end and separated by one flag.

    Same store, same signals, same returns. `--family-size 2` rejects one hypothesis on BH's
    inclusive comparison (`alpha`'s p-value is `0.25` and its critical value is `1 * 0.5 / 2`);
    `--family-size 8` rejects none, because the line falls to `0.0625` and the q-value rises to
    `1.0`. A command that recovered the family from its own rows could not produce the second
    answer at all, and every measured column is asserted identical across the two so the
    difference cannot be coming from the data.
    """
    _sdk, alpha, beta = _seed(tmp_path, frozen_now)
    runtime = str(tmp_path / "runtime")

    def _payload(family_size: str) -> dict[str, Any]:
        code, output = _cli(
            "validation",
            "statistics",
            "--signal",
            alpha,
            "--signal",
            beta,
            "--family-size",
            family_size,
            "--false-discovery-rate",
            str(RATE),
            "--dependence",
            INDEPENDENT,
            "--runtime-dir",
            runtime,
            "--json",
        )
        assert code == int(PanelExit.ok), output
        return json.loads(output)

    narrow = _payload("2")
    broad = _payload("8")

    assert narrow["family"] == {
        "family_size": 2,
        "reported_hypotheses": 2,
        "withheld_hypotheses": 0,
        "family_is_complete": True,
        "false_discovery_rate": RATE,
        "dependence": INDEPENDENT,
        "dependence_penalty": 1.0,
        "discoveries": 1,
        "largest_rejected_rank": 1,
    }
    assert broad["family"]["family_size"] == 8
    assert broad["family"]["withheld_hypotheses"] == 6
    assert broad["family"]["family_is_complete"] is False
    assert broad["family"]["discoveries"] == 0

    assert _row(narrow, alpha)["q_value"] == 0.5
    assert _row(narrow, alpha)["rejected"] is True
    assert _row(narrow, beta)["rejected"] is False
    assert _row(broad, alpha)["critical_value"] == 0.0625
    assert _row(broad, alpha)["q_value"] == 1.0
    assert _row(broad, alpha)["rejected"] is False

    for cohort_id in (alpha, beta):
        for column in (
            "sample_size",
            "gross_active_return",
            "cost_drag",
            "net_active_return",
            "unexplained_return",
            "interval",
            "test",
        ):
            assert _row(narrow, cohort_id)[column] == _row(broad, cohort_id)[column], column


def test_the_json_face_carries_the_p_value_its_null_and_every_limitation(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """A q-value resolves to a named procedure and to a stated family, both on the document.

    The two registries travel together because the q-value column comes from one and the
    interval column from the other, and a reader holding only half the caveats is the reader
    `V2-P5-005` was written for.
    """
    _sdk, alpha, beta = _seed(tmp_path, frozen_now)

    code, output = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--signal",
        beta,
        "--family-size",
        "2",
        "--false-discovery-rate",
        str(RATE),
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
        "--json",
    )
    payload = json.loads(output)

    assert code == int(PanelExit.ok), output
    assert _row(payload, alpha)["test"] == {
        "method": "sign-flip-randomization",
        "null_hypothesis": "the cohort's net active returns are symmetric about zero",
        "p_value": ALPHA_P_VALUE,
        "exact": True,
        "sign_patterns": 8,
        "random_seed": None,
    }
    assert _row(payload, beta)["test"]["p_value"] == BETA_P_VALUE
    assert _row(payload, alpha)["interval"]["method"] == "percentile-bootstrap"
    assert _row(payload, alpha)["interval"]["distinct_bootstrap_means"] == 7
    assert payload["minimum_sample_size"] == 2

    codes = {limitation["code"] for limitation in payload["limitations"]}
    assert "the_family_size_is_declared_and_no_check_can_confirm_it" in codes
    assert "the_observations_are_resampled_as_though_they_were_independent_draws" in codes
    assert len(codes) == 13


def test_the_cli_prints_the_absent_inference_and_its_reason_rather_than_a_blank_row(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """A cohort with one observation on the human face: columns, `not tested`, and the reason.

    This is the half of `V2-P5-008`'s refusal that only the rendering can deliver. The report
    object already withholds the interval and names the absence; a terminal face that printed a
    blank cell instead would put the reader back where a zero-width interval put them, which is
    the failure the refusal exists for. The reason is printed under the table, not truncated
    into a column.
    """
    sdk, alpha, beta = _seed(tmp_path, frozen_now)
    solitary = _seed_one_window(sdk, frozen_now)

    code, output = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--signal",
        beta,
        "--signal",
        solitary,
        "--family-size",
        "2",
        "--false-discovery-rate",
        str(RATE),
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == int(PanelExit.ok), output
    assert "2 hypotheses tested, 2 reported, 0 withheld" in output
    assert "not tested" in output
    assert f"{solitary}: no interval and no p-value" in output
    assert "MINIMUM_INTERVAL_SAMPLE_SIZE" in output
    assert "zero width at any confidence level" in output


def test_the_cli_refuses_a_dependence_that_is_not_one_of_the_two(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """No default and no silent fallback: the correction is the caller's declaration."""
    _sdk, alpha, _beta = _seed(tmp_path, frozen_now)

    code, output = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--family-size",
        "1",
        "--dependence",
        "whatever",
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == int(PanelExit.bad_request), output
    assert "independent-or-positively-dependent" in output


def test_the_cli_refuses_a_family_smaller_than_the_cohorts_it_was_given(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The one direction a declared count can be checked in, checked at the outermost face."""
    _sdk, alpha, beta = _seed(tmp_path, frozen_now)

    code, output = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--signal",
        beta,
        "--family-size",
        "1",
        "--dependence",
        INDEPENDENT,
        "--runtime-dir",
        str(tmp_path / "runtime"),
    )

    assert code == int(PanelExit.bad_request), output
    assert "family_size 1" in output or "family_size" in output


def test_an_arbitrary_dependence_declaration_withdraws_the_discovery_through_the_cli(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """`H_2 = 1.5` halves `alpha`'s line, and `alpha` was exactly on it.

    The flag is the only thing that changes, and it changes the answer -- which is what
    separates a declared assumption from a label printed beside one.
    """
    _sdk, alpha, beta = _seed(tmp_path, frozen_now)
    runtime = str(tmp_path / "runtime")

    def _payload(dependence: str) -> dict[str, Any]:
        code, output = _cli(
            "validation",
            "statistics",
            "--signal",
            alpha,
            "--signal",
            beta,
            "--family-size",
            "2",
            "--false-discovery-rate",
            str(RATE),
            "--dependence",
            dependence,
            "--runtime-dir",
            runtime,
            "--json",
        )
        assert code == int(PanelExit.ok), output
        return json.loads(output)

    independent = _payload(INDEPENDENT)
    arbitrary = _payload("arbitrary")

    assert independent["family"]["discoveries"] == 1
    assert arbitrary["family"]["discoveries"] == 0
    assert arbitrary["family"]["dependence_penalty"] == 1.5
    assert _row(arbitrary, alpha)["critical_value"] == 0.25 / 1.5

    code, rendered = _cli(
        "validation",
        "statistics",
        "--signal",
        alpha,
        "--signal",
        beta,
        "--family-size",
        "2",
        "--false-discovery-rate",
        str(RATE),
        "--dependence",
        "arbitrary",
        "--runtime-dir",
        runtime,
    )
    assert code == int(PanelExit.ok), rendered
    assert "Benjamini-Yekutieli" in rendered
    assert "Benjamini-Hochberg" not in rendered
