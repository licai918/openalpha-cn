"""The agent evening command (`V2-P7-013`, plan C Task 2.5): score, file, never trade.

Plan C registers one forward family over Step 0's survivor census -- the model-backed
`agent_llm_structured/v1` (its incremental partial rank IC at h=1 is C's one primary statistic,
decision D2) and the deterministic `agent_rule_baseline/v1` (permanently exploratory) -- and this
command is where both are produced. It runs six steps, named by `AGENT_STEPS`:

1. **registration** -- C's committed registration, admitted with `registry.admit_registered_code`
   against the command's own bytes (`C_SCRIPTS`), so the code filing a forward result is the code
   the registration binds. `kind` must be `agent_forward/v1`; the registration names the sample
   salt and the primary set's size, which are research parameters and are never defaulted here.
2. **step-0 record** -- Step 0's prediction store and panel are opened **read-only**: the session
   must hold exactly one bound, on-time `survivor_book_composite/v1` record, and Step 0's day
   journal must be complete, or the command refuses `Step 0 has not filed {session}` and writes
   nothing. Survivors come from `record_scope` (the day's `screen().survivors`, rebuilt from the
   record alone); the book's holdings and watch list come from `plan_names` of Step 0's journal.
3. **evidence** -- `panel_limit_events.limit_events_on` derives the session's limit events from
   Step 0's panel at the session's own 16:30 publication instant, `limit_event_snapshots` turns
   them into evidence, and the batch is appended to C's own evidence store. A restated session is
   refused by name before the append (`refuse_a_restated_session`, 011's rule); a rerun of the
   same session appends nothing, because the partition is content-addressed by `evidence_id`.
4. **rule family** -- one `ResearchEngine.run_cycle` per name of primary union coverage under
   `baseline_agents()`, the score mapped by `domain.agent_forward.score_of`, and two batches filed
   (`c-primary`, `c-coverage`) through `strategy_agent_forward.file_agent_batch`. Deterministic:
   a rerun files the same bytes and the same journal document.
5. **llm family** -- skipped by `--no-llm` and journalled `no_record: llm_disabled`. Otherwise
   the **same engine as step 4** runs one `ResearchRunRequest` per name with
   `StructuredSignalAgent(prompt=V2, include_facts=True, evidence_families={"market_event"})`
   and `max_attempts=2` over `llm_provider(DEEPSEEK_V4_1_FLASH)`. The engine path is what makes
   015's `refuse_llm_agents_in_history` history/72-hour guard structural on the only production
   model path and what stores a run manifest recording the model and prompt versions. The engine
   is there for the guard, the manifest and the replay record, **not for the number**: each
   name's stored score is the one composed agent's own structured frame -- its signed strength
   with no neutral band, or `None` plus `agent_abstained` when the agent declines, whatever
   evidence it cites -- never the engine's aggregate (fix round 2's N1). The provider's
   transport is a request budget applied **inside** `RecordingJsonTransport`: the hard
   `--max-model-requests` cap (default 600) and the wall `--deadline` (default 23:30 Shanghai)
   are spent only on requests that would reach the endpoint, and every HTTP attempt -- a
   provider retry included -- is one request, while a replayed answer costs none and is not
   deadline-gated. The provider is assembled `replay=False` only while a request still has to be
   sent; a rerun whose two batches are already filed sends none, so it is assembled
   `replay=True` -- no credential, no usage store -- and an unrecorded request is a refusal
   rather than a key error (fix round 2's N4). A missing or blank credential, any
   other `ModelConfigurationError`, a transport failure, the cap or the deadline leaves **no
   batch for that set** and journals the reason (decision D6, fix round 1's F1/F2/F8); an
   invalid answer after its retries is an abstention coded `model_invalid_output`, never a zero.
   Each stored exchange's `response.model` and `system_fingerprint` are journalled.
6. **summary** -- the write-once journal and the printed candidate lines (`候选`, "not traded"),
   the counts, and -- when the model family was not filed -- the distinct number of names left
   unscored, labelled with the family's `no_record` reason.

## The journal is write-once, and a rerun must agree with it

`agent_journal_dir_at(runtime)` holds one `<session>.json`, written at step 6 by a temporary file
plus `os.replace`. A rerun recomputes the day and compares the bytes: the same document is
`unchanged` and is not rewritten, a different one is refused and the first account stands. Every
field is therefore a fact of the day rather than of the run -- no wall clock, no `created`/
`unchanged` filing outcome -- and the model family's exchange list is read back from the stored
exchanges, so a replayed rerun journals the same identities as the live run.

A journal that recorded `no_record` for the model family is the day's account, and a rerun keeps
it: the family is not fileable that session (D6), so the rerun makes no model request and
journals the same reason. A day that *did* file is different: its rerun walks the stored
exchanges and the engine's completed runs, so it spends nothing against the cap, is not stopped
by the wall deadline, and reproduces the journal byte for byte -- including a rerun after a
crash that filed the batches but died before step 6. Such a rerun needs no credential: the two
filed batches put the provider in replay mode (N4), so a launchd environment without
`DEEPSEEK_API_KEY` finishes the day instead of journalling `credential_missing` over the store.

## The D7 dry week (`--dry-week`)

A mode of this command rather than a second script: the dry week exercises the same Step 0 read,
the same engine and the same provider assembly, and measures only what the endpoint *did* --
requests, blank and non-JSON answers, transport failures, latency and tokens -- on a scratch
runtime instead of C's. It needs no C registration (nothing is filed, so there is nothing to
register), files no prediction, writes no C evidence and no evening journal, and its only output
is one `dry_week/<session>.json` plus the root marker `c-dry-week-scratch.json` the evaluator and
the report refuse to read as C's forward sample. At most five evenings per scratch runtime and at
most 600 model requests each; the sixth evening and a rerun of a recorded one are refused.

## What it never does

It never trades, never touches Step 0's runtime, never edits the registration, and never spends a
request outside the cap. Every test drives a scripted transport or a recorded exchange store; the
socket guard stays on. Wording is candidate (`候选`) throughout: a passing suite is not evidence
that the family has an increment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from time import perf_counter
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402  (helpers only; this script never edits it)
import registry  # noqa: E402
from p7_smoke_requests import CappedCounter, RequestCapReached  # noqa: E402

from openalpha_cn.agents.baseline import baseline_agents  # noqa: E402
from openalpha_cn.agents.model import ModelProviderFailure, StructuredSignalAgent  # noqa: E402
from openalpha_cn.agents.prompts import STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT  # noqa: E402
from openalpha_cn.domain.agent_forward import (  # noqa: E402
    ABSTAIN_ALL_ABSTAINED,
    ABSTAIN_INVALID_OUTPUT,
    ABSTAIN_NOT_ROUTED,
    AGENT_LLM_FAMILY,
    AGENT_RULE_FAMILY,
    coverage_set,
    primary_sample,
    refuse_a_salt_tied_to_the_book,
    score_of,
)
from openalpha_cn.domain.alpha_model import PredictionBatch  # noqa: E402
from openalpha_cn.domain.json_value import canonical_json_bytes  # noqa: E402
from openalpha_cn.domain.run_mode import RunMode  # noqa: E402
from openalpha_cn.domain.run_request import ResearchRunRequest  # noqa: E402
from openalpha_cn.domain.trading_calendar import TradingCalendar  # noqa: E402
from openalpha_cn.evidence.limit_events import (  # noqa: E402
    PANEL_DERIVED_PROVIDER_ID,
    limit_event_snapshots,
    refuse_a_restated_session,
)
from openalpha_cn.models.deepseek import DEEPSEEK_V4_1_FLASH  # noqa: E402
from openalpha_cn.models.openai_compatible import (  # noqa: E402
    JsonTransport,
    ModelConfigurationError,
    ModelTransportError,
)
from openalpha_cn.models.recording import ModelExchangeNotStoredError  # noqa: E402
from openalpha_cn.panel.store import PanelStore  # noqa: E402
from openalpha_cn.panel_ingest import session_publication_instant  # noqa: E402
from openalpha_cn.panel_limit_events import limit_events_on  # noqa: E402
from openalpha_cn.runtime.composition import (  # noqa: E402
    agent_journal_dir_at,
    build_storage,
    evidence_store_at,
    llm_provider,
    model_exchange_store_at,
    prediction_store_at,
    research_engine,
)
from openalpha_cn.strategy_agent_evaluation import (  # noqa: E402
    DRY_WEEK_SCRATCH_MARKER,
    DRY_WEEK_SCRATCH_SCHEMA,
)
from openalpha_cn.strategy_agent_forward import (  # noqa: E402
    AGENT_SCRIPTS,
    REGISTRATION_HYPERPARAMETER,
    SCORING_SET_HYPERPARAMETER,
    AgentScore,
    agent_batch,
    agent_declaration,
    agent_run_id,
    file_agent_batch,
)
from openalpha_cn.strategy_forward_session import (  # noqa: E402
    ForwardSessionRefused,
    outcome_calendar,
)
from openalpha_cn.strategy_registration import (  # noqa: E402
    SHANGHAI,
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
    registered_at,
    registration_cutoff,
)
from openalpha_cn.strategy_survivor import read_survivor_registration  # noqa: E402
from openalpha_cn.strategy_survivor_forward import record_scope  # noqa: E402
from openalpha_cn.strategy_survivor_morning import MorningCheckError, plan_names  # noqa: E402
from openalpha_cn.strategy_survivor_registration import (  # noqa: E402
    SURVIVOR_FAMILY,
    survivor_record_is_bound,
)

AGENT_STEPS: Final[tuple[str, ...]] = (
    "registration",
    "step0_record",
    "evidence",
    "rule_family",
    "llm_family",
    "summary",
)
"""The six steps, in order; a refusal names the step it belongs to by this tuple."""

REPOSITORY: Final[Path] = _SCRIPTS.parent
"""The checkout this script lives in: the default registration, repository and journal base."""

REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p7-c-registration.json"
"""C's registration (plan C batch 3 writes it); a relative `--registration` resolves here."""
STEP0_REGISTRATION_IN_REPOSITORY: Final[str] = "docs/research/p7-registration.json"
"""Step 0's registration: the book whose survivors, holdings and watch list C scores."""
C_SCRIPTS: Final[tuple[str, ...]] = AGENT_SCRIPTS
"""The code this batch binds into C's admission: the evening command and the report.

One spelling (`strategy_agent_forward.AGENT_SCRIPTS`) is what the registration writer, this
command's step 1 and the report's own admission all read (`V2-P7-013` task 3.3, `V2-P7-014`)."""

