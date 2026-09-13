"""Fixtures shared across every test subtree (`unit/`, `integration/`, `contract/`).

Only fixtures genuinely needed from more than one top-level subtree live here. A fixture
needed by files that all sit under one subtree belongs in that subtree's own
`conftest.py` instead (see `tests/contract/providers/conftest.py`); a helper needed by
exactly one file stays defined in that file (see e.g.
`tests/unit/tools/test_evidence_lookup.py`'s local `item` fixture, which itself depends
on `frozen_now` below). `migration_now`/`migration_clock` below are the converse case:
originally scoped to `tests/integration/storage/conftest.py`, promoted here once
`tests/unit/runtime/test_composition_migrations.py` -- a second top-level subtree --
needed the identical clock too.

V2-P0B-013.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from checkout_guard import (
    MODE_VARIABLE,
    RunOutputs,
    Snapshot,
    changes,
    checkout_mode,
    run_outputs,
    snapshot,
)
from offline_guard import refusing_outbound_traffic

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.logging_setup import PACKAGE_LOGGER_NAME
from openalpha_cn.providers.base import ProviderMetadata


@pytest.fixture(autouse=True)
def _reset_openalpha_logging() -> Iterator[None]:
    """Every test starts with the `openalpha_cn` logger back at its library-safe,
    unconfigured default: only the permanent `NullHandler` (see `logging_setup.py`),
    level `NOTSET`.

    Without this, `configure_logging()`'s intentional "configure once per process"
    idempotency (many tests build their own `create_app()`/invoke `cli.main()`) would
    let whichever test happens to run first in the session silently pin the real
    `StreamHandler` and log level for every test that runs after it -- so a later test
    asserting a specific `OPENALPHA_LOG_LEVEL` actually took effect would observe
    stale state instead, and `uv run pytest -q`'s clean per-test output would depend
    on test execution order rather than being a structural guarantee.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = [h for h in original_handlers if isinstance(h, logging.NullHandler)]
    logger.setLevel(logging.NOTSET)
    yield
    logger.handlers = original_handlers
    logger.setLevel(original_level)
    logger.propagate = original_propagate


# --- the canonical point-in-time clock --------------------------------------------------
#
# This exact value -- 2026-07-24T10:30:00Z -- is load-bearing in four tests that assert a
# genuine point-in-time *visibility boundary*: a record available at :00 is visible, a
# record available at the following hour's :00 is not, and this instant must sit strictly
# between the two.
#   - tests/unit/tools/test_evidence_lookup.py
#   - tests/contract/providers/test_file_provider.py
#   - tests/integration/test_evidence_interfaces.py
#   - tests/integration/storage/test_parquet_evidence_store.py
# Every other consumer of `frozen_now` just needs *a* frozen, timezone-aware instant and
# does not depend on this specific value -- but they all share the same one so that a
# single, well-understood constant is the one source of truth instead of fourteen
# hand-copied literals.
FROZEN_NOW: datetime = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)

# A second, deliberately distinct instant (30 minutes earlier, same day) used by tests that
# need *a* frozen anchor but perform no point-in-time visibility check at all: domain-model
# construction/validation tests and a plain repository round-trip. Keeping it numerically
# different from `FROZEN_NOW` -- rather than merging the two -- means nobody can accidentally
# come to depend on it lining up with the visibility-boundary tests' 10:00/11:00 records.
PLAIN_FROZEN_NOW: datetime = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now() -> datetime:
    """The shared point-in-time clock. See `FROZEN_NOW` above for why this exact value."""
    return FROZEN_NOW


@pytest.fixture
def frozen_clock(frozen_now: datetime) -> Callable[[], datetime]:
    """A zero-arg `clock=` callable that always returns `frozen_now`."""
    return lambda: frozen_now


