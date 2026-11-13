"""`scripts/agent_report.py`: plan C's C report (`V2-P7-014`, D8).

The report is a **separate command**: Step 0's weekly report script is not modified, and C's
reading is printed by the code C's registration binds. It measures stored records only -- it
never runs an agent or calls a model -- and it refuses a D7 dry-week scratch runtime by name
before opening anything.

Every test here drives fabricated records, registrations and journals: nothing reads a real
research store, no panel is generated except in the one read-only wiring test, and the real
`docs/research/p7-c-registration.json` is never written. The evaluator itself is covered by
`tests/integration/test_agent_incremental_ic.py`; here it is replaced by a recorder so that what
this test file holds is the report's own behaviour -- what it prints, what it refuses, and what
it passes the evaluator.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final

import pytest
from panel_fixtures import generate_panel, write_generated_panel

from openalpha_cn.domain.agent_forward import AGENT_LLM_FAMILY, AGENT_RULE_FAMILY
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_agent_evaluation import (
    AGENT_FORWARD_ANCHOR_KEY,
    AGENT_FORWARD_ANCHOR_SCHEMA,
    AGENT_FORWARD_POWER_KEY,
    AGENT_FORWARD_POWER_SCHEMA,
    AGENT_FORWARD_PROTOCOL_KEY,
    AGENT_FORWARD_PROTOCOL_SCHEMA,
    AGENT_FORWARD_STEP0_KEY,
    AGENT_FORWARD_STEP0_SCHEMA,
    D5_WORDINGS,
    DETECTABLE_IC_BASIS,
    GAP_NO_AGENT_RECORD,
    NOT_MEASURABLE_WORDING,
    AgentEvaluation,
    ArmReading,
    DetectableIC,
    SessionIC,
    StatisticTest,
)
from openalpha_cn.strategy_registration import SHANGHAI, RegisteredConfiguration

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    for directory in (ROOT / "scripts", ROOT / "scripts" / "research"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module(name)


report = _script("agent_report")
registry = _script("registry")

SESSIONS: Final[tuple[date, ...]] = (
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
    date(2026, 1, 12),
)
ANCHOR: Final[date] = SESSIONS[0]
THROUGH: Final[date] = SESSIONS[5]
AS_OF: Final[datetime] = datetime(2026, 1, 17, 12, 0, tzinfo=SHANGHAI)
C_COMMITTED_AT: Final[datetime] = datetime(2026, 1, 5, 9, 0, tzinfo=SHANGHAI)
"""C's registration commit instant: before the anchor session's 16:30 publication."""
C_CONFIG_ID: Final[str] = "caf-0000000000000001"
C_CODE_COMMIT: Final[str] = "a" * 40
C_COMMIT: Final[str] = "b" * 40
STEP0_CONFIG_ID: Final[str] = "step0-config-id"
STEP0_COMMIT: Final[str] = "c" * 40
STEP0_SEED: Final[int] = 7
STEP0_PATH: Final[str] = "docs/research/p7-registration.json"
POWER_SAMPLE: Final[str] = "n300"
POWER_VERDICT: Final[str] = "testable"

BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=STEP0_SEED, min_listing_sessions=60)
ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "epoch_session": "2026-01-05",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
}

PROTOCOL_BLOCK: Final[dict[str, Any]] = {
    "schema_version": AGENT_FORWARD_PROTOCOL_SCHEMA,
    "primary_family": AGENT_LLM_FAMILY,
    "primary_set": "primary",
    "primary_horizon": 1,
    "secondary": [
        {"family": AGENT_RULE_FAMILY, "scoring_set": "coverage", "horizon": 1},
    ],
    "window_sessions": 6,
    "min_scored": 4,
    "min_measurable_sessions": 4,
    "interim_session": 2,
    "level": 0.05,
    "q": 0.1,
    "bootstrap_samples": 1000,
    "random_seed": 11,
}


def _step0_body() -> dict[str, Any]:
    return {
        "schema": "openalpha-research-registration/v1",
        "config": {
            "kind": "survivor_book/v1",
            "survivor_book": BOOK.model_dump(mode="json", exclude_computed_fields=True),
            "adapter": ADAPTER,
        },
        "config_id": STEP0_CONFIG_ID,
        "criteria": {"note": "fixture"},
        "code_commit": STEP0_COMMIT,
        "settings": {"random_seed": STEP0_SEED},
    }


STEP0_BODY: Final[dict[str, Any]] = _step0_body()
STEP0_CONTENT: Final[bytes] = (
    json.dumps(STEP0_BODY, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
).encode()
STEP0_SHA: Final[str] = hashlib.sha256(STEP0_CONTENT).hexdigest()


def _c_body(
    *,
    step0_sha: str = STEP0_SHA,
    step0_config_id: str = STEP0_CONFIG_ID,
    step0_seed: int = STEP0_SEED,
    kind: str = "agent_forward/v1",
    anchor: date = ANCHOR,
) -> dict[str, Any]:
    return {
        "schema": "openalpha-research-registration/v1",
        "config": {"kind": kind, "salt": "c-report-salt-2026", "primary_size": 4},
        "config_id": C_CONFIG_ID,
        "criteria": {"note": "fixture"},
        "code_commit": C_CODE_COMMIT,
        "settings": {
            AGENT_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK),
            AGENT_FORWARD_POWER_KEY: {
                "schema_version": AGENT_FORWARD_POWER_SCHEMA,
                "sample": POWER_SAMPLE,
                "verdict": POWER_VERDICT,
                "register_untestable": False,
                "report_sha256": "d" * 64,
                "measurable_sessions": 86,
                "detectable_mean": 0.021,
            },
            AGENT_FORWARD_STEP0_KEY: {
                "schema_version": AGENT_FORWARD_STEP0_SCHEMA,
                "path": STEP0_PATH,
                "registration_sha256": step0_sha,
                "config_id": step0_config_id,
                "seed": step0_seed,
            },
            AGENT_FORWARD_ANCHOR_KEY: {
                "schema_version": AGENT_FORWARD_ANCHOR_SCHEMA,
                "session": anchor.isoformat(),
            },
        },
    }


def calendar_over(
    sessions: tuple[date, ...],
    *,
    exchange: str = "SSE",
    first: date | None = None,
) -> TradingCalendar:
    """A trading calendar over the natural days spanning `sessions` and a week after.

    `build_trading_calendar` refuses a gap in the natural-day sequence, so every date from
    `first` (or the first session) to a week past the last is declared, open exactly where
    `sessions` says. The F3 anchor check reads this calendar the way the evaluator's label reader
    does, so it must cover the registration commit's own date (a weekend commit included).
    """
    days: list[CalendarDay] = []
    previous: date | None = None
    day = min(sessions) if first is None else min(first, min(sessions))
    last = max(sessions) + timedelta(days=7)
    while day <= last:
        trading = day in sessions
        days.append(
            CalendarDay(calendar_date=day, is_trading=trading, previous_trading_date=previous)
        )
        if trading:
            previous = day
        day += timedelta(days=1)
    return build_trading_calendar(exchange, days)


CALENDAR: Final[TradingCalendar] = calendar_over(SESSIONS, first=date(2026, 1, 1))


class SentinelLabels:
    """The labels reader a report run is handed: the recorder checks identity, F3 its calendar."""

    def __init__(self, calendar: TradingCalendar = CALENDAR) -> None:
        self.calendar = calendar


@dataclass
class Recorder:
    """The report's `evaluate` seam: records the call and answers a fabricated evaluation."""

    evaluation: AgentEvaluation
    call: dict[str, Any] | None = None

    def __call__(self, **arguments: Any) -> AgentEvaluation:
        self.call = arguments
        return self.evaluation


