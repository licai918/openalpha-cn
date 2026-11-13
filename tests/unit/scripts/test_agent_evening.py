"""`scripts/agent_evening.py`: plan C's evening command (`V2-P7-013`, plan Task 2.5).

The command scores Step 0's survivor census with two agent families -- the deterministic rule
family (`agent_rule_baseline/v1`) and, unless `--no-llm` is given, the model-backed
`agent_llm_structured/v1` -- and files them through the forward-registration protocol. It never
trades, and the tests hold it to that: every model exchange below is a scripted transport or a
recorded exchange store, the socket guard is on, and no test asks a real endpoint for anything.

Step 0's runtime is produced the way the plan says it must be: a generated panel written through
the real panel writers, and Step 0's one bound survivor record filed through
`strategy_forward_session.file_forward_record` into a temporary prediction store. Its day journal
is the document `strategy_survivor_morning.plan_names` reads. Nothing in the store is hand-faked.

The tests are grouped by behaviour, not by step: the refusals first, then the deterministic
`--no-llm` day and its byte-for-byte rerun, then the model family's replay/failure semantics,
then the two scoring sets, then the two operator commands (`--print-launchd`, `--pin-worktree`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import (
    LIMIT_STREAK_FIRST_SESSION_INDEX,
    LIMIT_STREAK_SECURITY_INDEX,
    generate_limit_streak_panel,
    write_generated_panel,
)

from openalpha_cn.domain.agent_forward import (
    ABSTAIN_INVALID_OUTPUT,
    ABSTAIN_NOT_ROUTED,
    AGENT_LLM_FAMILY,
    AGENT_RULE_FAMILY,
    coverage_set,
    primary_sample,
)
from openalpha_cn.domain.alpha_model import AlphaModelArtifact, Prediction, PredictionBatch
from openalpha_cn.domain.llm_contamination import HistoricalLLMRefusedError
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH
from openalpha_cn.models.openai_compatible import ModelConfigurationError, ModelTransportError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.runtime.composition import (
    build_storage,
    prediction_store_at,
    run_repository_at,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_forward_session import file_forward_record, outcome_calendar
from openalpha_cn.strategy_registration import SHANGHAI, RegisteredConfiguration
from openalpha_cn.strategy_survivor_forward import record_scope
from openalpha_cn.strategy_survivor_registration import (
    survivor_declaration,
    survivor_feature_ids,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    for directory in (ROOT / "scripts", ROOT / "scripts" / "research"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    return importlib.import_module(name)


agent = _script("agent_evening")
daily = _script("daily_selection")
registry = _script("registry")

SEED: Final[int] = 20260926
"""Step 0's registered seed -- a date-like integer, the shape the rollover refuses as C's salt."""
C_SEED: Final[int] = 7
C_PRIMARY_SIZE: Final[int] = 4
C_CONFIG_ID: Final[str] = "caf-0000000000000001"
C_CODE_COMMIT: Final[str] = "a" * 40
C_REGISTRATION_SHA: Final[str] = "c" * 64
"""C's registration's content digest, as the admission seam hands it back."""

C_BODY: Final[dict[str, Any]] = {
    "schema": "openalpha-research-registration/v1",
    "config": {
        "kind": "agent_forward/v1",
        "salt": "c-evening-salt-2026",
        "primary_size": C_PRIMARY_SIZE,
    },
    "config_id": C_CONFIG_ID,
    "criteria": {"note": "not evidence"},
    "code_commit": C_CODE_COMMIT,
    "settings": {"random_seed": C_SEED},
}
C_CONTENT: Final[bytes] = (
    json.dumps(C_BODY, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
).encode()

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
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=SEED, min_listing_sessions=60)
"""Step 0's book: the design's, but for the listing age the ten-session fixture calendar can age."""


def _git(repository: Path, *arguments: str) -> str:
    finished = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return finished.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(
        repository,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD")


def _step0_registration(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    """A throwaway repository holding Step 0's committed registration; return root, path, body."""
    repository = tmp_path / "step0-repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--template=")
    (repository / "README").write_text("step 0\n", encoding="utf-8")
    _git(repository, "add", "README")
    initial = _commit(repository, "initial")
    config = {
        "kind": "survivor_book/v1",
        "survivor_book": BOOK.model_dump(mode="json", exclude_computed_fields=True),
        "adapter": ADAPTER,
    }
    registration = repository / "docs" / "research" / "p7-registration.json"
    registry.register(
        config,
        {"note": "fixture"},
        registration,
        code_commit=initial,
        settings={"random_seed": SEED},
    )
    _git(repository, "add", "docs/research/p7-registration.json")
    _commit(repository, "register")
    return repository, registration, json.loads(registration.read_text(encoding="utf-8"))