@pytest.fixture
def plain_frozen_now() -> datetime:
    """A frozen instant for tests with no point-in-time visibility scenario to protect.

    See `PLAIN_FROZEN_NOW` above for why it is intentionally distinct from `frozen_now`.
    """
    return PLAIN_FROZEN_NOW


# --- the migration-engine clock -----------------------------------------------------------
#
# 2026-08-07T09:00:00Z, deliberately a *different* instant from `FROZEN_NOW` above.
# `tests/integration/storage/test_migrations.py` (the migration engine itself),
# `tests/integration/storage/test_versioned_reads.py` (reading pre-migration records back
# through the versioned-read path), and `tests/unit/runtime/test_composition_migrations.py`
# (`build_storage()` mounting the migration engine) all test schema-version/migration
# mechanics that have nothing to do with evidence point-in-time visibility, so this stays
# numerically distinct from `FROZEN_NOW`: keeping it a different date makes it structurally
# impossible for a future test to accidentally rely on it lining up with an evidence
# fixture's `available_time`.
MIGRATION_CLOCK: datetime = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def migration_now() -> datetime:
    """The raw frozen instant, for tests that embed it directly in a model
    (`as_of=migration_now`, `created_at=migration_now`, ...)."""
    return MIGRATION_CLOCK


@pytest.fixture
def migration_clock(migration_now: datetime) -> Callable[[], datetime]:
    """A zero-arg `clock=` callable pinned to `migration_now`, for `run_migrations(...,
    clock=...)` / `build_storage(..., clock=...)` call sites.

    A spawned `multiprocessing.Process` re-imports its target module fresh and has no
    fixture graph to draw from, so it cannot take this callable (or `migration_now`)
    directly -- `test_migrations.py`'s concurrency race instead passes `migration_now`'s
    already-*resolved* value as a plain, picklable argument and builds its own local
    zero-arg callable inside the child process.
    """
    return lambda: migration_now


# Behavioral guards for these fixtures (frozen-ness, distinctness) live in
# `tests/test_shared_fixtures.py`, not here: pytest's default collection patterns
# (`test_*.py` / `*_test.py`) do not pick up `test_*` functions declared inside a
# `conftest.py` when the suite is run normally via `testpaths`, so a `test_` function
# defined in this file would silently never run.


# --- shared factories --------------------------------------------------------------------


@pytest.fixture
def evidence(
    frozen_now: datetime,
) -> Callable[..., EvidenceSnapshot]:
    """Factory for a synthetic `EvidenceSnapshot` keyed by `kind`/`facts`, timestamped
    uniformly at `frozen_now` (event = available = ingested = revision).

    Collapses three call-for-call-identical helpers that built exactly this shape:
    `tests/integration/test_research_cycle.py`, `tests/integration/test_recovery_and_memory.py`,
    and `tests/unit/runtime/test_storage_protocol_doubles.py` (there named `_evidence`).
    Not shared with `tests/unit/agents/test_model_agent.py`'s local `theme_evidence`
    fixture (a fixed, zero-argument "theme" evidence with a different payload shape) or
    `tests/integration/storage/test_parquet_evidence_store.py`'s local `evidence` fixture
    (parameterized by `event_hour`/`available_hour` to drive its own point-in-time
    boundary tests) -- both are single-file, single-purpose helpers left where they are.
    """

    def _make(*, kind: str, facts: dict[str, object]) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            subject="000001.SZ",
            kind=kind,
            timeline=Timeline(
                event_time=frozen_now,
                available_time=frozen_now,
                ingested_time=frozen_now,
                revision_time=frozen_now,
            ),
            source_id="synthetic.a-share",
            source_uri=f"fixture://{kind}/000001.SZ",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary=f"Synthetic {kind}.",
            payload={
                "schema": "a-share-evidence/v1",
                "family": {
                    "limit_up": "market_event",
                    "theme": "theme",
                    "capital": "capital",
                }[kind],
                "facts": facts,
                "quality_flags": [],
            },
        )

    return _make


