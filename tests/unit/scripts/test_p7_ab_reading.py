"""The merged A/B reading in `scripts/research/p7_reading.py` (`V2-P7-027`, `V2-P7-028`).

One pre-registered, read-once `second_use` reading carries G-0's secondary diagnostics **and**
plan A's veto test and plan B's two factor tests: the registration bodies, the frozen thresholds,
the non-overlapping h20 engine arithmetic, the Benjamini-Hochberg family, the wording, and the
read-once refusals. `tests/unit/scripts/test_p7_reading.py` holds the read-once machinery itself;
this file holds the `ab_tests` extension and is built not to disturb it.

Everything is computed at test time: the panel fixtures are generated (hard rule 6), the git
repositories are throwaway ones, and no research store, registration, claim or result on disk is
read or written.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    _batch as batch,
)
from panel_fixtures import (
    _midnight_shanghai as midnight_shanghai,
)
from panel_fixtures import (
    _namechange_batch as namechange_batch,
)
from research_repo import commit_file, head
from strategy_fixtures import (
    COMMIT,
    PROBE_TRANSFORM,
    TWO_YEAR_BUILDS_FROM,
    TWO_YEAR_LAST,
    daily_requirement,
    write_two_year_corpus,
)

from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, compute_factor, write_factor_panels
from openalpha_cn.panel_ingest import (
    session_publication_instant,
    split_panel_batch_by_year,
    write_name_history,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
registry = _research_module("registry")
p7_diagnostics = _research_module("p7_diagnostics")
p7_read = _research_module("p7_read")
p7_reading = _research_module("p7_reading")
p7_windows = _research_module("p7_windows")

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
SECOND_USE: Final = p7_windows.WINDOWS["second_use"]
MOMENTUM: Final[str] = "momentum_20_sessions/v1"
"""A shipped factor used as B's stand-in on this branch: B's two factors arrive on
`feat/v2-p7-028-limit-factors`, and the reading must not hard-code their definitions here."""


# --- the registration section --------------------------------------------------------------------


def settings(*, horizon: int = 20, stride: int = 20) -> Any:
    """The read settings an `ab_tests` form is validated against (the same book G-0 reads)."""
    return p7_reading._Settings(
        explore=p7_diagnostics.ExploreSettings(
            as_of=datetime(2026, 10, 9, tzinfo=SHANGHAI),
            first=SECOND_USE.first,
            last=SECOND_USE.last,
            stride=stride,
            horizons=(horizon,),
            transform="cross_section_standard/v1",
            book=SurvivorBookConfig(seed=0),
            scores=("composite",),
            min_coverage=0.5,
            max_carry_sessions=250,
        ),
        tails=(1, 2, 3),
        loser_decile_count=2,
    )


def ab_form(**changes: Any) -> dict[str, Any]:
    """A registration's `ab_tests` section with this branch's stand-in factors."""
    form: dict[str, Any] = {
        "schema": p7_reading.AB_TESTS_SCHEMA,
        "window": SECOND_USE.name,
        "issues": ["V2-P7-027", "V2-P7-028"],
        "horizon": 20,
        "stride": 20,
        "false_discovery_rate": 0.10,
        "minimum_vetoed_observations": 60,
        "veto": {"rule": "market_bearish_5/v1", "lookback_sessions": 5},
        "factor_tests": [
            {"factor": MOMENTUM, "direction": "higher_is_better"},
            {"factor": "reversal_1d/v1", "direction": "lower_is_better"},
        ],
    }
    form.update(changes)
    return form


def spec(**changes: Any) -> Any:
    return p7_reading._ab_tests_of(ab_form(**changes), settings(), SECOND_USE)


def days(count: int) -> tuple[date, ...]:
    """`count` distinct ascending sessions well inside the second-use window."""
    return tuple(date(2024, 1, 2) + timedelta(days=20 * index) for index in range(count))


DEFAULT_VALUES: Final[Mapping[str, tuple[float, ...]]] = {
    p7_reading.VETO_TEST_ID: (-0.01, -0.02, -0.03, -0.04, -0.05, -0.06),
    MOMENTUM: (0.01, 0.02, 0.03, 0.04, 0.05, 0.06),
    "reversal_1d/v1": (0.001, -0.001, 0.001),
}
"""Hand-computable exact sign-flip p-values: six same-sign distinct values give a two-sided
p of 2/64 = 0.03125 (only the observe-all-the-same patterns reach the observed |sum|), and
(0.001, -0.001, 0.001) gives 1.0 (every sign pattern reaches it)."""


def measured(
    values: Mapping[str, Sequence[float]] | None = None,
    *,
    vetoed_observations: int | None = 60,
) -> list[Any]:
    """The three registered tests' measured series; `values` replaces chosen tests' series."""
    chosen = {**DEFAULT_VALUES, **({} if values is None else values)}
    built = spec()
    entry_kind = {
        p7_reading.VETO_TEST_ID: "veto_difference",
        **{item.factor: "factor_rank_ic" for item in built.factor_tests},
    }
    return [
        p7_reading.AbTestSeries(
            test_id=item.test_id,
            kind=entry_kind[item.test_id],
            factor=item.factor,
            sampled_sessions=len(chosen[item.test_id]),
            values=tuple(zip(days(len(chosen[item.test_id])), chosen[item.test_id], strict=True)),
            vetoed_observations=(
                vetoed_observations if item.test_id == p7_reading.VETO_TEST_ID else None
            ),
        )
        for item in built.entries
    ]


def evaluated(
    values: Mapping[str, Sequence[float]] | None = None,
    *,
    vetoed_observations: int | None = 60,
    **changes: Any,
) -> dict[str, Any]:
    return p7_reading.evaluate_ab_tests(
        spec(**changes), measured(values, vetoed_observations=vetoed_observations)
    )


def test_the_adopted_ab_tests_are_r_r1s_numbers_and_b_keys() -> None:
    """R-R1 freezes the window, h20, stride 20, q = 0.10, the 60-observation floor, the
    `lower_is_better` prior on B's two factors, and the three tests' family."""
    form = p7_reading.adopted_ab_tests()

    assert form["schema"] == p7_reading.AB_TESTS_SCHEMA
    assert form["window"] == "second_use"
    assert form["horizon"] == 20
    assert form["stride"] == 20
    assert form["false_discovery_rate"] == 0.10
    assert form["minimum_vetoed_observations"] == 60
    assert form["veto"] == {"rule": "market_bearish_5/v1", "lookback_sessions": 5}
    assert [item["factor"] for item in form["factor_tests"]] == [
        "limit_up_count_20/v1",
        "broken_board_count_20/v1",
    ]
    assert {item["direction"] for item in form["factor_tests"]} == {"lower_is_better"}
    assert form["issues"] == ["V2-P7-027", "V2-P7-028"]
    assert p7_reading.ab_test_ids(form) == (
        p7_reading.VETO_TEST_ID,
        "limit_up_count_20/v1",
        "broken_board_count_20/v1",
    )


def test_the_adopted_section_is_the_second_use_windows_own_shape() -> None:
    """The frozen h20/stride-20 pair is the request the real reading registers; a mismatch is
    refused rather than silently reading a different sample. The section is parsed with the
    stand-in factor keys because `feat/v2-p7-028-limit-factors` is where the real definitions
    arrive; the numbers under test are the adopted ones."""
    request = {
        "first": SECOND_USE.first.isoformat(),
        "last": SECOND_USE.last.isoformat(),
        "stride": 20,
        "horizons": [20],
        "scores": ["composite"],
        "tails": [1, 2, 3],
        "loser_decile_count": 2,
    }
    config = {
        "book": {"seed": 0},
        "transform": "cross_section_standard/v1",
        "exchange": "SSE",
        "min_coverage": 0.5,
        "max_carry_sessions": 250,
        "as_of": "2026-10-09T00:00:00+08:00",
    }
    section = {**p7_reading.adopted_ab_tests(), "factor_tests": ab_form()["factor_tests"]}
    body = p7_reading._body_of(
        reading="v2-p7-027-028-second-use",
        issue="V2-P7-027",
        role="secondary",
        window=SECOND_USE.name,
        request=request,
        config=config,
        stop_rule=None,
        expected_periods=None,
        code_commit="a" * 40,
        as_of_reason="the knowledge clock is the panel snapshot the read is taken under",
        ab_tests=section,
    )

    registered = p7_reading._registration_of(body)

    assert registered.ab_tests is not None
    assert registered.ab_tests.window == "second_use"
    assert registered.ab_tests.horizon == 20
    assert registered.ab_tests.stride == 20
    assert registered.ab_tests.false_discovery_rate == 0.10
    assert registered.ab_tests.veto.minimum_observations == 60
    assert registered.ab_tests.ids == (
        p7_reading.VETO_TEST_ID,
        *[item["factor"] for item in ab_form()["factor_tests"]],
    )


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"schema": "p7-ab-tests/v2"}, "p7-ab-tests/v1"),
        ({"window": "validation"}, "second_use"),
        ({"horizon": 10}, "horizon"),
        ({"stride": 10}, "stride"),
        ({"false_discovery_rate": 0.0}, "false_discovery_rate"),
        ({"false_discovery_rate": 1.0}, "false_discovery_rate"),
        ({"minimum_vetoed_observations": 0}, "minimum_vetoed_observations"),
        ({"veto": {"rule": "market_bullish_5/v1", "lookback_sessions": 5}}, "market_bearish_5/v1"),
        ({"veto": {"rule": "market_bearish_5/v1", "lookback_sessions": 6}}, "lookback"),
        ({"veto": {"rule": "market_bearish_5/v1"}}, "lacks"),
        ({"factor_tests": []}, "factor"),
        (
            {"factor_tests": [{"factor": "no_such_factor/v1", "direction": "lower_is_better"}]},
            "declared",
        ),
        ({"factor_tests": [{"factor": MOMENTUM, "direction": "lower_is_better"}]}, "direction"),
        (
            {
                "factor_tests": [
                    {"factor": MOMENTUM, "direction": "higher_is_better"},
                    {"factor": MOMENTUM, "direction": "higher_is_better"},
                ]
            },
            "twice",
        ),
        ({"issues": ["V2-P7-027", "V2-P7-027"]}, "issues"),
        ({"issues": ["not-an-issue"]}, "issues"),
        ({"extra": 1}, "extra"),
    ],
)
def test_an_ab_section_that_is_not_r_r1s_is_refused(changes: dict[str, Any], said: str) -> None:
    with pytest.raises(p7_reading.ReadingError, match=said):
        spec(**changes)


def test_the_direction_is_frozen_against_the_factor_definition() -> None:
    """A later branch cannot flip a factor's direction after the read: the registered direction
    must be the definition's declared one, and the definition is what orients the IC."""
    assert p7_reading.FACTOR_DEFINITIONS.get(MOMENTUM).direction == "higher_is_better"
    with pytest.raises(p7_reading.ReadingError, match="direction"):
        spec(factor_tests=[{"factor": MOMENTUM, "direction": "lower_is_better"}])