AGENT_REGISTRATION_KIND: Final[str] = "agent_forward/v1"
"""What C's registered configuration declares itself: never a survivor or a P6 configuration."""

JOURNAL_SCHEMA: Final[str] = "openalpha-agent-evening/v1"
LAUNCHD_LABEL: Final[str] = "com.openalpha.agent-evening"
LAUNCHD_HOUR: Final[int] = 20
LAUNCHD_MINUTE: Final[int] = 30
"""The printed launchd job: every weekday at 20:30 local time. Printed, never installed."""
DEFAULT_MAX_MODEL_REQUESTS: Final[int] = 600
DEFAULT_DEADLINE: Final[str] = "23:30"
"""Decision D7's budget: at most 600 model requests and no request after 23:30 Shanghai."""
AGENT_ID: Final[str] = "structured-signal-agent"
NOT_TRADED: Final[str] = "not traded"
CANDIDATE: Final[str] = "候选"

NO_RECORD_LLM_DISABLED: Final[str] = "llm_disabled"
NO_RECORD_TRANSPORT_ERROR: Final[str] = "transport_error"
NO_RECORD_REQUEST_CAP_REACHED: Final[str] = "request_cap_reached"
NO_RECORD_DEADLINE_REACHED: Final[str] = "deadline_reached"
NO_RECORD_CREDENTIAL_MISSING: Final[str] = "credential_missing"
NO_RECORD_CONFIGURATION_ERROR: Final[str] = "configuration_error"
"""The six `no_record` reasons the journal may carry for the model family (decision D6).

`credential_missing` and `configuration_error` are F1's ruled addition: a deployment fault is a
family failure, never an answer. `credential_missing` is the endpoint's named environment
variable unset or blank before anything was asked; `configuration_error` is any other
`ModelConfigurationError` from the provider assembly or the request path (an unverified endpoint,
a malformed base URL or extra payload, a credential that vanished mid-run). Only a genuine
invalid answer after its retries is `model_invalid_output`.
"""

DRY_WEEK_EVENING_SCHEMA: Final[str] = "openalpha-c-dry-week-evening/v1"
"""The dry-week evening's one journal document (D7): operational facts only, never an outcome."""
DRY_WEEK_JOURNAL_DIRECTORY: Final[str] = "dry_week"
"""Where a scratch runtime's dry-week evenings live, one document per session."""
DRY_WEEK_MAX_EVENINGS: Final[int] = 5
"""D7: at most five dry-week evenings per scratch runtime."""
DRY_WEEK_MAX_REQUESTS: Final[int] = 600
"""D7: at most 600 model requests per dry-week evening, a hard ceiling on `--max-model-requests`."""
DRY_WEEK_CONFIG_ID: Final[str] = "c-dry-week-scratch"
"""The run address a dry-week evening's cycles are minted under: never C's `config_id`."""
DRY_WEEK_COMPLETED: Final[str] = "completed"
"""The status of an evening that ran to its end; every other status names what stopped it."""

_EMPTY_SET: Final[str] = "empty_set"
"""Why a scoring set filed no batch: there is nothing in it. Never a `no_record` gap."""
_LIMIT_KINDS: Final[frozenset[str]] = frozenset({"limit_up", "consecutive_board", "broken_board"})
_MARKET_EVENT_FAMILIES: Final[frozenset[str]] = frozenset({"market_event"})


class AgentEveningExit:
    """What the command returns: the daily command's own division of a refusal."""

    ok: Final[int] = 0
    refused: Final[int] = 2
    no_registration: Final[int] = 2
    code_not_registered: Final[int] = 3


class StepFailedError(RuntimeError):
    """One step refused, with the step's name and the message a reviewer reads."""

    def __init__(
        self, step: str, detail: str, *, exit_code: int = AgentEveningExit.refused
    ) -> None:
        super().__init__(detail)
        self.step = step
        self.detail = detail
        self.exit_code = exit_code


class AgentRegistrationError(ValueError):
    """C's registration cannot be read as an agent-forward registration."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRegistration:
    """C's committed registration, read once: identity, seed, and the two sample parameters."""

    config_id: str
    registration_sha256: str
    registration_commit: str
    code_commit: str
    seed: int
    salt: str
    primary_size: int
    config: Mapping[str, Any]

    @property
    def registered(self) -> RegisteredConfiguration:
        """What a batch declares about this registration."""
        return RegisteredConfiguration(
            config_id=self.config_id,
            registration_sha256=self.registration_sha256,
            code_commit=self.code_commit,
            seed=self.seed,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StepZeroRegistration:
    """Step 0's registration, read for the two facts C needs: its book and its identity."""

    config_id: str
    registration_sha256: str
    registered: RegisteredConfiguration
    book: Any
    exchange: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StepZeroDay:
    """The session as Step 0 left it: its one record, its scope, its plan and its calendar."""

    record_id: str
    batch: PredictionBatch
    scored: int
    survivors: tuple[str, ...]
    holdings: tuple[str, ...]
    watch_list: tuple[str, ...]
    calendar: TradingCalendar
    cutoff: datetime
    exchange: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceDay:
    """The session's evidence as C's store now holds it, grouped by subject."""

    evidence_ids: tuple[str, ...]
    partition: str | None
    by_subject: Mapping[str, tuple[Any, ...]]


@dataclass(frozen=True, slots=True, kw_only=True)
class FamilyDay:
    """One family's journal block, the model requests it spent, and its exchanges' tokens."""

    block: Mapping[str, Any]
    requests: int = 0
    tokens: Mapping[str, int] | None = None


def _seed_of(settings: object) -> int:
    """The registration's `settings.random_seed`, read as `Registration.seed` reads it."""
    if not isinstance(settings, Mapping):
        return 0
    seed = settings.get("random_seed", 0)
    return seed if isinstance(seed, int) and not isinstance(seed, bool) else 0


def read_agent_registration(
    body: Mapping[str, Any], *, content: bytes, commit: str
) -> AgentRegistration:
    """C's registration body as `AgentRegistration`, or a refusal naming the missing fact.

    The configuration is `agent_forward/v1`'s: `kind`, `salt` and `primary_size` are required --
    a sample salt and a draw size are research parameters fixed at registration, and a command
    that defaulted either would be choosing C's protocol after it was registered. The
    registration's own `config_id` and `code_commit` are what the declaration and the engine run
    are addressed under; `registration_sha256` is the digest of the bytes this process admitted.
    """
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise AgentRegistrationError(f"C's registration names no configuration object: {config!r}")
    kind = config.get("kind")
    if kind != AGENT_REGISTRATION_KIND:
        raise AgentRegistrationError(
            f"C's configuration's kind is {kind!r}; the agent evening runs "
            f"{AGENT_REGISTRATION_KIND!r} and no other"
        )
    salt = config.get("salt")
    if not isinstance(salt, str) or not salt.strip():
        raise AgentRegistrationError("C's configuration names no sample salt")
    primary_size = config.get("primary_size")
    if isinstance(primary_size, bool) or not isinstance(primary_size, int) or primary_size < 1:
        raise AgentRegistrationError(
            f"C's configuration's primary_size is {primary_size!r}; a draw is at least one name"
        )
    config_id = body.get("config_id")
    code_commit = body.get("code_commit")
    if not isinstance(config_id, str) or not config_id.strip():
        raise AgentRegistrationError("C's registration names no config_id")
    if not isinstance(code_commit, str) or not code_commit.strip():
        raise AgentRegistrationError("C's registration names no code_commit")
    return AgentRegistration(
        config_id=config_id,
        registration_sha256=hashlib.sha256(content).hexdigest(),
        registration_commit=commit,
        code_commit=code_commit,
        seed=_seed_of(body.get("settings")),
        salt=salt,
        primary_size=primary_size,
        config=dict(config),
    )


def journal_path(runtime_dir: Path, session: date) -> Path:
    """The one journal document of `session` in `runtime_dir`."""
    return agent_journal_dir_at(runtime_dir) / f"{session.isoformat()}.json"


def _registration_path(options: argparse.Namespace) -> Path:
    named = options.registration
    path = Path(named) if named else REPOSITORY / REGISTRATION_IN_REPOSITORY
    return path if path.is_absolute() else REPOSITORY / path


def _admit(options: argparse.Namespace) -> tuple[Path, AgentRegistration]:
    """Step 1: C's committed registration, provided the running code is the code it binds."""
    registration = _registration_path(options)
    repository = Path(options.repo)
    if not registration.is_file():
        raise StepFailedError(
            "registration",
            f"{registration} does not exist; the agent evening runs the registered "
            "configuration and there is none to run",
            exit_code=AgentEveningExit.no_registration,
        )
    try:
        root, admitted = registry.admit_registered_code(
            registration, repository, also_bound=C_SCRIPTS
        )
    except registry.HoldoutConfigurationError as error:
        raise StepFailedError(
            "registration", str(error), exit_code=AgentEveningExit.no_registration
        ) from error
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=AgentEveningExit.code_not_registered,
        ) from error
    try:
        registration_read = read_agent_registration(
            admitted.registered, content=admitted.content, commit=admitted.commit
        )
    except AgentRegistrationError as error:
        raise StepFailedError(
            "registration", str(error), exit_code=AgentEveningExit.no_registration
        ) from error
    return root, registration_read


