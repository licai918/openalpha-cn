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
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import research_repo
from panel_fixtures import (
    LIMIT_STREAK_FIRST_SESSION_INDEX,
    LIMIT_STREAK_SECURITY_INDEX,
    generate_limit_streak_panel,
    write_generated_panel,
)

from openalpha_cn.domain.agent_forward import (
    ABSTAIN_ALL_ABSTAINED,
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
from openalpha_cn.panel_limit_events import limit_events_on
from openalpha_cn.runtime.composition import (
    build_storage,
    prediction_store_at,
    run_repository_at,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_agent_evaluation import (
    DRY_WEEK_SCRATCH_MARKER,
    DRY_WEEK_SCRATCH_SCHEMA,
)
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
    panel: Any

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
    committed_at: datetime | None = None,
    registered_anchor: date | None = None,
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
    # N2's seam: the anchor the registration froze and the commit instant the real admission
    # would report. By default the commit is before the anchor session's 16:30 publication, so
    # the anchor the evening derives is exactly the registered one and every other test keeps
    # its registered-evening path. A test that wants a mis-planned registration moves either.
    anchor_session = session if registered_anchor is None else registered_anchor
    registered_at = (
        datetime.combine(anchor_session, time(9, 0), tzinfo=SHANGHAI)
        if committed_at is None
        else committed_at
    )
    c = dataclasses.replace(
        agent.read_agent_registration(
            c_body, content=json.dumps(c_body, sort_keys=True).encode(), commit="b" * 40
        ),
        registration_committed_at=registered_at,
        registered_anchor=anchor_session,
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
        panel=panel,
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


class FrameModel:
    """A model transport that answers every request with one scripted `SignalFrame` (N1).

    `subject` and `as_of` are read from the request, because the agent refuses a frame that
    disagrees with its context; everything else is the test's. `cite_evidence` decides whether
    the answer repeats the evidence IDs it was handed -- an abstention is allowed to cite the
    facts it read, and the prompt cannot stop a model from doing so.
    """

    def __init__(
        self,
        *,
        direction: str,
        strength: float,
        cite_evidence: bool = True,
    ) -> None:
        self.direction = direction
        self.strength = strength
        self.cite_evidence = cite_evidence
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
        user = json.loads(payload["messages"][1]["content"])
        signal: dict[str, Any] = {
            "subject": user["subject"],
            "as_of": user["as_of"],
            "direction": self.direction,
            "strength": self.strength,
            "confidence": 0.5,
            "horizon": "5d",
            "evidence_ids": (
                [item["evidence_id"] for item in user["evidence"]] if self.cite_evidence else []
            ),
            "confirmation_conditions": [],
            "invalidation_conditions": [],
            "risk_flags": [],
        }
        if self.direction == "abstain":
            signal["strength"] = 0.0
            signal["abstention_reason"] = "the scripted model declined to take a direction"
        return {
            "id": f"req-{len(self.requests)}",
            "model": "deepseek-flash",
            "system_fingerprint": "fp-c-test",
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"signal": signal, "rationale": "the scripted frame"})
                    }
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }


def _journal(world: World) -> dict[str, Any]:
    return json.loads(world.journal_path().read_text(encoding="utf-8"))


def _llm_predictions(world: World, ts_code: str) -> list[Prediction]:
    """Every stored LLM prediction for one name, across both scoring sets."""
    return [
        prediction
        for record in _records(world, AGENT_LLM_FAMILY)
        for prediction in record.batch.predictions
        if prediction.ts_code == ts_code
    ]


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


# --- the LLM score is the agent's own frame, not the engine's aggregate (N1) --------------------


def test_an_abstention_that_cites_evidence_is_a_coded_abstention_never_a_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N1: declining to take a direction while citing the facts read is an abstention, not 0.0.

    `SignalFrame` allows an abstaining frame to carry `evidence_ids`, and the prompt's "empty
    evidence_ids" instruction cannot be enforced against a model. The engine's aggregate read
    that frame as `neutral` with zero strength and stored a permanent numeric `0.0` -- the
    abstention-as-zero D3 forbids, in a record that cannot be revised. The score must come from
    the one agent's own frame: an abstention is `None` plus `agent_abstained`.
    """
    world = _world(tmp_path, monkeypatch)
    model = FrameModel(direction="abstain", strength=0.0, cite_evidence=True)

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)

    capsys.readouterr()
    assert code == 0
    assert model.calls == 1, "only the evidence-bearing subject is routed"
    predictions = _llm_predictions(world, world.evidence_subject)
    assert predictions, "the evidence subject must be filed by the model family"
    assert {prediction.score for prediction in predictions} == {None}
    assert {prediction.abstention for prediction in predictions} == {ABSTAIN_ALL_ABSTAINED}


def test_a_weak_bullish_answer_keeps_its_own_strength_instead_of_the_engines_band(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N1: a directional answer with |strength| <= 0.15 is not collapsed to neutral 0.0.

    The engine's aggregate applies a 0.15 neutral band, which is not C's scoring rule: the
    family stores the signed strength the agent answered with. A bullish 0.1 is exactly 0.1,
    not the zero that would read as "no opinion" and enter the statistic as a ranked value.
    """
    world = _world(tmp_path, monkeypatch)
    model = FrameModel(direction="bullish", strength=0.1, cite_evidence=True)

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)

    capsys.readouterr()
    assert code == 0
    predictions = _llm_predictions(world, world.evidence_subject)
    assert predictions
    assert {prediction.score for prediction in predictions} == {0.1}
    assert {prediction.abstention for prediction in predictions} == {None}


