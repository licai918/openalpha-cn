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
from research_repo import commit_file, git, head

from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel, WindowReturn, build_label_window
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
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
    git(repo, "rm", "-q", str(measure.claim.relative_to(repo)))  # type: ignore[union-attr]
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


def register(path: Path, **changes: Any) -> str:
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
    git(repo, "rm", "-q", str(result.relative_to(repo)))
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
        git(repo, "rm", "-q", str(path.relative_to(repo)))
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
    secondary = (
        repo / "docs" / "research" / "p7-readings" / "v2-p7-024-second-use.registration.json"
    )
    register(
        secondary,
        reading="v2-p7-024-second-use",
        role="secondary",
        window="second_use",
        request=SECOND_USE_REQUEST,
        stop_rule=None,
        code_commit=head(repo),
    )
    commit_file(repo, secondary, "register the secondary reading", at=COMMITTED)
    run(secondary, repo, Measure())
    for path in (claim_of(secondary), window_claim_of(repo, "second_use")):
        commit_file(repo, path, f"commit {path.name}", at=COMMITTED + timedelta(minutes=30))
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
    measure = Measure()

    result = run(path, repo, measure)

    assert measure.calls == [("second_use", True)]
    assert "stop_rule" not in result
    text = path.with_name("v2-p7-024-second-use.result.txt").read_text(encoding="utf-8")
    assert text.startswith(
        "PRE-REGISTERED READ-ONCE v2-p7-024-second-use -- A FIRST OUT-OF-SAMPLE READING, NOT "
        "CONFIRMATORY EVIDENCE: second_use window 2024-01-02..2026-09-24"
    )
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


def test_a_minimum_the_plan_can_reach_is_registered(tmp_path: Path) -> None:
    """The bound for this window: 519 weekdays, at most 26 non-overlapping periods at stride 20
    and h20, at stride 5 and h20 (every fourth of 104 signals), and at stride 20 and h5."""
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
    assert {"trade_cal", "daily", "adj_factor", "suspend_d", "stk_limit", "namechange"} <= set(
        datasets
    )
    assert len([name for name in datasets if name.startswith("factor_proc_")]) == 4
    assert len([name for name in datasets if name.startswith("factor_procmn_")]) == 4


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
        turn = VALIDATION_CALENDAR.trading_days.index(window.entry_day) % 3 + 2
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