def _step0_registration(options: argparse.Namespace, root: Path) -> StepZeroRegistration:
    """Step 2's first half: Step 0's committed registration, and the book it runs."""
    named = options.step0_registration
    path = Path(named) if named else root / STEP0_REGISTRATION_IN_REPOSITORY
    if not path.is_absolute():
        path = root / path
    try:
        _root, admitted = registry.committed_registration(path, root)
    except registry.HoldoutRefusedError as error:
        raise StepFailedError("step0_record", f"{type(error).__name__}: {error}") from error
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise StepFailedError(
            "step0_record", f"{path} registers no configuration object; Step 0's book is unknown"
        )
    seed = _seed_of(body.get("settings"))
    try:
        survivor = read_survivor_registration(config, seed=seed)
    except StrategyRegistrationError as error:
        raise StepFailedError("step0_record", f"{type(error).__name__}: {error}") from error
    config_id = body.get("config_id")
    code_commit = body.get("code_commit")
    if not isinstance(config_id, str) or not isinstance(code_commit, str):
        raise StepFailedError("step0_record", f"{path} names no config_id or code_commit")
    sha256 = hashlib.sha256(admitted.content).hexdigest()
    return StepZeroRegistration(
        config_id=config_id,
        registration_sha256=sha256,
        registered=RegisteredConfiguration(
            config_id=config_id,
            registration_sha256=sha256,
            code_commit=code_commit,
            seed=seed,
        ),
        book=survivor.book,
        exchange=survivor.adapter.exchange,
    )


def _step0_day(
    options: argparse.Namespace,
    step0: StepZeroRegistration,
    *,
    session: date,
    clock: Callable[[], datetime],
) -> StepZeroDay:
    """Step 2: Step 0's one bound, on-time record, its scope, and its day journal's plan."""
    if not options.step0_runtime_dir:
        raise StepFailedError(
            "step0_record",
            "no --step0-runtime-dir was given; the agent evening scores Step 0's survivors and "
            "reads nothing of its own about them",
        )
    runtime = Path(options.step0_runtime_dir)
    published = session_publication_instant(session)
    panel = PanelStore(runtime / "panel")
    try:
        calendar = outcome_calendar(panel, step0.exchange, session, as_of=published)
        cutoff = registration_cutoff(calendar, session)
    except (StrategyRegistrationError, RuntimeError, ValueError) as error:
        raise StepFailedError("step0_record", f"{type(error).__name__}: {error}") from error
    store = prediction_store_at(runtime, clock=clock)
    held = []
    for record_id in store.list_ids():
        record = store.get(record_id)
        if record is None or record.batch.artifact.declaration.family != SURVIVOR_FAMILY:
            continue
        if record.batch.as_of.astimezone(SHANGHAI).date() != session:
            continue
        held.append(record)
    bound = [
        record for record in held if survivor_record_is_bound(record, step0.book, step0.registered)
    ]
    on_time = [record for record in bound if registered_at(record) < cutoff]
    if len(on_time) != 1:
        raise StepFailedError(
            "step0_record",
            f"Step 0 has not filed {session.isoformat()}: the store holds {len(on_time)} bound, "
            f"on-time {SURVIVOR_FAMILY} record(s) for it ({len(held)} of the family, "
            f"{len(bound)} bound); C scores the day Step 0 filed and nothing else",
        )
    record = on_time[0]
    scope = record_scope(record.batch, book=step0.book)
    journal_file = (
        daily_selection.survivor_journal_directory(runtime, step0.config_id)
        / f"{session.isoformat()}.json"
    )
    journal = daily_selection.read_survivor_journal(journal_file)
    if journal is None or not daily_selection.survivor_journal_complete(journal):
        raise StepFailedError(
            "step0_record",
            f"Step 0 has not filed {session.isoformat()}: its day journal "
            f"{journal_file} has no result, so the book's holdings and watch list are unknown",
        )
    try:
        plan = plan_names(journal["result"])
    except MorningCheckError as error:
        raise StepFailedError(
            "step0_record", f"Step 0 has not filed {session.isoformat()}: {error}"
        ) from error
    return StepZeroDay(
        record_id=record.record_id,
        batch=record.batch,
        scored=len(record.batch.scored),
        survivors=scope.survivors,
        holdings=plan.holdings,
        watch_list=plan.watch_list,
        calendar=calendar,
        cutoff=cutoff,
        exchange=step0.exchange,
    )


def primary_of(admitted: AgentRegistration, day: StepZeroDay, session: date) -> tuple[str, ...]:
    """The session's primary sample: the registered draw, a pure function of salt/session/set."""
    return primary_sample(
        day.survivors, salt=admitted.salt, session=session, size=admitted.primary_size
    )


def coverage_of(day: StepZeroDay) -> tuple[str, ...]:
    """The session's coverage set: survivor names the book holds or watches, never pooled."""
    return coverage_set(day.holdings, day.watch_list, day.survivors)


def _evidence(
    runtime: Path,
    options: argparse.Namespace,
    day: StepZeroDay,
    *,
    session: date,
    clock: Callable[[], datetime],
) -> EvidenceDay:
    """Step 3: derive the session's limit events from Step 0's panel, append them to C's store."""
    if not options.step0_runtime_dir:
        raise StepFailedError(
            "evidence",
            "--step0-runtime-dir is required: the limit events are read from Step 0's panel",
        )
    published = session_publication_instant(session)
    panel = PanelStore(Path(options.step0_runtime_dir) / "panel")
    try:
        events = limit_events_on(panel, session=session, as_of=published, exchange=day.exchange)
    except (RuntimeError, ValueError) as error:
        raise StepFailedError("evidence", f"{type(error).__name__}: {error}") from error
    arriving = limit_event_snapshots(events, session=session, fetched_at=clock())
    store = evidence_store_at(runtime)
    try:
        refuse_a_restated_session(store, session=session, arriving=arriving)
    except ValueError as error:
        raise StepFailedError("evidence", f"{type(error).__name__}: {error}") from error
    partition = store.append(arriving) if arriving else None
    by_subject: dict[str, list[Any]] = {}
    for snapshot in store.query(as_of=published):
        if (
            snapshot.timeline.available_time != published
            or snapshot.source_id != PANEL_DERIVED_PROVIDER_ID
            or snapshot.kind not in _LIMIT_KINDS
        ):
            continue
        by_subject.setdefault(snapshot.subject, []).append(snapshot)
    return EvidenceDay(
        evidence_ids=tuple(sorted(snapshot.evidence_id for snapshot in arriving)),
        partition=partition.name if partition is not None else None,
        by_subject={
            subject: tuple(sorted(items, key=lambda item: item.evidence_id))
            for subject, items in by_subject.items()
        },
    )


def _parse_deadline(named: str, session: date) -> datetime:
    """`HH:MM` Shanghai on `session`: the wall deadline past which no model request is spent."""
    try:
        hour_text, minute_text = named.strip().split(":", 1)
        point = time(int(hour_text), int(minute_text))
    except (ValueError, TypeError, AttributeError) as error:
        raise StepFailedError(
            "llm_family", f"--deadline {named!r} is not HH:MM ({error})"
        ) from error
    return datetime.combine(session, point, tzinfo=SHANGHAI)


class _DeadlineReached(RuntimeError):
    """The wall deadline passed while the model family was being scored."""


class _ModelBudget:
    """One evening's model-request allowance and its wall deadline, spent per HTTP request.

    The budget is applied **inside** `RecordingJsonTransport`, through `llm_provider`'s
    `wrap_transport` hook: a request the exchange store can answer -- a rerun, a crash-and-retry,
    a repeated request -- is answered before this decorator exists on the path, so it costs no
    request and is not deadline-gated (F2). Only a request that would be sent reaches `spend`,
    and every such request is counted -- a retryable transport error that the provider retries is
    another HTTP attempt and therefore another request against D7's ceiling (F8).
    """

    def __init__(
        self,
        *,
        allowance: int,
        deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None:
        self._deadline = deadline
        self._clock = clock
        self.counter = CappedCounter(allowance)

    @property
    def requests(self) -> int:
        """How many requests reached (or would have reached) the vendor's endpoint."""
        return self.counter.sent

    def decorate(self, inner: JsonTransport) -> JsonTransport:
        """`llm_provider`'s hook: wrap the inner HTTP transport, not the provider."""
        return _BudgetedTransport(inner, budget=self)

    def spend(self) -> None:
        """Check the deadline and count one HTTP request; refuse past either bound."""
        if self._clock() >= self._deadline:
            raise _DeadlineReached(
                f"the model family's wall deadline {self._deadline.isoformat()} passed at "
                f"{self._clock().isoformat()}; no further request was sent"
            )
        # The increment itself refuses past the allowance (`CappedCounter`), and it happens
        # before the call, so the request beyond the cap is never forwarded.
        self.counter["model"] = self.counter.get("model", 0) + 1


class _BudgetedTransport:
    """A `JsonTransport` that spends the evening's budget before forwarding one HTTP request."""

    def __init__(self, inner: JsonTransport, *, budget: _ModelBudget) -> None:
        self._inner = inner
        self._budget = budget

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self._budget.spend()
        return self._inner.post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )


# --- the D7 dry week ----------------------------------------------------------------------------


def dry_week_marker_path(runtime_dir: Path) -> Path:
    """The scratch runtime's dry-week marker: what makes it not C's forward runtime (D7)."""
    return Path(runtime_dir) / DRY_WEEK_SCRATCH_MARKER


def dry_week_journal_path(runtime_dir: Path, session: date) -> Path:
    """The one dry-week evening document of `session` in a scratch runtime."""
    return Path(runtime_dir) / DRY_WEEK_JOURNAL_DIRECTORY / f"{session.isoformat()}.json"