@pytest.fixture
def metadata() -> ProviderMetadata:
    """A synthetic `ProviderMetadata` for the generic "user-owned file" provider used by
    the point-in-time file-provider tests.

    Shared by `tests/contract/providers/test_file_provider.py` and
    `tests/integration/test_evidence_interfaces.py`, which previously each defined an
    equivalent but textually drifted factory -- no assertion in either file depends on the
    exact wording of `display_name`/`freshness`/`failure_semantics`, only on
    `provider_id="user.file"` and `redistribution="restricted"`, both preserved here.

    Deliberately NOT shared with `tests/unit/evidence/test_builder.py`'s own `metadata()`
    (kept local, not promoted to a fixture): that one must keep
    `provider_id="synthetic.a-share"` and `redistribution="allowed"` because
    `test_builder_normalizes_all_v1_a_share_evidence_families` asserts
    `item[0].source_id == "synthetic.a-share"` directly, and
    `test_builder_adds_quality_flags_without_losing_source_facts` relies on the base
    redistribution being *not* already `"restricted"` before its own override.
    """
    return ProviderMetadata(
        provider_id="user.file",
        display_name="User-owned file",
        source_license="user-supplied",
        redistribution="restricted",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input-file",
        failure_semantics="Malformed or unreadable inputs raise ProviderFailure.",
    )


@pytest.fixture
def bar() -> Callable[..., MarketBar]:
    """Factory for a flat (open == high == low == close) `MarketBar` on a given
    `trade_date`, used by multi-day portfolio simulation tests that only care about the
    day's closing mark, not intraday OHLC movement.

    Collapses `tests/unit/backtest/test_portfolio.py` and
    `tests/integration/test_portfolio_ledger_backtest.py`'s previously separate
    "close-only" `bar()` helpers (identical in every field except cosmetic differences:
    keyword-with-default vs. positional-required arguments, and `Decimal("10.00")` vs.
    `Decimal("10")` for `previous_close` -- numerically equal, and neither file asserts on
    its string form).

    Deliberately NOT shared with `tests/unit/backtest/test_execution.py`'s `bar(**updates)`
    kwargs-override helper: that one independently varies board/previous_close/open/high/
    low/close/suspended/is_st to drive execution-policy edge cases (star-market lot sizing,
    locked limit-up bars, commission math), a genuinely different need from a single
    flat closing price. See task-13-report.md for the full comparison.
    """

    def _make(trade_date: date, close: str = "10.00") -> MarketBar:
        price = Decimal(close)
        return MarketBar(
            subject="000001.SZ",
            trade_date=trade_date,
            board="main",
            previous_close=Decimal("10.00"),
            open=price,
            high=price,
            low=price,
            close=price,
            suspended=False,
            is_st=False,
        )

    return _make


