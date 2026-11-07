"""`scripts/research/p7_reading.py` (`V2-P7-004`): the one way into the validation window and the
spent holdout -- a committed registration, a claim written before the read, one read. Every git
repository is a throwaway under `tmp_path` (`research_repo`); the measure is a stand-in, so no
panel is read here."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import _batch as batch
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from research_repo import commit_file, git, head

from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel, WindowReturn, build_label_window
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import write_trading_calendar
from openalpha_cn.strategy_survivor_facts import SurvivorFactsError

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


registry = _research_module("registry")
p7_reading = _research_module("p7_reading")
COMMITTED: Final[datetime] = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BOUND: Final[tuple[str, ...]] = (
    "src/openalpha_cn/strategy.py",
    "scripts/research/grid.py",
    "pyproject.toml",
    "uv.lock",
)
REQUEST: Final[dict[str, Any]] = {
    "first": "2022-01-04",
    "last": "2023-12-29",
    "stride": 20,
    "horizons": [20],
    "scores": ["composite"],
    "tails": [1, 2, 3],
    "loser_decile_count": 2,
}
CONFIG: Final[dict[str, Any]] = {
    "book": {"seed": 0},
    "transform": "cross_section_standard/v1",
    "exchange": "SSE",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "as_of": "2026-10-09T00:00:00+08:00",
}
AS_OF_REASON: Final[str] = (
    "the 2026-10-09 08:00 panel is the newest snapshot before the code freeze, and every "
    "revision published before it is part of what the reading may know"
)
RULE = p7_reading.StopRule(
    score="composite",
    horizon=20,
    minimum_periods=3,
    any_of=(
        p7_reading.StopClause(metric="rank_ic", mean_at_or_below=0.02),
        p7_reading.StopClause(metric="ew_minus_worst_decile", mean_at_or_below=0.0),
    ),
)
"""Test values only: the stop rule's numbers are the user's (V2-P7-024)."""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name in BOUND:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "initial", at=COMMITTED - timedelta(days=1))
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


def registered(
    repo: Path,
    *,
    commit: bool = True,
    role: str = "primary",
    rule: Any = RULE,
    window: str = "validation",
    reading: str = "v2-p7-024-validation",
    as_of_reason: str = AS_OF_REASON,
) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / f"{reading}.registration.json"
    p7_reading.register_reading(
        path=path,
        reading=reading,
        issue="V2-P7-024",
        role=role,
        window=window,  # type: ignore[arg-type]
        request=REQUEST,
        config=CONFIG,
        stop_rule=rule,
        code_commit=head(repo),
        as_of_reason=as_of_reason,
    )
    if commit:
        commit_file(repo, path, "register the reading", at=COMMITTED)
    return path


def document(*, n: int = 24, ic: float = 0.05, spread: float = 0.01) -> dict[str, Any]:
    return {
        "kind": "p7-diagnostic-read-once/v1",
        "summary": {
            "composite": {
                "h20": {
                    "rank_ic": {"n": n, "mean": ic},
                    "ew_minus_worst_decile": {"n": n, "mean": spread},
                }
            }
        },
    }


class Measure:
    """A stand-in measure: records the call and whether the claim already existed."""

    def __init__(self, result: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.result = document() if result is None else result
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []
        self.claim: Path | None = None

    def __call__(
        self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((window.name, self.claim is not None and self.claim.exists()))
        if self.fail:
            raise RuntimeError("the process died mid-read")
        return {**self.result, "binding": dict(binding)}


def run(registration: Path, repo: Path, measure: Measure, *, hours: int = 1) -> dict[str, Any]:
    measure.claim = registration.with_name(
        registration.name.replace(".registration.json", ".claim.json")
    )
    return p7_reading.read_once(
        registration,
        repo=repo,
        runtime_dir=repo / "runtime",
        clock=lambda: COMMITTED + timedelta(hours=hours),
        measure=measure,
        preflight=lambda body: (),
    )


def test_a_registration_is_written_once_and_names_its_code_commit(repo: Path) -> None:
    path = registered(repo, commit=False)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == p7_reading.READING_SCHEMA
    assert (body["window"], body["role"], body["code_commit"]) == (
        "validation",
        "primary",
        head(repo),
    )
    with pytest.raises(p7_reading.ReadingError, match="already"):
        p7_reading.register_reading(
            path=path,
            reading="v2-p7-024-validation",
            issue="V2-P7-024",
            role="primary",
            window="validation",
            request={**REQUEST, "stride": 10},
            config=CONFIG,
            stop_rule=RULE,
            code_commit=head(repo),
            as_of_reason=AS_OF_REASON,
        )


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"window": "research"}, "explore"),
        ({"role": "secondary"}, "no stop rule"),
        ({"rule": None}, "stop rule"),
    ],
)
def test_a_registration_that_could_not_be_read_once_is_refused(
    repo: Path, changes: dict[str, Any], said: str
) -> None:
    with pytest.raises(p7_reading.ReadingError, match=said):
        registered(repo, commit=False, **changes)


@pytest.mark.parametrize("reason", ["", "   ", "one line\nand a second"])
def test_a_registration_says_why_its_as_of_is_the_knowledge_it_reads(
    repo: Path, reason: str
) -> None:
    with pytest.raises(p7_reading.ReadingError, match="as_of"):
        registered(repo, commit=False, as_of_reason=reason)


def test_the_claim_is_written_before_the_read_and_the_result_after(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure()

    result = run(registration, repo, measure)

    assert measure.calls == [("validation", True)]
    claim = json.loads(measure.claim.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert claim["registration_sha256"] == hashlib.sha256(registration.read_bytes()).hexdigest()
    assert (
        result["binding"]["claim_sha256"] == hashlib.sha256(measure.claim.read_bytes()).hexdigest()
    )  # type: ignore[union-attr]
    assert result["stop_rule"]["outcome"] == "not triggered"
    assert registration.with_name("v2-p7-024-validation.result.json").exists()


def test_a_read_that_crashed_after_its_claim_is_the_one_read(repo: Path) -> None:
    registration = registered(repo)
    with pytest.raises(RuntimeError, match="died"):
        run(registration, repo, Measure(fail=True))

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        run(registration, repo, Measure(), hours=2)


def test_a_claim_once_committed_cannot_be_deleted_back_into_a_read(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure(fail=True)
    with pytest.raises(RuntimeError):
        run(registration, repo, measure)
    commit_file(repo, measure.claim, "the claim", at=COMMITTED + timedelta(hours=1))  # type: ignore[arg-type]
    git(repo, "rm", "-q", measure.claim.relative_to(repo).as_posix())  # type: ignore[union-attr]
    git(repo, "commit", "-q", "-m", "drop the claim", at=COMMITTED + timedelta(hours=2))

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="committed"):
        run(registration, repo, Measure(), hours=3)


def test_an_uncommitted_registration_refuses_the_read(repo: Path) -> None:
    with pytest.raises(registry.RegistrationNotCommittedError):
        run(registered(repo, commit=False), repo, Measure())


def test_an_edit_after_the_commit_refuses_the_read(repo: Path) -> None:
    registration = registered(repo)
    registration.write_text(
        registration.read_text(encoding="utf-8").replace("0.02", "0.01"), encoding="utf-8"
    )
    with pytest.raises(registry.RegistrationNotCommittedError, match="bytes"):
        run(registration, repo, Measure())


def test_changed_bound_code_refuses_the_read(repo: Path) -> None:
    registration = registered(repo)
    (repo / BOUND[0]).write_text("RULES = 2\n", encoding="utf-8")
    commit_file(repo, repo / BOUND[0], "change the code", at=COMMITTED + timedelta(minutes=5))
    with pytest.raises(registry.SourceChangedError):
        run(registration, repo, Measure())


def test_a_preflight_problem_refuses_before_any_claim(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure()
    measure.claim = registration.with_name("v2-p7-024-validation.claim.json")
    with pytest.raises(p7_reading.ReadingPreflightError, match="2023"):
        p7_reading.read_once(
            registration,
            repo=repo,
            runtime_dir=repo / "runtime",
            clock=lambda: COMMITTED,
            measure=measure,
            preflight=lambda body: ("daily has no 2023 partition",),
        )
    assert not measure.claim.exists() and measure.calls == []


@pytest.mark.parametrize(
    ("result", "outcome", "said"),
    [
        (document(ic=0.02), "triggered", "rank_ic"),
        (document(spread=0.0), "triggered", "ew_minus_worst_decile"),
        (document(n=2), "triggered", "unreadable"),
        (document(ic=0.021, spread=0.001), "not triggered", "not evidence"),
    ],
)
def test_the_stop_rule_is_applied_as_written(
    result: dict[str, Any], outcome: str, said: str
) -> None:
    evaluated = p7_reading.evaluate_stop_rule(RULE, result)
    assert evaluated["outcome"] == outcome
    assert said in json.dumps(evaluated)


def test_the_result_text_opens_with_the_read_once_banner(repo: Path) -> None:
    registration = registered(repo)
    run(registration, repo, Measure())
    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert text.startswith("PRE-REGISTERED READ-ONCE v2-p7-024-validation")
    assert p7_reading.NOT_TRIGGERED_WORDING in text


def test_the_result_says_which_knowledge_clock_the_reading_is_under(repo: Path) -> None:
    registration = registered(repo)

    run(registration, repo, Measure())

    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert "read under the knowledge at 2026-10-09T00:00:00+08:00" in text
    assert AS_OF_REASON in text
    assert "not a replay at each session's publication instant" in text


def test_only_a_read_once_document_bound_to_this_claim_is_written(repo: Path) -> None:
    """A measure that hands back an exploratory document (or one bound to another claim) is not
    this read's result: refused by name, and the read is spent -- the claim stays."""
    registration = registered(repo)
    explored = {**document(), "kind": "p7-diagnostic-exploratory/v1"}

    with pytest.raises(p7_reading.ReadingConfigurationError, match="read-once"):
        run(registration, repo, Measure(explored))
    assert registration.with_name("v2-p7-024-validation.claim.json").exists()
    assert not registration.with_name("v2-p7-024-validation.result.json").exists()