def _survivor_batch(
    securities: tuple[str, ...],
    *,
    session: date,
    registered: RegisteredConfiguration,
    evening: datetime,
) -> PredictionBatch:
    """Step 0's record shaped as `survivor_batch` files one: every eligible name, one score each."""
    published = session_publication_instant(session)
    features = survivor_feature_ids(BOOK)
    scores = {
        ts_code: round((index + 1) / (len(securities) + 1), 6)
        for index, ts_code in enumerate(securities)
    }
    return PredictionBatch(
        as_of=published,
        predicted_at=evening,
        artifact=AlphaModelArtifact(
            declaration=survivor_declaration(BOOK, registered),
            feature_ids=features,
            training_cutoff=published,
            training_example_count=len(features) * len(scores),
            parameters=tuple((feature, 1.0 / len(features)) for feature in features),
        ),
        predictions=tuple(
            Prediction(ts_code=ts_code, score=score) for ts_code, score in sorted(scores.items())
        ),
    )


def _journal_document(
    session: date, holdings: tuple[str, ...], watch: tuple[str, ...]
) -> dict[str, Any]:
    """Step 0's day journal: the `result` `strategy_survivor_morning.plan_names` reads."""
    return {
        "schema": daily.SURVIVOR_JOURNAL_SCHEMA,
        "session": session.isoformat(),
        "result": {
            "paper": {
                "book": {"holdings": [{"ts_code": code} for code in holdings]},
                "open_orders": [],
                "decision": {"watch_list": list(watch)},
            }
        },
    }


def _on_sse(panel: Any) -> Any:
    """The generated panel on the SSE calendar: Step 0's adapter is `Literal["SSE"]`.

    `tests/panel_fixtures.py` builds its calendar under `EXCHANGE = "SZSE"`; only the calendar's
    subject is the exchange name, so re-subjecting that batch is the whole change -- the panel is
    still the fixture's own, written through the real writers.
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


@dataclass
class World:
    """One test's Step 0 runtime, C runtime, registrations and the plan they imply."""

    repo: Path
    step0: Path
    runtime: Path
    registration: Path
    step0_registration: Path
    session: date
    evening: datetime
    publication: datetime
    survivors: tuple[str, ...]
    primary: tuple[str, ...]
    coverage: tuple[str, ...]
    evidence_subject: str
    record_id: str | None
    c: Any

    def arguments(self, *extra: str) -> list[str]:
        return [
            "--runtime-dir",
            str(self.runtime),
            "--step0-runtime-dir",
            str(self.step0),
            "--step0-registration",
            str(self.step0_registration),
            "--registration",
            str(self.registration),
            "--repo",
            str(self.repo),
            "--as-of",
            self.evening.isoformat(),
            *extra,
        ]

    def journal_path(self) -> Path:
        return agent.journal_path(self.runtime, self.session)


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    filed: bool = True,
    holdings: tuple[str, ...] | None = None,
    watch: tuple[str, ...] | None = None,
    c_salt: str | None = None,
) -> World:
    """Step 0's runtime -- generated panel, filed record, complete day journal -- and C's seam."""
    # The provider in record mode names the credential's environment variable; the value is a
    # placeholder and no transport below reaches the network (the suite's socket guard is on).
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-not-a-real-key")
    repo, step0_registration, body = _step0_registration(tmp_path)
    step0 = tmp_path / "step0"
    panel = _on_sse(generate_limit_streak_panel())
    write_generated_panel(PanelStore(step0 / "panel"), panel)
    session = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 1]
    evening = datetime.combine(session, time(18, 30), tzinfo=SHANGHAI)
    publication = session_publication_instant(session)
    evidence_subject = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    registered = RegisteredConfiguration(
        config_id=body["config_id"],
        registration_sha256=hashlib.sha256(step0_registration.read_bytes()).hexdigest(),
        code_commit=body["code_commit"],
        seed=SEED,
    )
    record_id: str | None = None
    if filed:
        batch = _survivor_batch(
            panel.securities, session=session, registered=registered, evening=evening
        )
        calendar = outcome_calendar(PanelStore(step0 / "panel"), "SSE", session, as_of=publication)
        store = FilePredictionStore(step0 / "predictions", clock=lambda: evening)
        record, _outcome = file_forward_record(
            store, batch, calendar=calendar, clock=lambda: evening
        )
        record_id = record.record_id
        survivors = record_scope(record.batch, book=BOOK).survivors
    else:
        survivors = record_scope(
            _survivor_batch(
                panel.securities, session=session, registered=registered, evening=evening
            ),
            book=BOOK,
        ).survivors
    # The primary draw: pick the fixture's salt so the limit-event subject is sampled -- a
    # registered draw is a pure function of (salt, session, survivors), so this is a fixture
    # choice and not a reordering of the rule.
    primary: tuple[str, ...] = ()
    for attempt in range(50):
        candidate = primary_sample(
            survivors,
            salt=f"c-evening-salt-2026-{attempt}",
            session=session,
            size=C_PRIMARY_SIZE,
        )
        if evidence_subject in candidate:
            primary = candidate
            break
    assert primary, "no fixture salt samples the limit-event subject"
    chosen_holdings = holdings if holdings is not None else (primary[0],)
    chosen_watch = watch if watch is not None else (survivors[0],)
    coverage = coverage_set(chosen_holdings, chosen_watch, survivors)
    if filed:
        journal = daily.survivor_journal_directory(step0, body["config_id"]) / (
            f"{session.isoformat()}.json"
        )
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            json.dumps(_journal_document(session, chosen_holdings, chosen_watch)),
            encoding="utf-8",
        )
    c_config = {
        "kind": "agent_forward/v1",
        "salt": c_salt if c_salt is not None else f"c-evening-salt-2026-{attempt}",
        "primary_size": C_PRIMARY_SIZE,
    }
    c_body = {**C_BODY, "config": c_config}
    c = agent.read_agent_registration(
        c_body, content=json.dumps(c_body, sort_keys=True).encode(), commit="b" * 40
    )
    registration = tmp_path / "c-registration.json"

    monkeypatch.setattr(agent, "_admit", lambda options: (repo, c))

    return World(
        repo=repo,
        step0=step0,
        runtime=tmp_path / "runtime",
        registration=registration,
        step0_registration=step0_registration,
        session=session,
        evening=evening,
        publication=publication,
        survivors=survivors,
        primary=primary,
        coverage=coverage,
        evidence_subject=evidence_subject,
        record_id=record_id,
        c=c,
    )