def test_an_abstention_without_evidence_is_abstained_and_an_unrouted_name_is_not_routed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N1: the two coded abstentions of the model family are still told apart.

    An abstention the agent itself concluded (no evidence IDs cited) is `agent_abstained`; a
    name the router dropped for want of evidence never runs an agent, so it is `agent_not_routed`.
    Neither is a number.
    """
    world = _world(tmp_path, monkeypatch)
    model = FrameModel(direction="abstain", strength=0.0, cite_evidence=False)

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=model)

    capsys.readouterr()
    assert code == 0
    predictions = {
        prediction.ts_code: prediction
        for record in _records(world, AGENT_LLM_FAMILY)
        for prediction in record.batch.predictions
    }
    abstained = predictions[world.evidence_subject]
    assert abstained.score is None
    assert abstained.abstention == ABSTAIN_ALL_ABSTAINED
    others = [
        prediction
        for ts_code, prediction in predictions.items()
        if ts_code != world.evidence_subject
    ]
    assert others, "the fixture must have a subject with no evidence to route"
    assert {prediction.score for prediction in others} == {None}
    assert {prediction.abstention for prediction in others} == {ABSTAIN_NOT_ROUTED}


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


def test_a_crash_after_filing_before_the_journal_replays_without_a_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N4: a rerun that only has to read filed batches must not demand the credential.

    Step 5 filed both LLM batches and the evening died before step 6 (F2). The rerun happens
    without `DEEPSEEK_API_KEY`, for example from launchd with a different environment. Because
    the session's batches are already in the prediction store the provider is assembled in
    replay mode -- no key, no usage store -- so the day is finished from the stored exchanges.
    The credential pre-check still fired on the current command, journalling `credential_missing`
    while the store held the two records.
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
    before = {
        record.record_id: agent.batch_digest(record.batch)
        for record in _records(world, AGENT_LLM_FAMILY)
    }
    assert len(before) == 2, "both LLM batches were filed before the crash"

    monkeypatch.setattr(agent, "_write_journal", real_write_journal)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    replay = ScriptedModel()

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=replay)

    printed = capsys.readouterr().out
    assert code == 0
    assert replay.calls == 0, "replaying a filed day spends no request and needs no key"
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "filed"
    assert journal["llm_family"]["reason"] is None
    after = {
        record.record_id: agent.batch_digest(record.batch)
        for record in _records(world, AGENT_LLM_FAMILY)
    }
    assert after == before, "the filed records must be unchanged, not re-filed"
    assert "credential_missing" not in json.dumps(journal)
    assert "credential_missing" not in printed


def test_an_empty_coverage_set_day_still_replays_without_a_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N4 completeness: a day whose coverage set is empty still counts as filed.

    An empty coverage set is a supported state (the coverage batch is not filed at all,
    `coverage_absent: "empty_set"`), so a crash-after-filing rerun of such a day holds only the
    primary LLM batch. The replay predicate must accept primary alone when coverage is empty;
    otherwise this rerun demands the credential for a day whose one batch is already in the
    store.
    """
    world = _world(tmp_path, monkeypatch, holdings=(), watch=())
    assert world.coverage == (), "the fixture must produce an empty coverage set"
    real_write_journal = agent._write_journal

    def crash_after_filing(path: Path, document: Any) -> str:
        raise agent.StepFailedError("summary", "scripted crash after the batches were filed")

    monkeypatch.setattr(agent, "_write_journal", crash_after_filing)
    assert (
        agent.main(world.arguments(), clock=lambda: world.evening, transport=ScriptedModel("valid"))
        == agent.AgentEveningExit.refused
    )
    capsys.readouterr()
    before = {
        record.record_id: agent.batch_digest(record.batch)
        for record in _records(world, AGENT_LLM_FAMILY)
    }
    assert len(before) == 1, "an empty coverage set files the primary batch only"

    monkeypatch.setattr(agent, "_write_journal", real_write_journal)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    replay = ScriptedModel()

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=replay)

    printed = capsys.readouterr().out
    assert code == 0
    assert replay.calls == 0
    journal = _journal(world)
    assert journal["llm_family"]["status"] == "filed"
    assert journal["llm_family"]["coverage_absent"] == "empty_set"
    after = {
        record.record_id: agent.batch_digest(record.batch)
        for record in _records(world, AGENT_LLM_FAMILY)
    }
    assert after == before
    assert "credential_missing" not in json.dumps(journal)
    assert "credential_missing" not in printed