def test_the_horizon_and_stride_are_the_registered_requests_not_the_sections_own() -> None:
    """The section's h20/stride 20 must be the request's own horizon and stride: a reading that
    measured h10 while claiming h20 would be a different sample under the same thresholds."""
    with pytest.raises(p7_reading.ReadingError, match="horizon"):
        p7_reading._ab_tests_of(ab_form(), settings(horizon=10), SECOND_USE)
    assert (
        p7_reading._ab_tests_of(ab_form(horizon=10), settings(horizon=10), SECOND_USE).horizon == 10
    )
    with pytest.raises(p7_reading.ReadingError, match="stride"):
        p7_reading._ab_tests_of(ab_form(stride=10), settings(), SECOND_USE)


# --- the evaluation ------------------------------------------------------------------------------


def test_every_outcome_is_reachable_and_the_family_is_the_measured_tests() -> None:
    """A underperforms (candidate), the momentum stand-in holds its declared direction
    (candidate), the reversal stand-in's mean is positive but indistinguishable (no evidence)."""
    block = evaluated()

    outcomes = {item["id"]: item["outcome"] for item in block["tests"]}
    assert outcomes == {
        p7_reading.VETO_TEST_ID: p7_reading.AB_CANDIDATE,
        MOMENTUM: p7_reading.AB_CANDIDATE,
        "reversal_1d/v1": p7_reading.AB_NO_EVIDENCE,
    }
    assert block["family_size"] == 3
    assert block["dependence"] == p7_reading.AB_DEPENDENCE
    assert block["false_discovery_rate"] == 0.10
    assert "exploratory candidate" in block["wording"]
    assert set(block["family"]) == {p7_reading.VETO_TEST_ID, MOMENTUM, "reversal_1d/v1"}