def make_evaluation(
    *,
    outcome: str = "not_yet",
    mean: float | None = 0.25,
    measurable: int = 2,
    sessions: int = 2,
    missing: tuple[SessionIC, ...] = (),
    test: StatisticTest | None = None,
    power: DetectableIC | None = None,
    exploratory: tuple[ArmReading, ...] = (),
    adopted: tuple[str, ...] = D5_WORDINGS,
) -> AgentEvaluation:
    primary = ArmReading(
        family=AGENT_LLM_FAMILY,
        scoring_set="primary",
        horizon=1,
        positions=sessions,
        measurable=measurable,
        missing=len(missing),
        unmeasured=sessions - measurable - len(missing),
        mean=mean,
        test=test,
    )
    return AgentEvaluation(
        outcome=outcome,  # type: ignore[arg-type]
        primary=primary,
        exploratory=exploratory,
        sessions=sessions,
        measurable=measurable,
        missing=missing,
        power=power
        or DetectableIC(
            periods=measurable,
            sd=0.1,
            level=0.05,
            power=0.8,
            mean=0.02,
            basis=DETECTABLE_IC_BASIS,
        ),
        adopted=adopted,
    )


STATISTIC: Final[StatisticTest] = StatisticTest(
    two_sided_p_value=0.04,
    one_sided_p_value=0.02,
    exact=False,
    sign_patterns=4096,
    random_seed=11,
)