# --- beyond the plan's list: the rest of what `read_once` and `register_reading` promise ----------

p7_windows = _research_module("p7_windows")
p7_diagnostics = _research_module("p7_diagnostics")
REAL_IMPORTED_P7_SCRIPTS: Final = p7_reading._imported_p7_scripts
SECOND_USE_REQUEST: Final[dict[str, Any]] = {**REQUEST, "first": "2024-01-02", "last": "2026-09-24"}
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


def claim_of(registration: Path) -> Path:
    return registration.with_name(registration.name.replace(".registration.json", ".claim.json"))


def register(path: Path, *, runtime_dir: Path | None = None, **changes: Any) -> str:
    """`register_reading` with this module's values, any of them changed."""
    arguments: dict[str, Any] = {
        "path": path,
        "reading": "v2-p7-024-validation",
        "issue": "V2-P7-024",
        "role": "primary",
        "window": "validation",
        "request": REQUEST,
        "config": CONFIG,
        "stop_rule": RULE,
        "code_commit": "0123456789abcdef0123456789abcdef01234567",
        "as_of_reason": AS_OF_REASON,
        "runtime_dir": runtime_dir,
    }
    return p7_reading.register_reading(**{**arguments, **changes})  # type: ignore[no-any-return]


class Refusing(Measure):
    """A measure the data refuses: the refusal it raises is `make()`'s."""

    def __init__(self, make: Any) -> None:
        super().__init__()
        self.make = make

    def __call__(
        self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((window.name, self.claim is not None and self.claim.exists()))
        raise self.make()


def test_a_data_refusal_is_the_reads_recorded_error_and_closes_the_gate(repo: Path) -> None:
    registration = registered(repo)
    measure = Refusing(
        lambda: SurvivorFactsError("the panel holds no daily partition at or before 2022")
    )

    result = run(registration, repo, measure)

    assert measure.calls == [("validation", True)]
    assert result["error"].startswith("SurvivorFactsError: the panel holds no daily partition")
    assert (
        result["binding"]["claim_sha256"]
        == hashlib.sha256(claim_of(registration).read_bytes()).hexdigest()
    )
    assert result["stop_rule"]["outcome"] == "triggered"
    assert result["stop_rule"]["reason"].startswith("unreadable")
    written = registration.with_name("v2-p7-024-validation.result.json")
    assert json.loads(written.read_text(encoding="utf-8")) == result
    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert result["error"] in text and p7_reading.STOP_TRIGGERED_WORDING in text
    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        run(registration, repo, Measure(), hours=2)


@pytest.mark.parametrize("link", ["cause", "context"])
def test_a_window_refusal_behind_a_data_refusal_propagates_and_writes_no_result(
    repo: Path, link: str
) -> None:
    registration = registered(repo)
    breach = p7_windows.DiagnosticWindowError(
        "daily [2024] is past the validation window's last year"
    )

    def laundered() -> Exception:
        wrapper = SurvivorFactsError("a loader wrapped what the door raised")
        if link == "cause":
            wrapper.__cause__ = breach
        else:
            wrapper.__context__ = breach
        return wrapper

    with pytest.raises(p7_windows.DiagnosticWindowError) as raised:
        run(registration, repo, Refusing(laundered))

    assert raised.value is breach
    assert claim_of(registration).exists()
    assert not registration.with_name("v2-p7-024-validation.result.json").exists()


def test_a_result_once_committed_without_its_claim_cannot_be_deleted_back_into_a_read(
    repo: Path,
) -> None:
    registration = registered(repo)
    run(registration, repo, Measure())
    result = registration.with_name("v2-p7-024-validation.result.json")
    commit_file(repo, result, "the result alone", at=COMMITTED + timedelta(hours=1))
    git(repo, "rm", "-q", result.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop the result", at=COMMITTED + timedelta(hours=2))
    claim_of(registration).unlink()
    window_claim_of(repo).unlink(missing_ok=True)
    registration.with_name("v2-p7-024-validation.result.txt").unlink()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="a result was committed"):
        run(registration, repo, Measure(), hours=3)


# --- one read per window, whatever reading names it (fix round 1, ruling 1) ----------------------


def window_claim_of(repo: Path, window: str = "validation") -> Path:
    return repo / "docs" / "research" / "p7-readings" / f"{window}.window-claim.json"


def test_the_window_is_claimed_beside_the_reading_and_before_the_read(repo: Path) -> None:
    registration = registered(repo)
    seen: list[bool] = []

    class Watching(Measure):
        def __call__(
            self, body: Mapping[str, Any], window: Any, binding: Mapping[str, Any]
        ) -> dict[str, Any]:
            seen.append(window_claim_of(repo).exists())
            return super().__call__(body, window, binding)

    run(registration, repo, Watching())

    assert seen == [True]
    claimed = json.loads(window_claim_of(repo).read_text(encoding="utf-8"))
    assert claimed["reading"] == "v2-p7-024-validation"
    assert claimed["registration_sha256"] == hashlib.sha256(registration.read_bytes()).hexdigest()
    assert claimed["claimed_at"] == (COMMITTED + timedelta(hours=1)).isoformat()


def test_a_second_reading_of_a_window_whose_first_claim_is_on_disk_is_refused(repo: Path) -> None:
    """The reviewer's probe: the first reading's claim and results committed, a second
    registration of the same window under another name -- refused, and nothing measured."""
    first = registered(repo)
    run(first, repo, Measure())
    for name in ("claim.json", "result.json", "result.txt"):
        commit_file(
            repo,
            first.with_name(f"v2-p7-024-validation.{name}"),
            f"commit {name}",
            at=COMMITTED + timedelta(hours=2),
        )
    second = registered(repo, reading="v2-p7-024-validation-again")
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="validation window"):
        run(second, repo, measure, hours=4)
    assert measure.calls == [] and not claim_of(second).exists()


def test_a_second_reading_of_a_window_whose_first_claims_were_committed_and_deleted_is_refused(
    repo: Path,
) -> None:
    first = registered(repo)
    run(first, repo, Measure())
    written = [
        window_claim_of(repo),
        *(
            first.with_name(f"v2-p7-024-validation.{name}")
            for name in ("claim.json", "result.json", "result.txt")
        ),
    ]
    for path in written:
        commit_file(repo, path, f"commit {path.name}", at=COMMITTED + timedelta(hours=2))
    for path in written:
        git(repo, "rm", "-q", path.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop every trace", at=COMMITTED + timedelta(hours=3))
    second = registered(repo, reading="v2-p7-024-validation-again")
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="window claim was committed"):
        run(second, repo, measure, hours=4)
    assert measure.calls == []


def test_a_window_claim_left_by_a_crashed_first_attempt_refuses_the_second(repo: Path) -> None:
    """A first attempt that claimed the window and died, its reading's claim then deleted (it was
    never committed): the window's claim still holds, for this reading and for any other."""
    first = registered(repo)
    with pytest.raises(RuntimeError, match="died"):
        run(first, repo, Measure(fail=True))
    claim_of(first).unlink()
    other = registered(repo, reading="v2-p7-024-validation-again")

    for registration in (first, other):
        measure = Measure()
        with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="validation window"):
            run(registration, repo, measure, hours=2)
        assert measure.calls == []