# --- the offline guarantee ----------------------------------------------------------------
#
# "The test suite does not touch the network" was a convention until `tests/e2e/` landed: every
# provider test injected a transport, and nothing checked that they all did. A convention is
# exactly what a new test file is written without knowing about, and the cost of getting it
# wrong is not a red build -- it is a *green* one that depended on an endpoint being up, on a
# token being present, and on today's market data looking like yesterday's.
#
# The fixture below turns it into a property of the run. Its limits, stated rather than
# implied:
#
#   - It refuses `GUARDED_AUDIT_EVENTS` on `AF_INET`/`AF_INET6` sockets **in this process**. A
#     child process gets a fresh interpreter and is not guarded: `subprocess` always launches
#     one, and so does `multiprocessing` when it uses the `spawn` start method -- which this
#     repository's own multiprocessing tests choose explicitly
#     (`multiprocessing.get_context("spawn")`), never the platform default (on Linux, `fork`
#     before Python 3.14, which copies the parent process instead of starting a fresh
#     interpreter, and `forkserver` from it).
#     `tests/unit/test_repository_assets.py` shells out to `git`, and
#     `tests/integration/storage/test_migrations.py` and
#     `tests/integration/panel/test_panel_store.py` each spawn worker processes this way; none
#     of that is a network call. `tests/e2e/` reaches Tushare exactly this way, through the
#     real `openalpha` binary.
#   - It does not intercept name resolution. `getaddrinfo` alone transfers nothing, and refusing
#     it would break `socket.getaddrinfo("localhost", ...)`-style calls inside the standard
#     library that never go on to connect. This limit is unchanged by `V2-P4-105` and is not
#     quietly narrowed by it: `offline_guard.UNGUARDED_RESOLUTION_EVENT` names the event that is
#     deliberately absent, and `tests/unit/test_offline_suite.py::
#     test_name_resolution_is_outside_the_guard_and_stays_outside` asserts it stays absent.
#   - `AF_UNIX` and every other family are left alone: a local socket is not the network, and
#     refusing one would be a guess about what a future test needs rather than a rule about
#     what this one forbids.
#   - It refuses by **family**, never by destination. A datagram aimed at loopback is refused on
#     the same terms as one aimed at a routable host, because a guard that read the address
#     would have to decide which addresses are the network, and `127.0.0.1` is the answer a
#     test reaches for when it wants to be sure -- which is exactly the reasoning that let the
#     `sendto` hole `V2-P4-039` filed look harmless.
#   - It refuses what goes through `_socket`, which is every socket CPython itself opens, and it
#     is **not** a sandbox. Code that reaches the kernel without passing through the socket
#     module -- `ctypes.CDLL(None).connect(...)` is the short spelling -- raises no audit event
#     and is not seen. That is the same class of limit as the child process above: it is
#     deliberate evasion rather than the drift this guard exists to catch, and no in-process
#     mechanism available to a test suite closes it.
#   - It covers an item's whole protocol and the collection phase, and **not** the teardown of a
#     session-scoped fixture, which runs after the last item's protocol has closed. This limit
#     is stated as a fifth because until `V2-P5-031` the guard's real scope was far narrower
#     than any of the four above implied -- it was a function-scoped autouse fixture, so module
#     import time and every broad-scoped fixture's *setup* were unguarded, and a reader checking
#     whether "fetch it once in a session fixture" was covered found a list of limits that did
#     not mention the question. Nothing outside `tests/e2e/` is session-scoped today, and
#     `tests/unit/test_offline_suite.py::
#     test_the_scope_the_guard_does_not_reach_is_the_declared_one`
#     is what keeps that a measurement rather than a hope.
#
# `V2-P4-039` widened the first limit from `connect`/`connect_ex` to four method names, and
# `V2-P4-105` moved it off method names altogether. Shadowing names on `socket.socket` guarded
# the Python *wrapper* class, which inherits all four from the C `_socket.socket` and defines
# none of them -- so `import _socket` walked straight out, as did re-wrapping a guarded socket's
# own `detach()`ed file descriptor. Widening the shadow onto the base class is impossible
# (`_socket.socket` is an immutable extension type), so the guard is a PEP 578 audit hook and
# sits *below* the class graph instead of spreading across it. `offline_guard.py`'s docstrings
# carry the escapes, the measurement and the closure argument.
#
# `tests/unit/test_offline_suite.py` proves the guard is live rather than merely installed.