@dataclass
class ReportWorld:
    """One test's repository, paths, C registration and step 0 registration."""

    repo: Path
    runtime: Path
    step0_runtime: Path
    registration: Path
    c_body: dict[str, Any]
    c_content: bytes
    c_sha: str
    step0_content: bytes
    evaluation: AgentEvaluation
    recorder: Recorder

    def arguments(self, *extra: str) -> list[str]:
        return [
            "--runtime-dir",
            str(self.runtime),
            "--step0-runtime-dir",
            str(self.step0_runtime),
            "--registration",
            str(self.registration),
            "--repo",
            str(self.repo),
            "--as-of",
            AS_OF.isoformat(),
            "--through",
            THROUGH.isoformat(),
            *extra,
        ]


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    c_body: dict[str, Any] | None = None,
    evaluation: AgentEvaluation | None = None,
    labels: Any = None,
    admitted: Any = None,
    step0_admitted: Any = None,
    step0_runtime: Path | None = None,
    committed_at: datetime = C_COMMITTED_AT,
    anchor: date | None = None,
) -> ReportWorld:
    """A report world whose admission, step 0 registration and evaluator are seams.

    `registry.admit_registered_code` and `registry.committed_registration` are replaced so the
    report's own logic is what fails or passes; the real ones are exercised by the refusal tests.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    step0 = step0_runtime if step0_runtime is not None else tmp_path / "step0"
    step0.mkdir(parents=True, exist_ok=True)
    body = _c_body(anchor=ANCHOR if anchor is None else anchor) if c_body is None else c_body
    content = (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    registration = tmp_path / "c-registration.json"
    registration.write_bytes(content)
    resolved_labels = SentinelLabels() if labels is None else labels
    fabricated = make_evaluation() if evaluation is None else evaluation
    recorder = Recorder(fabricated)
    monkeypatch.setattr(report, "evaluate", recorder)
    monkeypatch.setattr(report, "OutcomeLabels", lambda *args, **kwargs: resolved_labels)
    admitted_value = (
        admitted
        if admitted is not None
        else SimpleNamespace(
            content=content, registered=body, commit=C_COMMIT, committed_at=committed_at
        )
    )
    step0_value = (
        step0_admitted
        if step0_admitted is not None
        else SimpleNamespace(content=STEP0_CONTENT, registered=STEP0_BODY, commit=STEP0_COMMIT)
    )
    monkeypatch.setattr(
        report.registry,
        "admit_registered_code",
        lambda path, repository, *, also_bound: (repo, admitted_value),
    )
    monkeypatch.setattr(
        report.registry,
        "committed_registration",
        lambda path, repository: (repo, step0_value),
    )
    return ReportWorld(
        repo=repo,
        runtime=runtime,
        step0_runtime=step0,
        registration=registration,
        c_body=body,
        c_content=content,
        c_sha=hashlib.sha256(content).hexdigest(),
        step0_content=STEP0_CONTENT,
        evaluation=fabricated,
        recorder=recorder,
    )


def _llm_record(
    session: date,
    *,
    scored: tuple[str, ...] = (),
    abstained: tuple[str, ...] = (),
    registration_sha256: str = "unset",
) -> PredictionRecord:
    """One stored LLM primary record: the report reads its rows for the abstention rate."""
    as_of = session_publication_instant(session)
    declaration = AlphaModelDeclaration(
        name="c-primary",
        family=AGENT_LLM_FAMILY,
        horizon="5d",
        feature_version=C_CONFIG_ID,
        seed=7,
        code_commit=C_CODE_COMMIT,
        hyperparameters=tuple(
            sorted(
                (
                    ("scoring_set", "primary"),
                    ("registration_sha256", registration_sha256),
                )
            )
        ),
    )
    rows = [Prediction(ts_code=code, score=0.1) for code in scored]
    rows += [Prediction(ts_code=code, abstention="agent_abstained") for code in abstained]
    batch = PredictionBatch(
        as_of=as_of,
        predicted_at=as_of,
        artifact=AlphaModelArtifact(
            declaration=declaration,
            feature_ids=("test/v1",),
            training_cutoff=as_of,
            training_example_count=max(1, len(rows)),
        ),
        predictions=tuple(sorted(rows, key=lambda row: row.ts_code)),
    )
    return PredictionRecord(
        batch=batch,
        outcome_known_at=as_of + timedelta(days=3),
        recorded_at=as_of,
    )


def _write_record(runtime: Path, record: PredictionRecord) -> None:
    path = runtime / "predictions" / f"{record.record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(exclude_computed_fields=True), encoding="utf-8")


def _write_journal(
    runtime: Path,
    session: date,
    *,
    model: str = "deepseek-flash",
    fingerprint: str = "fp-a",
) -> None:
    path = runtime / "agent_evening" / f"{session.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "openalpha-agent-evening/v1",
                "session": session.isoformat(),
                "llm_family": {
                    "status": "filed",
                    "exchanges": [{"model": model, "system_fingerprint": fingerprint}],
                },
            }
        ),
        encoding="utf-8",
    )


# --- the refusals -------------------------------------------------------------------------------


def test_a_dry_week_marked_runtime_is_refused_before_anything_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """D7/D8: a scratch runtime is never read as C's forward sample, and the refusal comes first."""
    world = _world(tmp_path, monkeypatch)
    marker = world.runtime / "c-dry-week-scratch.json"
    marker.write_text(
        json.dumps({"schema": "openalpha-c-dry-week-scratch/v1", "evenings": ["2026-01-05"]}),
        encoding="utf-8",
    )

    def admission_must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a dry-week runtime is refused before admission is attempted")

    monkeypatch.setattr(report.registry, "admit_registered_code", admission_must_not_run)

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    err = capsys.readouterr().err

    assert code == 2
    assert "dry-week" in err
    assert not (world.runtime / "reports").exists()