def test_a_foreign_registrations_llm_batches_do_not_count_as_filed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N4 completeness: only C's own registration makes a day replayable.

    The predicate must filter on the stored declaration's registration digest. If a foreign
    registration's same-session LLM batches were accepted, a day C has never filed would be
    assembled in replay mode and step 5 would refuse a missing exchange by name instead of
    journalling the credential gap. The foreign records below are filed under a different
    registration; with no key C's own day is still a credential gap.
    """
    world = _world(tmp_path, monkeypatch)
    foreign = dataclasses.replace(
        world.c.registered, config_id="f" * 64, registration_sha256="e" * 64
    )
    store = prediction_store_at(world.runtime, clock=lambda: world.evening)
    calendar = outcome_calendar(
        PanelStore(world.step0 / "panel"), "SSE", world.session, as_of=world.publication
    )
    for scoring_set in ("primary", "coverage"):
        declaration = agent.agent_declaration(
            family=AGENT_LLM_FAMILY,
            scoring_set=scoring_set,
            agent_kind="llm_backed",
            registered=foreign,
            model_ref="deepseek/foreign",
            prompt_ref="foreign/prompt",
        )
        batch = agent.agent_batch(
            session=world.session,
            scores=[
                agent.AgentScore(
                    ts_code=world.survivors[0], score=0.1, abstention=None, run_id="foreign-run"
                )
            ],
            declaration=declaration,
            predicted_at=world.evening,
        )
        file_forward_record(store, batch, calendar=calendar, clock=lambda: world.evening)

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=ScriptedModel())

    journal = _journal(world)
    assert code == 0
    assert journal["llm_family"]["status"] == "no_record"
    assert journal["llm_family"]["reason"] == "credential_missing"
    assert "step 5 (llm_family) failed" not in capsys.readouterr().err


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


# --- the D7 dry week ---------------------------------------------------------------------------
#
# The dry week measures the model family's operational behaviour before C's registration exists:
# at most five evenings in a scratch runtime, at most 600 model requests each, operational
# metrics only -- never a score, an IC or a return. It files no prediction, writes no C evidence
# and no evening journal, and its scratch runtime carries the marker the evaluator and the report
# refuse to read as C's forward sample (`strategy_agent_evaluation.DRY_WEEK_SCRATCH_MARKER`).

_DRY_WEEK_JOURNAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "session",
        "status",
        "names",
        "requests",
        "scored",
        "abstained",
        "not_routed",
        "invalid_output",
        "blank_responses",
        "invalid_json_responses",
        "transport_failures",
        "latency_ms",
        "tokens",
        "cap",
        "deadline",
    }
)


def _dry_week_arguments(world: World, scratch: Path, *extra: str) -> list[str]:
    """A dry-week command line against `scratch`, with C's registration deliberately absent.

    No C registration may exist for the dry week (`world.registration` is not a file in this
    fixture); the two registrations it does need are Step 0's committed one and its runtime.
    """
    return [
        "--dry-week",
        "--runtime-dir",
        str(scratch),
        "--step0-runtime-dir",
        str(world.step0),
        "--step0-registration",
        str(world.step0_registration),
        "--registration",
        str(world.registration),
        "--repo",
        str(world.repo),
        "--as-of",
        world.evening.isoformat(),
        *extra,
    ]


def _dry_week_journal(world: World, scratch: Path) -> dict[str, Any]:
    path = agent.dry_week_journal_path(scratch, world.session)
    return json.loads(path.read_text(encoding="utf-8"))


def _dry_week_marker(scratch: Path) -> dict[str, Any]:
    return json.loads(agent.dry_week_marker_path(scratch).read_text(encoding="utf-8"))


def test_a_dry_week_evening_files_no_prediction_journal_or_evidence_and_leaves_step0_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: one dry-week evening is operational measurement, not a filed forward prediction.

    The census is the survivors that carry a limit event; the model family runs through the real
    engine (so its run manifest records the model, and 015's guard is on the path), and the only
    things the scratch runtime gains are the exchange/usage records, the dry-week journal and the
    scratch marker. No prediction batch, no evening journal, no evidence record, and Step 0's own
    store is byte-identical afterwards.
    """
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    before_step0 = _files(world.step0, skip_panel_locks=True)
    model = ScriptedModel("valid")

    code = agent.main(
        _dry_week_arguments(world, scratch), clock=lambda: world.evening, transport=model
    )
    printed = capsys.readouterr().out

    assert code == 0, printed
    journal = _dry_week_journal(world, scratch)
    assert journal["schema"] == agent.DRY_WEEK_EVENING_SCHEMA
    assert journal["session"] == world.session.isoformat()
    assert journal["status"] == "completed"
    assert journal["names"] == 1, "only the survivor carrying a limit event is asked about"
    assert journal["requests"] == 1
    assert journal["scored"] == 1
    assert journal["abstained"] == journal["not_routed"] == journal["invalid_output"] == 0
    assert journal["blank_responses"] == journal["invalid_json_responses"] == 0
    assert journal["transport_failures"] == 0
    assert journal["cap"] == agent.DEFAULT_MAX_MODEL_REQUESTS
    assert datetime.fromisoformat(journal["deadline"]).strftime("%H:%M") == "23:30"
    assert model.calls == 1

    # The real engine path: the run manifest records the model that answered.
    storage = build_storage(runtime_dir=scratch, clock=lambda: world.evening)
    run_id = agent.agent_run_id(
        config_id=agent.DRY_WEEK_CONFIG_ID,
        session=world.session,
        family=AGENT_LLM_FAMILY,
        ts_code=world.evidence_subject,
    )
    manifest = storage.repository.get_run(run_id)
    assert manifest is not None
    assert [(ref.component, ref.version) for ref in manifest.model_versions] == [
        (DEEPSEEK_V4_1_FLASH.provider_id, DEEPSEEK_V4_1_FLASH.model)
    ]

    # Nothing was filed and no C account was written.
    assert prediction_store_at(scratch, clock=lambda: world.evening).list_ids() == ()
    assert not world.journal_path().exists(), "the dry week writes no evening journal"
    assert not (scratch / "agent_evening").exists()
    assert list((scratch / "evidence").rglob("*.parquet")) == [], "no evidence record exists"
    assert _files(world.step0, skip_panel_locks=True) == before_step0

    marker = _dry_week_marker(scratch)
    assert marker == {"schema": DRY_WEEK_SCRATCH_SCHEMA, "evenings": [world.session.isoformat()]}
    assert "候选" in printed and agent.NOT_TRADED in printed
    assert "dry week" in printed.lower() and "request" in printed.lower()