def _dry_week_evenings(runtime_dir: Path) -> tuple[str, ...]:
    """The sessions the marker lists, or a refusal naming why its status cannot be stated."""
    path = dry_week_marker_path(runtime_dir)
    if not path.is_file():
        return ()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StepFailedError(
            "dry_week",
            f"{path} cannot be read ({error}); a scratch runtime whose dry-week status cannot "
            "be stated is not written to",
        ) from error
    if not isinstance(body, Mapping) or body.get("schema") != DRY_WEEK_SCRATCH_SCHEMA:
        raise StepFailedError(
            "dry_week",
            f"{path} is not a {DRY_WEEK_SCRATCH_SCHEMA} marker; the dry week writes its own "
            "marker and never overwrites a document it cannot read",
        )
    evenings = body.get("evenings")
    if not isinstance(evenings, list) or any(not isinstance(item, str) for item in evenings):
        raise StepFailedError(
            "dry_week",
            f"{path} lists no sessions in `evenings`; the marker is not one this command wrote",
        )
    return tuple(sorted(evenings))


def _write_dry_week_marker(runtime_dir: Path, evenings: Sequence[str]) -> Path:
    """Write the scratch marker atomically: temporary file, then one `os.replace`."""
    path = dry_week_marker_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {"schema": DRY_WEEK_SCRATCH_SCHEMA, "evenings": list(evenings)},
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    ).encode()
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return path


