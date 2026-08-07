"""Tests for `openalpha_cn.logging_setup`: structured JSON-lines logging, configured only
by entry points (`cli.py::main()`, `api/app.py::create_app()`), never at import time and
never via `logging.basicConfig()` (V2-P0B-007).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from openalpha_cn.api.app import create_app
from openalpha_cn.logging_setup import PACKAGE_LOGGER_NAME, JsonLinesFormatter, configure_logging


def test_package_logger_carries_a_null_handler_merely_from_importing_the_package() -> None:
    """Library-safe by default: importing `openalpha_cn` (which every test in this suite
    does, transitively) must never let a log record from any of its modules fall through
    to Python's "handler of last resort" -- a bare line printed straight to the real
    process stderr for any logger with zero handlers anywhere in its chain -- before any
    entry point has called `configure_logging()`. Mirrors the pre-existing
    `dotenv.main` `NullHandler` precedent in `config.py`.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)

    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)


def test_openalpha_logger_starts_this_test_without_a_real_handler_attached() -> None:
    """Proves the shared `tests/conftest.py` isolation fixture actually resets state
    between tests -- otherwise whichever test in the session happens to call
    `configure_logging()` first would leak a real `StreamHandler` (writing structured
    JSON to this process's actual stderr) into every test that runs after it, and
    `uv run pytest -q`'s clean output would depend on test execution order.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)

    assert all(isinstance(handler, logging.NullHandler) for handler in logger.handlers)


def test_configure_logging_never_calls_logging_basicconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard constraint from the task brief: never `logging.basicConfig()` -- that
    configures the real root logger, which would hijack a host process's own logging
    setup when this package is embedded as a library."""
    calls: list[object] = []

    def _record(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(logging, "basicConfig", _record)

    configure_logging("INFO")

    assert calls == []


def test_configure_logging_never_touches_the_root_logger() -> None:
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_level = root.level

    configure_logging("INFO")

    assert root.handlers == before_handlers
    assert root.level == before_level


def test_configure_logging_attaches_exactly_one_real_handler() -> None:
    configure_logging("INFO")

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    real_handlers = [h for h in logger.handlers if not isinstance(h, logging.NullHandler)]
    assert len(real_handlers) == 1


def test_configure_logging_is_idempotent_within_one_process() -> None:
    """Both `cli.py::main()` and `api/app.py::create_app()` may run more than once in
    the same process (every test that builds its own `create_app()` instance does), so
    a second call -- even with a different level -- must never stack a second handler
    or silently re-pin the level."""
    configure_logging("INFO")
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    first_handlers = list(logger.handlers)

    configure_logging("DEBUG")

    assert logger.handlers == first_handlers
    assert logger.level == logging.INFO


def test_configure_logging_sets_the_requested_level() -> None:
    configure_logging("WARNING")

    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.WARNING


def test_json_lines_formatter_emits_one_parseable_json_object_per_line() -> None:
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.storage.migrations",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="migration_applied",
        args=(),
        exc_info=None,
    )
    record.migration_version = 2
    record.migration_name = "demo_add_runs_archived_at"

    line = formatter.format(record)

    payload = json.loads(line)
    assert payload["event"] == "migration_applied"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "openalpha_cn.storage.migrations"
    assert payload["migration_version"] == 2
    assert payload["migration_name"] == "demo_add_runs_archived_at"
    assert "timestamp" in payload


def test_json_lines_formatter_never_embeds_str_of_an_exception_argument() -> None:
    """Structural leak guard: even if a future call site passed `exc_info=True` -- no
    call site in this package does, see `logging_setup.py`'s module docstring -- the
    formatter itself must never fold the exception's own `str()` into the record."""
    formatter = JsonLinesFormatter()
    try:
        raise ValueError("token=sk-should-never-be-formatted-99887")
    except ValueError:
        record = logging.LogRecord(
            name="openalpha_cn.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="something_failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    line = formatter.format(record)

    assert "sk-should-never-be-formatted-99887" not in line


# --- JsonLinesFormatter allowlist: a call site is guilty until proven vetted --------------
#
# A reviewer proved the formatter had no whitelist: it iterated `record.__dict__` and
# `json.dumps(..., default=str)`-ed whatever it found, so any future call site could leak
# through three natural-looking mistakes. Each test below is one of those three exact
# experiments, reproduced against the fixed formatter -- every one must now be blocked.


def test_json_lines_formatter_drops_an_exception_object_passed_as_an_extra_field() -> None:
    """Reviewer's leak #1: `extra={"error": some_exception}` used to dump the full
    exception message via `default=str`. "error" is not on the allowlist -- an
    exception object is never a safe extra value -- so it must be dropped, not
    stringified."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="something_failed",
        args=(),
        exc_info=None,
    )
    record.error = ValueError("token=sk-leak-experiment-one-should-never-appear-11001")

    line = formatter.format(record)

    assert "sk-leak-experiment-one-should-never-appear-11001" not in line
    assert "error" not in json.loads(line)


def test_json_lines_formatter_drops_a_nested_dict_passed_as_an_extra_field() -> None:
    """Reviewer's leak #2: `extra={"config": {"tushare_token": "..."}}` used to
    serialize the nested dict verbatim. "config" is not on the allowlist, and a dict
    is never a safe scalar extra value either -- both bars must independently catch
    this."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="config_loaded",
        args=(),
        exc_info=None,
    )
    record.config = {"tushare_token": "sk-leak-experiment-two-should-never-appear-11002"}

    line = formatter.format(record)

    assert "sk-leak-experiment-two-should-never-appear-11002" not in line
    assert "config" not in json.loads(line)