def test_the_dry_week_journal_carries_only_operational_facts_never_a_score_or_a_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: the journal's key set is the operational list, and no outcome field can sneak in.

    The dry week is never evaluated: a key named score, IC, return, mean or label -- at any depth
    the documented shape allows -- would let a reader compute something the decision D7 says the
    dry week does not measure.
    """
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"

    assert (
        agent.main(
            _dry_week_arguments(world, scratch),
            clock=lambda: world.evening,
            transport=ScriptedModel("valid"),
        )
        == 0
    )
    capsys.readouterr()
    journal = _dry_week_journal(world, scratch)
    assert set(journal) == _DRY_WEEK_JOURNAL_FIELDS
    assert set(journal["latency_ms"]) == {"p50", "p95", "mean"}
    assert set(journal["tokens"]) == {"input", "output", "total"}
    # `latency_ms.mean` is the one field D7 asks for under that name; no outcome field may exist
    # anywhere else in the document.
    forbidden = frozenset({"score", "ic", "return", "returns", "mean", "label", "labels"})
    found: list[tuple[str, ...]] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in forbidden and (*path, key) != ("latency_ms", "mean"):
                    found.append((*path, key))
                walk(item, (*path, key))

    walk(journal, ())
    assert found == [], f"the dry-week journal carries outcome fields {found}"
    serialised = json.dumps(journal)
    for word in ("score", "return", "label"):
        assert not re.search(rf'"{word}"\s*:', serialised), word
    assert journal["latency_ms"]["mean"] is not None


def test_a_rerun_of_a_recorded_dry_week_evening_is_refused_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: one journal per session; the second run is refused before anything is written."""
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    assert (
        agent.main(
            _dry_week_arguments(world, scratch),
            clock=lambda: world.evening,
            transport=ScriptedModel("valid"),
        )
        == 0
    )
    capsys.readouterr()
    journal_before = agent.dry_week_journal_path(scratch, world.session).read_bytes()
    marker_before = agent.dry_week_marker_path(scratch).read_bytes()

    code = agent.main(
        _dry_week_arguments(world, scratch), clock=lambda: world.evening, transport=ScriptedModel()
    )

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "already" in err and world.session.isoformat() in err
    assert agent.dry_week_journal_path(scratch, world.session).read_bytes() == journal_before
    assert agent.dry_week_marker_path(scratch).read_bytes() == marker_before


def test_a_fifth_recorded_evening_exhausts_the_scratch_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7's hard cap: at most five evenings per scratch runtime, enforced by the marker."""
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    scratch.mkdir()
    recorded = [f"2026-01-{day:02d}" for day in (5, 6, 7, 8, 9)]
    agent.dry_week_marker_path(scratch).write_text(
        json.dumps({"schema": DRY_WEEK_SCRATCH_SCHEMA, "evenings": recorded}), encoding="utf-8"
    )

    code = agent.main(
        _dry_week_arguments(world, scratch), clock=lambda: world.evening, transport=ScriptedModel()
    )

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "5" in err and ("dry-week" in err or "D7" in err)
    assert not agent.dry_week_journal_path(scratch, world.session).exists()
    assert _dry_week_marker(scratch)["evenings"] == recorded, "the marker was not rewritten"


def test_the_600_request_ceiling_is_hard_and_the_evening_stops_when_it_is_reached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: 600 requests per evening is a ceiling, and crossing it stops the evening by name.

    A smaller cap exercises the same path cheaply: the invalid answer triggers a retry, the retry
    is the request past the allowance, and the evening records `request_cap_reached` with the
    requests it did spend. A cap above 600 is refused before the run starts.
    """
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    model = ScriptedModel("invalid", "valid")

    code = agent.main(
        _dry_week_arguments(world, scratch, "--max-model-requests", "1"),
        clock=lambda: world.evening,
        transport=model,
    )
    capsys.readouterr()

    assert code == 0
    assert model.calls == 1, "the request past the allowance was never sent"
    journal = _dry_week_journal(world, scratch)
    assert journal["status"] == agent.NO_RECORD_REQUEST_CAP_REACHED
    assert journal["requests"] == 1
    assert journal["cap"] == 1
    assert journal["invalid_output"] == 0, "a cap is not an answer"

    above = tmp_path / "dry-scratch-above"
    code = agent.main(
        _dry_week_arguments(world, above, "--max-model-requests", "601"),
        clock=lambda: world.evening,
        transport=ScriptedModel("valid"),
    )
    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "600" in err
    assert not agent.dry_week_journal_path(above, world.session).exists()


def test_a_dry_week_evening_stops_with_the_named_status_it_met(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D6/D7: a transport failure, a deadline and a configuration fault are statuses, not answers.

    Each stops the evening and is journalled; the operational account of a failed evening is what
    the dry week exists to collect, so the journal is written with the status and the counts it
    reached, and no score is recorded.
    """
    cases = (
        ("transport", ScriptedModel("error"), None, agent.NO_RECORD_TRANSPORT_ERROR),
        ("configuration", ConfigurationFailingModel(), None, agent.NO_RECORD_CONFIGURATION_ERROR),
        ("deadline", ScriptedModel("valid"), time(23, 31), agent.NO_RECORD_DEADLINE_REACHED),
    )
    for name, model, wall_time, expected in cases:
        case = tmp_path / name
        case.mkdir()
        world = _world(case, monkeypatch)
        scratch = case / "dry-scratch"

        def clock(world: World = world, wall_time: time | None = wall_time) -> datetime:
            if wall_time is None:
                return world.evening
            return datetime.combine(world.session, wall_time, tzinfo=SHANGHAI)

        code = agent.main(_dry_week_arguments(world, scratch), clock=clock, transport=model)
        capsys.readouterr()

        assert code == 0, f"{name}: a failed dry-week evening is still journalled"
        journal = _dry_week_journal(world, scratch)
        assert journal["status"] == expected, name
        assert journal["scored"] == 0, name
        assert journal["requests"] >= 0, name
        marker = json.loads(agent.dry_week_marker_path(scratch).read_text(encoding="utf-8"))
        assert marker == {
            "schema": DRY_WEEK_SCRATCH_SCHEMA,
            "evenings": [world.session.isoformat()],
        }, (
            f"{name}: every started evening is in the marker -- a failed one included, or the "
            "D7 cap and the dry-week isolation guard would not see it"
        )