def test_the_second_use_window_is_claimed_once_too(repo: Path) -> None:
    paths = []
    for reading in ("v2-p7-024-second-use", "v2-p7-024-second-use-again"):
        path = repo / "docs" / "research" / "p7-readings" / f"{reading}.registration.json"
        register(
            path,
            reading=reading,
            role="secondary",
            window="second_use",
            request=SECOND_USE_REQUEST,
            stop_rule=None,
            code_commit=head(repo),
        )
        commit_file(repo, path, f"register {reading}", at=COMMITTED)
        paths.append(path)
    # The second_use window waits for the validation window's committed claim (V2-P7-024 item 3).
    write_validation_window_claim(repo, commit=True)
    run(paths[0], repo, Measure())
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="second_use window"):
        run(paths[1], repo, measure, hours=2)
    assert measure.calls == [] and window_claim_of(repo, "second_use").exists()


# --- every claim naming the window witnesses its one read (fix round 2, finding 1) ---------------


def first_read_then_its_own_files_committed(repo: Path) -> Path:
    """The first reading read, its claim and results committed (`v2-p7-024-validation.*`), and
    the differently named window claim never committed and then gone -- a `git clean`, another
    worktree, a fresh clone."""
    first = registered(repo)
    run(first, repo, Measure())
    for name in ("claim.json", "result.json", "result.txt"):
        commit_file(
            repo,
            first.with_name(f"v2-p7-024-validation.{name}"),
            f"commit {name}",
            at=COMMITTED + timedelta(hours=2),
        )
    window_claim_of(repo).unlink()
    return first


def test_a_committed_reading_claim_naming_the_window_refuses_another_reading(repo: Path) -> None:
    """The re-reviewer's probe A: the window claim is gone, the first reading's claim -- which
    names the window -- is in the tree; a second registration of the window measures nothing."""
    first_read_then_its_own_files_committed(repo)
    second = registered(repo, reading="v2-p7-024-validation-again")
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="names the validation window"):
        run(second, repo, measure, hours=4)
    assert measure.calls == [] and not claim_of(second).exists()
    assert not window_claim_of(repo).exists()


def test_a_reading_claim_naming_the_window_committed_and_then_deleted_still_refuses(
    repo: Path,
) -> None:
    first = first_read_then_its_own_files_committed(repo)
    for name in ("claim.json", "result.json", "result.txt"):
        git(
            repo,
            "rm",
            "-q",
            first.with_name(f"v2-p7-024-validation.{name}").relative_to(repo).as_posix(),
        )
    git(repo, "commit", "-q", "-m", "drop the first reading", at=COMMITTED + timedelta(hours=3))
    second = registered(repo, reading="v2-p7-024-validation-again")
    measure = Measure()

    with pytest.raises(
        p7_reading.ReadingAlreadyClaimedError,
        match="a claim naming the validation window was committed",
    ):
        run(second, repo, measure, hours=4)
    assert measure.calls == []


@pytest.mark.parametrize("committed", [False, True])
def test_a_claim_file_that_cannot_be_read_refuses_the_read(repo: Path, committed: bool) -> None:
    """It might name this window: the window rule fails closed, on disk and in history."""
    stray = repo / "docs" / "research" / "p7-readings" / "stray.claim.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not a claim\n", encoding="utf-8")
    if committed:
        commit_file(repo, stray, "a stray claim", at=COMMITTED - timedelta(hours=1))
        git(repo, "rm", "-q", stray.relative_to(repo).as_posix())
        git(repo, "commit", "-q", "-m", "drop it", at=COMMITTED - timedelta(minutes=30))
    registration = registered(repo)
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="cannot be read as a claim"):
        run(registration, repo, measure)
    assert measure.calls == [] and not claim_of(registration).exists()


def test_a_claim_of_another_window_does_not_spend_this_one(repo: Path) -> None:
    """A committed claim naming the second_use window does not stop the validation window's read.
    (The second_use *reading* cannot come first any more -- V2-P7-024 item 3 -- so the claim is
    hand-written here, which is the artifact the ordering guard reads.)"""
    secondary_claim = repo / "docs" / "research" / "p7-readings" / "second_use.window-claim.json"
    secondary_claim.parent.mkdir(parents=True, exist_ok=True)
    secondary_claim.write_text(
        json.dumps({"reading": "some-secondary", "window": "second_use"}) + "\n",
        encoding="utf-8",
    )
    commit_file(repo, secondary_claim, "a second_use claim", at=COMMITTED)
    measure = Measure()

    run(registered(repo), repo, measure, hours=2)

    assert measure.calls == [("validation", True)]


def test_the_result_text_says_the_claims_must_be_committed(repo: Path) -> None:
    registration = registered(repo)
    run(registration, repo, Measure())
    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert (
        "commit v2-p7-024-validation.claim.json and validation.window-claim.json, and keep that "
        "commit in this repository's refs"
    ) in text


# --- history git hides by default: merges, stashes, root commits, shallow clones (fix round 3) ---


def files_of(first: Path, repo: Path) -> list[Path]:
    """The window claim, the first reading's claim and its two results."""
    return [
        window_claim_of(repo),
        *(
            first.with_name(f"v2-p7-024-validation.{name}")
            for name in ("claim.json", "result.json", "result.txt")
        ),
    ]


def refused(registration: Path, repo: Path, *, hours: int, said: str) -> None:
    measure = Measure()
    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match=said):
        run(registration, repo, measure, hours=hours)
    assert measure.calls == []


def test_claims_added_and_deleted_on_a_merged_and_deleted_branch_still_refuse(
    repo: Path,
) -> None:
    """The re-reviewer's E2: the first read on a branch, its claims committed and then removed
    there, the branch merged with a merge commit and deleted. Every commit is reachable from the
    main line, but git's default history simplification follows only the merge's TREESAME parent,
    so the branch's commits were invisible: the same registration and another one read again."""
    first = registered(repo)
    git(repo, "checkout", "-q", "-b", "side")
    run(first, repo, Measure())
    written = files_of(first, repo)
    for path in written:
        commit_file(repo, path, f"commit {path.name}", at=COMMITTED + timedelta(hours=2))
    for path in written:
        git(repo, "rm", "-q", path.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop every trace", at=COMMITTED + timedelta(hours=3))
    git(repo, "checkout", "-q", "-")
    git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "merge side",
        "side",
        at=COMMITTED + timedelta(hours=3),
    )
    git(repo, "branch", "-q", "-D", "side")

    # The per-file check finds the same registration's own claim; the window scan, another's.
    refused(first, repo, hours=4, said="a claim was committed at")
    refused(registered(repo, reading="v2-p7-024-validation-again"), repo, hours=5, said="window")