_ANSWER_DIRECTORIES: Final[tuple[str, ...]] = (
    "predictions",
    "evidence",
    "agent_evening",
    "model_exchanges",
)
"""What a rerun must leave byte-identical: the filed records, the evidence, the journal and the
recorded exchanges. The `state.sqlite3` run repository is not in this list on purpose -- the
engine writes its own run manifests and recovery heartbeats there, which are not the day's
answer, and the tests below compare the answer."""


def _answers(root: Path) -> dict[str, bytes]:
    """The day's answer files: the four directories a rerun must leave alone."""
    found: dict[str, bytes] = {}
    for directory in _ANSWER_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _files(root: Path, *, skip_panel_locks: bool = False) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if skip_panel_locks and (
            relative.endswith((".lock", ".wal", ".tmp")) or ".duckdb" in relative
        ):
            continue
        found[relative] = path.read_bytes()
    return found


def _records(world: World, family: str) -> list[Any]:
    store = prediction_store_at(world.runtime, clock=lambda: world.evening)
    held = []
    for record_id in store.list_ids():
        record = store.get(record_id)
        if record is None:
            continue
        if record.batch.artifact.declaration.family != family:
            continue
        if record.batch.as_of.astimezone(SHANGHAI).date() != world.session:
            continue
        held.append(record)
    return held


# --- the refusals -------------------------------------------------------------------------------


def test_refuses_when_step0_has_not_filed_the_session_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch, filed=False)

    code = agent.main(world.arguments("--no-llm"), clock=lambda: world.evening)

    err = capsys.readouterr().err
    assert code != 0
    assert f"Step 0 has not filed {world.session.isoformat()}" in err
    assert not world.runtime.exists()
    assert not world.journal_path().exists()


def test_a_salt_tied_to_step0s_registered_seed_is_refused_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review Focus 2's coupling: C's draw salt is its own value, never Step 0's seed.

    The registration writer refuses this too (plan C task 3.3, not in this batch); this is the
    forward-run half -- it runs after both registrations are read and before anything else, so a
    deployment whose salt was copied from the book leaves no record, no evidence and no journal.
    """
    world = _world(tmp_path, monkeypatch, c_salt=str(SEED))

    code = agent.main(world.arguments("--no-llm"), clock=lambda: world.evening)

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "step 1 (registration) failed" in err
    assert "Step 0's registered seed" in err
    assert not world.runtime.exists()
    assert not world.journal_path().exists()


def test_after_0915_nothing_is_filed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    late = datetime.combine(world.session + timedelta(days=1), time(9, 20), tzinfo=SHANGHAI)

    code = agent.main(world.arguments("--no-llm"), clock=lambda: late)

    err = capsys.readouterr().err
    assert code != 0
    assert "call auction" in err
    assert not world.journal_path().exists()
    assert not (world.runtime / "evidence").exists()
    assert not (world.runtime / "predictions").exists()


# --- the deterministic rule family --------------------------------------------------------------


def test_rule_family_is_byte_identical_across_two_runs_with_llm_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)

    first = agent.main(world.arguments("--no-llm"), clock=lambda: world.evening)
    capsys.readouterr()
    after_first = _answers(world.runtime)

    second = agent.main(world.arguments("--no-llm"), clock=lambda: world.evening)
    printed = capsys.readouterr().out
    after_second = _answers(world.runtime)

    assert first == second == 0
    assert after_second == after_first
    assert "unchanged" in printed
    journal = json.loads(world.journal_path().read_text(encoding="utf-8"))
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "llm_disabled"


def test_a_subject_without_evidence_abstains_not_routed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)

    assert agent.main(world.arguments("--no-llm"), clock=lambda: world.evening) == 0
    capsys.readouterr()

    rule = [record for record in _records(world, AGENT_RULE_FAMILY)]
    assert len(rule) == 2
    without = [
        prediction
        for record in rule
        for prediction in record.batch.predictions
        if prediction.ts_code != world.evidence_subject
    ]
    assert without
    assert {prediction.abstention for prediction in without} == {ABSTAIN_NOT_ROUTED}
    assert all(prediction.score is None for prediction in without)


def test_the_step0_runtime_is_never_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    before = _files(world.step0, skip_panel_locks=True)

    assert agent.main(world.arguments("--no-llm"), clock=lambda: world.evening) == 0
    capsys.readouterr()

    after = _files(world.step0, skip_panel_locks=True)
    assert after == before


# --- the model-backed family --------------------------------------------------------------------


def _evening_clock(world: World) -> Any:
    """A clock pinned to `world.evening`, bound once so a loop cannot capture a stale world."""
    return lambda: world.evening


def test_a_missing_or_blank_credential_is_a_family_failure_never_an_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F1: a deployment failure is `no_record`, not a whole day of `model_invalid_output`.

    `llm_provider` needs the credential named by the endpoint's `api_key_env`; when the variable
    is unset or blank the evening has not asked the model anything, so no answer can have been
    invalid. The rule family is still filed, the model family journals the gap by name, and a
    transport that would have answered is never reached.
    """
    for blank in ("unset", "blank"):
        case = tmp_path / blank
        case.mkdir()
        world = _world(case, monkeypatch)
        if blank == "unset":
            monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        else:
            monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
        model = ScriptedModel("valid")

        code = agent.main(world.arguments(), clock=_evening_clock(world), transport=model)
        capsys.readouterr()

        assert code == 0
        assert model.calls == 0, "a missing credential must prevent every request"
        journal = _journal(world)
        assert journal["llm_family"]["status"] == "no_record"
        assert journal["llm_family"]["reason"] == "credential_missing"
        assert journal["llm_family"]["primary"] is None
        assert journal["llm_family"]["coverage"] is None
        assert _records(world, AGENT_LLM_FAMILY) == []
        assert len(_records(world, AGENT_RULE_FAMILY)) == 2