def test_dry_week_requires_its_own_scratch_runtime_and_step0_and_refuses_a_registered_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: the scratch runtime and Step 0's runtime are required, and C's runtime is refused.

    A runtime that already carries a registered evening's journal or filed predictions is C's
    forward runtime, not a scratch directory: writing the marker there would mark a real trial's
    store as disposable, so the refusal happens before anything is read or written.
    """
    world = _world(tmp_path, monkeypatch)

    code = agent.main(["--dry-week", "--step0-runtime-dir", str(world.step0)])
    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "--runtime-dir is required" in err

    code = agent.main(["--dry-week", "--runtime-dir", str(tmp_path / "scratch")])
    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "--step0-runtime-dir is required" in err
    assert not (tmp_path / "scratch").exists()

    assert agent.main(world.arguments("--no-llm"), clock=lambda: world.evening) == 0
    capsys.readouterr()
    before = _files(world.runtime)
    marker = agent.dry_week_marker_path(world.runtime)

    code = agent.main(
        _dry_week_arguments(world, world.runtime),
        clock=lambda: world.evening,
        transport=ScriptedModel("valid"),
    )

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "registered" in err and "scratch" in err
    assert not marker.exists()
    assert _files(world.runtime) == before


def test_a_dry_week_run_does_not_require_a_c_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D7: the dry week precedes registration, so step 1 is not run and C's file is not read."""
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    assert not world.registration.exists()

    def unreachable_admission(options: Any) -> Any:
        raise AssertionError("the dry week must not admit a C registration")

    monkeypatch.setattr(agent, "_admit", unreachable_admission)

    code = agent.main(
        _dry_week_arguments(world, scratch),
        clock=lambda: world.evening,
        transport=ScriptedModel("valid"),
    )
    capsys.readouterr()

    assert code == 0
    assert agent.dry_week_journal_path(scratch, world.session).is_file()


class _BlankThenInvalidModel(ScriptedModel):
    """A transport whose answers are blank first, then not JSON: both are operations to count."""

    def __init__(self) -> None:
        super().__init__("blank", "invalid")

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
        content = "" if action == "blank" else "not a json object"
        return {
            "id": f"req-{len(self.requests)}",
            "model": "deepseek-flash",
            "system_fingerprint": "fp-c-test",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }


def test_blank_and_invalid_json_answers_are_counted_and_an_invalid_output_is_not_a_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D6/D7: the meter counts what the endpoint did, and `model_invalid_output` is an answer.

    A blank content and a non-JSON content are two failed answers to the same subject; after the
    retries the agent's coded abstention is `model_invalid_output`, which is the family's answer,
    so the evening completes and the operational counts say what happened.
    """
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    model = _BlankThenInvalidModel()

    code = agent.main(
        _dry_week_arguments(world, scratch), clock=lambda: world.evening, transport=model
    )
    capsys.readouterr()

    assert code == 0
    assert model.calls == 2, "the blank answer was retried once and the second answer was invalid"
    journal = _dry_week_journal(world, scratch)
    assert journal["status"] == "completed"
    assert journal["requests"] == 2
    assert journal["blank_responses"] == 1
    assert journal["invalid_json_responses"] == 1
    assert journal["invalid_output"] == 1
    assert journal["scored"] == 0
    assert journal["transport_failures"] == 0
    assert journal["tokens"] == {"input": 22, "output": 14, "total": 36}


def _evening_of(session: date) -> datetime:
    """The wall clock a dry-week evening of `session` runs at: after its publication, before the
    next open's call auction."""
    return datetime.combine(session, time(18, 30), tzinfo=SHANGHAI)


def _dry_week_session_arguments(
    world: World, scratch: Path, session: date, *extra: str
) -> list[str]:
    """`_dry_week_arguments` with the pinned clock moved to `session`."""
    arguments = _dry_week_arguments(world, scratch, *extra)
    arguments[arguments.index("--as-of") + 1] = _evening_of(session).isoformat()
    return arguments