@pytest.hookimpl(wrapper=True)
def pytest_collection(session: pytest.Session) -> Generator[None, object, object]:
    """Guard the import of every test module, which is where the guard used to begin too late.

    `V2-P5-031`. Collection is when module bodies run, and a module body is a place a
    "fetch it once at import" line can be written. Guarding here costs nothing measurable and
    covers every module in the tree, `tests/e2e/` included: those modules are still *imported*
    in a default run (only their tests are deselected), and none of them touches the network to
    be imported -- measured, the whole tree collects clean under the guard.
    """
    with refusing_outbound_traffic():
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Guard a whole item -- setup, call and teardown -- rather than only its call phase.

    This was a **function-scoped autouse fixture**, and `V2-P5-031` measured what that left
    open. `_depth` rose when the fixture ran and fell when it unwound, so everything pytest does
    around a test was outside the guard:

        module-import-time            -> NOT REFUSED, connection completed
        session-scoped fixture setup  -> NOT REFUSED, connection completed
        module-scoped fixture setup   -> NOT REFUSED, connection completed
        class-scoped fixture setup    -> NOT REFUSED, connection completed
        inside a test body            -> refused: OfflineSuiteViolation

    "Fetch it once and share it" is the most natural sentence in the language of a session-
    scoped fixture, and it was the one place the guard could not see. `tests/conftest.py`
    declared four limits and this was not among them, which is worse than declaring it: a
    reader checking whether their fixture was covered found a list that did not mention the
    question.

    A hook wrapper rather than a wider fixture, because a wider fixture cannot express the
    exemption. Broad-scoped fixtures are instantiated *before* any function-scoped autouse
    fixture, so a session-scoped guard would be holding `_depth` at one while `tests/e2e/`'s
    own session-scoped `built_panel` fetched a real panel, and the way back out would be a
    fixture that decrements a counter another fixture owns. `pytest_runtest_protocol` brackets
    the entire item, so the broad-scoped fixtures an item pulls in are set up inside its
    bracket -- guarded for an unmarked item, and never entered at all for an `e2e` one.

    **The limit that remains, stated rather than implied**: a broad-scoped fixture's *teardown*
    runs when its scope ends, and for a session-scoped one that is after the last item's
    protocol has closed. Nothing in this tree has a session-scoped finaliser, and
    `tests/unit/test_offline_suite.py::test_the_scope_the_guard_does_not_reach_is_the_declared_one`
    is what makes that a measured statement rather than a hopeful one.
    """
    if item.get_closest_marker("e2e") is not None:
        return (yield)

    with refusing_outbound_traffic():
        return (yield)


# --- no inherited git variables ------------------------------------------------------------
#
# D13. Another lane gated each commit with `git rebase --exec`, and git exported `GIT_DIR` to the
# gate -- a hook gets `GIT_WORK_TREE` and `GIT_INDEX_FILE` as well. Every test that builds a
# throwaway repository then aimed its `git init`/`config`/`add`/`commit` at the real one: the
# shared `.git/config` came back with `core.bare=true` and `user.email=test@example.com`, the
# detached HEAD with a test commit, the index with `file.txt`. A pre-commit or pre-push hook that
# runs this suite is the same environment, and the repository is then the user's.
#
# `pytest_configure` below takes every `GIT_*` variable out of this process before a single test
# module is imported, so no import, no fixture of any scope, no test and no process any of them
# starts can inherit one. Its limits, stated rather than implied:
#
#   - A test may still set `GIT_*` itself, and the git it starts sees what it set.
#     `tests/unit/test_repository_assets.py::
#     test_the_scratch_repository_ignores_git_templates_user_excludes_and_git_environment` sets
#     five on purpose, and `_without_git_variables` in that file is what keeps them out of its
#     scratch repository -- which is why that helper stays.
#   - It changes nothing git discovers from a working directory. A test that runs git inside this
#     checkout reads this checkout -- the publication gate's `git ls-files`, and
#     `resolve_code_commit()` anchored at the installed package -- and is meant to.
#   - Only pytest's own start-up and this module's imports run before it, and neither starts git.
#     `--noconftest` removes this file and the protection with it, which is exactly what
#     `tests/unit/test_inherited_git_variables.py` uses as its control.
#   - A name is compared upper-cased, the spelling `_without_git_variables` uses too. Nothing
#     measurable rests on it: CPython already upper-cases every `os.environ` key on Windows, and
#     git reads only upper-case names on POSIX, so a mutation that drops `.upper()` survives. It
#     stays so that the two spellings of one rule agree, not because either platform needs it.
#   - It takes *every* `GIT_*`, not only the ones that point git at a repository (`GIT_DIR`,
#     `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`). Configuration the environment
#     carries under that prefix goes with them -- `GIT_CONFIG_COUNT` with its `GIT_CONFIG_KEY_<n>`
#     and `GIT_CONFIG_VALUE_<n>` pairs, `GIT_CONFIG_PARAMETERS` (how `git -c` reaches the
#     commands it starts), `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_NOSYSTEM` -- and
#     so does an identity given as `GIT_AUTHOR_*` or `GIT_COMMITTER_*`, and every other `GIT_*`.
#     What git reads from the environment without the prefix stays, and it is not nothing: `EMAIL`
#     is where git takes an author's and a committer's address from when no `user.email` is
#     configured, and `HOME` and `XDG_CONFIG_HOME` decide which global configuration file it
#     reads. Measured under this hook: with `GIT_AUTHOR_EMAIL` and a `GIT_CONFIG_*` `user.email`
#     both taken, a test's `git var GIT_AUTHOR_IDENT` gave `EMAIL`'s address. None of those three
#     can point git at another repository, which is the incident this hook exists for, so they
#     are left; a test that needs a known identity passes it on its own command line, `git -c`.
#     `pytest_unconfigure` hands back every variable it took, not only the four.
#
# A hook and not the function-scoped autouse fixture that is the obvious spelling, for the reason
# `V2-P5-031` gives above for the offline guard: such a fixture is live for a test's body and its
# function-scoped fixtures, and a module-scoped fixture that builds a throwaway repository -- the
# natural "build it once" -- is set up before it. `tests/unit/test_inherited_git_variables.py`
# measures every phase, and the hand-back in `pytest_unconfigure`.

_INHERITED_GIT_VARIABLES = pytest.StashKey[dict[str, str]]()
"""What `pytest_configure` took out, kept on the run's own `Config` for `pytest_unconfigure`."""