def test_a_missing_or_uncommitted_registration_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report admits the committed registration with the real guard, or refuses by name."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    step0 = tmp_path / "step0"
    step0.mkdir()
    arguments = [
        "--runtime-dir",
        str(runtime),
        "--step0-runtime-dir",
        str(step0),
        "--registration",
        str(tmp_path / "absent.json"),
        "--repo",
        str(tmp_path),
        "--as-of",
        AS_OF.isoformat(),
    ]

    code = report.main(arguments, clock=lambda: AS_OF)
    err = capsys.readouterr().err

    assert code == 2
    assert "RegistrationNotCommittedError" in err

    uncommitted = tmp_path / "uncommitted.json"
    uncommitted.write_text("{}\n", encoding="utf-8")
    uncommitted_arguments = [*arguments[:5], str(uncommitted), *arguments[6:]]

    code = report.main(uncommitted_arguments, clock=lambda: AS_OF)
    err = capsys.readouterr().err

    assert code == 2
    assert "RegistrationNotCommittedError" in err or "not a git repository" in err


def test_a_step0_registration_whose_recorded_digest_differs_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Step 0 block records what C reads; a store/registration disagreement is a refusal."""
    wrong = _c_body(step0_sha="0" * 64)
    world = _world(
        tmp_path,
        monkeypatch,
        c_body=wrong,
    )

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    err = capsys.readouterr().err

    assert code == 2
    assert "registration_sha256" in err or "sha256" in err


def test_a_step0_registration_of_another_config_id_or_seed_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """All three recorded facts must agree: the path's bytes, its `config_id` and its seed."""
    for change in (
        {"step0_config_id": "another-config"},
        {"step0_seed": STEP0_SEED + 1},
    ):
        case = tmp_path / next(iter(change))
        case.mkdir()
        body = _c_body(**change)
        world = _world(case, monkeypatch, c_body=body)

        code = report.main(world.arguments(), clock=lambda: AS_OF)
        err = capsys.readouterr().err

        assert code == 2, change
        assert next(iter(change)).replace("step0_", "") in err, change


# --- what the report prints ---------------------------------------------------------------------


def test_before_the_window_the_report_prints_counts_and_a_descriptive_mean_without_a_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Before the window matures a report may describe the values and may not test them.

    A p-value before the registered window is a second look; the report prints the counts and a
    descriptive mean, and the registered test appears only after every position matured.
    """
    world = _world(tmp_path, monkeypatch)

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0
    assert "not_yet" in out
    assert "descriptive" in out
    for forbidden in ("p-value", "p =", "two-sided", "sign-flip", "alpha"):
        assert forbidden not in out, f"before the window the report said {forbidden!r}"


@pytest.mark.parametrize(
    ("outcome", "expected", "forbidden"),
    [
        ("not_yet", "not_yet", "candidate_increment"),
        ("candidate_increment", "候选", "futility_stop"),
        ("no_increment_detected", "no_increment_detected", "candidate_increment"),
        ("futility_stop", "futility", "no_increment_detected"),
        ("not_measurable", "not measurable", "no_increment_detected"),
    ],
)
def test_the_five_outcomes_print_their_fixed_wording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    expected: str,
    forbidden: str,
) -> None:
    """D5/D8: each of the five outcomes is printed with its own fixed wording.

    `not_measurable` is exactly the evaluator's wording and never 'no evidence'; the candidate
    outcome keeps the candidate words the whole research file uses.
    """
    evaluation_for_outcome = {
        "not_yet": make_evaluation(outcome="not_yet"),
        "candidate_increment": make_evaluation(
            outcome="candidate_increment", mean=0.4, test=STATISTIC
        ),
        "no_increment_detected": make_evaluation(
            outcome="no_increment_detected",
            mean=-0.1,
            test=StatisticTest(
                two_sided_p_value=0.9,
                one_sided_p_value=0.95,
                exact=True,
                sign_patterns=512,
                random_seed=None,
            ),
        ),
        "futility_stop": make_evaluation(outcome="futility_stop", mean=-0.2),
        "not_measurable": make_evaluation(
            outcome="not_measurable", mean=None, measurable=2, sessions=6
        ),
    }[outcome]
    world = _world(tmp_path, monkeypatch, evaluation=evaluation_for_outcome)

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0
    assert expected in out
    assert forbidden not in out
    if outcome == "not_measurable":
        assert NOT_MEASURABLE_WORDING in out
        assert out.count("no evidence") == NOT_MEASURABLE_WORDING.count("no evidence")
    if outcome == "candidate_increment":
        assert "not evidence" in out


def test_the_primary_test_is_printed_only_after_the_window_and_carries_its_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After the window the one test is printed: mean, both readings, level, exact/randomized."""
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(outcome="candidate_increment", mean=0.4, test=STATISTIC),
    )

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0
    assert "two-sided p 0.04" in out
    assert "one-sided p 0.02" in out
    assert "level 0.05" in out
    assert "randomized" in out
    assert "candidate_increment" in out