def _file_step0_day(world: World, session: date) -> None:
    """File Step 0's one bound, on-time record and complete day journal for `session`.

    The five-evening cap test needs more than the fixture's single day; the records are filed
    through the real forward-session protocol, not hand-faked.
    """
    body = json.loads(world.step0_registration.read_text(encoding="utf-8"))
    registered = RegisteredConfiguration(
        config_id=body["config_id"],
        registration_sha256=hashlib.sha256(world.step0_registration.read_bytes()).hexdigest(),
        code_commit=body["code_commit"],
        seed=SEED,
    )
    evening = _evening_of(session)
    store = FilePredictionStore(world.step0 / "predictions", clock=lambda: evening)
    batch = _survivor_batch(
        tuple(world.panel.securities), session=session, registered=registered, evening=evening
    )
    calendar = outcome_calendar(
        PanelStore(world.step0 / "panel"),
        "SSE",
        session,
        as_of=session_publication_instant(session),
    )
    file_forward_record(store, batch, calendar=calendar, clock=lambda: evening)
    journal = daily.survivor_journal_directory(world.step0, body["config_id"]) / (
        f"{session.isoformat()}.json"
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps(_journal_document(session, (), ())), encoding="utf-8")


def _sessions_with_limit_events(world: World, count: int) -> tuple[date, ...]:
    """The first `count` panel sessions whose survivors carry a limit event.

    A dry-week evening only spends a request when its census is non-empty, so the cap test picks
    sessions that are measurable by construction (the fixture's scripted streak) rather than
    assuming every session has an event.
    """
    panel = PanelStore(world.step0 / "panel")
    chosen: list[date] = []
    for session in world.panel.sessions:
        events = limit_events_on(
            panel,
            session=session,
            as_of=session_publication_instant(session),
            exchange="SSE",
        )
        if events:
            chosen.append(session)
        if len(chosen) == count:
            return tuple(chosen)
    raise AssertionError(f"the fixture panel prices fewer than {count} sessions with limit events")


class _RefusingTransport:
    """A model transport that fails the test if it is ever asked to send a request."""

    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, **arguments: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("a dry-week evening past the cap must send no model request")


def test_five_failed_evenings_exhaust_the_scratch_runtime_and_the_sixth_spends_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F1 (blocking): D7's cap counts every evening that STARTS, whatever its status.

    Five evenings that each stop on the request cap are five measurements of the deployment;
    the marker records each of them, so the sixth is refused by the cap with zero model requests
    and no new journal. Before this fix the failed evenings were invisible to the cap, so an
    operator could run unlimited evenings and spend unlimited requests.
    """
    world = _world(tmp_path, monkeypatch)
    scratch = tmp_path / "dry-scratch"
    sessions = _sessions_with_limit_events(world, 5)
    for session in sessions:
        _file_step0_day(world, session)
    for index, session in enumerate(sessions):
        transport = _RefusingTransport()
        evening = _evening_of(session)

        code = agent.main(
            _dry_week_session_arguments(world, scratch, session, "--max-model-requests", "0"),
            clock=lambda evening=evening: evening,
            transport=transport,
        )
        capsys.readouterr()

        assert code == 0, f"evening {index} was journalled"
        assert transport.calls == 0, "a zero cap sends no request"
        journal = json.loads(
            agent.dry_week_journal_path(scratch, session).read_text(encoding="utf-8")
        )
        assert journal["status"] == agent.NO_RECORD_REQUEST_CAP_REACHED
        marker = json.loads(agent.dry_week_marker_path(scratch).read_text(encoding="utf-8"))
        assert len(marker["evenings"]) == index + 1, (
            "each started evening is in the marker, failed or not"
        )
        assert session.isoformat() in marker["evenings"]

    sixth = sessions[-1] + timedelta(days=1)
    transport = _RefusingTransport()
    code = agent.main(
        _dry_week_session_arguments(world, scratch, sixth, "--max-model-requests", "0"),
        clock=lambda: _evening_of(sixth),
        transport=transport,
    )
    err = capsys.readouterr().err

    assert code == agent.AgentEveningExit.refused
    assert "5" in err and "dry-week" in err
    assert transport.calls == 0, "the cap is checked before any request is constructed"
    assert not agent.dry_week_journal_path(scratch, sixth).exists()


def test_the_registered_evening_refuses_a_registration_whose_anchor_is_not_the_first_session_after_the_commit(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N2: a mis-planned commit time fails closed on day 1, not at L6 six months later.

    The registration is written before the commit exists; the anchor is right only if it is the
    first trading session whose 16:30 publication is after the commit's time. Here the commit
    landed after the anchor session's close, so the first session after it is the next trading
    day: the registered evening must refuse in step 1 with nothing written and no model request.
    """
    world = _world(tmp_path, monkeypatch)
    expected = world.panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 2]
    after_the_close = datetime.combine(world.session, time(16, 31), tzinfo=SHANGHAI)
    misplanned = dataclasses.replace(world.c, registration_committed_at=after_the_close)
    monkeypatch.setattr(agent, "_admit", lambda options: (world.repo, misplanned))
    assert world.session != expected, "the fixture's next session must differ from the anchor's"
    before_step0 = _files(world.step0, skip_panel_locks=True)
    transport = _RefusingTransport()

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=transport)
    err = capsys.readouterr().err

    assert code == agent.AgentEveningExit.refused
    assert "step 1 (registration) failed" in err
    assert world.session.isoformat() in err, "the registered anchor is named"
    assert expected.isoformat() in err, "the expected first session after the commit is named"
    assert after_the_close.isoformat() in err, "the commit instant is named"
    assert transport.calls == 0, "the refusal comes before any model request"
    assert not world.runtime.exists(), "a step-1 refusal writes nothing"
    assert not world.journal_path().exists()
    assert not (world.runtime / "predictions").exists()
    assert not (world.runtime / "evidence").exists()
    assert _files(world.step0, skip_panel_locks=True) == before_step0


