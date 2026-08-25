"""`V2-P5-010`'s scheduling primitive, met the way an operator meets it (`V2-P5-013`).

`V2-P5-010` shipped `job_contracts.py`, `storage/jobs.py` and `scheduler.py` and said in its own
row that it was closing only half the work: the three modules have no CLI command, no REST
route, are not in `build_storage`, and nothing in the shipping product calls them. Audit `F98`
carries the same sentence -- the primitive is closed and the caller is not. Every guarantee
those three modules provide was tested at its own boundary against real SQLite, and **"an
operator can run due jobs" was not tested at all**, because there was nothing to run.

So every assertion here starts at a `CliRunner` or a `TestClient` over a real runtime directory
holding a real generated panel. The work `openalpha jobs run` performs is a point-in-time panel
health report at each owed session's **own** publication instant, which is what makes the
assertions below able to separate a scheduler that reads the calendar from one that does not:
the same command over the same store answers `succeeded` for 2026-01-16 and `failed` for
2026-01-19, and the only thing that differs is the instant the session publishes at.

**What the fixture holds, measured rather than assumed.** `panel_fixtures.generate_panel` writes
a 2026 calendar of 259 sessions and `daily` rows through 2026-01-16. So at 2026-01-16's
publication instant the `daily` dataset is clean, and at 2026-01-19's it has a `date_gap` -- an
ordinary, honest failure of exactly the kind a scheduled job meets on a Tuesday, and the one that
found `retry_session` missing from the store its own docstring named.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.job_contracts import CatchUpPolicy, ScheduledJob
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.storage.jobs import SQLiteJobStore

DATASET: Final[str] = "daily"
YEAR: Final[str] = "2026"
SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)

CLEAN_SESSION: Final[date] = date(2026, 1, 16)
"""A Friday the fixture's `daily` rows reach, so the health report at its instant is clean."""

GAPPED_SESSION: Final[date] = date(2026, 1, 19)
"""The Monday after. The calendar has it; the fixture's rows stop before it, so it is a
`date_gap` -- a real failure, not an invented one."""

NEWER_SESSION: Final[date] = date(2026, 1, 20)
"""The Tuesday after that, so `GAPPED_SESSION` is a *missed* session rather than the newest."""

CLEAN_AT: Final[str] = "2026-01-16T20:00:00+08:00"
"""Past 16:30 Asia/Shanghai on 2026-01-16, so that session has published and is the newest."""

LATER_AT: Final[str] = "2026-01-20T20:00:00+08:00"
"""Past 16:30 on 2026-01-20. Two sessions have published since `CLEAN_AT`: the 19th and the
20th. 2026-01-17 and -18 are a weekend, which is what makes "two sessions" and "four days"
different answers -- a scheduler counting `timedelta(days=1)` is red here."""