def test_the_adopted_d5_rules_are_printed_after_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rules the evaluation applied are stated with the outcome, never as a proposal."""
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(outcome="candidate_increment", mean=0.4, test=STATISTIC),
    )

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    out = capsys.readouterr().out

    for rule in D5_WORDINGS:
        assert rule in out, "the adopted D5 rules are always stated with the outcome"
    assert "PROVISIONAL" not in out


def test_power_n_and_the_registered_d1_verdict_are_always_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sample size, the detectable mean and its design basis print either side of a window."""
    for name, fabricated in (
        ("before", make_evaluation(outcome="not_yet", measurable=2)),
        ("after", make_evaluation(outcome="candidate_increment", mean=0.4, test=STATISTIC)),
    ):
        case = tmp_path / name
        case.mkdir()
        world = _world(case, monkeypatch, evaluation=fabricated)

        assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
        out = capsys.readouterr().out

        assert "n=2" in out, name
        assert "detectable" in out, name
        assert "design" in out and "observed power" in out, name
        assert f"D1 power verdict: {POWER_VERDICT}" in out, name
        assert POWER_SAMPLE in out, name


def test_the_exploratory_block_is_labelled_and_read_against_the_registered_q(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exploratory arms are labelled EXPLORATORY, never pooled, and read below/at-or-above q."""
    arms = (
        ArmReading(
            family=AGENT_RULE_FAMILY,
            scoring_set="coverage",
            horizon=1,
            positions=6,
            measurable=6,
            missing=0,
            unmeasured=0,
            mean=0.3,
            test=STATISTIC,
            q_value=0.04,
            rejected=True,
        ),
        ArmReading(
            family=AGENT_LLM_FAMILY,
            scoring_set="primary",
            horizon=5,
            positions=2,
            measurable=1,
            missing=0,
            unmeasured=1,
            mean=0.1,
            test=None,
            q_value=0.4,
            rejected=False,
        ),
    )
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(
            outcome="candidate_increment",
            mean=0.4,
            test=STATISTIC,
            exploratory=arms,
        ),
    )

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    out = capsys.readouterr().out

    assert out.count("EXPLORATORY") == len(arms)
    assert "below q" in out and "at or above q" in out
    assert "q=0.1" in out, "the registered q is the threshold the reading is compared to"
    assert "never pooled" in out.lower()


def test_the_abstention_rate_is_read_from_the_stored_llm_primary_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Abstentions are counted from the filed rows, and the rate is printed even at zero."""
    world = _world(tmp_path, monkeypatch)
    _write_record(
        world.runtime,
        _llm_record(
            SESSIONS[0],
            scored=("600001.SH", "600002.SH"),
            abstained=("600003.SH",),
            registration_sha256=world.c_sha,
        ),
    )
    _write_record(
        world.runtime,
        _llm_record(SESSIONS[1], scored=("600001.SH",), registration_sha256=world.c_sha),
    )

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    out = capsys.readouterr().out

    assert "1 abstained of 4 rows" in out
    assert "25.0%" in out
    assert "never a 0 score" in out


def test_model_alias_changes_and_missing_journals_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report names the model identities it saw, a change between them, and missing journals."""
    world = _world(tmp_path, monkeypatch)
    _write_journal(world.runtime, SESSIONS[0], fingerprint="fp-a")
    _write_journal(world.runtime, SESSIONS[1], fingerprint="fp-b")
    for session in SESSIONS[:3]:
        _write_record(
            world.runtime,
            _llm_record(session, scored=("600001.SH",), registration_sha256=world.c_sha),
        )

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    out = capsys.readouterr().out

    assert "deepseek-flash/fp-a" in out
    assert "deepseek-flash/fp-b" in out
    assert "changed: yes" in out
    assert f"missing: {SESSIONS[2].isoformat()}" in out


def test_the_report_names_the_records_witnessed_missing_and_the_primary_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report carries the window's positions, its gaps by session and the primary mean."""
    missing = (SessionIC(session=SESSIONS[2], n_scored=0, value=None, reason=GAP_NO_AGENT_RECORD),)
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(
            outcome="not_yet", mean=0.25, measurable=3, sessions=4, missing=missing
        ),
    )

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    out = capsys.readouterr().out

    assert "3 witnessed of 4 positions" in out
    assert f"{SESSIONS[2].isoformat()}: {GAP_NO_AGENT_RECORD}" in out
    assert "agent_llm_structured/v1/primary/h1" in out
    assert "mean 0.25" in out