def test_the_anchor_check_refuses_a_missing_step0_panel_without_creating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """N2's step-1 calendar read must not create a missing Step 0 panel (fix round 2).

    PanelStore's opener creates its root; a refusal must write nothing, so the missing panel is
    named before the store is opened and the directory is not created.
    """
    world = _world(tmp_path, monkeypatch)
    missing = tmp_path / "missing-step0"
    args = world.arguments()
    args[args.index("--step0-runtime-dir") + 1] = str(missing)
    transport = _RefusingTransport()

    code = agent.main(args, clock=lambda: world.evening, transport=transport)
    err = capsys.readouterr().err

    assert code == agent.AgentEveningExit.refused
    assert "step 1 (registration) failed" in err
    assert "panel" in err
    assert transport.calls == 0
    assert not (missing / "panel").exists(), "the refusal must not create the panel directory"
    assert not world.runtime.exists()


def _git_at(repository: Path, *arguments: str, at: datetime) -> str:
    """Run git on `repository` with pinned dates through the suite's hermetic helper (N5).

    `research_repo.git` drops every inherited `GIT_*` variable, pins `GIT_CONFIG_NOSYSTEM=1` and
    `GIT_CONFIG_GLOBAL=os.devnull`, and fixes the author/committer identity; this wrapper adds the
    fixed `-c user.name`/`-c user.email` and always passes `-C <repository>`.
    """
    return research_repo.git(
        repository,
        "-C",
        str(repository),
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        *arguments,
        at=at,
    ).strip()


def _git_unpinned(repository: Path, *arguments: str) -> str:
    """The hermetic, unpinned-date form of `_git_at` (N5): same env and `-C repository`."""
    return research_repo.git(
        repository,
        "-C",
        str(repository),
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        *arguments,
    ).strip()


def _copy_bound_code(repository: Path) -> None:
    """The code the registration binds, copied into the throwaway repository (rule 6: generated)."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache")
    for name in ("src", "scripts"):
        shutil.copytree(ROOT / name, repository / name, ignore=ignore)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, repository / name)


def test_the_real_admission_git_helpers_are_hermetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N5: the subprocess test's git helpers must not honour an inherited git environment.

    They run `git` on a throwaway repository; an inherited `GIT_DIR` would act on the outer
    repository (the incident class that once set `core.bare=true` on the shared repo), and an
    inherited global config could sign or hook the commit. The helpers drop every `GIT_*`
    variable, pin `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`, and pass `-C` to the
    throwaway repository.
    """
    repository = tmp_path / "hermetic"
    repository.mkdir()
    (repository / "file.txt").write_text("content\n", encoding="utf-8")
    hostile_global = tmp_path / "hostile.gitconfig"
    hostile_global.write_text("[commit]\n\tgpgsign = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "hostile-outer-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile_global))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")

    _git_unpinned(repository, "init", "-q", "--template=")
    _git_unpinned(repository, "add", "file.txt")
    _git_at(
        repository,
        "commit",
        "-q",
        "-m",
        "pinned",
        at=datetime(2026, 1, 5, 9, 0, tzinfo=SHANGHAI),
    )

    assert (
        _git_unpinned(repository, "-C", str(repository), "rev-parse", "--is-inside-work-tree")
        == "true"
    )
    assert _git_unpinned(repository, "rev-parse", "--is-inside-work-tree") == "true"