def _is_git_variable(name: str) -> bool:
    return name.upper().startswith("GIT_")


def pytest_configure(config: pytest.Config) -> None:
    """Take every `GIT_*` variable this run inherited out of the environment, before collection."""
    config.stash[_INHERITED_GIT_VARIABLES] = {
        name: os.environ.pop(name) for name in list(os.environ) if _is_git_variable(name)
    }


def pytest_unconfigure(config: pytest.Config) -> None:
    """Hand them back: the run is over, and a process that called `pytest.main` is not ours."""
    os.environ.update(config.stash.get(_INHERITED_GIT_VARIABLES, {}))


# --- the checkout is not the tests' to write -----------------------------------------------------
#
# D13 I-D. Four tests rewrote tracked modules under `src/` with one more import and wrote the
# original back with `Path.write_text`, which writes CRLF on Windows: both Windows legs of CI
# printed `CRLF will be replaced by LF` for those four files after every run, while Linux and macOS
# got their bytes back and `git status` said nothing. Six more created a probe module under `src/`
# and deleted it in `finally`, and `grimp.build_graph` left `.grimp_cache/` at the root.
#
# `tests/unit/test_no_test_writes_the_checkout.py` reads the test tree for writes like those. The
# four hooks below measure a run for them instead: a `checkout_guard.snapshot` when the session
# starts, another when collection has finished and a last one when the session ends, and any
# `checkout_guard.changes` between them fail the run however its tests went. What the run writes
# because its own options said so -- a junit or coverage report, a basetemp, pytest's cache -- is
# read off those options when the session starts (`checkout_guard.run_outputs`) and left out.
# `tests/checkout_guard.py` says what a snapshot holds and ignores, and what fails a session
# although no test caused it: the guard sees that a path changed, never who changed it.
#
# `OPENALPHA_CHECKOUT_GUARD=report` downgrades the failure to the same report and leaves the exit
# status to the tests; unset, the mode is `fail`, which is what CI runs, and any other value is a
# usage error. It is a downgrade and not an off switch on purpose: `--noconftest`, the only way
# out before it, took the `GIT_*` protection and the offline guard with it.
#
# The root measured is `config.rootpath`, the project pytest was started on, which is this
# checkout whenever pytest is run on it. A child pytest a test starts with `-p conftest` and its
# own `--rootdir` measures that directory instead, which is how
# `tests/unit/test_no_test_writes_the_checkout.py` drives these hooks without writing here.