def test_a_not_measurable_test_stays_out_of_the_bh_family() -> None:
    """Below 60 vetoed observations the A test is `not_measurable`: no p-value, not in the
    family, and the family size the output states is the measured count."""
    block = evaluated(vetoed_observations=59)

    a_test = block["tests"][0]
    assert a_test["outcome"] == p7_reading.AB_NOT_MEASURABLE
    assert a_test["p_one_sided"] is None and a_test["p_adjusted"] is None
    assert a_test["vetoed_observations"] == 59
    assert "60" in (a_test["reason"] or "")
    assert block["family_size"] == 2
    assert p7_reading.VETO_TEST_ID not in block["family"]
    assert {
        item["id"] for item in block["tests"] if item["outcome"] != p7_reading.AB_NOT_MEASURABLE
    } == set(block["family"])


def test_the_sixty_observation_floor_is_the_registered_bound() -> None:
    """The count is the registration's, not a code literal: 60 passes, 59 does not, and a
    registration that freezes 61 makes 60 not measurable."""
    assert evaluated(vetoed_observations=60)["tests"][0]["outcome"] == (p7_reading.AB_CANDIDATE)
    assert evaluated(vetoed_observations=59)["tests"][0]["outcome"] == (
        p7_reading.AB_NOT_MEASURABLE
    )
    raised = evaluated(vetoed_observations=60, minimum_vetoed_observations=61)
    assert raised["tests"][0]["outcome"] == p7_reading.AB_NOT_MEASURABLE


def test_the_p_values_are_the_gn0_engines_own_and_family_size_is_not_page_counting() -> None:
    """Two-sided p is `sign_flip_test` on the non-overlapping series; the one-sided reading is
    `grid.one_sided_p_value` in the declared direction. BH ranks by (p, id) and its family size is
    the measured-test count, never `len(tests)`."""
    from openalpha_cn.backtest.multiple_testing import (
        HypothesisTest,
        MultipleTestingRequest,
        control_false_discovery_rate,
    )
    from openalpha_cn.backtest.outcome_statistics import sign_flip_test

    block = evaluated()
    by_id = {item["id"]: item for item in block["tests"]}

    expected = sign_flip_test(
        DEFAULT_VALUES[MOMENTUM],
        bootstrap_samples=grid.PROTOCOL_BOOTSTRAP_SAMPLES,
        random_seed=grid.PROTOCOL_RANDOM_SEED,
    )
    assert by_id[MOMENTUM]["p_two_sided"] == expected.p_value
    assert by_id[MOMENTUM]["p_one_sided"] == expected.p_value / 2

    family = tuple(
        HypothesisTest(
            hypothesis_id=item["id"],
            p_value=item["p_one_sided"],
            test=p7_reading.AB_SIGN_FLIP_TEST,
        )
        for item in block["tests"]
        if item["outcome"] != p7_reading.AB_NOT_MEASURABLE
    )
    report = control_false_discovery_rate(
        MultipleTestingRequest(
            tests=family,
            family_size=len(family),
            false_discovery_rate=0.10,
            dependence=p7_reading.AB_DEPENDENCE,
        )
    )
    for verdict in report.verdicts:
        assert by_id[verdict.hypothesis_id]["bh_rank"] == verdict.rank
        assert by_id[verdict.hypothesis_id]["p_adjusted"] == verdict.q_value
        assert by_id[verdict.hypothesis_id]["rejected"] == verdict.rejected
    assert (
        by_id[MOMENTUM]["t"]
        == p7_reading.summarize_series(list(zip(days(6), DEFAULT_VALUES[MOMENTUM], strict=True))).t
    )