# --- the evaluate seam and the read-only contract ------------------------------------------------


def test_evaluate_is_called_with_the_registered_identity_anchor_and_step0_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report measures what the registration recorded, and passes every bound value through."""
    labels = SentinelLabels()
    world = _world(tmp_path, monkeypatch, labels=labels)
    seen: list[tuple[Any, str, tuple[str, ...]]] = []
    step0_paths: list[Path] = []
    real_admit = report.registry.admit_registered_code

    def recording_admit(path: Path, repository: Path, *, also_bound: tuple[str, ...]) -> Any:
        seen.append((path, str(repository), tuple(also_bound)))
        return real_admit(path, repository, also_bound=also_bound)

    monkeypatch.setattr(report.registry, "admit_registered_code", recording_admit)
    real_committed = report.registry.committed_registration

    def recording_committed(path: Path, repository: Path) -> Any:
        step0_paths.append(path)
        return real_committed(path, repository)

    monkeypatch.setattr(report.registry, "committed_registration", recording_committed)

    assert report.main(world.arguments(), clock=lambda: AS_OF) == 0
    capsys.readouterr()

    assert seen and seen[0][2] == report.AGENT_SCRIPTS
    assert step0_paths == [world.repo / STEP0_PATH], "the recorded path is repo-relative"
    call = world.recorder.call
    assert call is not None
    assert call["registered_sha256"] == hashlib.sha256(world.c_content).hexdigest()
    assert call["anchor_session"] == ANCHOR
    assert call["through"] == THROUGH
    assert call["as_of"] == AS_OF
    assert call["agent_runtime_dir"] == world.runtime
    assert call["labels"] is labels
    assert call["book"] == BOOK
    step0_registered = call["step0_registered"]
    assert isinstance(step0_registered, RegisteredConfiguration)
    assert step0_registered.config_id == STEP0_CONFIG_ID
    assert step0_registered.registration_sha256 == STEP0_SHA
    assert step0_registered.seed == STEP0_SEED
    protocol = call["protocol"]
    assert protocol.min_scored == PROTOCOL_BLOCK["min_scored"]
    assert protocol.q == PROTOCOL_BLOCK["q"]
    assert call["agent_store"].path == world.runtime / "predictions"
    assert call["step0_store"].path == world.step0_runtime / "predictions"


def test_the_report_and_a_missing_record_set_still_prints_a_not_yet_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No records yet is an outcome (`not_yet`, n=0), not a refusal: the report still prints."""
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(outcome="not_yet", mean=None, measurable=0, sessions=0),
    )

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0
    assert "not_yet" in out
    assert "n=0" in out


def _on_sse(panel: Any) -> Any:
    """The generated panel on the SSE calendar: Step 0's adapter is `Literal["SSE"]`.

    `tests/panel_fixtures.py` builds its calendar under `SZSE`; only the calendar batch's
    subject is the exchange name, so re-subjecting that batch is the whole change (the same
    fixture adaptation `tests/unit/scripts/test_agent_evening.py` makes).
    """
    calendar_batch = panel.batch(TRADING_CALENDAR_DATASET)
    subjects = ("SSE",) * len(calendar_batch.subjects)
    return dataclasses.replace(
        panel,
        batches={
            **panel.batches,
            TRADING_CALENDAR_DATASET: dataclasses.replace(calendar_batch, subjects=subjects),
        },
        exchange="SSE",
    )