class ScriptedModel:
    """A model transport whose answers are scripted: `valid`, `invalid` or a transport failure."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.requests.append(payload)
        action = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        if action == "retryable":
            raise ModelTransportError("the scripted transport failed", retryable=True)
        if action == "error":
            raise ModelTransportError("the scripted transport failed", retryable=False)
        content = "not a json object" if action == "invalid" else json.dumps(self._answer(payload))
        return {
            "id": f"req-{len(self.requests)}",
            "model": "deepseek-flash",
            "system_fingerprint": "fp-c-test",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    @staticmethod
    def _answer(payload: dict[str, Any]) -> dict[str, Any]:
        user = json.loads(payload["messages"][1]["content"])
        return {
            "signal": {
                "subject": user["subject"],
                "as_of": user["as_of"],
                "direction": "bullish",
                "strength": 0.5,
                "confidence": 0.6,
                "horizon": "5d",
                "evidence_ids": [item["evidence_id"] for item in user["evidence"]],
                "confirmation_conditions": [],
                "invalidation_conditions": [],
                "risk_flags": [],
            },
            "rationale": "the scripted answer",
        }


def _journal(world: World) -> dict[str, Any]:
    return json.loads(world.journal_path().read_text(encoding="utf-8"))


def _instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider's backoff does not sleep in tests: HTTP attempts are what the cap counts."""
    real = agent.llm_provider

    def provider(*args: Any, **kwargs: Any) -> Any:
        built = real(*args, **kwargs)
        built.sleeper = lambda _seconds: None
        return built

    monkeypatch.setattr(agent, "llm_provider", provider)


def test_llm_rerun_replays_stored_exchanges_with_zero_inner_calls_and_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    live = ScriptedModel("valid")

    assert agent.main(world.arguments(), clock=lambda: world.evening, transport=live) == 0
    capsys.readouterr()
    assert live.calls >= 1
    journal_after_first = world.journal_path().read_bytes()
    files_after_first = _answers(world.runtime)
    assert _journal(world)["llm_family"]["status"] == "filed"
    exchanges = _journal(world)["llm_family"]["exchanges"]
    assert exchanges and exchanges[0]["model"] == "deepseek-flash"
    assert exchanges[0]["system_fingerprint"] == "fp-c-test"

    replay = ScriptedModel()

    assert agent.main(world.arguments(), clock=lambda: world.evening, transport=replay) == 0
    printed = capsys.readouterr().out

    assert replay.calls == 0
    assert world.journal_path().read_bytes() == journal_after_first
    assert _answers(world.runtime) == files_after_first
    assert "unchanged" in printed


def test_a_transport_error_mid_set_files_no_llm_batch_and_journals_no_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    # The first request answers invalidly, so the agent retries: the failure lands mid-set.
    model = ScriptedModel("invalid", "error")

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)

    capsys.readouterr()
    assert code == 0
    assert model.calls == 2
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "transport_error"
    assert journal["llm_family"]["primary"] is None
    assert journal["llm_family"]["coverage"] is None
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2


def test_the_request_cap_stops_the_day_and_files_nothing_for_that_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    model = ScriptedModel("invalid", "valid")

    code = agent.main(
        world.arguments("--max-model-requests", "1"),
        clock=lambda: world.evening,
        transport=model,
    )

    capsys.readouterr()
    assert code == 0
    assert model.calls == 1
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "request_cap_reached"
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2