def test_a_claim_added_only_in_a_merge_commit_and_then_deleted_still_refuses(repo: Path) -> None:
    """The re-reviewer's E3: `git log` shows a merge commit's changes only with `-m`."""
    first = registered(repo)
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("x\n", encoding="utf-8")
    commit_file(repo, repo / "side.txt", "side", at=COMMITTED + timedelta(minutes=10))
    git(repo, "checkout", "-q", "-")
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "side")
    run(first, repo, Measure())
    git(repo, "add", claim_of(first).relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "merge side, with the claim", at=COMMITTED + timedelta(hours=2))
    git(repo, "rm", "-q", claim_of(first).relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop the claim", at=COMMITTED + timedelta(hours=3))
    for path in files_of(first, repo):
        path.unlink(missing_ok=True)

    refused(
        registered(repo, reading="v2-p7-024-validation-again"),
        repo,
        hours=4,
        said="a claim naming the validation window was committed",
    )


def test_a_stash_of_the_first_reads_files_still_refuses(repo: Path) -> None:
    """The re-reviewer's E7: `git stash -u` files untracked files in a root commit that only a
    parent of `refs/stash` reaches."""
    first = registered(repo)
    run(first, repo, Measure())
    git(repo, "stash", "-q", "-u")
    assert not window_claim_of(repo).exists() and not claim_of(first).exists()

    refused(registered(repo, reading="v2-p7-024-validation-again"), repo, hours=4, said="window")


def test_a_claim_in_a_root_commit_still_refuses_whatever_the_configuration(repo: Path) -> None:
    """A claim of another reading committed as the root of an unrelated history, in a repository
    configured with `log.showRoot = false`, under which `git log` lists no root commit's files
    (the reading pins it on the command line)."""
    git(repo, "config", "log.showRoot", "false")
    main = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(repo, "checkout", "-q", "--orphan", "elsewhere")
    git(repo, "rm", "-q", "-r", "--cached", ".")
    stray = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-elsewhere.claim.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text(json.dumps({"window": "validation", "reading": "elsewhere"}) + "\n", "utf-8")
    commit_file(repo, stray, "a root commit holding a claim", at=COMMITTED - timedelta(hours=2))
    git(repo, "checkout", "-q", "-f", main)
    assert not stray.exists()

    refused(registered(repo), repo, hours=1, said="a claim naming the validation window")


def test_a_claim_renamed_and_edited_in_one_commit_still_refuses(repo: Path) -> None:
    """A claim of another window renamed and edited into a claim of this one in a single commit,
    then deleted: `git log` pairs the two names into a rename (`R`, not `A` or `M`) unless asked
    not to, so the edited version would never be read."""
    readings = repo / "docs" / "research" / "p7-readings"
    readings.mkdir(parents=True, exist_ok=True)
    before, after = readings / "x.claim.json", readings / "y.claim.json"
    body = {"reading": "x", "registration_sha256": "0" * 64, "window": "second_use"}
    before.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    commit_file(repo, before, "a claim of the second-use window", at=COMMITTED - timedelta(hours=3))
    git(repo, "mv", before.relative_to(repo).as_posix(), after.relative_to(repo).as_posix())
    after.write_text(json.dumps({**body, "window": "validation"}, indent=2) + "\n", "utf-8")
    commit_file(repo, after, "renamed and edited", at=COMMITTED - timedelta(hours=2))
    git(repo, "rm", "-q", after.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop it", at=COMMITTED - timedelta(hours=1))

    refused(registered(repo), repo, hours=1, said="a claim naming the validation window")


def test_a_claim_with_a_non_ascii_name_committed_and_deleted_still_refuses(repo: Path) -> None:
    """The re-reviewer's E5: git quotes such a name under the default `core.quotePath`."""
    stray = repo / "docs" / "research" / "p7-readings" / "读.claim.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text(json.dumps({"window": "validation", "reading": "x"}) + "\n", encoding="utf-8")
    commit_file(repo, stray, "a claim", at=COMMITTED - timedelta(hours=1))
    git(repo, "rm", "-q", stray.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop it", at=COMMITTED - timedelta(minutes=30))

    refused(registered(repo), repo, hours=1, said="a claim naming the validation window")


def test_a_shallow_repository_refuses_the_read_and_names_the_remedy(repo: Path) -> None:
    """The re-reviewer's E4: a shallow clone's history stops at its cut, so a claim committed
    before it cannot be seen; the read is refused until `git fetch --unshallow`."""
    registration = registered(repo)
    (repo / ".git" / "shallow").write_text(head(repo) + "\n", encoding="utf-8")

    refused(registration, repo, hours=1, said="git fetch --unshallow")
    assert not claim_of(registration).exists() and not window_claim_of(repo).exists()


@pytest.mark.parametrize("named", ["Validation", " validation", "validation\n", "VALIDATION"])
def test_a_claim_naming_no_known_window_is_unreadable_and_refuses(repo: Path, named: str) -> None:
    """The re-reviewer's E6: a near-miss of a window's name is not another window."""
    stray = repo / "docs" / "research" / "p7-readings" / "hand.claim.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text(json.dumps({"window": named, "reading": "hand"}) + "\n", encoding="utf-8")

    refused(registered(repo), repo, hours=1, said="cannot be read as a claim")


def test_a_committed_claim_naming_no_known_window_refuses_too(repo: Path) -> None:
    stray = repo / "docs" / "research" / "p7-readings" / "hand.claim.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text(json.dumps({"window": "Validation", "reading": "hand"}) + "\n", "utf-8")
    commit_file(repo, stray, "a near-miss claim", at=COMMITTED - timedelta(hours=1))
    git(repo, "rm", "-q", stray.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop it", at=COMMITTED - timedelta(minutes=30))

    refused(registered(repo), repo, hours=1, said="cannot be read as a claim")


# --- reflogs, replacement objects and grafts (fix round 4) ----------------------------------------


def test_a_stash_pushed_down_by_a_later_one_still_refuses(repo: Path) -> None:
    """The re-reviewer's R1: the first read's files stashed, then any other stash. `git stash
    list` still shows the claims' stash as `stash@{1}`, but only the reflog of `refs/stash`
    reaches it."""
    first = registered(repo)
    run(first, repo, Measure())
    git(repo, "stash", "-q", "-u")
    (repo / "scratch.txt").write_text("x\n", encoding="utf-8")
    git(repo, "stash", "-q", "-u")
    assert len(git(repo, "stash", "list").strip().splitlines()) == 2

    refused(registered(repo, reading="v2-p7-024-validation-again"), repo, hours=4, said="window")


def test_claims_committed_and_then_reset_away_still_refuse_while_the_reflog_holds_them(
    repo: Path,
) -> None:
    """Committed, then `git reset --hard` past the commit: no ref reaches it, the reflog does."""
    first = registered(repo)
    run(first, repo, Measure())
    for path in files_of(first, repo):
        commit_file(repo, path, f"commit {path.name}", at=COMMITTED + timedelta(hours=2))
    git(repo, "reset", "-q", "--hard", "HEAD~4")
    assert not window_claim_of(repo).exists() and not claim_of(first).exists()

    refused(first, repo, hours=4, said="a claim was committed at")
    refused(registered(repo, reading="v2-p7-024-validation-again"), repo, hours=5, said="window")


def first_read_on_a_merged_and_deleted_branch(repo: Path) -> Path:
    """E2's history: the first read on `side`, its claims committed and removed there, `side`
    merged with a merge commit and deleted."""
    first = registered(repo)
    git(repo, "checkout", "-q", "-b", "side")
    run(first, repo, Measure())
    written = files_of(first, repo)
    for path in written:
        commit_file(repo, path, f"commit {path.name}", at=COMMITTED + timedelta(hours=2))
    for path in written:
        git(repo, "rm", "-q", path.relative_to(repo).as_posix())
    git(repo, "commit", "-q", "-m", "drop every trace", at=COMMITTED + timedelta(hours=3))
    git(repo, "checkout", "-q", "-")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side", at=COMMITTED + timedelta(hours=3))
    git(repo, "branch", "-q", "-D", "side")
    return first


@pytest.mark.parametrize("reflogs", ["kept", "expired"])
def test_a_replace_graft_that_hides_a_merged_branch_does_not_hide_its_claims(
    repo: Path, reflogs: str
) -> None:
    """The re-reviewer's R5: `git replace --graft <merge> <first parent>` rewrites no branch, but
    makes the merge look single-parent to every reader that honours replacement refs. With the
    reflogs expired, only the merge's own (replaced) parents reach the branch's commits."""
    first = first_read_on_a_merged_and_deleted_branch(repo)
    git(repo, "replace", "--graft", "HEAD", "HEAD^1")
    if reflogs == "expired":
        git(repo, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")

    refused(first, repo, hours=4, said="a claim was committed at")
    refused(registered(repo, reading="v2-p7-024-validation-again"), repo, hours=5, said="window")


def test_a_grafts_file_refuses_the_read(repo: Path) -> None:
    """The deprecated `info/grafts` file hides history the same way, and a repository holding
    one still says it is not shallow: it is refused by name."""
    first = first_read_on_a_merged_and_deleted_branch(repo)
    merge, parent = git(repo, "rev-parse", "HEAD", "HEAD^1").split()
    grafts = repo / ".git" / "info" / "grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{merge} {parent}\n", encoding="utf-8")

    refused(first, repo, hours=4, said="grafts")


def replaced_blob(repo: Path, tmp_path: Path, revision: str, content: str) -> None:
    """`git replace` the blob at `revision` with one holding `content`: no ref is rewritten."""
    replacement = tmp_path / "replacement"
    replacement.write_text(content, encoding="utf-8")
    original = git(repo, "rev-parse", revision).strip()
    other = git(repo, "hash-object", "-w", str(replacement)).strip()
    git(repo, "replace", original, other)


def test_a_replaced_claim_blob_does_not_hide_the_window(repo: Path, tmp_path: Path) -> None:
    """The first reading's committed claim replaced by one naming another window, then removed
    from the tree: the scan reads the claim's own bytes, not the replacement's."""
    first = first_read_then_its_own_files_committed(repo)
    for name in ("claim.json", "result.json", "result.txt"):
        git(
            repo,
            "rm",
            "-q",
            first.with_name(f"v2-p7-024-validation.{name}").relative_to(repo).as_posix(),
        )
    git(repo, "commit", "-q", "-m", "drop the first reading", at=COMMITTED + timedelta(hours=3))
    claimed = claim_of(first).relative_to(repo).as_posix()
    replaced_blob(repo, tmp_path, f"HEAD~1:{claimed}", json.dumps({"window": "second_use"}) + "\n")

    refused(
        registered(repo, reading="v2-p7-024-validation-again"),
        repo,
        hours=4,
        said="a claim naming the validation window",
    )


def test_a_replaced_registration_blob_is_not_the_committed_registration(
    repo: Path, tmp_path: Path
) -> None:
    """The registration's blob replaced by an edited one and the edit written to disk: the
    bytes on disk are compared with what was committed, not with the replacement."""
    registration = registered(repo)
    edited = registration.read_text(encoding="utf-8").replace("0.02", "0.01")
    replaced_blob(repo, tmp_path, f"HEAD:{registration.relative_to(repo).as_posix()}", edited)
    registration.write_text(edited, encoding="utf-8")
    measure = Measure()

    with pytest.raises(registry.RegistrationNotCommittedError, match="bytes"):
        run(registration, repo, measure)
    assert measure.calls == []


# --- the claim follows the filed registration, not the path handed in (ruling 2) ----------------


@pytest.mark.parametrize("inside", [True, False])
def test_a_symlink_to_the_registration_reads_nothing_twice(
    repo: Path, tmp_path: Path, inside: bool
) -> None:
    """The reviewer's probe: a link to the filed registration from elsewhere -- inside the
    repository or outside it -- finds the filed claim, and writes no claim beside itself."""
    registration = registered(repo)
    run(registration, repo, Measure())
    elsewhere = (repo / "scratch") if inside else (tmp_path / "outside")
    elsewhere.mkdir()
    link = elsewhere / registration.name
    link.symlink_to(registration)
    measure = Measure()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        run(link, repo, measure, hours=2)
    assert measure.calls == []
    assert not (elsewhere / "v2-p7-024-validation.claim.json").exists()


def test_a_p7_script_imported_from_elsewhere_refuses_the_read_before_any_claim(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = registered(repo)
    elsewhere = tmp_path / "elsewhere" / "scripts" / "research" / "p7_diagnostics.py"
    monkeypatch.setattr(p7_reading, "_imported_p7_scripts", lambda: (elsewhere,))
    measure = Measure()

    with pytest.raises(p7_reading.ForeignP7ScriptsError, match="p7_diagnostics"):
        run(registration, repo, measure)
    assert measure.calls == [] and not claim_of(registration).exists()
    assert not window_claim_of(repo).exists()


def test_the_p7_scripts_checked_are_the_three_this_process_imported() -> None:
    scripts = REAL_IMPORTED_P7_SCRIPTS()
    assert [script.name for script in scripts] == [
        "p7_windows.py",
        "p7_diagnostics.py",
        "p7_reading.py",
    ]
    assert all(script.parent == RESEARCH.resolve() for script in scripts)


def test_a_claim_that_appears_after_the_checks_loses_to_the_first_and_measures_nothing(
    repo: Path,
) -> None:
    """Two processes past step 4 at once: the claim is created exclusively, so the second loses."""
    registration = registered(repo)
    claim = claim_of(registration)
    measure = Measure()

    def racing(body: Mapping[str, Any]) -> tuple[str, ...]:
        claim.write_text("{}\n", encoding="utf-8")
        return ()

    with pytest.raises(p7_reading.ReadingAlreadyClaimedError, match="one read"):
        p7_reading.read_once(
            registration,
            repo=repo,
            runtime_dir=repo / "runtime",
            clock=lambda: COMMITTED,
            measure=measure,
            preflight=racing,
        )
    assert measure.calls == [] and claim.read_text(encoding="utf-8") == "{}\n"


def test_a_secondary_reading_of_the_spent_holdout_has_no_stop_rule_and_says_so(repo: Path) -> None:
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    register(
        path,
        reading="v2-p7-024-second-use",
        role="secondary",
        window="second_use",
        request=SECOND_USE_REQUEST,
        stop_rule=None,
        code_commit=head(repo),
    )
    commit_file(repo, path, "register the secondary reading", at=COMMITTED)
    # The second_use window waits for the validation window's committed claim (V2-P7-024 item 3).
    write_validation_window_claim(repo, commit=True)
    measure = Measure()

    result = run(path, repo, measure)

    assert measure.calls == [("second_use", True)]
    assert "stop_rule" not in result
    text = path.with_name("v2-p7-024-second-use.result.txt").read_text(encoding="utf-8")
    written = path.with_name("v2-p7-024-second-use.result.json").read_text(encoding="utf-8")
    assert text.startswith(
        "PRE-REGISTERED READ-ONCE v2-p7-024-second-use -- A SECONDARY, NON-CONFIRMATORY READING "
        "OF A SPENT HOLDOUT: second_use window 2024-01-02..2026-09-24\n"
    )
    assert "first out-of-sample" not in (text + written).lower()
    assert "no stop rule" in text


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"window": "holdout"}, "holdout"),
        ({"request": {**REQUEST, "last": "2024-01-02"}}, "past"),
        ({"request": {**REQUEST, "first": "2021-12-31"}}, "before"),
        ({"request": {**REQUEST, "power_means": [0.05]}}, "power_means"),
        ({"request": {k: v for k, v in REQUEST.items() if k != "tails"}}, "tails"),
        ({"request": {**REQUEST, "scores": ["momentum"]}}, "momentum"),
        ({"request": {**REQUEST, "horizons": [20.0]}}, "horizons"),
        ({"request": {**REQUEST, "tails": [1, 1]}}, "tail"),
        ({"config": {**CONFIG, "exchange": "SZSE"}}, "SSE"),
        ({"config": {**CONFIG, "as_of": "2026-10-09T00:00:00"}}, "timezone"),
        ({"config": {**CONFIG, "as_of": "2023-12-29T09:00:00+08:00"}}, "published"),
        ({"config": {**CONFIG, "transform": "no_such/v1"}}, "transform"),
        ({"config": {**CONFIG, "book": {"seed": 0, "holdings": 3}}}, "book"),
        ({"config": {**CONFIG, "min_coverage": 1.5}}, "min_coverage"),
        (
            {
                "stop_rule": p7_reading.StopRule(
                    score="composite", horizon=5, minimum_periods=3, any_of=RULE.any_of
                )
            },
            "horizon",
        ),
        (
            {
                "stop_rule": p7_reading.StopRule(
                    score="residual_vol_60/v1", horizon=20, minimum_periods=3, any_of=RULE.any_of
                )
            },
            "score",
        ),
        ({"code_commit": "0123456789abcdef0123456789abcdef01234567-dirty"}, "commit"),
        ({"code_commit": "unknown"}, "commit"),
        ({"issue": "P7-024"}, "issue"),
        ({"reading": "V2_P7_024"}, "reading"),
    ],
)
def test_a_registration_the_read_could_not_use_is_refused_before_it_is_written(
    tmp_path: Path, changes: dict[str, Any], said: str
) -> None:
    reading = changes.get("reading", "v2-p7-024-validation")
    path = tmp_path / f"{reading}.registration.json"
    with pytest.raises(p7_reading.ReadingError, match=said):
        register(path, **changes)
    assert not path.exists()


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        # The reviewer's probe: a Saturday first day, refused by the plan only after the claim.
        ({"request": {**REQUEST, "first": "2022-01-08"}}, "after the validation window's first"),
        ({"request": {**REQUEST, "first": "2022-03-01"}}, "whole window"),
        ({"request": {**REQUEST, "last": "2023-06-30"}}, "before the validation window's last"),
        (
            {"window": "second_use", "role": "secondary", "stop_rule": None},
            "second_use window's first",
        ),
        # The reviewer's probe: the book drops the worst two tenths, the request four.
        ({"request": {**REQUEST, "loser_decile_count": 4}}, "loser_decile_count 4 is not the book"),
        # The reviewer's probe: 519 weekdays at stride 20 and h20 hold at most 26 periods.
        (
            {
                "stop_rule": p7_reading.StopRule(
                    score="composite", horizon=20, minimum_periods=36, any_of=RULE.any_of
                )
            },
            "at most 26",
        ),
        (
            {
                "stop_rule": p7_reading.StopRule(
                    score="composite", horizon=20, minimum_periods=27, any_of=RULE.any_of
                )
            },
            "minimum_periods 27 cannot be reached",
        ),
    ],
)
def test_a_registration_that_could_only_spend_the_window_badly_is_refused_before_any_claim(
    tmp_path: Path, changes: dict[str, Any], said: str
) -> None:
    """Fix round 1, rulings 3a, 3c and 6: whatever needs no outcome data is checked before the
    window is claimed -- a reading covers its whole window, its request reads the book's survivors,
    and its stop rule's minimum can be reached."""
    path = tmp_path / "v2-p7-024-validation.registration.json"
    with pytest.raises(p7_reading.ReadingError, match=said):
        register(path, **changes)
    assert not path.exists()