def test_json_lines_formatter_drops_an_unlisted_plain_string_field() -> None:
    """Reviewer's leak #3: `extra={"url": "https://...?token=..."}` -- a plain
    string, so a type-based guard alone (reject non-scalars) would never catch it.
    Only a name allowlist blocks this: "url" is not a vetted field, so it is dropped
    regardless of its value being an otherwise-harmless-looking string."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_sent",
        args=(),
        exc_info=None,
    )
    record.url = "https://api.example.com/data?token=sk-leak-experiment-three-11003"

    line = formatter.format(record)

    assert "sk-leak-experiment-three-11003" not in line
    assert "url" not in json.loads(line)


def test_json_lines_formatter_drops_an_allowlisted_name_with_a_non_scalar_value() -> None:
    """Defense in depth: even a field name that *is* on the allowlist must still be
    dropped if some future call site passes the wrong shape for it (e.g. a dict
    instead of the `str` every current call site sends for `batch_id`). The name
    allowlist alone would wave this through -- the scalar-type check is the second,
    independent bar."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="batch_submitted",
        args=(),
        exc_info=None,
    )
    record.batch_id = {"nested": "sk-leak-experiment-four-should-never-appear-11004"}

    line = formatter.format(record)

    assert "sk-leak-experiment-four-should-never-appear-11004" not in line
    assert "batch_id" not in json.loads(line)


def test_json_lines_formatter_records_dropped_field_names_as_visible_evidence() -> None:
    """Blocking must be visible, not silent: a dropped field leaves its *name* (never
    its value) behind in the same JSON line, so a reviewer grepping structured logs
    can see that a call site attempted to log something the formatter refused, rather
    than the field just silently vanishing."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="something_failed",
        args=(),
        exc_info=None,
    )
    record.error = RuntimeError("must not appear")
    record.url = "https://example.com/?token=must-not-appear"

    payload = json.loads(formatter.format(record))

    assert sorted(payload["_dropped_fields"]) == ["error", "url"]


def test_json_lines_formatter_still_emits_every_allowlisted_field_todays_call_sites_use() -> None:
    """Positive control: the allowlist must not be so tight it silently starts
    dropping any of the fields the 10 audited, clean call sites actually pass today.
    """
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="openalpha_cn.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="probe",
        args=(),
        exc_info=None,
    )
    todays_fields = {
        "provider_id": "tushare.pro",
        "category": "authentication",
        "dataset": "limit_up",
        "batch_id": "b-1",
        "item_count": 3,
        "max_concurrency": 4,
        "retry": False,
        "status": "completed",
        "runtime_dir": "/tmp/runtime",
        "schema_version": 2,
        "backup_path": "/tmp/runtime/backups/state.sqlite3.v1.bak",
        "from_version": 1,
        "migration_version": 2,
        "migration_name": "demo_add_runs_archived_at",
    }
    for key, value in todays_fields.items():
        setattr(record, key, value)

    payload = json.loads(formatter.format(record))

    for key, value in todays_fields.items():
        assert payload[key] == value
    assert "_dropped_fields" not in payload


# --- create_app() entry-point wiring ------------------------------------------------------


def test_create_app_configures_the_package_logger_from_openalpha_log_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "DEBUG")

    create_app(runtime_dir=tmp_path)

    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.DEBUG


def test_create_app_never_calls_logging_basicconfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def _record(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(logging, "basicConfig", _record)

    create_app(runtime_dir=tmp_path)

    assert calls == []