def test_the_deadline_stops_the_llm_family_and_journals_no_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    model = ScriptedModel("valid")
    after_deadline = datetime.combine(world.session, time(23, 31), tzinfo=SHANGHAI)

    code = agent.main(world.arguments(), clock=lambda: after_deadline, transport=model)

    capsys.readouterr()
    assert code == 0
    assert model.calls == 0
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "deadline_reached"
    assert _records(world, AGENT_LLM_FAMILY) == []


class ConfigurationFailingModel:
    """A transport that raises `ModelConfigurationError`: a deployment fault mid-run."""

    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, **arguments: Any) -> dict[str, Any]:
        self.calls += 1
        raise ModelConfigurationError("the credential vanished between the check and the call")


def test_a_configuration_error_on_the_request_path_is_also_a_family_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F1: the catch beside `ModelTransportError` covers a config error raised on the call path.

    The provider is assembled (the credential pre-check passed) but the request path raises a
    `ModelConfigurationError`; it is refused as a deployment fault before any retry, never filed
    as an invalid answer.
    """
    world = _world(tmp_path, monkeypatch)
    model = ConfigurationFailingModel()

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)
    capsys.readouterr()

    assert code == 0
    assert model.calls == 1, "a configuration error is not retried as an invalid answer"
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "configuration_error"
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2


def test_an_unverified_endpoint_is_a_configuration_gap_not_an_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F1's controller ruling: any `ModelConfigurationError` from the assembly is a family gap.

    `llm_provider` still refuses the live request by name while `DEEPSEEK_V4_1_FLASH.verified_on`
    is `None` -- nothing reaches a transport and the placeholder model id is never spent -- but
    the evening now records that deployment failure as `no_record: configuration_error` and goes
    on: the rule family is filed, no model batch exists, and exit is 0. Before this ruling the
    same error was a step-5 refusal and the day had no journal at all; that is the F1 ruled
    behaviour change, not a silent adaptation. Once the endpoint is verified -- at gated step L2
    -- this test skips: it pins the placeholder's gap, not a fact about the deployment.
    """
    if DEEPSEEK_V4_1_FLASH.verified_on is not None:
        pytest.skip("the endpoint has been verified; the unverified path is unreachable")
    world = _world(tmp_path, monkeypatch)

    code = agent.main(world.arguments(), clock=lambda: world.evening)
    capsys.readouterr()

    assert code == 0
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "configuration_error"
    assert journal["llm_family"]["primary"] is None
    assert journal["llm_family"]["coverage"] is None
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2


def test_an_invalid_answer_after_retries_is_an_abstention_coded_model_invalid_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    model = ScriptedModel("invalid", "invalid")

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)

    capsys.readouterr()
    assert code == 0
    assert model.calls == 2
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "filed"
    predictions = [
        prediction
        for record in _records(world, AGENT_LLM_FAMILY)
        for prediction in record.batch.predictions
    ]
    invalid = [
        prediction for prediction in predictions if prediction.ts_code == world.evidence_subject
    ]
    assert invalid
    assert {prediction.abstention for prediction in invalid} == {ABSTAIN_INVALID_OUTPUT}


# --- the deadline and the cap are spent on HTTP requests only (F2/F8) ---------------------------