def test_the_data_free_bound_is_the_weekday_bound(tmp_path: Path) -> None:
    """The data-free bound for this window: 519 weekdays, at most 26 non-overlapping periods at
    stride 20 and h20, at stride 5 and h20 (every fourth of 104 signals), and at stride 20 and h5.
    It is a necessary bound only: with no `runtime_dir` the exact calendar bound is not read, so
    a rule at 26 still registers here (the exact bound is checked before the claim)."""
    rule = p7_reading.StopRule(
        score="composite", horizon=20, minimum_periods=26, any_of=RULE.any_of
    )
    register(tmp_path / "v2-p7-024-validation.registration.json", stop_rule=rule)
    assert (
        p7_reading._most_periods(
            p7_windows.VALIDATION.first, p7_windows.VALIDATION.last, stride=20, horizon=20
        )
        == 26
    )
    assert (
        p7_reading._most_periods(
            p7_windows.VALIDATION.first, p7_windows.VALIDATION.last, stride=5, horizon=20
        )
        == 26
    )
    assert (
        p7_reading._most_periods(
            p7_windows.VALIDATION.first, p7_windows.VALIDATION.last, stride=20, horizon=5
        )
        == 26
    )


@pytest.fixture(scope="module")
def calendar_panel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A panel whose only dataset is `trade_cal` 2021..2023, every weekday a session: what the
    registration's exact bound reads, written at test time (hard rule 6)."""
    root = tmp_path_factory.mktemp("calendar")
    store = PanelStore(root / "panel")
    for year in (2021, 2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    return root


def _calendar_batch(year: int) -> Any:
    days = [date(year, 1, 1) + timedelta(days=offset) for offset in range(366)]
    days = [day for day in days if day.year == year]
    previous: list[str | None] = []
    seen: date | None = None
    for day in days:
        previous.append(None if seen is None else seen.isoformat())
        if day.weekday() < 5:
            seen = day
    published = midnight_shanghai(date(year, 1, 1))
    return batch(
        TRADING_CALENDAR_DATASET,
        subjects=["SSE"] * len(days),
        columns=[
            PanelColumn("cal_date", "string", tuple(day.isoformat() for day in days)),
            PanelColumn("is_open", "boolean", tuple(day.weekday() < 5 for day in days)),
            PanelColumn("pretrade_date", "string", tuple(previous)),
        ],
        event_time=[midnight_shanghai(day) for day in days],
        available_time=[published] * len(days),
        fetched_at=published,
    )


def test_a_minimum_above_what_the_panels_calendar_allows_is_refused_at_registration(
    calendar_panel: Path, tmp_path: Path
) -> None:
    """519 weekdays at stride 20 and h=20 give the data-free bound 26; the last signal day's
    label exits past 2023-12-29, so the calendar makes 25. 26 passes `_registration_of` and
    would spend the one read on 'unreadable'; 25 is the most a rule may ask for."""
    path = tmp_path / "v2-p7-024-validation.registration.json"
    too_many = p7_reading.StopRule(
        score="composite", horizon=20, minimum_periods=26, any_of=RULE.any_of
    )
    with pytest.raises(p7_reading.ReadingError, match="at most 25"):
        register(path, runtime_dir=calendar_panel, stop_rule=too_many)
    assert not path.exists()

    reachable = p7_reading.StopRule(
        score="composite", horizon=20, minimum_periods=25, any_of=RULE.any_of
    )
    register(path, runtime_dir=calendar_panel, stop_rule=reachable)
    assert path.exists()


def test_a_registration_whose_calendar_cannot_be_read_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "partial"
    store = PanelStore(root / "panel")
    for year in (2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    path = tmp_path / "v2-p7-024-validation.registration.json"

    with pytest.raises(p7_reading.ReadingError, match="2021"):
        register(path, runtime_dir=root)

    assert not path.exists()


def test_the_preflight_refuses_when_the_calendar_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact bound fails closed before the claim too: a calendar the bound needs but cannot
    read is a problem returned to `read_once`, never a skipped check."""
    root = tmp_path / "partial"
    store = PanelStore(root / "panel")
    for year in (2022, 2023):
        write_trading_calendar(store, _calendar_batch(year))
    # The catalogue completeness of the other datasets is another check's; this one is the
    # calendar's, so only it is exercised here.
    monkeypatch.setattr(p7_reading, "preflight_problems", lambda *args, **kwargs: ())
    body = {
        "request": REQUEST,
        "config": CONFIG,
        "stop_rule": p7_reading._rule_form(RULE),
    }

    problems = p7_reading._panel_preflight(root, p7_windows.VALIDATION)(body)

    assert problems and "calendar" in problems[0] and "2021" in problems[0]


@pytest.mark.parametrize(("stride", "horizon"), [(20, 20), (5, 20), (20, 5), (1, 2)])
def test_the_data_free_bound_is_never_below_what_the_calendar_allows(
    calendar_panel: Path, stride: int, horizon: int
) -> None:
    """Keeping `_most_periods` as an early refusal is sound only if it can never understate the
    calendar's maximum: sessions <= weekdays, embargoes only drop days, and ceil is monotone."""
    store = p7_windows.capped_panel_store(calendar_panel, p7_windows.VALIDATION)
    settings = p7_reading._settings_of(
        {**REQUEST, "stride": stride, "horizons": [horizon]}, CONFIG, p7_windows.VALIDATION
    )

    exact = p7_reading._exact_most_periods(
        store, window=p7_windows.VALIDATION, settings=settings, horizon=horizon
    )

    assert (
        p7_reading._most_periods(
            p7_windows.VALIDATION.first, p7_windows.VALIDATION.last, stride=stride, horizon=horizon
        )
        >= exact
    )


def test_a_registration_is_named_after_its_reading(tmp_path: Path) -> None:
    with pytest.raises(p7_reading.ReadingError, match=r"v2-p7-024-validation\.registration\.json"):
        register(tmp_path / "validation.json")


@pytest.mark.parametrize(
    ("clause", "said"),
    [
        ({"metric": "sharpe", "mean_at_or_below": 0.0}, "sharpe"),
        ({"metric": "rank_ic", "mean_at_or_below": float("nan")}, "finite"),
        ({"metric": "rank_ic", "mean_at_or_below": True}, "number"),
    ],
)
def test_a_stop_clause_names_a_measured_metric_and_a_finite_bound(
    clause: dict[str, Any], said: str
) -> None:
    with pytest.raises(p7_reading.ReadingError, match=said):
        p7_reading.StopClause(**clause)


@pytest.mark.parametrize(
    ("rule", "said"),
    [
        ({"minimum_periods": 0}, "minimum_periods"),
        ({"horizon": 0}, "horizon"),
        ({"any_of": ()}, "clause"),
        ({"score": ""}, "score"),
    ],
)
def test_a_stop_rule_has_a_score_a_horizon_a_minimum_and_a_clause(
    rule: dict[str, Any], said: str
) -> None:
    written = {"score": "composite", "horizon": 20, "minimum_periods": 3, "any_of": RULE.any_of}
    with pytest.raises(p7_reading.ReadingError, match=said):
        p7_reading.StopRule(**{**written, **rule})


def committed_by_hand(
    repo: Path,
    body: Mapping[str, Any],
    *,
    directory: str = "docs/research/p7-readings",
    name: str = "v2-p7-024-validation.registration.json",
) -> Path:
    path = repo / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    commit_file(repo, path, "a registration written by hand", at=COMMITTED)
    return path


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"window": "research"}, "explore"),
        ({"stop_rule": None}, "stop rule"),
        ({"schema": "openalpha-research-registration/v1"}, "openalpha-p7-reading-registration/v1"),
    ],
)
def test_a_hand_written_registration_the_read_could_not_use_is_refused_before_any_claim(
    repo: Path, change: dict[str, Any], said: str
) -> None:
    written = registered(repo, commit=False)
    body = {**json.loads(written.read_text(encoding="utf-8")), **change}
    written.unlink()
    registration = committed_by_hand(repo, body)
    measure = Measure()

    with pytest.raises(p7_reading.ReadingConfigurationError, match=said):
        run(registration, repo, measure)
    assert measure.calls == [] and not claim_of(registration).exists()
    assert not window_claim_of(repo).exists()