def test_the_real_admission_derives_the_anchor_from_the_committed_registration(
    tmp_path: Path,
) -> None:
    """The real `_admit` wiring (review-batch3-3 coverage note), not a monkeypatched seam.

    `admit_registered_code` requires the registration committed in a repository whose bound code
    is the registration's `code_commit`, and the running package/scripts imported from that
    repository. That cannot hold in the test process (the package is imported from this worktree),
    so the test builds a throwaway repository from the worktree's `src`/`scripts` and runs the
    real `agent_evening` in a child process with `PYTHONPATH` pointing at the copy. The registered
    anchor is the anchor session itself, but the registration commit lands at 16:31 on that
    session: the first trading session after the commit is the next one, so real step 1 must
    refuse by name with no model request and no write.
    """
    panel = _on_sse(generate_limit_streak_panel())
    step0 = tmp_path / "step0"
    write_generated_panel(PanelStore(step0 / "panel"), panel)
    session = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 1]
    expected = panel.sessions[LIMIT_STREAK_FIRST_SESSION_INDEX + 2]
    evening = datetime.combine(session, time(18, 30), tzinfo=SHANGHAI)
    committed_at = datetime.combine(session, time(16, 31), tzinfo=SHANGHAI)

    repository = tmp_path / "repo"
    repository.mkdir()
    _git_unpinned(repository, "init", "-q", "--template=")
    _copy_bound_code(repository)
    _git_unpinned(repository, "add", "-A")
    _git_at(
        repository,
        "commit",
        "-q",
        "-m",
        "code",
        at=datetime.combine(session, time(9, 0), tzinfo=SHANGHAI),
    )
    code_commit = _git_unpinned(repository, "rev-parse", "HEAD")

    step0_config = {
        "kind": "survivor_book/v1",
        "survivor_book": BOOK.model_dump(mode="json", exclude_computed_fields=True),
        "adapter": ADAPTER,
    }
    registry.register(
        step0_config,
        {"note": "fixture"},
        repository / "docs" / "research" / "p7-registration.json",
        code_commit=code_commit,
        settings={"random_seed": SEED},
    )
    c_config = {
        "kind": "agent_forward/v1",
        "salt": "c-real-admission-salt-2026",
        "primary_size": C_PRIMARY_SIZE,
    }
    registry.register(
        c_config,
        {"note": "fixture"},
        repository / "docs" / "research" / "p7-c-registration.json",
        code_commit=code_commit,
        settings={
            "random_seed": C_SEED,
            "agent_forward_anchor": {
                "schema_version": "agent-forward-anchor/v1",
                "session": session.isoformat(),
            },
        },
    )
    _git_unpinned(repository, "add", "docs/research")
    _git_at(
        repository,
        "commit",
        "-q",
        "-m",
        "register",
        at=committed_at,
    )

    runtime = tmp_path / "runtime"
    before = _files(step0, skip_panel_locks=True)
    arguments = [
        "--runtime-dir",
        str(runtime),
        "--step0-runtime-dir",
        str(step0),
        "--step0-registration",
        "docs/research/p7-registration.json",
        "--registration",
        "docs/research/p7-c-registration.json",
        "--repo",
        str(repository),
        "--as-of",
        evening.isoformat(),
    ]
    snippet = (
        "import datetime\n"
        "import sys\n"
        f"sys.path.insert(0, {str(repository / 'scripts')!r})\n"
        f"sys.path.insert(0, {str(repository / 'scripts' / 'research')!r})\n"
        "import agent_evening as agent\n"
        "class Refusing:\n"
        "    calls = 0\n"
        "    def post_json(self, *args, **kwargs):\n"
        "        raise AssertionError('the evening must send no request')\n"
        f"code = agent.main({arguments!r}, clock=lambda: datetime.datetime.fromisoformat("
        f"{evening.isoformat()!r}), transport=Refusing())\n"
        "print('EXIT', code)\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "DEEPSEEK_API_KEY", "NO_COLOR"}
    }
    environment |= {
        "PYTHONPATH": str(repository / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    finished = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    assert "EXIT 2" in finished.stdout, (finished.stdout, finished.stderr)
    assert "step 1 (registration) failed" in finished.stderr
    assert session.isoformat() in finished.stderr, "the registered anchor is named"
    assert expected.isoformat() in finished.stderr, "the expected session is named"
    assert committed_at.isoformat() in finished.stderr, "the commit instant is named"
    assert not runtime.exists(), "a step-1 refusal writes nothing"
    assert _files(step0, skip_panel_locks=True) == before


def test_the_registered_evening_refuses_a_dry_week_marked_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2: a dry-week scratch runtime is never filed into, by name and before admission.

    Pointing C's registered evening at the L4 scratch directory would file forward records into
    a runtime the evaluator refuses six months later; the refusal belongs in step 1, before any
    read, any request and any write.
    """
    world = _world(tmp_path, monkeypatch)
    world.runtime.mkdir()
    marker = world.runtime / DRY_WEEK_SCRATCH_MARKER
    marker.write_text(
        json.dumps({"schema": DRY_WEEK_SCRATCH_SCHEMA, "evenings": [world.session.isoformat()]}),
        encoding="utf-8",
    )
    before = _files(world.runtime)
    transport = _RefusingTransport()

    code = agent.main(world.arguments(), clock=lambda: world.evening, transport=transport)

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "step 1 (registration) failed" in err
    assert "dry-week" in err and DRY_WEEK_SCRATCH_MARKER in err
    assert transport.calls == 0
    assert _files(world.runtime) == before, "a refused evening writes nothing"
    assert not world.journal_path().exists()


def test_pin_worktree_refuses_a_dry_week_marked_runtime_and_changes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F2's operator half: `--pin-worktree` refuses a marked runtime before any git work.

    The pin command is where the L5 operator names C's runtime; a scratch runtime there would
    pin the wrong store, so the marker is refused by name and no worktree is created or moved.
    """
    repository, registration = _pin_world(tmp_path)
    marked = tmp_path / "marked"
    marked.mkdir()
    (marked / DRY_WEEK_SCRATCH_MARKER).write_text(
        json.dumps({"schema": DRY_WEEK_SCRATCH_SCHEMA, "evenings": []}), encoding="utf-8"
    )

    def checkout_must_not_run(*arguments: Any, **keywords: Any) -> Any:
        raise AssertionError("a marked runtime is refused before the checkout is looked up")

    monkeypatch.setattr(registry, "registered_checkout", checkout_must_not_run)
    pinned = tmp_path / "pinned"

    code = agent.main(
        [
            "--pin-worktree",
            str(pinned),
            "--repo",
            str(repository),
            "--registration",
            str(registration),
            "--runtime-dir",
            str(marked),
        ]
    )

    err = capsys.readouterr().err
    assert code == agent.AgentEveningExit.refused
    assert "dry-week" in err
    assert not pinned.exists(), "nothing was created"