def test_the_step0_runtime_is_read_only_while_the_report_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report opens Step 0's store and panel read-only: its data files are byte-identical.

    A generated panel (`AGENTS.md` hard rule 6) and the real `OutcomeLabels` reader make this a
    wiring test of the read-only promise; only DuckDB catalog locks and sidecars may appear
    beside the data, exactly as the evening command's own read-only test allows.
    """
    panel = _on_sse(generate_panel(shapes=("daily.close_moves_between_sessions",)))
    store = PanelStore(tmp_path / "step0" / "panel")
    write_generated_panel(
        store,
        panel,
        datasets=(
            "trade_cal",
            "stock_basic",
            "adj_factor",
            "daily",
            "daily_basic",
            "stk_limit",
            "suspend_d",
        ),
    )
    before = _files(tmp_path / "step0")
    world = _world(tmp_path, monkeypatch)
    # Keep the real label reader: the point of this test is the read-only panel access. The
    # evaluator is still the recorder, so the labels are constructed but not queried.
    real_labels = importlib.import_module("openalpha_cn.model_view").OutcomeLabels
    monkeypatch.setattr(report, "OutcomeLabels", real_labels)

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    capsys.readouterr()

    assert code == 0
    after = _files(tmp_path / "step0")
    assert after == before, "the report modified Step 0's runtime"


def _files(root: Path) -> dict[str, bytes]:
    """The files under `root`, less the DuckDB catalog locks and sidecars a read may create."""
    found: dict[str, bytes] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.endswith((".lock", ".wal", ".tmp")) or ".duckdb" in relative:
            continue
        found[relative] = path.read_bytes()
    return found


def test_the_json_report_repeats_the_printed_facts_and_print_only_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json PATH` writes the same facts atomically; without it the report writes no file."""
    world = _world(
        tmp_path,
        monkeypatch,
        evaluation=make_evaluation(outcome="candidate_increment", mean=0.4, test=STATISTIC),
    )
    destination = tmp_path / "out" / "agent-report.json"
    printed_only = tmp_path / "printed-only"
    printed_only.mkdir()

    code = report.main(world.arguments("--json", str(destination)), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0
    assert destination.is_file()
    view = json.loads(destination.read_text(encoding="utf-8"))
    assert view["schema"] == report.REPORT_SCHEMA
    assert view["outcome"] == "candidate_increment"
    assert view["outcome_wording"] in out
    assert view["window"]["anchor_session"] == ANCHOR.isoformat()
    assert view["window"]["through"] == THROUGH.isoformat()
    assert view["primary"]["mean"] == 0.4
    assert view["power"]["mean"] == 0.02
    assert view["not_traded"] is True
    assert view["candidate"] is True
    assert view["adopted"] == list(D5_WORDINGS)
    assert list((tmp_path / "out").iterdir()) == [destination], "no partial file is left behind"

    # A print-only run writes nothing at all.
    solo = _world(printed_only, monkeypatch)
    before = _files(tmp_path)
    code = report.main(solo.arguments(), clock=lambda: AS_OF)
    capsys.readouterr()
    assert code == 0
    assert _files(tmp_path) == before


def test_the_report_script_reaches_no_agent_or_model_module() -> None:
    """A report that could run an agent or call a model is not a measurement of stored records.

    Plan C task 3.5's review rule: the script's imports reach neither `openalpha_cn.agents` nor
    `openalpha_cn.models`. The evaluator has its own grimp test; scripts are not part of that
    graph, so the AST of this one is read directly.
    """
    source = (ROOT / "scripts" / "agent_report.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = [
        name
        for name in imported
        if name == "openalpha_cn.agents"
        or name.startswith("openalpha_cn.agents.")
        or name == "openalpha_cn.models"
        or name.startswith("openalpha_cn.models.")
    ]
    assert forbidden == [], f"the report imports {forbidden}"
    assert "llm_provider" not in source, "the report assembles no model provider"


# --- F3: the window anchor is the first trading session after C's registration commit ----------


def test_the_registered_anchor_is_the_first_trading_session_after_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """D5: 120 forward sessions *after the registration commit*, checked exactly on the report side.

    A trading-day commit before the 16:30 publication anchors that same session (its outcome had
    not published when the registration was committed); a commit after the close anchors the next
    session; a Saturday commit anchors the next trading session. The writer's old "a weekday, at
    most 16 days old" rule allowed anchors before the commit, whose first positions can never
    have a C record and would be permanent gaps.
    """
    cases = (
        ("trading-day-before-the-close", C_COMMITTED_AT, ANCHOR),
        ("trading-day-after-the-close", datetime(2026, 1, 5, 16, 31, tzinfo=SHANGHAI), SESSIONS[1]),
        ("saturday", datetime(2026, 1, 3, 10, 0, tzinfo=SHANGHAI), ANCHOR),
    )
    for index, (name, committed_at, anchor) in enumerate(cases):
        case = tmp_path / f"{index}-{name}"
        case.mkdir()
        world = _world(case, monkeypatch, committed_at=committed_at, anchor=anchor)

        code = report.main(world.arguments(), clock=lambda: AS_OF)
        out = capsys.readouterr().out

        assert code == 0, (name, out)
        assert world.recorder.call is not None, name
        assert world.recorder.call["anchor_session"] == anchor, name


def test_a_holiday_commit_anchors_the_next_trading_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A commit on a non-trading weekday cannot anchor that day; the window starts after it."""
    holiday = date(2026, 1, 6)
    calendar = calendar_over(
        tuple(session for session in SESSIONS if session != holiday), first=date(2026, 1, 1)
    )
    labels = SentinelLabels(calendar)
    committed_at = datetime.combine(holiday, datetime.min.time()).replace(hour=10, tzinfo=SHANGHAI)
    expected = SESSIONS[2]
    world = _world(
        tmp_path,
        monkeypatch,
        labels=labels,
        committed_at=committed_at,
        anchor=expected,
    )

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    out = capsys.readouterr().out

    assert code == 0, out
    assert world.recorder.call is not None
    assert world.recorder.call["anchor_session"] == expected


@pytest.mark.parametrize("anchor", [SESSIONS[0] - timedelta(days=3), SESSIONS[1]])
def test_an_anchor_that_is_not_the_first_trading_session_after_the_commit_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    anchor: date,
) -> None:
    """The report refuses an anchor before, or later than, the commit's first trading session.

    The message names the commit instant, the expected session and the registered anchor, so an
    operator can see which of the three the registration got wrong. The evaluator is never
    called: a window read from a wrong anchor cannot be un-read.
    """
    world = _world(tmp_path, monkeypatch, anchor=anchor)

    code = report.main(world.arguments(), clock=lambda: AS_OF)
    err = capsys.readouterr().err

    assert code == 2
    assert C_COMMITTED_AT.isoformat() in err
    assert ANCHOR.isoformat() in err
    assert anchor.isoformat() in err
    assert world.recorder.call is None, "a wrong anchor is refused before the evaluation"


# --- F8: every printed figure covers the registered window ------------------------------------


def test_the_abstention_and_model_alias_figures_are_window_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F8: the abstention rate and the model identities cover anchor..through only.

    A record or a journal outside the registered window belongs to another reading: counting it
    would print a rate and an alias history the primary arm never used. The hand-computed window
    facts are two scored rows and one abstention (1/3), one identity and no missing journal.
    """
    world = _world(tmp_path, monkeypatch)
    before_anchor = date(2026, 1, 2)
    after_through = date(2026, 1, 13)
    _write_record(
        world.runtime,
        _llm_record(
            before_anchor,
            scored=("600001.SH",),
            abstained=("600002.SH", "600003.SH"),
            registration_sha256=world.c_sha,
        ),
    )
    _write_record(
        world.runtime,
        _llm_record(
            after_through,
            scored=("600001.SH", "600002.SH"),
            abstained=("600003.SH",),
            registration_sha256=world.c_sha,
        ),
    )
    _write_record(
        world.runtime,
        _llm_record(
            SESSIONS[0],
            scored=("600001.SH", "600002.SH"),
            registration_sha256=world.c_sha,
        ),
    )
    _write_record(
        world.runtime,
        _llm_record(SESSIONS[5], abstained=("600003.SH",), registration_sha256=world.c_sha),
    )
    _write_journal(world.runtime, before_anchor, fingerprint="fp-outside-before")
    _write_journal(world.runtime, after_through, fingerprint="fp-outside-after")
    _write_journal(world.runtime, SESSIONS[0], fingerprint="fp-window")
    _write_journal(world.runtime, SESSIONS[5], fingerprint="fp-window")
    destination = tmp_path / "report.json"

    code = report.main(world.arguments("--json", str(destination)), clock=lambda: AS_OF)
    out = capsys.readouterr().out
    view = json.loads(destination.read_text(encoding="utf-8"))

    assert code == 0
    assert "2 scored, 1 abstained of 3 rows (33.3%)" in out
    assert view["abstention"] == {
        "scored": 2,
        "abstained": 1,
        "rows": 3,
        "rate": pytest.approx(1 / 3),
    }
    assert view["model_aliases"]["identities"] == [
        {
            "model": "deepseek-flash",
            "system_fingerprint": "fp-window",
            "sessions": [SESSIONS[0].isoformat(), SESSIONS[5].isoformat()],
        }
    ]
    assert view["model_aliases"]["changed"] is False
    assert view["model_aliases"]["missing_journals"] == []
    assert "fp-window" in out
    assert "fp-outside-before" not in out and "fp-outside-after" not in out