@pytest.mark.parametrize(
    ("directory", "name", "said"),
    [
        ("docs/research/elsewhere", "v2-p7-024-validation.registration.json", "p7-readings"),
        ("docs/research/p7-readings", "another-name.registration.json", "another-name"),
    ],
)
def test_a_registration_filed_anywhere_but_its_own_name_in_the_reading_directory_is_refused(
    repo: Path, directory: str, name: str, said: str
) -> None:
    written = registered(repo, commit=False)
    body = json.loads(written.read_text(encoding="utf-8"))
    written.unlink()
    registration = committed_by_hand(repo, body, directory=directory, name=name)
    measure = Measure()

    with pytest.raises(p7_reading.ReadingConfigurationError, match=said):
        run(registration, repo, measure)
    assert measure.calls == [] and list(registration.parent.glob("*.claim.json")) == []
    assert not window_claim_of(repo).exists()


class Catalogue:
    """Only what the preflight may ask: the years each dataset holds."""

    def __init__(self, years: Mapping[str, tuple[int, ...]]) -> None:
        self.years = years
        self.asked: list[str] = []

    def registered_years(self, dataset: str) -> tuple[int, ...]:
        self.asked.append(dataset)
        return self.years.get(dataset, ())


def test_the_preflight_names_each_year_a_dataset_lacks_the_year_before_the_window_included() -> (
    None
):
    """The calendar and the label reader also read the year before the window (2021 here), so a
    dataset that lacks only that year is a problem too (fix round 1, ruling 3b)."""
    catalogue = Catalogue(
        {"daily": (2021, 2022, 2023), "adj_factor": (2022,), "trade_cal": (2022, 2023)}
    )

    problems = p7_reading.preflight_problems(
        catalogue,  # type: ignore[arg-type]
        window=p7_windows.VALIDATION,
        datasets=("daily", "adj_factor", "suspend_d", "trade_cal"),
    )

    assert catalogue.asked == ["daily", "adj_factor", "suspend_d", "trade_cal"]
    assert len(problems) == 3
    assert problems[0].startswith("adj_factor lists no partition for [2021, 2023] ")
    assert problems[1].startswith("suspend_d lists no partition for [2021, 2022, 2023] ")
    assert problems[2].startswith("trade_cal lists no partition for [2021] ")