def _cli(*arguments: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, list(arguments))
    return result.exit_code, result.stdout, result.output


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A runtime directory holding the generated panel and nothing else.

    Module-scoped for the panel build; every test below registers its **own** job id, so no two
    of them touch the same row of `scheduled_jobs`.
    """
    root = tmp_path_factory.mktemp("scheduled-jobs")
    write_generated_panel(PanelStore(root / "panel"), generate_panel(shapes=SHAPES))
    return root


def _register(root: Path, job_id: str, catch_up: str) -> tuple[int, str, str]:
    return _cli("jobs", "register", job_id, "--catch-up", catch_up, "--runtime-dir", str(root))


def _due(root: Path, job_id: str, *, as_of: str) -> tuple[int, dict[str, Any]]:
    code, stdout, output = _cli(
        "jobs",
        "due",
        job_id,
        "--year",
        YEAR,
        "--exchange",
        EXCHANGE,
        "--as-of",
        as_of,
        "--runtime-dir",
        str(root),
        "--json",
    )
    assert stdout.strip(), output
    return code, json.loads(stdout.strip())


def _run_job(root: Path, job_id: str, as_of: str, *extra: str) -> tuple[int, dict[str, Any]]:
    code, stdout, output = _cli(
        "jobs",
        "run",
        job_id,
        "--dataset",
        DATASET,
        "--year",
        YEAR,
        "--exchange",
        EXCHANGE,
        "--as-of",
        as_of,
        "--runtime-dir",
        str(root),
        "--json",
        *extra,
    )
    assert stdout.strip(), output
    return code, json.loads(stdout.strip())


def _stored(root: Path, job_id: str) -> ScheduledJob:
    job = SQLiteJobStore(root / "state.sqlite3").get(job_id)
    assert job is not None
    return job


def test_an_operator_registers_a_schedule_and_reads_it_back(runtime: Path) -> None:
    """The first half of a face: a schedule that survives the process that declared it.

    Asserted through `jobs list` rather than through the store, because a `register` that wrote
    a row no listing could show would be a durable table with no operator on the other end of
    it -- which is the state `V2-P5-010` shipped and this row exists to leave.
    """
    code, _, output = _register(runtime, "registered-job", "run-each-missed")
    assert code == 0, output

    listed_code, stdout, listed_output = _cli(
        "jobs", "list", "--runtime-dir", str(runtime), "--json"
    )
    assert listed_code == 0, listed_output
    rows = {row["job_id"]: row for row in json.loads(stdout.strip())["jobs"]}

    assert rows["registered-job"]["catch_up"] == CatchUpPolicy.RUN_EACH_MISSED.value
    assert rows["registered-job"]["last_fired_session"] is None
    assert rows["registered-job"]["lease_owner"] is None


def test_a_second_registration_does_not_reset_progress(runtime: Path) -> None:
    """A restart re-declares its schedule; that must not re-run every session since the last.

    `SQLiteJobStore.register` has always been idempotent by declaration. What was untested is
    that the *command* is -- an implementation that deleted and re-inserted would satisfy every
    unit test in `tests/unit/test_job_store.py`, which never goes through this face.
    """
    _register(runtime, "restarted-job", "run-each-missed")
    code, _ = _run_job(runtime, "restarted-job", CLEAN_AT)
    assert code == 0

    again, _, output = _register(runtime, "restarted-job", "skip-missed")

    assert again == 0, output
    stored = _stored(runtime, "restarted-job")
    assert stored.last_fired_session == CLEAN_SESSION
    assert stored.catch_up is CatchUpPolicy.RUN_EACH_MISSED, (
        "re-registering silently rewrote the catch-up policy, which is how a SKIP_MISSED job "
        "quietly becomes a RUN_EACH_MISSED one on the next restart"
    )


def test_what_is_due_is_read_off_the_calendar_and_not_off_a_stored_fire_time(
    runtime: Path,
) -> None:
    """The design claim `V2-P5-010` asked to be doubted, driven from the command line.

    A stored fire time is derived from a calendar that changes, so `due()` never reads it. This
    writes a fire time into the row saying the job is not due for a year and asserts the command
    still owes the sessions the calendar says it owes.

    The control is the same command before the write. Without it, a `due` that always answered
    the same list would satisfy the equality below, and so would one that answered nothing.
    """
    _register(runtime, "fire-time-job", "run-each-missed")
    _run_job(runtime, "fire-time-job", CLEAN_AT)
    before_code, before = _due(runtime, "fire-time-job", as_of=LATER_AT)
    assert before_code == 0
    assert before["owed"] == [GAPPED_SESSION.isoformat(), NEWER_SESSION.isoformat()]

    store = SQLiteJobStore(runtime / "state.sqlite3")
    stored = _stored(runtime, "fire-time-job")
    with store._connect() as connection:
        connection.execute(
            "UPDATE scheduled_jobs SET next_fire_time = ? WHERE job_id = ?",
            ("2027-06-01T16:30:00+08:00", "fire-time-job"),
        )
        connection.commit()

    after_code, after = _due(runtime, "fire-time-job", as_of=LATER_AT)

    assert after_code == 0
    assert after["owed"] == before["owed"]
    assert stored.last_fired_session == CLEAN_SESSION


def test_running_a_job_performs_the_work_at_the_owed_session_s_own_instant(
    runtime: Path,
) -> None:
    """The claim the whole row is about: an operator can run due jobs, and something happens.

    Two attempts of one command over one store, and they answer differently **because the
    sessions publish at different instants**: 2026-01-16 is inside the fixture's `daily` rows
    and 2026-01-19 is past them. A command that ran the health report once at wall-clock now and
    stamped it onto whichever session was owed would answer the same for both, so this is the
    assertion that separates a point-in-time run from a run that merely records a date.

    The second half is the catch-up *stopping*. `finish_session` does not advance
    `last_fired_session` past a failure, but a later success in the same loop would move the
    watermark over it -- a daily ingest that failed on Monday and succeeded on Wednesday would
    report itself complete through Wednesday with Monday's hole still open. So the loop stops at
    the first failure, and `last_fired_session` is asserted to still be behind it.
    """
    _register(runtime, "point-in-time-job", "run-each-missed")

    first_code, first = _run_job(runtime, "point-in-time-job", CLEAN_AT)

    assert first_code == 0, first
    assert first["owed"] == [CLEAN_SESSION.isoformat()]
    assert [(a["session"], a["status"]) for a in first["attempts"]] == [
        (CLEAN_SESSION.isoformat(), "succeeded")
    ]
    assert _stored(runtime, "point-in-time-job").last_fired_session == CLEAN_SESSION

    second_code, second = _run_job(runtime, "point-in-time-job", LATER_AT)

    assert second_code == int(PanelExit.unhealthy), second
    assert second["owed"] == [GAPPED_SESSION.isoformat(), NEWER_SESSION.isoformat()]
    assert [(a["session"], a["status"]) for a in second["attempts"]] == [
        (GAPPED_SESSION.isoformat(), "failed")
    ], "the catch-up ran on past a failed session and left a hole behind the watermark"
    assert second["attempts"][0]["error_type"] == "date_gap"
    assert second["stopped_after"] == GAPPED_SESSION.isoformat()
    assert _stored(runtime, "point-in-time-job").last_fired_session == CLEAN_SESSION


def test_a_session_already_run_is_not_run_a_second_time(runtime: Path) -> None:
    """The per-trading-day idempotency key, met through the command that a cron line runs.

    Two shapes of "already", and they are different facts:

    1. A session the job **succeeded** at is no longer owed at all -- `last_fired_session` moved
       past it -- so the second invocation attempts nothing and exits `0`. A cron line firing
       every ten minutes must not re-run the day's work nine more times.
    2. A session the job **failed** at is still owed and its row is still there, so the second
       invocation refuses to attempt it rather than meeting `JobAlreadyRanError` as a crash.
       That refusal is the one that needs `--retry-failed`, and it is a non-zero exit because
       the work has still not happened.
    """
    _register(runtime, "idempotent-job", "run-each-missed")
    _run_job(runtime, "idempotent-job", CLEAN_AT)

    repeat_code, repeat = _run_job(runtime, "idempotent-job", CLEAN_AT)

    assert repeat_code == 0, repeat
    assert repeat["owed"] == []
    assert repeat["attempts"] == []

    _run_job(runtime, "idempotent-job", LATER_AT)
    blocked_code, blocked = _run_job(runtime, "idempotent-job", LATER_AT)

    assert blocked_code == int(PanelExit.unhealthy), blocked
    assert [(a["session"], a["status"]) for a in blocked["attempts"]] == [
        (GAPPED_SESSION.isoformat(), "already_attempted")
    ]
    assert "--retry-failed" in json.dumps(blocked)


def test_retry_failed_reopens_the_failed_session_rather_than_leaving_it_stuck(
    runtime: Path,
) -> None:
    """`V2-P5-013`'s store fix, driven from the face that found it.

    Without `retry_session` a failed session is owed for ever and refused for ever: the run row
    holds the primary key and `last_fired_session` never moves past it, so the job can never
    reach that session **or any session after it**. Nothing had met it because nothing outside
    `tests/unit/` called this store.

    The assertion that separates a real retry from a no-op is `started_at` moving while the
    session does not: a retry is another attempt at the same trading day. The failure is
    expected to repeat -- the fixture's `date_gap` is not something time fixes -- and that is
    the point: the operator gets to attempt it again and gets the same honest answer, rather
    than a `JobAlreadyRanError` traceback.
    """
    _register(runtime, "retry-job", "run-each-missed")
    _run_job(runtime, "retry-job", CLEAN_AT)
    _run_job(runtime, "retry-job", LATER_AT)
    store = SQLiteJobStore(runtime / "state.sqlite3")
    before = store.run_for("retry-job", GAPPED_SESSION)
    assert before is not None and before.status == "failed"

    code, retried = _run_job(runtime, "retry-job", LATER_AT, "--retry-failed")

    assert code == int(PanelExit.unhealthy), retried
    assert [(a["session"], a["status"]) for a in retried["attempts"]] == [
        (GAPPED_SESSION.isoformat(), "failed")
    ]
    after = store.run_for("retry-job", GAPPED_SESSION)
    assert after is not None
    assert after.session == GAPPED_SESSION
    assert after.started_at > before.started_at, (
        "the retry recorded no new attempt, so --retry-failed did nothing but re-report the old one"
    )


def test_a_skip_missed_job_advances_past_what_it_skipped_and_records_no_run_for_it(
    runtime: Path,
) -> None:
    """The second catch-up policy, and the reason it is not implemented as a fake success.

    A skipped session must leave **no** `job_runs` row, so `runs()` keeps meaning "what this job
    did" rather than "what this job was scheduled for". It must also be *reported* -- a command
    that logs "ran 2026-01-20" and never mentions the session it decided not to run has made the
    policy invisible at the moment it mattered.

    Both are asserted, because either alone passes under a wrong implementation: reporting
    without advancing leaves the session owed for ever, and advancing without reporting is the
    silent skip.
    """
    _register(runtime, "snapshot-job", "skip-missed")
    _run_job(runtime, "snapshot-job", CLEAN_AT)

    code, skipped = _run_job(runtime, "snapshot-job", LATER_AT)

    assert code == int(PanelExit.unhealthy), skipped
    assert skipped["skipped"] == [GAPPED_SESSION.isoformat()]
    assert skipped["owed"] == [NEWER_SESSION.isoformat()]
    assert [a["session"] for a in skipped["attempts"]] == [NEWER_SESSION.isoformat()]

    store = SQLiteJobStore(runtime / "state.sqlite3")
    assert store.run_for("snapshot-job", GAPPED_SESSION) is None
    assert [run.session for run in store.runs("snapshot-job")] == [
        CLEAN_SESSION,
        NEWER_SESSION,
    ]


def test_a_job_another_process_holds_is_left_alone_rather_than_run_twice(
    runtime: Path,
) -> None:
    """A cron line that fires while the previous run is still going must be a no-op, not a race.

    Exit `0` and not a failure: the work is being done, by somebody. The `claimed` key is what
    tells that apart from "there was nothing to do", which is the distinction a log line reading
    only "nothing to do" would lose.

    The lease is taken by a second `SQLiteJobStore` over the same file, which is how "another
    process" is expressed against a store whose whole point is that SQLite arbitrates.
    """
    _register(runtime, "leased-job", "run-each-missed")
    store = SQLiteJobStore(runtime / "state.sqlite3")
    held = store.claim(
        "leased-job",
        owner="another-process",
        now=datetime.now(UTC),
        lease_for=timedelta(minutes=30),
    )
    assert held is not None

    code, body = _run_job(runtime, "leased-job", CLEAN_AT)

    assert code == 0, body
    assert body["claimed"] is False
    assert body["attempts"] == []
    assert store.run_for("leased-job", CLEAN_SESSION) is None


def test_a_job_that_was_never_registered_is_refused_by_name(runtime: Path) -> None:
    """`bad_request` and not a traceback, and not `unhealthy` either.

    A scheduler asked about a schedule it does not hold has been given the wrong name; answering
    "nothing is due" would be indistinguishable from a correctly idle job, which is the reason
    `TradingDayScheduler.due` raises `KeyError` rather than returning an empty answer. This is
    where that `KeyError` becomes an exit code and a sentence.
    """
    code, _, output = _cli(
        "jobs",
        "due",
        "never-declared",
        "--year",
        YEAR,
        "--exchange",
        EXCHANGE,
        "--as-of",
        CLEAN_AT,
        "--runtime-dir",
        str(runtime),
        "--json",
    )

    assert code == int(PanelExit.bad_request), output
    assert "never-declared" in output
    assert "openalpha jobs register" in output


# --- the REST face -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rest(runtime: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime)) as client:
        yield client


def test_the_rest_face_lists_the_same_schedules_the_cli_lists(
    runtime: Path, rest: TestClient
) -> None:
    """`GET /api/v1/jobs`, asserted **equal** to `openalpha jobs list --json`.

    Read-only on purpose and stated here rather than only in prose: this API has no
    authentication at all (`F101`, still open), so registering a schedule and taking a lease stay
    on the machine that holds the runtime directory. What an unauthenticated reader can have is
    the answer to "is the daily job running", which is the operational question a dashboard asks.
    """
    _register(runtime, "rest-visible-job", "skip-missed")
    _, stdout, _ = _cli("jobs", "list", "--runtime-dir", str(runtime), "--json")

    response = rest.get("/api/v1/jobs")

    assert response.status_code == 200, response.text
    assert response.json() == json.loads(stdout.strip())
    assert "rest-visible-job" in {row["job_id"] for row in response.json()["jobs"]}


def test_the_rest_face_serves_one_job_s_runs_and_404s_an_unregistered_one(
    runtime: Path, rest: TestClient
) -> None:
    """`GET /api/v1/jobs/{job_id}` carries the per-session runs the listing does not.

    Both halves are driven, because `404` alone is what an unrouted path answers too: the found
    case is required to carry the run rows, and the missing case is required to carry this
    module's `{reason, message}` object rather than the router's bare `"Not Found"` string.
    """
    _register(runtime, "rest-runs-job", "run-each-missed")
    _run_job(runtime, "rest-runs-job", CLEAN_AT)

    found = rest.get("/api/v1/jobs/rest-runs-job")
    missing = rest.get("/api/v1/jobs/never-declared")

    assert found.status_code == 200, found.text
    assert found.json()["job"]["last_fired_session"] == CLEAN_SESSION.isoformat()
    assert [(r["session"], r["status"]) for r in found.json()["runs"]] == [
        (CLEAN_SESSION.isoformat(), "succeeded")
    ]
    assert missing.status_code == 404, missing.text
    assert isinstance(missing.json()["detail"], dict), missing.text
    assert missing.json()["detail"]["reason"] == "not_held"