def _refuse_a_registered_runtime(runtime_dir: Path, *, clock: Callable[[], datetime]) -> None:
    """Refuse a runtime that already holds a registered evening or a filed prediction (D7).

    The dry week writes a marker that makes the runtime unreadable as C's forward sample, so it
    must never be pointed at a store that *is* one: a registered evening leaves an
    `openalpha-agent-evening/v1` journal, and any filed prediction at all means the directory has
    a forward history. A marker already present means this is a scratch runtime and is not
    refused -- the cap and the rerun check handle it.
    """
    if dry_week_marker_path(runtime_dir).is_file():
        return
    journal_directory = agent_journal_dir_at(Path(runtime_dir))
    if journal_directory.is_dir():
        for path in sorted(journal_directory.glob("*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise StepFailedError(
                    "dry_week",
                    f"{path} cannot be read ({error}); a runtime whose evening history cannot "
                    "be stated is not used as a dry-week scratch",
                ) from error
            if isinstance(body, Mapping) and body.get("schema") == JOURNAL_SCHEMA:
                raise StepFailedError(
                    "dry_week",
                    f"{runtime_dir} holds a registered evening journal ({path}); it is C's "
                    "forward runtime, not a dry-week scratch, and the dry week never marks a "
                    "registered runtime as disposable",
                )
    store = prediction_store_at(Path(runtime_dir), clock=clock)
    filed = store.list_ids()
    if filed:
        raise StepFailedError(
            "dry_week",
            f"{runtime_dir} holds {len(filed)} filed prediction record(s); a dry-week scratch "
            "runtime files none, so this is a forward runtime and is not used as a scratch",
        )


class _OperationalMeter:
    """What one dry-week evening asks of the endpoint, counted without reading any answer (D7).

    Every HTTP attempt the budget lets through is observed: blank and non-JSON content are
    operational facts even though the agent retries them, and a transport failure is recorded
    before the provider turns it into a family gap. `tokens` is the vendor-reported usage, summed
    over the stored exchanges (or, for a failed attempt, over nothing).
    """

    def __init__(self) -> None:
        self.attempts: int = 0
        self.blank_responses: int = 0
        self.invalid_json_responses: int = 0
        self.transport_failures: int = 0
        self.latencies_ms: list[float] = []
        self.tokens: dict[str, int] = {"input": 0, "output": 0, "total": 0}

    def observe_response(self, response: Mapping[str, Any]) -> None:
        """Classify one response's content without interpreting it, and add its usage."""
        content: object = None
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
        if isinstance(content, str):
            if not content.strip():
                self.blank_responses += 1
            else:
                try:
                    json.loads(content)
                except ValueError:
                    self.invalid_json_responses += 1
        else:
            self.invalid_json_responses += 1
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            self.tokens["input"] += _token_count(usage.get("prompt_tokens"))
            self.tokens["output"] += _token_count(usage.get("completion_tokens"))
            self.tokens["total"] += _token_count(usage.get("total_tokens"))


class _MeteredTransport:
    """A `JsonTransport` that observes one HTTP attempt and forwards it unchanged."""

    def __init__(self, inner: JsonTransport, *, meter: _OperationalMeter) -> None:
        self._inner = inner
        self._meter = meter

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            response = self._inner.post_json(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except ModelTransportError:
            self._meter.transport_failures += 1
            raise
        finally:
            self._meter.attempts += 1
            self._meter.latencies_ms.append((perf_counter() - started) * 1000.0)
        self._meter.observe_response(response)
        return response


def _latency_summary(samples: Sequence[float]) -> dict[str, float | None]:
    """`p50`, `p95` and the mean of the evening's request latencies, in milliseconds."""
    if not samples:
        return {"p50": None, "p95": None, "mean": None}
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "mean": round(statistics.fmean(ordered), 3),
    }


def _dry_week_document(
    *,
    session: date,
    status: str,
    names: Sequence[str],
    requests: int,
    counts: Mapping[str, int],
    meter: _OperationalMeter,
    cap: int,
    deadline: datetime,
) -> dict[str, Any]:
    """One evening's operational account: what was asked, what came back, and nothing else."""
    return {
        "schema": DRY_WEEK_EVENING_SCHEMA,
        "session": session.isoformat(),
        "status": status,
        "names": len(names),
        "requests": requests,
        "scored": counts["scored"],
        "abstained": counts["abstained"],
        "not_routed": counts["not_routed"],
        "invalid_output": counts["invalid_output"],
        "blank_responses": meter.blank_responses,
        "invalid_json_responses": meter.invalid_json_responses,
        "transport_failures": meter.transport_failures,
        "latency_ms": _latency_summary(meter.latencies_ms),
        "tokens": dict(meter.tokens),
        "cap": cap,
        "deadline": deadline.isoformat(),
    }


def _write_dry_week_journal(path: Path, document: Mapping[str, Any]) -> None:
    """Write the evening document once, atomically: temporary file, then one `os.replace`."""
    payload = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _dry_week_family(
    runtime: Path,
    day: StepZeroDay,
    *,
    session: date,
    names: Sequence[str],
    by_subject: Mapping[str, Sequence[Any]],
    options: argparse.Namespace,
    clock: Callable[[], datetime],
    transport: JsonTransport | None,
) -> tuple[str, dict[str, int], int, _OperationalMeter, datetime]:
    """The model family as an operational measurement: counts and a status, never a score.

    The engine is on the path for the same reason the registered evening puts it there -- 015's
    history/72-hour guard, the run manifest and the replay record -- and each name's answer is
    mapped only to which operational bucket it fell in. A transport failure, the request cap, the
    wall deadline or a configuration fault stops the evening with that named status; an invalid
    answer after its retries is the family's coded abstention (`model_invalid_output`), which is
    an answer and therefore not a stop (D6).
    """
    counts = {"scored": 0, "abstained": 0, "not_routed": 0, "invalid_output": 0}
    meter = _OperationalMeter()
    deadline = _parse_deadline(options.deadline, session)
    key_env = DEEPSEEK_V4_1_FLASH.api_key_env
    if key_env is not None and not os.environ.get(key_env, "").strip():
        return NO_RECORD_CREDENTIAL_MISSING, counts, 0, meter, deadline
    budget = _ModelBudget(allowance=options.max_model_requests, deadline=deadline, clock=clock)

    def wrap_transport(inner: JsonTransport) -> JsonTransport:
        return _BudgetedTransport(_MeteredTransport(inner, meter=meter), budget=budget)

    try:
        provider = llm_provider(
            DEEPSEEK_V4_1_FLASH,
            runtime_dir=runtime,
            clock=clock,
            replay=False,
            transport=transport,
            wrap_transport=wrap_transport,
        )
    except ModelConfigurationError:
        return NO_RECORD_CONFIGURATION_ERROR, counts, budget.requests, meter, deadline
    agent = StructuredSignalAgent(
        agent_id=AGENT_ID,
        evidence_families=_MARKET_EVENT_FAMILIES,
        provider=provider,
        max_attempts=2,
        prompt=STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT,
        include_facts=True,
    )
    storage = build_storage(runtime_dir=runtime, clock=clock)
    engine = research_engine(
        repository=storage.repository,
        memory=storage.memory,
        recovery_store=storage.recovery_store,
        clock=clock,
        agents=[agent],
    )
    published = session_publication_instant(session)
    try:
        code_commit = _git_output(Path(options.repo), "rev-parse", "HEAD")
    except StepFailedError:
        code_commit = DRY_WEEK_CONFIG_ID
    config_digest = hashlib.sha256(
        canonical_json_bytes({"config_id": DRY_WEEK_CONFIG_ID, "session": session.isoformat()})
    ).hexdigest()
    status = DRY_WEEK_COMPLETED
    for ts_code in names:
        request = ResearchRunRequest(
            run_id=agent_run_id(
                config_id=DRY_WEEK_CONFIG_ID,
                session=session,
                family=AGENT_LLM_FAMILY,
                ts_code=ts_code,
            ),
            mode=RunMode.daily,
            subject=ts_code,
            as_of=published,
            evidence=tuple(by_subject.get(ts_code, ())),
            code_commit=code_commit,
            config_digest=config_digest,
            random_seed=0,
        )
        try:
            result = engine.run_cycle(request)
        except ModelProviderFailure:
            counts["invalid_output"] += 1
            continue
        except ModelTransportError:
            status = NO_RECORD_TRANSPORT_ERROR
            break
        except RequestCapReached:
            status = NO_RECORD_REQUEST_CAP_REACHED
            break
        except _DeadlineReached:
            status = NO_RECORD_DEADLINE_REACHED
            break
        except ModelConfigurationError:
            status = NO_RECORD_CONFIGURATION_ERROR
            break
        except (RuntimeError, ValueError) as error:
            raise StepFailedError(
                "dry_week", f"{ts_code}: {type(error).__name__}: {error}"
            ) from error
        if not result.agent_results:
            counts["not_routed"] += 1
        elif result.agent_results[0].signal.direction == "abstain":
            counts["abstained"] += 1
        else:
            counts["scored"] += 1
    return status, counts, budget.requests, meter, deadline


def _print_dry_week(
    *,
    session: date,
    document: Mapping[str, Any],
    path: Path,
    marker: Path | None,
) -> None:
    """The printed dry-week account: candidate wording, operations, never a result."""
    latency = document["latency_ms"]
    tokens = document["tokens"]
    print(f"{CANDIDATE} / candidate: the dry week evening of {session.isoformat()}")
    print(
        f"{NOT_TRADED}: the dry week files no prediction and trades nothing; its numbers are "
        "operational only and are never read as C's forward sample"
    )
    print(
        f"dry week status {document['status']}; names {document['names']}, requests "
        f"{document['requests']}/{document['cap']}, scored {document['scored']}, abstained "
        f"{document['abstained']}, not routed {document['not_routed']}, invalid output "
        f"{document['invalid_output']}"
    )
    print(
        f"meter: blank {document['blank_responses']}, invalid json "
        f"{document['invalid_json_responses']}, transport failures "
        f"{document['transport_failures']}, latency ms p50 {latency['p50']} / p95 "
        f"{latency['p95']} / mean {latency['mean']}"
    )
    print(
        f"tokens {tokens['input']} in / {tokens['output']} out / {tokens['total']} total; "
        f"deadline {document['deadline']}"
    )
    print(f"dry-week journal written: {path}")
    if marker is not None:
        print(f"scratch marker updated: {marker}")


def _dry_week(
    options: argparse.Namespace,
    *,
    clock: Callable[[], datetime],
    transport: JsonTransport | None,
) -> int:
    """One D7 dry-week evening: no registration, no filing, operational metrics only.

    The scratch runtime and Step 0's runtime are required; C's registration is not read (there is
    nothing to register yet), and a runtime already holding a registered evening or a filed
    prediction is refused. The cap and the rerun checks run before anything is opened, so a
    refused evening leaves the scratch runtime untouched.
    """
    _required(options, "runtime_dir", step="dry_week")
    _required(options, "step0_runtime_dir", step="dry_week")
    runtime = Path(options.runtime_dir)
    pinned = _now(options, clock)
    if pinned.tzinfo is None:
        pinned = pinned.replace(tzinfo=SHANGHAI)
    session = pinned.astimezone(SHANGHAI).date()
    cap = options.max_model_requests
    if isinstance(cap, bool) or cap < 0 or cap > DRY_WEEK_MAX_REQUESTS:
        raise StepFailedError(
            "dry_week",
            f"--max-model-requests is {cap!r}; D7's hard ceiling is {DRY_WEEK_MAX_REQUESTS} "
            "requests one evening, and a cap is zero or more up to it",
        )
    _refuse_a_registered_runtime(runtime, clock=clock)
    recorded = _dry_week_evenings(runtime)
    if len(recorded) >= DRY_WEEK_MAX_EVENINGS:
        raise StepFailedError(
            "dry_week",
            f"{runtime} has recorded {len(recorded)} dry-week evenings; D7 runs at most "
            f"{DRY_WEEK_MAX_EVENINGS} per scratch runtime, and a sixth would be a sixth "
            "measurement of the same deployment",
        )
    journal_file = dry_week_journal_path(runtime, session)
    if journal_file.exists() or session.isoformat() in recorded:
        raise StepFailedError(
            "dry_week",
            f"the dry-week evening of {session.isoformat()} is already recorded "
            f"({journal_file}); an evening is measured once and the held account stands",
        )
    step0 = _step0_registration(options, Path(options.repo))
    day = _step0_day(options, step0, session=session, clock=clock)
    wall = clock()
    if wall >= day.cutoff:
        raise _late(day, session, wall)
    published = session_publication_instant(session)
    panel = PanelStore(Path(options.step0_runtime_dir) / "panel")
    try:
        events = limit_events_on(panel, session=session, as_of=published, exchange=day.exchange)
        arriving = limit_event_snapshots(events, session=session, fetched_at=wall)
    except (RuntimeError, ValueError) as error:
        raise StepFailedError("dry_week", f"{type(error).__name__}: {error}") from error
    by_subject: dict[str, list[Any]] = {}
    for snapshot in arriving:
        by_subject.setdefault(snapshot.subject, []).append(snapshot)
    names = sorted(set(day.survivors) & set(by_subject))
    status, counts, requests, meter, deadline = _dry_week_family(
        runtime,
        day,
        session=session,
        names=names,
        by_subject=by_subject,
        options=options,
        clock=clock,
        transport=transport,
    )
    document = _dry_week_document(
        session=session,
        status=status,
        names=names,
        requests=requests,
        counts=counts,
        meter=meter,
        cap=cap,
        deadline=deadline,
    )
    _write_dry_week_journal(journal_file, document)
    marker: Path | None = None
    if status == DRY_WEEK_COMPLETED:
        evenings = tuple(sorted({*recorded, session.isoformat()}))
        marker = _write_dry_week_marker(runtime, evenings)
    _print_dry_week(session=session, document=document, path=journal_file, marker=marker)
    return AgentEveningExit.ok


def _stable_block(block: Mapping[str, Any]) -> dict[str, Any]:
    """A filed batch's journal block: what the record is, not how this run came by it.

    `outcome` (`created`/`unchanged`) and `recorded_at` are facts of the run, not of the day, and
    the journal is write-once: a rerun must compute the same document. Everything else in
    `strategy_forward_session.prediction_block` is a property of the filed record.
    """
    return {
        key: block[key]
        for key in (
            "family",
            "record_id",
            "horizon",
            "standing",
            "as_of",
            "outcome_known_at",
            "scored",
            "abstained",
            "batch_digest",
            "provenance",
        )
    }


def _file(
    store: Any,
    batch: PredictionBatch,
    *,
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
    step: str,
) -> Mapping[str, Any]:
    """File `batch`, print how this run came by it, and return the journal's stable block."""
    try:
        block = file_agent_batch(store, batch, calendar=calendar, clock=clock)
    except ForwardSessionRefused as error:
        raise StepFailedError(step, f"{type(error).__name__}: {error}") from error
    declaration = batch.artifact.declaration
    scoring_set = dict(declaration.hyperparameters)["scoring_set"]
    print(f"  {declaration.family} {scoring_set}: {block['record_id']} {block['outcome']}")
    return _stable_block(block)


def _rule_family(
    runtime: Path,
    admitted: AgentRegistration,
    day: StepZeroDay,
    evidence: EvidenceDay,
    *,
    session: date,
    clock: Callable[[], datetime],
) -> FamilyDay:
    """Step 4: one deterministic cycle per name of primary union coverage, filed as two batches."""
    primary = primary_of(admitted, day, session)
    coverage = coverage_of(day)
    storage = build_storage(runtime_dir=runtime, clock=clock)
    engine = research_engine(
        repository=storage.repository,
        memory=storage.memory,
        recovery_store=storage.recovery_store,
        clock=clock,
        agents=baseline_agents(),
    )
    published = session_publication_instant(session)
    config_digest = hashlib.sha256(canonical_json_bytes(dict(admitted.config))).hexdigest()
    names = sorted(set(primary) | set(coverage))
    scores: dict[str, AgentScore] = {}
    for ts_code in names:
        run_id = agent_run_id(
            config_id=admitted.config_id,
            session=session,
            family=AGENT_RULE_FAMILY,
            ts_code=ts_code,
        )
        request = ResearchRunRequest(
            run_id=run_id,
            mode=RunMode.daily,
            subject=ts_code,
            as_of=published,
            evidence=tuple(evidence.by_subject.get(ts_code, ())),
            code_commit=admitted.code_commit,
            config_digest=config_digest,
            random_seed=admitted.seed,
        )
        try:
            result = engine.run_cycle(request)
        except (RuntimeError, ValueError) as error:
            raise StepFailedError(
                "rule_family", f"{ts_code}: {type(error).__name__}: {error}"
            ) from error
        if result.signal.direction == "abstain":
            reason = ABSTAIN_NOT_ROUTED if not result.agent_results else ABSTAIN_ALL_ABSTAINED
            score, abstention = score_of("abstain", 0.0, reason)
        else:
            score, abstention = score_of(result.signal.direction, result.signal.strength, None)
        scores[ts_code] = AgentScore(
            ts_code=ts_code, score=score, abstention=abstention, run_id=run_id
        )
    blocks: dict[str, Mapping[str, Any] | None] = {"primary": None, "coverage": None}
    absent: str | None = None
    for scoring_set, set_names in (("primary", primary), ("coverage", coverage)):
        if not set_names:
            absent = _EMPTY_SET
            continue
        declaration = agent_declaration(
            family=AGENT_RULE_FAMILY,
            scoring_set="primary" if scoring_set == "primary" else "coverage",
            agent_kind="deterministic",
            registered=admitted.registered,
            model_ref=None,
            prompt_ref=None,
        )
        batch = agent_batch(
            session=session,
            scores=[scores[name] for name in set_names],
            declaration=declaration,
            predicted_at=clock(),
        )
        blocks[scoring_set] = _file(
            storage.prediction_store,
            batch,
            calendar=day.calendar,
            clock=clock,
            step="rule_family",
        )
    return FamilyDay(
        block={
            "primary": blocks["primary"],
            "coverage": blocks["coverage"],
            "coverage_absent": absent,
        }
    )


def _exchange_as_of(exchange: Any) -> datetime | None:
    """The `as_of` instant a stored exchange's user payload names, or `None` if unreadable.

    Compared as an instant, not as a string: the engine normalises a request's `as_of` to UTC
    (`domain.time.ensure_aware`), so the same 16:30 Shanghai publication is stored as
    `08:30+00:00` and a string comparison against the session's Shanghai spelling would report a
    recorded answer as missing.
    """
    request = exchange.request
    if not isinstance(request, Mapping):
        return None
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    message = messages[1]
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    as_of = payload.get("as_of")
    if not isinstance(as_of, str):
        return None
    try:
        point = datetime.fromisoformat(as_of)
    except ValueError:
        return None
    return point if point.tzinfo is not None else None


def _token_count(value: object) -> int:
    """One provider-reported token count; anything that is not a number is zero."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _session_exchanges(
    runtime: Path, published: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The session's stored exchanges and their reported token usage, in address order."""
    store = model_exchange_store_at(runtime)
    directory = runtime / "model_exchanges"
    found: list[dict[str, Any]] = []
    tokens = {"input": 0, "output": 0, "total": 0}
    if not directory.is_dir():
        return found, tokens
    for path in sorted(directory.glob("*.json")):
        exchange = store.get(path.stem)
        if exchange is None or _exchange_as_of(exchange) != published:
            continue
        response = exchange.response if isinstance(exchange.response, Mapping) else {}
        found.append(
            {
                "request_digest": exchange.request_digest,
                "model": response.get("model"),
                "system_fingerprint": response.get("system_fingerprint"),
            }
        )
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            tokens["input"] += _token_count(usage.get("prompt_tokens"))
            tokens["output"] += _token_count(usage.get("completion_tokens"))
            tokens["total"] += _token_count(usage.get("total_tokens"))
    return found, tokens


def _no_record(reason: str) -> FamilyDay:
    return FamilyDay(
        block={
            "status": "no_record",
            "reason": reason,
            "primary": None,
            "coverage": None,
            "coverage_absent": None,
            "exchanges": [],
        }
    )


def _prior_reason(runtime: Path, session: date) -> str | None:
    """The stored journal's `no_record` reason for the model family, if the day recorded one."""
    path = journal_path(runtime, session)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise StepFailedError("summary", f"{path} is not UTF-8 JSON ({error})") from error
    if not isinstance(body, Mapping) or body.get("schema") != JOURNAL_SCHEMA:
        raise StepFailedError(
            "summary",
            f"{path} is not a {JOURNAL_SCHEMA} journal; it is left as it is, and a journal is "
            "never rewritten",
        )
    family = body.get("llm_family")
    if not isinstance(family, Mapping):
        raise StepFailedError("summary", f"{path} has no llm_family block; it is left as it is")
    if family.get("status") == "no_record":
        reason = family.get("reason")
        return str(reason) if reason is not None else "no_record"
    return None


def _llm_batches_filed(
    runtime: Path,
    session: date,
    *,
    clock: Callable[[], datetime],
    scoring_sets: frozenset[str],
    registration_sha256: str,
) -> bool:
    """Whether the session's expected LLM batches are already in the prediction store (N4).

    Read from the store *before* the credential is checked, because a rerun that only has to
    reproduce the day sends no request and so must not need a key. A filed batch is a fact of
    the day; the run that will replay it is a later reader.

    Two filters keep the answer about C's own day rather than about whatever the store holds:
    the declaration must carry C's `registration_sha256` (a foreign registration's same-session
    batches do not make C's day filed, and accepting them would put a never-filed day into
    replay mode), and only the expected scoring sets count -- `primary` always, `coverage` only
    when the day has a non-empty coverage set, because an empty coverage set is a supported
    state that files no coverage batch at all. Anything less is a day that still has a request
    to spend.
    """
    published = session_publication_instant(session)
    store = prediction_store_at(runtime, clock=clock)
    filed: set[str] = set()
    for record_id in store.list_ids():
        record = store.get(record_id)
        if record is None:
            continue
        declaration = record.batch.artifact.declaration
        if declaration.family != AGENT_LLM_FAMILY:
            continue
        if record.batch.as_of != published:
            continue
        declared = dict(declaration.hyperparameters)
        if declared.get(REGISTRATION_HYPERPARAMETER) != registration_sha256:
            continue
        scoring_set = declared.get(SCORING_SET_HYPERPARAMETER)
        if scoring_set in scoring_sets:
            filed.add(str(scoring_set))
    return scoring_sets <= filed


def _llm_family(
    runtime: Path,
    admitted: AgentRegistration,
    day: StepZeroDay,
    evidence: EvidenceDay,
    *,
    session: date,
    clock: Callable[[], datetime],
    options: argparse.Namespace,
    transport: JsonTransport | None,
) -> FamilyDay:
    """Step 5: the model-backed family through the research engine, capped and journalled.

    Three orderings are load-bearing. The credential is checked before the provider is
    assembled, so a missing key can never become an invalid answer (F1) -- but a rerun whose
    two LLM batches are already filed sends no request, so it is assembled `replay=True` and
    needs neither a credential nor a usage store (N4). The request budget is handed to
    `llm_provider` as `wrap_transport`, so it decorates the HTTP transport inside the exchange
    recorder and a replay costs nothing (F2/F8). And every cycle goes through
    `ResearchEngine.run_cycle`, so `refuse_llm_agents_in_history` runs before the agent does and
    every run id names a stored manifest (F6).

    The engine is on the path for the guard, the manifest and the replay record -- **not for the
    number**. Each name's stored score is the one composed agent's own structured frame
    (`result.agent_results`): its signed strength with no neutral band, or `None` plus
    `agent_abstained` when the agent declines, whatever evidence it cites; no agent routed is
    `agent_not_routed`. The engine's aggregate is deliberately not read: its 0.15 band would zero
    a weak directional answer and its evidence rule would turn an evidence-citing abstention into
    `0.0` (N1).
    """
    if options.no_llm:
        return _no_record(NO_RECORD_LLM_DISABLED)
    prior = _prior_reason(runtime, session)
    if prior is not None:
        return _no_record(prior)
    if isinstance(options.max_model_requests, bool) or options.max_model_requests < 0:
        raise StepFailedError(
            "llm_family",
            f"--max-model-requests is {options.max_model_requests!r}; a cap is the number of "
            "requests one evening may spend, zero or more",
        )
    deadline = _parse_deadline(options.deadline, session)
    published = session_publication_instant(session)
    primary = primary_of(admitted, day, session)
    coverage = coverage_of(day)
    expected_sets = frozenset({"primary"}) | (frozenset({"coverage"}) if coverage else frozenset())
    # N4: a rerun of a day whose expected LLM batches are already filed will send no request, so
    # it reads them back in replay mode -- the credential is not needed and the usage store is
    # not opened. Only a day that still has a request to spend is checked for the key (F1).
    replay = _llm_batches_filed(
        runtime,
        session,
        clock=clock,
        scoring_sets=expected_sets,
        registration_sha256=admitted.registered.registration_sha256,
    )
    key_env = DEEPSEEK_V4_1_FLASH.api_key_env
    if not replay and key_env is not None and not os.environ.get(key_env, "").strip():
        return _no_record(NO_RECORD_CREDENTIAL_MISSING)
    budget = _ModelBudget(
        allowance=options.max_model_requests,
        deadline=deadline,
        clock=clock,
    )
    try:
        provider = llm_provider(
            DEEPSEEK_V4_1_FLASH,
            runtime_dir=runtime,
            clock=clock,
            replay=replay,
            transport=None if replay else transport,
            wrap_transport=None if replay else budget.decorate,
        )
    except ModelConfigurationError:
        # F1: an endpoint whose reading has not been recorded or a malformed endpoint is a
        # deployment fault, and a deployment fault is a family gap -- never an answer and never
        # a step refusal that leaves the day with no account.
        return _no_record(NO_RECORD_CONFIGURATION_ERROR)
    agent = StructuredSignalAgent(
        agent_id=AGENT_ID,
        evidence_families=_MARKET_EVENT_FAMILIES,
        provider=provider,
        max_attempts=2,
        prompt=STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT,
        include_facts=True,
    )
    # F6: the model family runs through the same engine the rule family uses, one cycle per
    # name. That is what makes 015's `refuse_llm_agents_in_history` and the 72-hour lag guard
    # structural on the only production model path, and what stores a run manifest whose
    # `model_versions`/`prompt_versions` name the model and the prompt that answered.
    storage = build_storage(runtime_dir=runtime, clock=clock)
    engine = research_engine(
        repository=storage.repository,
        memory=storage.memory,
        recovery_store=storage.recovery_store,
        clock=clock,
        agents=[agent],
    )
    config_digest = hashlib.sha256(canonical_json_bytes(dict(admitted.config))).hexdigest()
    names = sorted(set(primary) | set(coverage))
    scores: dict[str, AgentScore] = {}
    model_ref = f"{DEEPSEEK_V4_1_FLASH.provider_id}/{DEEPSEEK_V4_1_FLASH.model}"
    prompt_ref = STRUCTURED_SIGNAL_PROMPT_V2_JSON_OBJECT.name
    for ts_code in names:
        run_id = agent_run_id(
            config_id=admitted.config_id,
            session=session,
            family=AGENT_LLM_FAMILY,
            ts_code=ts_code,
        )
        request = ResearchRunRequest(
            run_id=run_id,
            mode=RunMode.daily,
            subject=ts_code,
            as_of=published,
            evidence=tuple(evidence.by_subject.get(ts_code, ())),
            code_commit=admitted.code_commit,
            config_digest=config_digest,
            random_seed=admitted.seed,
        )
        try:
            result = engine.run_cycle(request)
        except ModelProviderFailure:
            # A genuine invalid answer after its retries is C's coded abstention, never a zero
            # (D6); it is the one model failure that is still the family's answer.
            scores[ts_code] = AgentScore(
                ts_code=ts_code, score=None, abstention=ABSTAIN_INVALID_OUTPUT, run_id=run_id
            )
            continue
        except ModelTransportError:
            return _no_record(NO_RECORD_TRANSPORT_ERROR)
        except ModelConfigurationError:
            return _no_record(NO_RECORD_CONFIGURATION_ERROR)
        except RequestCapReached:
            return _no_record(NO_RECORD_REQUEST_CAP_REACHED)
        except _DeadlineReached:
            return _no_record(NO_RECORD_DEADLINE_REACHED)
        except ModelExchangeNotStoredError as error:
            # N4: replay has no transport by design, so a filed day whose stored exchange is
            # missing is a refusal of step 5 -- never a credential error and never an invented
            # answer.
            raise StepFailedError(
                "llm_family", f"{ts_code}: {type(error).__name__}: {error}"
            ) from error
        except (RuntimeError, ValueError) as error:
            # Everything else the engine raises is a refusal of the day, not a family gap: 015's
            # history/72h guard lands here, and the operator sees step 5 refuse by name.
            raise StepFailedError(
                "llm_family", f"{ts_code}: {type(error).__name__}: {error}"
            ) from error
        # N1: the stored score is the one composed agent's own frame, not `result.signal` (the
        # engine's aggregate). The aggregate re-thresholds the direction with a 0.15 neutral
        # band -- a bullish 0.1 becomes `neutral` and stores 0.0 -- and reads an abstention that
        # cites evidence as `neutral`, storing an abstention as 0.0. Both would file a number
        # the model did not answer, and a stored record cannot be revised. So: no agent routed is
        # `agent_not_routed`; an agent that declined is `agent_abstained` whatever it cites; a
        # direction keeps its own signed strength with no band.
        if not result.agent_results:
            score, abstention = score_of("abstain", 0.0, ABSTAIN_NOT_ROUTED)
        else:
            # Exactly one agent is composed above, so the cycle's results are that agent's own
            # frame or nothing.
            agent_signal = result.agent_results[0].signal
            if agent_signal.direction == "abstain":
                score, abstention = score_of("abstain", 0.0, ABSTAIN_ALL_ABSTAINED)
            else:
                score, abstention = score_of(agent_signal.direction, agent_signal.strength, None)
        scores[ts_code] = AgentScore(
            ts_code=ts_code, score=score, abstention=abstention, run_id=run_id
        )
    blocks: dict[str, Mapping[str, Any] | None] = {"primary": None, "coverage": None}
    absent: str | None = None
    prediction_store = storage.prediction_store
    for scoring_set, set_names in (("primary", primary), ("coverage", coverage)):
        if not set_names:
            absent = _EMPTY_SET
            continue
        declaration = agent_declaration(
            family=AGENT_LLM_FAMILY,
            scoring_set="primary" if scoring_set == "primary" else "coverage",
            agent_kind="llm_backed",
            registered=admitted.registered,
            model_ref=model_ref,
            prompt_ref=prompt_ref,
        )
        batch = agent_batch(
            session=session,
            scores=[scores[name] for name in set_names],
            declaration=declaration,
            predicted_at=clock(),
        )
        blocks[scoring_set] = _file(
            prediction_store,
            batch,
            calendar=day.calendar,
            clock=clock,
            step="llm_family",
        )
    exchanges, tokens = _session_exchanges(runtime, published)
    return FamilyDay(
        block={
            "status": "filed",
            "reason": None,
            "primary": blocks["primary"],
            "coverage": blocks["coverage"],
            "coverage_absent": absent,
            "exchanges": exchanges,
        },
        requests=budget.requests,
        tokens=tokens,
    )


def _journal_document(
    *,
    admitted: AgentRegistration,
    day: StepZeroDay,
    step0: StepZeroRegistration,
    evidence: EvidenceDay,
    primary: tuple[str, ...],
    coverage: tuple[str, ...],
    rule: FamilyDay,
    llm: FamilyDay,
    session: date,
    failed: int,
) -> dict[str, Any]:
    """The session's one journal document: every fact of the day, none of the run."""
    exchanges = list(llm.block.get("exchanges") or ())
    tokens = dict(llm.tokens or {"input": 0, "output": 0, "total": 0})
    scored = 0
    abstained = 0
    for block in (rule.block, llm.block):
        for scoring_set in ("primary", "coverage"):
            entry = block.get(scoring_set)
            if isinstance(entry, Mapping):
                scored += int(entry["scored"])
                abstained += int(entry["abstained"])
    return {
        "schema": JOURNAL_SCHEMA,
        "session": session.isoformat(),
        "as_of": session_publication_instant(session).isoformat(),
        "not_traded": True,
        "steps": list(AGENT_STEPS),
        "registration": {
            "config_id": admitted.config_id,
            "registration_sha256": admitted.registration_sha256,
            "registration_commit": admitted.registration_commit,
            "code_commit": admitted.code_commit,
            "seed": admitted.seed,
            "salt": admitted.salt,
            "primary_size": admitted.primary_size,
        },
        "step0": {
            "registration_sha256": step0.registration_sha256,
            "config_id": step0.config_id,
            "record_id": day.record_id,
            "batch_digest": batch_digest(day.batch),
            "scored": day.scored,
            "survivors": len(day.survivors),
            "holdings": list(day.holdings),
            "watch_list": list(day.watch_list),
        },
        "evidence": {
            "session": session.isoformat(),
            "count": len(evidence.evidence_ids),
            "evidence_ids": list(evidence.evidence_ids),
            "partition": evidence.partition,
        },
        "scoring_sets": {"primary": list(primary), "coverage": list(coverage)},
        "rule_family": dict(rule.block),
        "llm_family": dict(llm.block),
        "summary": {
            "scored": scored,
            "abstained": abstained,
            "failed": failed,
            "requests": len(exchanges),
            "tokens": tokens,
        },
    }


def _write_journal(path: Path, document: Mapping[str, Any]) -> str:
    """Write the day's journal once; a rerun must agree with it.

    Write-once by temporary file plus `os.replace`, so a reader never sees half a document. A
    held document with the same bytes is `unchanged` and is not rewritten; a different one is
    refused and the held account stands -- the first evening's account of a day is the day's.
    """
    payload = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    if path.is_file():
        if path.read_bytes() == payload:
            return "unchanged"
        raise StepFailedError(
            "summary",
            f"{path} already holds a different journal for this session; the journal is "
            "write-once and the held account stands, so nothing was rewritten",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return "created"


def _late(day: StepZeroDay, session: date, now: datetime) -> StepFailedError:
    return StepFailedError(
        "evidence",
        f"the scores of {session.isoformat()} are traded at the next session's open, whose call "
        f"auction starts {day.cutoff.isoformat()}, and it is {now.isoformat()}: part of their "
        "outcome is published already, so registered now they would not be a prediction made "
        "before its outcome. Nothing was filed",
    )


def _now(options: argparse.Namespace, clock: Callable[[], datetime]) -> datetime:
    if options.as_of:
        try:
            point = datetime.fromisoformat(options.as_of)
        except ValueError as error:
            raise StepFailedError(
                "registration", f"--as-of {options.as_of!r} is not ISO 8601"
            ) from error
        return point if point.tzinfo is not None else point.replace(tzinfo=SHANGHAI)
    return clock()


def _required(options: argparse.Namespace, name: str, *, step: str) -> str:
    value = getattr(options, name)
    if not value:
        raise StepFailedError(step, f"--{name.replace('_', '-')} is required here")
    return str(value)


def _run(
    options: argparse.Namespace,
    *,
    clock: Callable[[], datetime],
    transport: JsonTransport | None,
) -> int:
    """One evening: the six steps in order, then the printed summary.

    Two clocks, `scripts/daily_selection.py`'s division: `--as-of` pins the day's reads (the
    session, its evidence and its panel), while the injected wall clock decides lateness and the
    model deadline and stamps the filed batches. A rerun tomorrow of last night's session reads
    as last night did and is refused by the same call auction.
    """
    pinned = _now(options, clock)
    if pinned.tzinfo is None:
        pinned = pinned.replace(tzinfo=SHANGHAI)
    wall = clock
    session = pinned.astimezone(SHANGHAI).date()
    _required(options, "runtime_dir", step="evidence")
    runtime = Path(options.runtime_dir)
    root, admitted = _admit(options)
    step0 = _step0_registration(options, root)
    # Plan C Review Focus 2's coupling, enforced at the only place both salts are known: C's
    # draw salt is its own registered value, never Step 0's seed or a string carrying it. The
    # registration writer refuses this too (plan C task 3.3); this is the forward-run half, and
    # it runs before anything is read or written so a refused deployment leaves no trace.
    try:
        refuse_a_salt_tied_to_the_book(admitted.salt, book_seed=step0.registered.seed)
    except ValueError as error:
        raise StepFailedError("registration", str(error)) from error
    day = _step0_day(options, step0, session=session, clock=wall)
    if wall() >= day.cutoff:
        raise _late(day, session, wall())
    evidence = _evidence(runtime, options, day, session=session, clock=wall)
    primary = primary_of(admitted, day, session)
    coverage = coverage_of(day)
    rule = _rule_family(runtime, admitted, day, evidence, session=session, clock=wall)
    llm = _llm_family(
        runtime,
        admitted,
        day,
        evidence,
        session=session,
        clock=wall,
        options=options,
        transport=transport,
    )
    filed = options.no_llm or llm.block.get("status") == "filed"
    # F11: the count is of names, not set memberships -- a name drawn into primary and held in
    # the coverage set is one name the day did not score, and `--no-llm` is a decision, not a
    # failed family.
    failed = 0 if filed else len(set(primary) | set(coverage))
    document = _journal_document(
        admitted=admitted,
        day=day,
        step0=step0,
        evidence=evidence,
        primary=primary,
        coverage=coverage,
        rule=rule,
        llm=llm,
        session=session,
        failed=failed,
    )
    outcome = _write_journal(journal_path(runtime, session), document)
    _print_summary(
        session=session,
        llm=llm,
        document=document,
        outcome=outcome,
        path=journal_path(runtime, session),
        requests=rule.requests + llm.requests,
    )
    return AgentEveningExit.ok


def _print_summary(
    *,
    session: date,
    llm: FamilyDay,
    document: Mapping[str, Any],
    outcome: str,
    path: Path,
    requests: int,
) -> None:
    """The printed lines step 6 owes: candidate wording, counts, tokens and requests.

    When the model family was not filed the failure count is the number of distinct names the
    day left unscored and the line names the family's `no_record` reason (F11); a filed day and
    `--no-llm` print `failed 0`, because a decision is not a failure.
    """
    summary = document["summary"]
    status = "filed" if llm.block.get("status") == "filed" else "no_record"
    reason = llm.block.get("reason")
    print(f"{CANDIDATE} / candidate: the agent evening of {session.isoformat()}")
    print(
        f"{NOT_TRADED}: these are candidate signals only; nothing was traded, and a passing "
        "suite is not evidence that the family has an increment"
    )
    failed = int(summary["failed"])
    if failed:
        print(
            f"scored {summary['scored']}, abstained {summary['abstained']}, "
            f"not scored (family not filed: {reason}): {failed}"
        )
    else:
        print(f"scored {summary['scored']}, abstained {summary['abstained']}, failed 0")
    print(
        f"model requests   {requests} this run "
        f"({len(llm.block.get('exchanges') or ())} held for the session)"
    )
    tokens = summary["tokens"]
    print(f"tokens {tokens['input']} in / {tokens['output']} out / {tokens['total']} total")
    print(f"{AGENT_LLM_FAMILY}: {status}" + (f" ({reason})" if reason else ""))
    print(f"journal {outcome}: {path}")


# --- the operator commands ----------------------------------------------------------------------


def _launchd_plist(options: argparse.Namespace) -> str:
    """The printed launchd job: weekdays at 20:30, from the pinned worktree, never installed.

    Every argument the evening's step 2 refuses without is in the job: `--step0-runtime-dir`
    (F3 -- a printed job without it exits 2 at step 2 on every scheduled evening) as an absolute
    path, and `--step0-registration` when the operator named one. The command prints and never
    installs: nothing under `~/Library` is touched and no directory named here is created.
    """
    worktree = Path(_required(options, "worktree", step="registration")).resolve()
    runtime = Path(_required(options, "runtime_dir", step="registration"))
    step0_runtime = Path(_required(options, "step0_runtime_dir", step="registration")).resolve()
    named = options.registration
    registration = Path(named) if named else worktree / REGISTRATION_IN_REPOSITORY
    if not registration.is_absolute():
        registration = worktree / registration
    step0_registration: list[str] = []
    if options.step0_registration:
        named_step0_registration = Path(options.step0_registration)
        if not named_step0_registration.is_absolute():
            # Relative to the repository the evening runs from, exactly as `_step0_registration`
            # resolves a named path against its admission root.
            named_step0_registration = worktree / named_step0_registration
        step0_registration = [
            "--step0-registration",
            str(named_step0_registration.resolve()),
        ]
    logs = Path(options.log_dir) if options.log_dir else worktree / "logs"
    arguments = [
        str(worktree / ".venv" / "bin" / "python"),
        str(worktree / "scripts" / "agent_evening.py"),
        "--registration",
        str(registration),
        "--runtime-dir",
        str(runtime),
        "--step0-runtime-dir",
        str(step0_runtime),
        *step0_registration,
        "--repo",
        str(worktree),
    ]
    weekdays = "".join(
        f"    <dict><key>Weekday</key><integer>{weekday}</integer>"
        f"<key>Hour</key><integer>{LAUNCHD_HOUR}</integer>"
        f"<key>Minute</key><integer>{LAUNCHD_MINUTE}</integer></dict>\n"
        for weekday in range(1, 6)
    )
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0"><dict>',
        f"  <key>Label</key><string>{LAUNCHD_LABEL}</string>",
        "  <key>ProgramArguments</key><array>",
        *[f"    <string>{argument}</string>" for argument in arguments],
        "  </array>",
        f"  <key>WorkingDirectory</key><string>{worktree}</string>",
        "  <key>StartCalendarInterval</key><array>",
        weekdays.rstrip("\n"),
        "  </array>",
        f"  <key>StandardOutPath</key><string>{logs / 'agent-evening.out'}</string>",
        f"  <key>StandardErrorPath</key><string>{logs / 'agent-evening.err'}</string>",
        "</dict></plist>",
        "",
        "printed, never installed: a launchd job is installed by the operator, not by this "
        "command, and nothing under ~/Library was touched",
    ]
    return "\n".join(lines)


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    finished = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if finished.returncode != 0:
        raise StepFailedError(
            "registration",
            f"git {' '.join(arguments)} in {repository} failed: "
            f"{finished.stderr.strip() or finished.stdout.strip()}",
        )
    return finished


def _git_output(repository: Path, *arguments: str) -> str:
    return _git(repository, *arguments).stdout.strip()


def _common_directory(path: Path) -> Path:
    common = Path(_git_output(path, "rev-parse", "--git-common-dir"))
    return (path / common).resolve() if not common.is_absolute() else common.resolve()


def _pin_worktree(options: argparse.Namespace) -> int:
    """`--pin-worktree DIR`: create or move a detached worktree at C's registered commit.

    The commit is `registry.registered_checkout`'s -- the one that last touched the registration,
    after checking the bound code there is the registered `code_commit` -- so the scheduled run
    stands on code that passes this command's own step-1 admission however far development has
    moved on. An existing directory must be a clean worktree of the same repository; it is moved,
    never overwritten. Then the credentials link and the offline environment sync run exactly as
    the daily command's own pin does (`daily_selection`'s helpers).
    """
    repository = Path(options.repo)
    registration = _registration_path(options)
    try:
        root, commit, _code = registry.registered_checkout(
            registration, repository, also_bound=C_SCRIPTS
        )
    except registry.HoldoutRefusedError as error:
        raise StepFailedError(
            "registration",
            f"{type(error).__name__}: {error}",
            exit_code=AgentEveningExit.code_not_registered,
        ) from error
    directory = Path(_required(options, "pin_worktree", step="registration"))
    common = _common_directory(root)
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        _git(root, "worktree", "add", "--detach", str(directory), commit)
    else:
        if _common_directory(directory) != common:
            raise StepFailedError(
                "registration", f"{directory} is not a worktree of {root}; nothing was changed"
            )
        if _git_output(directory, "status", "--porcelain", "--untracked-files=all"):
            raise StepFailedError(
                "registration",
                f"{directory} has uncommitted changes; a pinned checkout is never edited, and "
                "nothing was changed",
            )
        _git(directory, "checkout", "-q", "--detach", commit)
    print(daily_selection.link_env_file(root, directory))
    uv = Path(options.uv) if options.uv else Path(shutil.which("uv") or "uv")
    environment = daily_selection.sync_pinned_environment(directory, uv=uv)
    print(f"pinned {directory} at {commit} (the registration's commit); environment {environment}")
    return AgentEveningExit.ok


# --- the CLI ------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_evening.py",
        description=(
            "The agent evening: score Step 0's survivors with the rule family and (unless "
            "--no-llm) the model-backed family, file them as forward predictions, and journal "
            "the session. Never trades."
        ),
    )
    parser.add_argument("--runtime-dir", help="C's runtime directory (the agent family's own)")
    parser.add_argument(
        "--step0-runtime-dir", default=None, help="Step 0's runtime directory, opened read-only"
    )
    parser.add_argument(
        "--step0-registration",
        default=None,
        help=f"Step 0's committed registration (default: {STEP0_REGISTRATION_IN_REPOSITORY})",
    )
    parser.add_argument(
        "--registration",
        default=None,
        help=f"C's committed registration (default: {REGISTRATION_IN_REPOSITORY})",
    )
    parser.add_argument("--repo", default=str(REPOSITORY), help="the repository root")
    parser.add_argument("--as-of", default=None, help="the evening's pinned clock (ISO 8601)")
    parser.add_argument("--no-llm", action="store_true", help="file the rule family only")
    parser.add_argument(
        "--dry-week",
        action="store_true",
        help=(
            "run one D7 dry-week evening on a scratch runtime: operational metrics only, no "
            "prediction filed, no C registration needed, at most five evenings"
        ),
    )
    parser.add_argument(
        "--max-model-requests",
        type=int,
        default=DEFAULT_MAX_MODEL_REQUESTS,
        help=f"hard cap on model requests this evening (default {DEFAULT_MAX_MODEL_REQUESTS})",
    )
    parser.add_argument(
        "--deadline",
        default=DEFAULT_DEADLINE,
        help=f"Shanghai wall deadline for model requests (default {DEFAULT_DEADLINE})",
    )
    parser.add_argument("--worktree", default=None, help="the pinned worktree (--print-launchd)")
    parser.add_argument("--log-dir", default=None, help="where the printed job writes its logs")
    parser.add_argument(
        "--pin-worktree",
        default=None,
        metavar="DIR",
        help="create or move the pinned worktree at C's registered commit",
    )
    parser.add_argument("--uv", default=None, help="the uv executable --pin-worktree syncs with")
    parser.add_argument(
        "--print-launchd",
        action="store_true",
        help="print the launchd plist for the weekday 20:30 job; never install it",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    transport: JsonTransport | None = None,
) -> int:
    """Run one evening, or one operator command; return the exit code."""
    parser = _parser()
    try:
        options = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as error:
        return int(error.code or 0)
    now = clock or (lambda: datetime.now(SHANGHAI))
    try:
        if options.print_launchd:
            print(_launchd_plist(options))
            return AgentEveningExit.ok
        if options.pin_worktree:
            return _pin_worktree(options)
        if options.dry_week:
            return _dry_week(options, clock=now, transport=transport)
        return _run(options, clock=now, transport=transport)
    except StepFailedError as error:
        if error.step in AGENT_STEPS:
            number = AGENT_STEPS.index(error.step) + 1
            print(f"step {number} ({error.step}) failed: {error.detail}", file=sys.stderr)
        else:
            print(f"{error.step} failed: {error.detail}", file=sys.stderr)
        return error.exit_code
    except AgentRegistrationError as error:
        print(f"registration failed: {type(error).__name__}: {error}", file=sys.stderr)
        return AgentEveningExit.refused


if __name__ == "__main__":
    sys.exit(main())