def test_a_post_deadline_rerun_of_a_filed_day_replays_with_zero_inner_calls_and_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: the 23:30 wall deadline is not a gate on a rerun of a day already answered.

    The first evening filed the model family; every request of a rerun is answered by the
    exchange store (or by the engine's completed run), so the deadline never applies and the
    day's account is reproduced byte for byte.
    """
    world = _world(tmp_path, monkeypatch)
    live = ScriptedModel("valid")

    assert agent.main(world.arguments(), clock=lambda: world.evening, transport=live) == 0
    capsys.readouterr()
    journal_after_first = world.journal_path().read_bytes()
    answers_after_first = _answers(world.runtime)
    assert _journal(world)["llm_family"]["status"] == "filed"

    after_deadline = datetime.combine(world.session, time(23, 45), tzinfo=SHANGHAI)
    replay = ScriptedModel()

    code = agent.main(world.arguments(), clock=lambda: after_deadline, transport=replay)
    printed = capsys.readouterr().out

    assert code == 0
    assert replay.calls == 0, "a replay of a filed day costs no request, whatever the hour"
    assert world.journal_path().read_bytes() == journal_after_first
    assert _answers(world.runtime) == answers_after_first
    assert _journal(world)["llm_family"]["status"] == "filed"
    assert "unchanged" in printed


def test_a_crash_after_filing_before_the_journal_is_finished_by_a_post_deadline_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2's second failure scenario: the batches are filed, the evening dies before step 6.

    The rerun after the wall deadline must finish the day from the stored exchanges -- filing
    nothing new and spending no request -- and journal the family `filed` with the same records.
    """
    world = _world(tmp_path, monkeypatch)
    live = ScriptedModel("valid")
    real_write_journal = agent._write_journal

    def crash_after_filing(path: Path, document: Any) -> str:
        raise agent.StepFailedError("summary", "scripted crash after the batches were filed")

    monkeypatch.setattr(agent, "_write_journal", crash_after_filing)
    assert (
        agent.main(world.arguments(), clock=lambda: world.evening, transport=live)
        == agent.AgentEveningExit.refused
    )
    capsys.readouterr()
    assert not world.journal_path().exists()
    first_records = {record.record_id for record in _records(world, AGENT_LLM_FAMILY)}
    assert len(first_records) == 2, "both LLM batches were filed before the crash"

    monkeypatch.setattr(agent, "_write_journal", real_write_journal)
    after_deadline = datetime.combine(world.session, time(23, 45), tzinfo=SHANGHAI)
    replay = ScriptedModel()

    code = agent.main(world.arguments(), clock=lambda: after_deadline, transport=replay)
    capsys.readouterr()

    assert code == 0
    assert replay.calls == 0
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "filed"
    assert {record.record_id for record in _records(world, AGENT_LLM_FAMILY)} == first_records


def test_a_zero_request_cap_rerun_of_a_filed_day_replays_and_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: `--max-model-requests 0` bounds a rerun's new requests, not its replays."""
    world = _world(tmp_path, monkeypatch)
    live = ScriptedModel("valid")

    assert agent.main(world.arguments(), clock=lambda: world.evening, transport=live) == 0
    capsys.readouterr()
    journal_after_first = world.journal_path().read_bytes()

    replay = ScriptedModel()

    code = agent.main(
        world.arguments("--max-model-requests", "0"),
        clock=lambda: world.evening,
        transport=replay,
    )
    printed = capsys.readouterr().out

    assert code == 0
    assert replay.calls == 0
    assert world.journal_path().read_bytes() == journal_after_first
    assert _journal(world)["llm_family"]["status"] == "filed"
    assert "unchanged" in printed


def test_provider_retries_count_every_http_attempt_against_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F8: the cap is D7's ceiling on HTTP requests, so a provider retry is a second request.

    Two retryable transport failures then a valid answer are three requests sent, and the
    printed spend says three even though one exchange is held.
    """
    world = _world(tmp_path, monkeypatch)
    _instant_retries(monkeypatch)
    model = ScriptedModel("retryable", "retryable", "valid")

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)
    printed = capsys.readouterr().out

    assert code == 0
    assert model.calls == 3, "each provider retry is another HTTP request"
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "filed"
    assert "model requests   3 this run" in printed
    assert len(journal["llm_family"]["exchanges"]) == 1, "one successful answer is held"


def test_the_cap_counts_http_attempts_so_retries_past_the_allowance_are_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F8: the request past the allowance is never sent, retries included."""
    world = _world(tmp_path, monkeypatch)
    _instant_retries(monkeypatch)
    model = ScriptedModel("retryable", "retryable", "valid")

    code = agent.main(
        world.arguments("--max-model-requests", "2"),
        clock=lambda: world.evening,
        transport=model,
    )
    capsys.readouterr()

    assert code == 0
    assert model.calls == 2, "the third HTTP attempt crossed the allowance and was not sent"
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "request_cap_reached"
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2


# --- the model family runs through the engine (F6) ----------------------------------------------


def test_the_llm_family_runs_through_the_engine_so_the_history_guard_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F6: 015's history/72h guard is structural on the model path, because the engine runs it.

    The engine's own `refuse_llm_agents_in_history` is patched to refuse an `llm_backed` roster
    (the deterministic rule roster still passes through): a day that could only be produced by a
    historical replay must refuse at step 5, file no journal and spend no model request.
    """
    import openalpha_cn.runtime.engine as engine_module

    world = _world(tmp_path, monkeypatch)

    def refusing_guard(
        *,
        path: str,
        mode: Any,
        as_of: datetime,
        ran_at: datetime,
        agents: Any,
    ) -> None:
        if any(kind == "llm_backed" for _agent_id, kind in agents):
            raise HistoricalLLMRefusedError(
                "engine refuses the model roster: this cycle follows the patched history guard"
            )

    monkeypatch.setattr(engine_module, "refuse_llm_agents_in_history", refusing_guard)
    model = ScriptedModel("valid")

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)
    err = capsys.readouterr().err

    assert code != 0
    assert "step 5 (llm_family) failed" in err
    assert "HistoricalLLMRefusedError" in err
    assert model.calls == 0, "the guard runs before the agent, so no request is spent"
    assert not world.journal_path().exists()
    assert _records(world, AGENT_LLM_FAMILY) == []
    assert len(_records(world, AGENT_RULE_FAMILY)) == 2, "the deterministic family was filed"


def test_the_llm_runs_have_stored_manifests_recording_model_and_prompt_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F6: every model run is an engine run, so its manifest names the model and the prompt.

    Before this fix the LLM family called `agent.analyze` directly and the `run_id` given to it
    named no stored run. The evidence-bearing subject's run must now hold a manifest whose
    `model_versions` and `prompt_versions` are the endpoint's model and the V2 prompt; a subject
    with no routable evidence still holds a manifest, with empty provenance because no agent ran.
    """
    world = _world(tmp_path, monkeypatch)
    model = ScriptedModel("valid")

    assert agent.main(world.arguments(), clock=lambda: world.evening, transport=model) == 0
    capsys.readouterr()

    storage = build_storage(runtime_dir=world.runtime, clock=lambda: world.evening)
    names = sorted(set(world.primary) | set(world.coverage))
    run_ids = {
        name: agent.agent_run_id(
            config_id=world.c.registered.config_id,
            session=world.session,
            family=AGENT_LLM_FAMILY,
            ts_code=name,
        )
        for name in names
    }
    manifests = {name: storage.repository.get_run(run_id) for name, run_id in run_ids.items()}
    assert all(manifest is not None for manifest in manifests.values()), (
        "every LLM run must be stored; a run id that names no run is not a declared run"
    )

    evidence_manifest = manifests[world.evidence_subject]
    assert evidence_manifest is not None
    assert evidence_manifest.model_versions, "the model that answered is not recorded"
    assert [(ref.component, ref.version) for ref in evidence_manifest.model_versions] == [
        (DEEPSEEK_V4_1_FLASH.provider_id, DEEPSEEK_V4_1_FLASH.model)
    ]
    assert evidence_manifest.prompt_versions, "the prompt that was sent is not recorded"
    assert evidence_manifest.prompt_versions[0].component.endswith(
        agent.STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT.name
    )

    for name, manifest in manifests.items():
        if name == world.evidence_subject:
            continue
        assert manifest is not None
        assert manifest.model_versions == ()
        assert manifest.prompt_versions == ()


# --- the two scoring sets -----------------------------------------------------------------------


def test_coverage_and_primary_are_filed_separately_and_overlap_reuses_one_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = _world(tmp_path, monkeypatch)
    assert set(world.primary) & set(world.coverage)

    assert agent.main(world.arguments("--no-llm"), clock=lambda: world.evening) == 0
    capsys.readouterr()

    journal = _journal(world)
    assert journal["scoring_sets"]["primary"] == list(world.primary)
    assert journal["scoring_sets"]["coverage"] == list(world.coverage)

    rule = _records(world, AGENT_RULE_FAMILY)
    declarations = {record.batch.artifact.declaration.name for record in rule}
    assert declarations == {"c-primary", "c-coverage"}
    sets = {
        record.batch.artifact.declaration.name: dict(
            record.batch.artifact.declaration.hyperparameters
        )["scoring_set"]
        for record in rule
    }
    assert sets == {"c-primary": "primary", "c-coverage": "coverage"}
    union = sorted(set(world.primary) | set(world.coverage))
    assert {record.batch.artifact.declaration.family for record in rule} == {AGENT_RULE_FAMILY}

    runs = run_repository_at(world.runtime).list_runs()
    filed = {run.run_id for run in runs if run.run_id.startswith(f"agent-{world.session:%Y%m%d}-")}
    expected = {
        agent.agent_run_id(
            config_id=world.c.registered.config_id,
            session=world.session,
            family=AGENT_RULE_FAMILY,
            ts_code=ts_code,
        )
        for ts_code in union
    }
    assert filed == expected
    # One cycle per name of the union: the overlap did not buy the name a second run.
    assert len(filed) == len(union)


# --- the summary's failure count (F11) -----------------------------------------------------------


def test_a_no_record_day_counts_distinct_names_and_labels_them_not_scored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F11: the count is of names a filed family would have scored, not of set memberships.

    The fixture's coverage set overlaps the primary draw; a transport failure leaves the model
    family unfiled, and a name in both sets is one name that was not scored. The printed line
    says why, so "failed 5" is not read as five bad answers.
    """
    world = _world(tmp_path, monkeypatch)
    assert set(world.primary) & set(world.coverage), "the fixture must overlap the two sets"
    model = ScriptedModel("error")

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)
    printed = capsys.readouterr().out

    assert code == 0
    distinct = len(set(world.primary) | set(world.coverage))
    assert distinct < len(world.primary) + len(world.coverage)
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["summary"]["failed"] == distinct
    assert f"not scored (family not filed: transport_error): {distinct}" in printed


def test_a_no_llm_day_reports_zero_not_scored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F11: `--no-llm` is a decision, not a failed family, so it reports 0."""
    world = _world(tmp_path, monkeypatch)

    assert agent.main(world.arguments("--no-llm"), clock=lambda: world.evening) == 0
    printed = capsys.readouterr().out

    journal = _journal(world)
    assert journal["summary"]["failed"] == 0
    assert "failed 0" in printed


# --- the operator commands ----------------------------------------------------------------------


def _plist_program_arguments(printed: str) -> list[str]:
    """The `ProgramArguments` strings of a printed plist, read with a real XML parser."""
    root = ElementTree.fromstring(printed.split("</plist>")[0] + "</plist>")
    dictionary = root.find("dict")
    assert dictionary is not None, "the printed plist has no top-level dictionary"
    children = list(dictionary)
    for index, child in enumerate(children):
        if child.tag == "key" and child.text == "ProgramArguments":
            array = children[index + 1]
            assert array.tag == "array"
            return [str(element.text) for element in array]
    raise AssertionError("the printed plist has no ProgramArguments")


def test_the_launchd_plist_is_printed_never_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    worktree = tmp_path / "pinned"
    runtime = tmp_path / "runtime"
    step0 = tmp_path / "step0"

    code = agent.main(
        [
            "--print-launchd",
            "--worktree",
            str(worktree),
            "--runtime-dir",
            str(runtime),
            "--step0-runtime-dir",
            str(step0),
            "--registration",
            str(worktree / "docs" / "research" / "p7-c-registration.json"),
        ]
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert agent.LAUNCHD_LABEL == "com.openalpha.agent-evening"
    assert f"<string>{agent.LAUNCHD_LABEL}</string>" in printed
    assert "<key>Hour</key>" in printed and "<integer>20</integer>" in printed
    assert "<key>Minute</key>" in printed and "<integer>30</integer>" in printed
    for weekday in range(1, 6):
        assert f"<integer>{weekday}</integer>" in printed
    assert "agent_evening.py" in printed
    assert "never installed" in printed
    assert not runtime.exists()
    assert not worktree.exists()
    assert not step0.exists()


def test_the_launchd_arguments_satisfy_the_evening_step2_precondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F3: the printed job carries `--step0-runtime-dir` (absolute) and step 0's registration.

    Without that argument every scheduled evening exits 2 at step 2, so the operator's installed
    job would file nothing. The arguments are fed back through the command's own parser: the
    installed job runs the evening, not another `--print-launchd`.
    """
    worktree = tmp_path / "pinned"
    runtime = tmp_path / "runtime"
    monkeypatch.chdir(tmp_path)

    code = agent.main(
        [
            "--print-launchd",
            "--worktree",
            str(worktree),
            "--runtime-dir",
            str(runtime),
            "--step0-runtime-dir",
            "step0",
            "--step0-registration",
            "docs/research/p7-registration.json",
        ]
    )
    printed = capsys.readouterr().out

    assert code == 0
    arguments = _plist_program_arguments(printed)
    assert [Path(arguments[0]).name, Path(arguments[0]).parent.name] == ["python", "bin"]
    assert Path(arguments[1]).name == "agent_evening.py"

    options = agent._parser().parse_args(arguments[2:])
    assert options.print_launchd is False, "the installed job runs the evening, not the printer"
    assert options.step0_runtime_dir == str((tmp_path / "step0").resolve())
    assert Path(options.step0_runtime_dir).is_absolute()
    assert options.step0_registration == str(
        (worktree / "docs" / "research" / "p7-registration.json").resolve()
    )
    assert options.runtime_dir == str(runtime)


def test_print_launchd_refuses_without_the_step0_runtime_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F3: a job that could never run step 2 is not printed at all."""
    worktree = tmp_path / "pinned"
    runtime = tmp_path / "runtime"

    code = agent.main(
        [
            "--print-launchd",
            "--worktree",
            str(worktree),
            "--runtime-dir",
            str(runtime),
        ]
    )

    captured = capsys.readouterr()
    assert code == agent.AgentEveningExit.refused
    assert captured.out == ""
    assert "--step0-runtime-dir is required" in captured.err
    assert not worktree.exists()
    assert not runtime.exists()


def _pin_world(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway repository with one commit, and the path of C's registration inside it."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--template=")
    (repository / "README").write_text("pinned\n", encoding="utf-8")
    (repository / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    _git(repository, "add", "README")
    _commit(repository, "initial")
    registration = repository / "docs" / "research" / "p7-c-registration.json"
    registration.parent.mkdir(parents=True)
    registration.write_text("{}\n", encoding="utf-8")
    return repository, registration


def _pin_args(repository: Path, registration: Path, directory: Path) -> list[str]:
    return [
        "--pin-worktree",
        str(directory),
        "--repo",
        str(repository),
        "--registration",
        str(registration),
    ]


def test_pin_worktree_refuses_a_dirty_or_foreign_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, registration = _pin_world(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD")
    seen: list[tuple[Any, ...]] = []

    def checkout(path: Path, root: Path, *, also_bound: tuple[str, ...]) -> tuple[Path, str, str]:
        seen.append((path, root, also_bound))
        return repository.resolve(), commit, commit

    monkeypatch.setattr(registry, "registered_checkout", checkout)
    monkeypatch.setattr(
        daily, "sync_pinned_environment", lambda worktree, *, uv: worktree / ".venv"
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q", "--template=")

    code = agent.main(_pin_args(repository, registration, foreign))
    err = capsys.readouterr().err
    assert code != 0
    assert "not a worktree of" in err

    dirty = tmp_path / "dirty"
    _git(repository, "worktree", "add", "--detach", str(dirty), commit)
    (dirty / "stray.txt").write_text("uncommitted\n", encoding="utf-8")

    code = agent.main(_pin_args(repository, registration, dirty))
    err = capsys.readouterr().err
    assert code != 0
    assert "uncommitted changes" in err

    fresh = tmp_path / "fresh"

    code = agent.main(_pin_args(repository, registration, fresh))
    printed = capsys.readouterr().out

    assert code == 0, printed
    assert fresh.is_dir()
    assert (fresh / "README").is_file()
    assert _git(fresh, "rev-parse", "HEAD") == commit
    assert (fresh / ".env").is_symlink()
    assert seen and seen[0][2] == agent.C_SCRIPTS