_CHECKOUT_MODE = pytest.StashKey[str]()
"""`fail` or `report`, from `OPENALPHA_CHECKOUT_GUARD`, read once when the session starts."""

_CHECKOUT_OUTPUTS = pytest.StashKey[RunOutputs]()
"""Where this run writes because its own options said so, read once when the session starts."""

_CHECKOUT_SNAPSHOTS = pytest.StashKey[dict[str, Snapshot]]()
"""The snapshots taken so far in this run, keyed by the instant each was taken at."""

_CHECKOUT_WRITES = pytest.StashKey[list[str]]()
"""What `pytest_sessionfinish` found, kept for `pytest_terminal_summary` to print."""


def pytest_sessionstart(session: pytest.Session) -> None:
    """Read the mode and this run's own outputs, and snapshot the checkout before collection."""
    config = session.config
    config.stash[_CHECKOUT_MODE] = checkout_mode()
    config.stash[_CHECKOUT_OUTPUTS] = run_outputs(config)
    config.stash[_CHECKOUT_SNAPSHOTS] = {"start": snapshot(config.rootpath)}


def pytest_collection_finish(session: pytest.Session) -> None:
    """Snapshot it again once collection has imported what it is going to import."""
    taken = session.config.stash.get(_CHECKOUT_SNAPSHOTS, None)
    if taken is not None:
        taken["collected"] = snapshot(session.config.rootpath)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Compare the snapshots; in `fail` mode, make a run that wrote the checkout a failed run."""
    config = session.config
    taken = config.stash.get(_CHECKOUT_SNAPSHOTS, None)
    if taken is None:
        return
    outputs = config.stash[_CHECKOUT_OUTPUTS]
    finished = snapshot(config.rootpath)
    collected = taken.get("collected")
    if collected is None:
        written = [
            f"before collection finished, {change}"
            for change in changes(taken["start"], finished, outputs)
        ]
    else:
        written = [
            *(
                f"while collecting, {change}"
                for change in changes(taken["start"], collected, outputs)
            ),
            *(f"while the tests ran, {change}" for change in changes(collected, finished, outputs)),
        ]
    if not written:
        return
    config.stash[_CHECKOUT_WRITES] = written
    failing = config.stash[_CHECKOUT_MODE] == "fail"
    if failing and session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Say what changed and where, under a heading of its own, above the counts."""
    written = config.stash.get(_CHECKOUT_WRITES, [])
    if not written:
        return
    reporting = config.stash.get(_CHECKOUT_MODE, "fail") == "report"
    markup = {"yellow": True} if reporting else {"red": True}
    terminalreporter.section("this run wrote the checkout it ran from", bold=True, **markup)
    for line in written:
        terminalreporter.write_line(line, **markup)
    terminalreporter.write_line(
        f"(under {config.rootpath}) Each of these changed while this run was in progress. A test "
        "that writes ought to write its tmp_path; but an editor, a formatter, git or a second run "
        "working in this checkout meanwhile changes them too, and so does an output of this run "
        "that tests/checkout_guard.py does not read -- its docstring lists what fails a run "
        "although no test caused it."
    )
    terminalreporter.write_line(
        f"{MODE_VARIABLE}=report: reported only; the exit status is the tests' own."
        if reporting
        else f"The run fails for this whatever its tests did. {MODE_VARIABLE}=report prints the "
        "same and leaves the exit status to the tests; a suite you mean to edit during is better "
        "run in a worktree of its own."
    )