def test_the_default_preflight_asks_for_every_dataset_the_read_opens() -> None:
    datasets = p7_reading._preflight_datasets(SurvivorBookConfig(seed=0))
    assert {
        "trade_cal",
        "daily",
        "adj_factor",
        "suspend_d",
        "stk_limit",
        "namechange",
        "stock_basic",
    } <= set(datasets)
    assert len([name for name in datasets if name.startswith("factor_proc_")]) == 4
    assert len([name for name in datasets if name.startswith("factor_procmn_")]) == 4


# --- the year before the window, asked only of what reads it (fix round 2, finding 2) -----------

YEAR_BEFORE: Final[tuple[str, ...]] = (
    "trade_cal",
    "daily",
    "adj_factor",
    "suspend_d",
    "namechange",
    "stock_basic",
)
"""What the measure reads for the year before the window: the calendar, the label reader over
`label_years` (calendar, registry, adjustment factors, halts, bars) and the facts day's history."""


def research_store_shape(book: SurvivorBookConfig) -> dict[str, tuple[int, ...]]:
    """The validation window's catalogue as the research store holds it: every year-before
    dataset from 2021, `stk_limit` and the factor tiers from the window's own first year."""
    return {
        dataset: (2021, 2022, 2023) if dataset in YEAR_BEFORE else (2022, 2023)
        for dataset in p7_reading._preflight_datasets(book)
    }


def test_the_preflight_accepts_factors_and_limits_that_begin_with_the_window() -> None:
    """The re-reviewer's probe shape: factor builds start in the window's first year (the
    measure reads them, and `stk_limit`, for signal days only), so nothing is missing."""
    book = SurvivorBookConfig(seed=0)
    catalogue = Catalogue(research_store_shape(book))

    assert (
        p7_reading.preflight_problems(
            catalogue,  # type: ignore[arg-type]
            window=p7_windows.VALIDATION,
            datasets=p7_reading._preflight_datasets(book),
        )
        == ()
    )


@pytest.mark.parametrize("dataset", YEAR_BEFORE)
def test_the_preflight_refuses_a_missing_year_before_of_each_dataset_that_reads_it(
    dataset: str,
) -> None:
    book = SurvivorBookConfig(seed=0)
    held = research_store_shape(book)
    held[dataset] = (2022, 2023)

    problems = p7_reading.preflight_problems(
        Catalogue(held),  # type: ignore[arg-type]
        window=p7_windows.VALIDATION,
        datasets=p7_reading._preflight_datasets(book),
    )

    assert len(problems) == 1 and problems[0].startswith(
        f"{dataset} lists no partition for [2021] "
    )


@pytest.mark.parametrize(
    "dataset",
    [
        "stk_limit",
        *(
            name
            for name in p7_reading._preflight_datasets(SurvivorBookConfig(seed=0))
            if name.startswith("factor_proc")
        ),
    ],
)
def test_the_preflight_refuses_a_missing_window_year_of_a_signal_day_dataset(dataset: str) -> None:
    book = SurvivorBookConfig(seed=0)
    held = research_store_shape(book)
    held[dataset] = (2022,)

    problems = p7_reading.preflight_problems(
        Catalogue(held),  # type: ignore[arg-type]
        window=p7_windows.VALIDATION,
        datasets=p7_reading._preflight_datasets(book),
    )

    assert len(problems) == 1 and problems[0].startswith(
        f"{dataset} lists no partition for [2023] "
    )


def test_the_default_preflight_refuses_a_runtime_with_no_panel_before_any_claim(repo: Path) -> None:
    registration = registered(repo)
    measure = Measure()

    with pytest.raises(p7_reading.ReadingPreflightError, match="catalog"):
        p7_reading.read_once(
            registration,
            repo=repo,
            runtime_dir=repo / "runtime",
            clock=lambda: COMMITTED,
            measure=measure,
        )
    assert measure.calls == [] and not claim_of(registration).exists()
    assert not window_claim_of(repo).exists() and not (repo / "runtime").exists()


# --- the default measure: its reads and its partition hashes in one hold ------------------------

CODES: Final[tuple[str, ...]] = tuple(f"6000{n:02d}.SH" for n in range(20))


def weekday_calendar(first: date, last: date) -> TradingCalendar:
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


VALIDATION_CALENDAR: Final[TradingCalendar] = weekday_calendar(date(2021, 1, 4), date(2023, 12, 29))


class HoldingStore:
    """A stand-in for the capped store: counts `reading()` holds, and says whether the partition
    hashes were asked for inside one."""

    def __init__(self) -> None:
        self.depth = 0

    @contextmanager
    def reading(self) -> Iterator[None]:
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def partitions_read(self) -> tuple[tuple[str, int, str | None], ...]:
        return (("trade_cal", 2022, "inside the hold" if self.depth else "outside the hold"),)


class HeldScores:
    def __init__(self, store: HoldingStore) -> None:
        self.store = store
        self.depths: list[int] = []

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        self.depths.append(self.store.depth)
        return {"composite": tuple((code, float(p)) for p, code in enumerate(CODES))}