def test_bh_order_is_by_p_then_id_and_the_columns_follow_the_ranks() -> None:
    block = evaluated()
    measured = [item for item in block["tests"] if item["outcome"] != p7_reading.AB_NOT_MEASURABLE]
    ordered = sorted(measured, key=lambda item: (item["p_one_sided"], item["id"]))

    assert block["family"] == [item["id"] for item in ordered]
    assert [item["bh_rank"] for item in ordered] == list(range(1, len(ordered) + 1))


def test_the_registered_rate_is_the_one_applied_not_a_module_constant() -> None:
    """At q = 0.10 the reversal stand-in's p = 0.5 stays out; freeze q = 0.6 and it is a
    candidate, with no code change."""
    at_ten = evaluated()
    at_sixty = evaluated(false_discovery_rate=0.6)

    assert {item["id"]: item["outcome"] for item in at_ten["tests"]}["reversal_1d/v1"] == (
        p7_reading.AB_NO_EVIDENCE
    )
    assert {item["id"]: item["outcome"] for item in at_sixty["tests"]}["reversal_1d/v1"] == (
        p7_reading.AB_CANDIDATE
    )
    assert at_sixty["false_discovery_rate"] == 0.6


def test_a_test_with_one_measured_period_is_not_measurable() -> None:
    """A measured period count below the sign-flip test's floor is its own conclusion: the test
    is not measurable and stays out of the family, while a neighbour with a real sample is read."""
    built = spec(factor_tests=[{"factor": MOMENTUM, "direction": "higher_is_better"}])
    one = p7_reading.AbTestSeries(
        test_id=MOMENTUM,
        kind="factor_rank_ic",
        factor=MOMENTUM,
        sampled_sessions=1,
        values=((days(1)[0], 0.03),),
    )
    veto = p7_reading.AbTestSeries(
        test_id=p7_reading.VETO_TEST_ID,
        kind="veto_difference",
        factor=None,
        sampled_sessions=6,
        values=tuple(zip(days(6), DEFAULT_VALUES[p7_reading.VETO_TEST_ID], strict=True)),
        vetoed_observations=60,
    )
    block = p7_reading.evaluate_ab_tests(built, [veto, one])

    assert block["tests"][1]["outcome"] == p7_reading.AB_NOT_MEASURABLE
    assert block["tests"][1]["measured_periods"] == 1
    assert block["family_size"] == 1
    assert block["family"] == [p7_reading.VETO_TEST_ID]


def test_the_series_must_be_the_registered_tests_in_order() -> None:
    built = spec()
    with pytest.raises(p7_reading.ReadingError, match="series"):
        p7_reading.evaluate_ab_tests(built, [])
    with pytest.raises(p7_reading.ReadingError, match="series"):
        p7_reading.evaluate_ab_tests(
            built,
            [
                p7_reading.AbTestSeries(
                    test_id="reversal_1d/v1",
                    kind="factor_rank_ic",
                    factor="reversal_1d/v1",
                    sampled_sessions=6,
                    values=tuple(
                        zip(
                            days(len(DEFAULT_VALUES["reversal_1d/v1"])),
                            DEFAULT_VALUES["reversal_1d/v1"],
                            strict=True,
                        )
                    ),
                )
            ],
        )


# --- the read-once path --------------------------------------------------------------------------

# The throwaway repository and the stand-in measure are the ones
# `tests/unit/scripts/test_p7_reading.py` uses for the read-once machinery; the only new ingredient
# is the `ab_tests` section.

AS_OF_REASON: Final[str] = (
    "the 2026-10-09 08:00 panel is the newest snapshot before the code freeze, and every revision "
    "published before it is part of what the reading may know"
)
BOUND_PATHS: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository whose bound code, package and P7 imports are the throwaway tree's."""
    from research_repo import git

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in BOUND_PATHS:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "initial", at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
    # The second-use window is read only after the validation window, and the ordering is
    # witnessed by a committed validation window claim.
    claim = repo / p7_reading.READING_DIRECTORY / "validation.window-claim.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(
        '{"reading": "v2-p7-024-validation", "window": "validation"}\n', encoding="utf-8"
    )
    commit_file(
        repo, claim, "the validation window was read", at=datetime(2026, 11, 1, 13, 0, tzinfo=UTC)
    )
    research = repo / "scripts" / "research"
    monkeypatch.setattr(
        registry, "_imported_package", lambda: repo / "src" / "openalpha_cn" / "__init__.py"
    )
    monkeypatch.setattr(
        registry, "_imported_scripts", lambda: (research / "grid.py", research / "registry.py")
    )
    monkeypatch.setattr(
        p7_reading,
        "_imported_p7_scripts",
        lambda: tuple(
            research / f"{name}.py" for name in ("p7_windows", "p7_diagnostics", "p7_reading")
        ),
    )
    return repo