class HeldLabels:
    calendar = VALIDATION_CALENDAR

    def __init__(self, store: HoldingStore) -> None:
        self.store = store
        self.depths: list[int] = []

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        return build_label_window(
            as_of=instant, zone=SHANGHAI, horizon=horizon, calendar=self.calendar
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel:
        self.depths.append(self.store.depth)
        turn = self.calendar.trading_days.index(window.entry_day) % 3 + 2
        value = (CODES.index(ts_code) * turn % 20) / 1000 - 0.005
        return OutcomeLabel(
            ts_code=ts_code,
            window=window,
            refusals=(),
            entry_touch=None,
            exit_touch=None,
            window_return=WindowReturn(
                ts_code=ts_code,
                entry_day=window.entry_day,
                exit_day=window.exit_day,
                published=value,
                adjusted=value,
                unadjusted=value,
                entry_adjusted_close=1.0,
                exit_adjusted_close=1.0 + value,
                per_session=(),
            ),
        )


def test_the_default_measure_reads_and_hashes_inside_one_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No panel: the capped store, the calendar and the two sources are stand-ins that record the
    hold they were used in; the plan, the engine and the document are the real ones."""
    path = tmp_path / "v2-p7-024-validation.registration.json"
    register(path)
    body = json.loads(path.read_text(encoding="utf-8"))
    store = HoldingStore()
    scores = HeldScores(store)
    labels = HeldLabels(store)
    calendar_depths: list[int] = []
    opened: list[tuple[Path, str]] = []

    def capped(runtime_dir: Path, window: Any) -> HoldingStore:
        opened.append((runtime_dir, window.name))
        return store

    def calendar(*args: Any, **kwargs: Any) -> TradingCalendar:
        calendar_depths.append(store.depth)
        return VALIDATION_CALENDAR

    monkeypatch.setattr(p7_reading, "capped_panel_store", capped)
    monkeypatch.setattr(p7_reading, "load_trading_calendar", calendar)
    monkeypatch.setattr(p7_reading, "PanelScoreSource", lambda **kwargs: scores)
    monkeypatch.setattr(p7_reading, "panel_label_source", lambda *args, **kwargs: labels)
    binding = {
        "reading": "v2-p7-024-validation",
        "registration_sha256": "a" * 64,
        "registration_commit": "b" * 40,
        "claim_sha256": "c" * 64,
    }

    document = p7_reading._panel_measure(tmp_path / "runtime")(body, p7_windows.VALIDATION, binding)

    assert opened == [(tmp_path / "runtime", "validation")]
    assert calendar_depths == [1] and set(scores.depths) == {1} and set(labels.depths) == {1}
    assert document["provenance"]["partitions_read"] == [
        {"dataset": "trade_cal", "year": 2022, "content_hash": "inside the hold"}
    ]
    assert document["kind"] == p7_diagnostics.READ_ONCE_KIND and document["binding"] == binding
    assert document["provenance"]["code_commit"] == body["code_commit"]
    assert document["window"]["name"] == "validation" and document["plan"]["first"] == "2022-01-04"
    evaluated = p7_reading.evaluate_stop_rule(RULE, document)
    assert [clause["n"] for clause in evaluated["clauses"]] == [
        len(document["plan"]["sessions"])
    ] * 2
    assert evaluated["outcome"] in ("triggered", "not triggered") and evaluated["reason"] is None
    # The reviewer's probe (fix round 1, ruling 5): the one reading of the validation window is not
    # in-sample, in the result's JSON or in its text.
    result = {**document, "stop_rule": evaluated}
    text = "\n".join(p7_reading.reading_lines(result, body))
    assert document["summary"]["composite"]["h20"]["power_if_planning_sd_were_this"] is not None
    assert p7_diagnostics.READ_ONCE_POWER_WORDING in text
    assert "in-sample" not in text and "in-sample" not in json.dumps(result)


# --- the spent holdout is a secondary reading, never a first out-of-sample one (final review) ---

SECOND_USE_CALENDAR: Final[TradingCalendar] = weekday_calendar(date(2023, 1, 2), date(2026, 9, 30))


class SecondUseLabels(HeldLabels):
    calendar = SECOND_USE_CALENDAR


def test_a_validation_reading_keeps_its_first_out_of_sample_banner_byte_for_byte(
    repo: Path,
) -> None:
    registration = registered(repo)
    run(registration, repo, Measure())
    text = registration.with_name("v2-p7-024-validation.result.txt").read_text(encoding="utf-8")
    assert text.splitlines()[0] == (
        "PRE-REGISTERED READ-ONCE v2-p7-024-validation -- A FIRST OUT-OF-SAMPLE READING, NOT "
        "CONFIRMATORY EVIDENCE: validation window 2022-01-04..2023-12-29"
    )


def test_the_spent_holdouts_measured_document_and_text_never_say_first_out_of_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default measure on the second-use window (stand-in store, calendar and sources; the
    real plan, engine and document): what `.result.json` and `.result.txt` would hold."""
    path = tmp_path / "v2-p7-024-second-use.registration.json"
    register(
        path,
        reading="v2-p7-024-second-use",
        role="secondary",
        window="second_use",
        request=SECOND_USE_REQUEST,
        stop_rule=None,
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    store = HoldingStore()
    monkeypatch.setattr(p7_reading, "capped_panel_store", lambda runtime_dir, window: store)
    monkeypatch.setattr(p7_reading, "load_trading_calendar", lambda *a, **k: SECOND_USE_CALENDAR)
    monkeypatch.setattr(p7_reading, "PanelScoreSource", lambda **kwargs: HeldScores(store))
    monkeypatch.setattr(p7_reading, "panel_label_source", lambda *a, **k: SecondUseLabels(store))
    binding = {
        "reading": "v2-p7-024-second-use",
        "registration_sha256": "a" * 64,
        "registration_commit": "b" * 40,
        "claim_sha256": "c" * 64,
    }

    document = p7_reading._panel_measure(tmp_path / "runtime")(body, p7_windows.SECOND_USE, binding)
    text = "\n".join(p7_reading.reading_lines(document, body))

    assert document["wording"][0] == p7_diagnostics.SECOND_USE_READ_ONCE_WORDING
    assert document["summary"]["composite"]["h20"]["read"] > 0
    assert text.splitlines()[0] == (
        "PRE-REGISTERED READ-ONCE v2-p7-024-second-use -- A SECONDARY, NON-CONFIRMATORY READING "
        "OF A SPENT HOLDOUT: second_use window 2024-01-02..2026-09-24"
    )
    assert p7_diagnostics.SECOND_USE_READ_ONCE_WORDING in text
    assert "first out-of-sample" not in (text + json.dumps(document)).lower()


def test_read_once_refuses_a_runtime_directory_inside_the_main_checkout_before_any_claim(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = registered(repo)
    measure = Measure()
    monkeypatch.setattr(p7_windows, "MAIN_CHECKOUT", repo.resolve())

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        run(registration, repo, measure)

    assert measure.calls == []
    assert not claim_of(registration).exists()
    assert not window_claim_of(repo).exists()


def test_the_default_measure_and_preflight_refuse_a_directory_inside_the_main_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(p7_windows, "MAIN_CHECKOUT", repo.resolve())

    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        p7_reading._panel_measure(repo / "runtime")
    with pytest.raises(p7_windows.RuntimeDirectoryRefusedError, match="main checkout"):
        p7_reading._panel_preflight(repo / "runtime", p7_windows.VALIDATION)


# --- the second_use window waits for the validation window (V2-P7-024 item 3) -------------------


def registered_second_use(repo: Path) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    p7_reading.register_reading(
        path=path,
        reading="v2-p7-024-second-use",
        issue="V2-P7-024",
        role="secondary",
        window="second_use",
        request=SECOND_USE_REQUEST,
        config=CONFIG,
        stop_rule=None,
        code_commit=head(repo),
        as_of_reason=AS_OF_REASON,
    )
    commit_file(repo, path, "register the secondary reading", at=COMMITTED)
    return path


def write_validation_window_claim(repo: Path, *, commit: bool) -> Path:
    path = repo / "docs" / "research" / "p7-readings" / "validation.window-claim.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "reading": "v2-p7-024-validation",
                "registration_sha256": "0" * 64,
                "window": "validation",
                "claimed_at": COMMITTED.isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if commit:
        commit_file(repo, path, "the validation window's claim", at=COMMITTED)
    return path


def test_a_second_use_read_before_the_validation_windows_claim_is_refused(repo: Path) -> None:
    """2024-2026 seen first could still change the numbers of the validation stop rule written
    afterwards: the secondary read waits for the validation window's committed claim."""
    registration = registered_second_use(repo)
    measure = Measure()

    with pytest.raises(p7_reading.ValidationNotSpentError, match="validation"):
        run(registration, repo, measure)

    assert measure.calls == []
    assert not claim_of(registration).exists()
    assert not window_claim_of(repo, "second_use").exists()


def test_a_validation_window_claim_on_disk_but_not_committed_does_not_open_second_use(
    repo: Path,
) -> None:
    """An uncommitted claim can be deleted, and the validation window read again after
    2024-2026 was seen, so 'on disk' is not the artifact: the commit is."""
    registration = registered_second_use(repo)
    write_validation_window_claim(repo, commit=False)

    with pytest.raises(p7_reading.ValidationNotSpentError, match="commit"):
        run(registration, repo, Measure())


def test_a_committed_validation_window_claim_opens_second_use(repo: Path) -> None:
    registration = registered_second_use(repo)
    write_validation_window_claim(repo, commit=True)

    result = run(registration, repo, Measure())

    assert "stop_rule" not in result
    assert claim_of(registration).exists()


def test_a_committed_validation_window_claim_naming_another_window_does_not_open_second_use(
    repo: Path,
) -> None:
    """The file's name is not the artifact: the committed bytes must name the validation window.
    (`research` is the other valid window name that reaches this check; a claim naming
    `second_use` is refused earlier, by `_refuse_any_claim_of_the_window`, as a claim of
    second_use itself.)"""
    registration = registered_second_use(repo)
    path = write_validation_window_claim(repo, commit=False)
    path.write_text(
        json.dumps({"reading": "some-other", "window": "research"}) + "\n", encoding="utf-8"
    )
    commit_file(repo, path, "a claim named validation but not of it", at=COMMITTED)

    with pytest.raises(p7_reading.ValidationNotSpentError, match="does not read as a claim"):
        run(registration, repo, Measure())


def test_a_validation_read_does_not_wait_for_the_second_use_window(repo: Path) -> None:
    """The other order needs nothing: a claim of the secondary window does not stop the
    validation window's read (only a claim naming validation could)."""
    claim_of_second = repo / "docs/research/p7-readings/second_use.window-claim.json"
    claim_of_second.parent.mkdir(parents=True, exist_ok=True)
    claim_of_second.write_text(
        json.dumps({"reading": "some-secondary", "window": "second_use"}) + "\n",
        encoding="utf-8",
    )

    registration = registered(repo)
    result = run(registration, repo, Measure())

    assert result["stop_rule"]["outcome"] == "not triggered"