def second_use_body(*, ab_tests: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A secondary `second_use` registration body, with or without the A/B section."""
    request = {
        "first": SECOND_USE.first.isoformat(),
        "last": SECOND_USE.last.isoformat(),
        "stride": 20,
        "horizons": [20],
        "scores": ["composite"],
        "tails": [1, 2, 3],
        "loser_decile_count": 2,
    }
    return {
        "schema": p7_reading.READING_SCHEMA,
        "reading": "v2-p7-027-028-second-use",
        "issue": "V2-P7-027",
        "role": "secondary",
        "window": SECOND_USE.name,
        "request": request,
        "config": {
            "book": {"seed": 0},
            "transform": "cross_section_standard/v1",
            "exchange": "SSE",
            "min_coverage": 0.5,
            "max_carry_sessions": 250,
            "as_of": "2026-10-09T00:00:00+08:00",
        },
        "stop_rule": None,
        "expected_periods": None,
        "code_commit": "a" * 40,
        "as_of_reason": AS_OF_REASON,
        **({} if ab_tests is None else {"ab_tests": dict(ab_tests)}),
    }


def registered_body(repo: Path, body: Mapping[str, Any]) -> Path:
    """Write a registration body as bytes into the throwaway repository and commit it.

    The bound `code_commit` is the tree commit the registration measures with: the throwaway
    repository's own HEAD before the registration commit, so the read-once code check passes.
    """
    body = {**body, "code_commit": head(repo)}
    path = repo / p7_reading.READING_DIRECTORY / f"{body['reading']}.registration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(p7_reading._json_bytes(body))
    commit_file(repo, path, "register the reading", at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    return path


def run_read(
    registration: Path,
    repo: Path,
    measure: Any,
    *,
    hours: int = 1,
) -> dict[str, Any]:
    return p7_reading.read_once(
        registration,
        repo=repo,
        runtime_dir=repo / "runtime",
        clock=lambda: datetime(2026, 11, 2, 12, 0, tzinfo=UTC) + timedelta(hours=hours),
        measure=measure,
        preflight=lambda body: (),
    )


def ab_document(**changes: Any) -> dict[str, Any]:
    """A stand-in read-once document carrying a valid A/B block (no engine needed)."""
    return {
        "kind": p7_diagnostics.READ_ONCE_KIND,
        "summary": {},
        "ab_tests": evaluated(**changes),
    }


class StubMeasure:
    """A measure that returns a fixed document and records the binding it was given."""

    def __init__(self, document: Mapping[str, Any] | None = None) -> None:
        self.document = dict(document if document is not None else ab_document())
        self.binding: Mapping[str, Any] | None = None

    def __call__(
        self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.binding = dict(binding)
        return {**self.document, "binding": dict(binding)}


def register_ab(repo: Path, *, section: Mapping[str, Any] | None = None) -> Path:
    """Register and commit the second-use reading with a stand-in-valid A/B section."""
    body = second_use_body(ab_tests=ab_form() if section is None else section)
    return registered_body(repo, body)


def test_a_reading_with_the_ab_section_writes_its_family_and_wording_once(repo: Path) -> None:
    registration = register_ab(repo)
    measure = StubMeasure()

    result = run_read(registration, repo, measure)

    assert result["binding"] == measure.binding
    block = result["ab_tests"]
    assert block["family_size"] == 3
    text = registration.with_name("v2-p7-027-028-second-use.result.txt").read_text(encoding="utf-8")
    assert "ab tests" in text
    assert text.count("  test ") == 3
    assert "exploratory candidate" in text
    assert "exploratory candidate" in json.dumps(result["ab_tests"])
    assert "family size 3" in text
    for line in (line for line in text.splitlines() if line.startswith("  test ")):
        assert "exploratory candidate" in line
    assert "confirmed" not in text.lower()
    assert "is confirmatory" not in text.lower()
    assert "never confirmatory" in text or "non-confirmatory" in text


def test_a_reading_with_the_ab_section_has_one_read(repo: Path) -> None:
    registration = register_ab(repo)
    run_read(registration, repo, StubMeasure())

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        run_read(registration, repo, StubMeasure(), hours=2)


def test_a_document_without_or_with_a_changed_ab_block_is_refused(repo: Path) -> None:
    """The measure is not trusted blindly: the block must be this registration's, and a refusal
    writes no result while the claim stays spent."""
    registration = register_ab(repo)
    for document in (
        {"kind": p7_diagnostics.READ_ONCE_KIND, "summary": {}},
        ab_document(false_discovery_rate=0.5),
        ab_document(minimum_vetoed_observations=1),
    ):
        with pytest.raises(p7_reading.ReadingConfigurationError):
            run_read(registration, repo, StubMeasure(document))
        assert not registration.with_name("v2-p7-027-028-second-use.result.json").exists()
        assert registration.with_name("v2-p7-027-028-second-use.claim.json").exists()
        # Remove the claim the refused read wrote, so the next case can try: what this test
        # checks is the refusal, not the window rule, and the real read has one read only.
        registration.with_name("v2-p7-027-028-second-use.claim.json").unlink()
        registration.with_name("second_use.window-claim.json").unlink()


def test_a_registration_without_the_ab_section_is_read_as_before(repo: Path) -> None:
    """No section, no block, no line: a `v2-p7-024`-shaped registration reads exactly as it did."""
    body = second_use_body()
    registration = registered_body(repo, body)

    result = run_read(
        registration, repo, StubMeasure({"kind": p7_diagnostics.READ_ONCE_KIND, "summary": {}})
    )

    assert "ab_tests" not in result
    text = registration.with_name("v2-p7-027-028-second-use.result.txt").read_text(encoding="utf-8")
    assert "ab tests" not in text and "exploratory candidate" not in text


def test_the_committed_v2_p7_024_registration_is_byte_for_byte_what_it_was() -> None:
    """Hard constraint: the section is optional, and the one real registration on disk must still
    parse and serialise to the same bytes -- so its claim's `registration_sha256` is unchanged."""
    import hashlib
    import json

    directory = ROOT / "docs" / "research" / "p7-readings"
    registration = directory / "v2-p7-024-validation.registration.json"
    claim = json.loads((directory / "v2-p7-024-validation.claim.json").read_text(encoding="utf-8"))
    content = registration.read_bytes()
    body = json.loads(content)

    assert hashlib.sha256(content).hexdigest() == claim["registration_sha256"]
    assert "ab_tests" not in body
    parsed = p7_reading._registration_of(body)
    assert parsed.ab_tests is None
    rebuilt = p7_reading._body_of(
        reading=body["reading"],
        issue=body["issue"],
        role=body["role"],
        window=body["window"],
        request=body["request"],
        config=body["config"],
        stop_rule=p7_reading._rule_of(body["stop_rule"]),
        expected_periods=body["expected_periods"],
        code_commit=body["code_commit"],
        as_of_reason=body["as_of_reason"],
    )
    assert "ab_tests" not in rebuilt
    assert p7_reading._json_bytes(rebuilt) == content


# --- the veto window: sessions, not calendar days -------------------------------------------------


def weekday_calendar(first: date, last: date) -> Any:
    from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar

    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


def an_event(session: date, *, kind: str = "broken_board") -> Any:
    from openalpha_cn.domain.limit_events import LimitEvent

    return LimitEvent(
        ts_code="600000.SH",
        trade_date=session,
        kind=kind,
        board_count=1,
        one_price=False,
        close=10.0,
        high=11.0,
        pct_chg=-1.0,
        halted_sessions_skipped=0,
        board_count_is_lower_bound=False,
    )


def test_the_veto_reads_the_session_and_its_four_predecessors_and_nothing_older() -> None:
    """Six sessions of history are handed a broken board on the sixth: the rule reads five
    sessions ending at the day, so it is never asked for, and the answer is not vetoed."""
    calendar = weekday_calendar(date(2026, 1, 1), date(2026, 2, 28))
    day = date(2026, 1, 12)
    five = [day]
    for _ in range(4):
        five.append(calendar.previous_trading_day(five[-1]))
    sixth = calendar.previous_trading_day(five[-1])
    calls: list[date] = []

    def events_for(session: date) -> tuple[Any, ...]:
        calls.append(session)
        return (an_event(session),) if session == sixth else ()

    assert (
        p7_reading._vetoed_on(day, calendar=calendar, events_for=events_for, lookback=5)
        == frozenset()
    )
    assert calls == five
    assert sixth not in calls

    def broken_on_the_oldest(session: date) -> tuple[Any, ...]:
        return (an_event(session),) if session == five[-1] else ()

    assert p7_reading._vetoed_on(
        day, calendar=calendar, events_for=broken_on_the_oldest, lookback=5
    ) == frozenset({"600000.SH"})


def test_each_session_of_the_veto_window_is_read_at_its_own_publication_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restatement published after a session cannot enter its events: every session is read at
    its own 16:30, not at the reading's single `as_of`."""
    seen: list[tuple[date, datetime, str]] = []

    def fake_limit_events_on(
        store: object, *, session: date, as_of: datetime, exchange: str
    ) -> tuple[Any, ...]:
        seen.append((session, as_of, exchange))
        return ()

    monkeypatch.setattr(p7_reading, "limit_events_on", fake_limit_events_on)
    reader = p7_reading._limit_events_reader(object())  # type: ignore[arg-type]
    for session in (date(2026, 1, 5), date(2026, 1, 6)):
        reader(session)

    from openalpha_cn.panel_ingest import session_publication_instant

    assert [(session, moment) for session, moment, _ in seen] == [
        (session, session_publication_instant(session))
        for session in (date(2026, 1, 5), date(2026, 1, 6))
    ]
    assert {exchange for _, _, exchange in seen} == {p7_reading.EXCHANGE}


# --- the merged reading on a generated panel -----------------------------------------------------
#
# `tests/unit/test_p7_reading_rehearsal.py` rehearses the default measure on the two-year corpus;
# this is the same corpus with one stand-in factor's raw builds and a synthetic limit-event reader,
# so the A/B section is measured by the real facts day, screen, label reader, engine and factor
# read -- the only synthetic part is which securities carry a broken board on a session.

READING_WINDOW: Final = p7_windows.DiagnosticWindow(
    name="second_use", first=date(2027, 1, 4), last=TWO_YEAR_LAST, ceiling_year=2027
)
"""A fixture window borrowing the name the section freezes (the real one is 2024..2026); the
measure takes any `DiagnosticWindow`, the command line takes names from `p7_windows.WINDOWS`."""
READING_BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(
    seed=0, composite_factors=("reversal_1d/v1",), min_listing_sessions=60
)
RENAMED: Final[NameRecord] = NameRecord(
    ts_code="600000.SH",
    name="Renamed Bank",
    effective_from=date(2027, 1, 11),
    announced_on=date(2027, 1, 5),
    change_reason="rename",
)


class _Transforms:
    """`FACTOR_TRANSFORMS` with the fixture's probe transform declared beside the shipped ones."""

    def __init__(self, shipped: Any) -> None:
        self.shipped = shipped

    def get(self, key: str) -> Any:
        return PROBE_TRANSFORM if key == PROBE_TRANSFORM.qualified_key else self.shipped.get(key)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    """The two-year corpus, a 2027 name change, and raw `momentum_20_sessions/v1` builds on
    every session the reading's window measures (the shipped evaluator, so the values are real
    20-session momentum, not a stand-in number)."""
    root = tmp_path_factory.mktemp("ab-reading")
    panel = write_two_year_corpus(
        root,
        builds_from=TWO_YEAR_BUILDS_FROM,
        processed={PROBE_TRANSFORM: TWO_YEAR_LAST},
    )
    store = PanelStore(root / "panel")
    for _year, part in split_panel_batch_by_year(namechange_batch(panel.name_records)):
        write_name_history(store, part)
    announced = midnight_shanghai(RENAMED.announced_on)
    write_name_history(
        store,
        batch(
            NAMECHANGE_DATASET,
            subjects=[RENAMED.ts_code],
            columns=[
                PanelColumn(NAME_COLUMN, "string", (RENAMED.name,)),
                PanelColumn(NAME_EFFECTIVE_COLUMN, "string", (RENAMED.effective_from.isoformat(),)),
                PanelColumn(
                    NAME_ANNOUNCEMENT_COLUMN, "string", (RENAMED.announced_on.isoformat(),)
                ),
                PanelColumn(NAME_REASON_COLUMN, "string", (RENAMED.change_reason,)),
            ],
            event_time=[announced],
            available_time=[announced],
            fetched_at=announced,
        ),
    )
    definition = FACTOR_DEFINITIONS.get("momentum_20_sessions/v1")
    builds = []
    for session in panel.sessions:
        if not TWO_YEAR_BUILDS_FROM <= session <= TWO_YEAR_LAST:
            continue
        instant = session_publication_instant(session)
        years = tuple(year for year in (session.year - 1, session.year) if year >= 2026)
        builds.append(
            compute_factor(
                store,
                definition,
                as_of=instant,
                subjects=tuple(panel.securities),
                universe=frozenset(panel.securities),
                requirements={
                    "daily": daily_requirement(
                        panel.calendar(),
                        years=years,
                        as_of=instant,
                        max_staleness=timedelta(days=30),
                    )
                },
                code_commit=COMMIT,
                built_at=instant,
            )
        )
    write_factor_panels(store, builds)
    return root, panel


def reading_body(panel: Any, *, changes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The A/B registration body over the fixture window: the adapted section's thresholds (so
    the test can watch them arrive from the body) with the fixture's horizon and stride."""
    book = READING_BOOK.model_dump(mode="json")
    book.pop("config_id", None)
    section = {
        **ab_form(),
        "horizon": 2,
        "stride": 2,
        "minimum_vetoed_observations": 1,
        "false_discovery_rate": 0.5,
        **(changes or {}),
    }
    return p7_reading._body_of(
        reading="v2-p7-027-028-second-use",
        issue="V2-P7-027",
        role="secondary",
        window=READING_WINDOW.name,
        request={
            "first": READING_WINDOW.first.isoformat(),
            "last": READING_WINDOW.last.isoformat(),
            "stride": 2,
            "horizons": [2],
            "scores": ["composite"],
            "tails": [1, 2, 3],
            "loser_decile_count": READING_BOOK.loser_decile_count,
        },
        config={
            "book": book,
            "transform": PROBE_TRANSFORM.qualified_key,
            "exchange": "SZSE",
            "min_coverage": 0.5,
            "max_carry_sessions": 250,
            "as_of": panel.as_of.isoformat(),
        },
        stop_rule=None,
        expected_periods=None,
        code_commit="0" * 40,
        as_of_reason="the knowledge clock is the generated panel's last write instant",
        ab_tests=section,
    )


def test_the_merged_read_measures_the_three_tests_and_keeps_gn0s_diagnostics(
    corpus: tuple[Path, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, panel = corpus
    monkeypatch.setattr(p7_reading, "EXCHANGE", "SZSE")
    monkeypatch.setattr(p7_reading, "FACTOR_TRANSFORMS", _Transforms(p7_reading.FACTOR_TRANSFORMS))
    vetoed = tuple(panel.securities[::2])

    def synthetic_events(
        store: object, *, session: date, as_of: datetime, exchange: str
    ) -> tuple[Any, ...]:
        from openalpha_cn.domain.limit_events import LimitEvent

        return tuple(
            LimitEvent(
                ts_code=code,
                trade_date=session,
                kind="broken_board",
                board_count=0,
                one_price=False,
                close=10.0,
                high=11.0,
                pct_chg=1.0,
                halted_sessions_skipped=0,
                board_count_is_lower_bound=False,
            )
            for code in vetoed
        )

    monkeypatch.setattr(p7_reading, "limit_events_on", synthetic_events)
    binding = {
        "reading": "v2-p7-027-028-second-use",
        "registration_sha256": "a" * 64,
        "registration_commit": "b" * 40,
        "claim_sha256": "c" * 64,
    }
    document = p7_reading._panel_measure(root)(reading_body(panel), READING_WINDOW, binding)

    # G-0's secondary diagnostics are still the document: the merged read is one read.
    assert document["kind"] == p7_diagnostics.READ_ONCE_KIND
    assert document["binding"] == binding
    assert document["summary"]["composite"]["h2"]["read"] > 0
    assert document["provenance"]["partitions_read"]
    # And the A/B block is the registration's, measured by the real engine.
    block = document["ab_tests"]
    assert block["schema"] == p7_reading.AB_TESTS_SCHEMA
    assert block["window"] == "second_use"
    assert block["horizon"] == 2 and block["stride"] == 2
    assert block["false_discovery_rate"] == 0.5
    assert block["minimum_vetoed_observations"] == 1
    assert [item["id"] for item in block["tests"]] == [
        p7_reading.VETO_TEST_ID,
        "momentum_20_sessions/v1",
        "reversal_1d/v1",
    ]
    assert {item["outcome"] for item in block["tests"]} <= set(p7_reading.AB_OUTCOMES)
    measured = [item for item in block["tests"] if item["outcome"] != p7_reading.AB_NOT_MEASURABLE]
    assert block["family_size"] == len(measured)
    assert block["family"] == [item["id"] for item in measured]
    assert "exploratory candidate" in block["wording"]
    veto_test = block["tests"][0]
    assert veto_test["vetoed_observations"] > 0
    assert veto_test["measured_periods"] > 0
    factors = {item["factor"]: item for item in block["tests"][1:]}
    assert factors["momentum_20_sessions/v1"]["measured_periods"] > 0
    assert factors["momentum_20_sessions/v1"]["mean"] is not None
    assert factors["reversal_1d/v1"]["measured_periods"] > 0

    # The thresholds are the body's, not the module's adopted constants: the same registration
    # with the real floor would leave A unmeasurable on this sample.
    as_adopted = p7_reading._panel_measure(root)(
        reading_body(panel, changes={"minimum_vetoed_observations": 60}),
        READING_WINDOW,
        binding,
    )
    assert as_adopted["ab_tests"]["tests"][0]["outcome"] == p7_reading.AB_NOT_MEASURABLE
    assert as_adopted["ab_tests"]["minimum_vetoed_observations"] == 60


# --- the command line: the section is opt-in -----------------------------------------------------


def test_register_writes_the_adopted_section_only_when_asked(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`register --ab-tests` is the one way the merged read's section is written; without the
    flag the command writes a registration with no section, exactly as it did before. On this
    branch the adopted section's factor keys are replaced by the two shipped factors whose
    directions are the frozen ones, because B's definitions arrive on its own branch."""
    monkeypatch.setattr(
        p7_reading,
        "AB_LIMIT_EVENT_FACTORS",
        (
            ("momentum_20_sessions/v1", "higher_is_better"),
            ("reversal_1d/v1", "lower_is_better"),
        ),
    )
    common = [
        "register",
        "--repo",
        str(repo),
        "--runtime-dir",
        str(repo / "runtime"),
        "--window",
        "second_use",
        "--reading",
        "v2-p7-027-028-second-use",
        "--issue",
        "V2-P7-027",
        "--as-of",
        "2026-10-09T00:00:00+08:00",
        "--as-of-reason",
        AS_OF_REASON,
        "--stride",
        "20",
        "--horizons",
        "20",
        "--tails",
        "1",
        "2",
        "3",
        "--score",
        "composite",
        "--book",
        '{"seed": 0}',
        "--transform",
        "cross_section_standard/v1",
        "--min-coverage",
        "0.5",
        "--max-carry-sessions",
        "250",
        "--exchange",
        "SSE",
        "--dry-run",
    ]
    path = repo / p7_reading.READING_DIRECTORY / "v2-p7-027-028-second-use.registration.json"

    assert p7_read.main([*common, "--ab-tests"]) == 0
    printed = capsys.readouterr().out
    assert "the registration carries the pre-registered A/B tests" in printed
    assert p7_reading.VETO_TEST_ID in printed
    assert "dry run: nothing was written and nothing was claimed" in printed
    assert not path.exists()

    assert p7_read.main(common) == 0
    printed = capsys.readouterr().out
    assert "pre-registered A/B tests" not in printed
    assert not path.exists()


def test_the_applied_section_is_r_r1s_frozen_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `--ab-tests` writes is R-R1, not a per-run choice: the window, h20, stride 20, q, the
    floor, the rule, and B's two factor keys are `adopted_ab_tests`' constants."""
    form = p7_reading.adopted_ab_tests()
    assert form == {
        "schema": p7_reading.AB_TESTS_SCHEMA,
        "window": "second_use",
        "issues": ["V2-P7-027", "V2-P7-028"],
        "horizon": 20,
        "stride": 20,
        "false_discovery_rate": 0.10,
        "minimum_vetoed_observations": 60,
        "veto": {"rule": "market_bearish_5/v1", "lookback_sessions": 5},
        "factor_tests": [
            {"factor": "limit_up_count_20/v1", "direction": "lower_is_better"},
            {"factor": "broken_board_count_20/v1", "direction": "lower_is_better"},
        ],
    }


def test_the_committed_v2_p7_024_result_text_is_reproduced_byte_for_byte() -> None:
    """The old reading's text is not a stale artifact: the current `reading_lines` on the committed
    document and registration writes exactly the bytes on disk, with no A/B line added."""
    directory = ROOT / "docs" / "research" / "p7-readings"
    body = json.loads(
        (directory / "v2-p7-024-validation.registration.json").read_text(encoding="utf-8")
    )
    result = json.loads(
        (directory / "v2-p7-024-validation.result.json").read_text(encoding="utf-8")
    )
    text = (directory / "v2-p7-024-validation.result.txt").read_text(encoding="utf-8")

    assert ("\n".join(p7_reading.reading_lines(result, body)) + "\n") == text
    assert "ab tests" not in text
