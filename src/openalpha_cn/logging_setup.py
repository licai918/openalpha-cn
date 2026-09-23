"""Structured, library-safe logging configuration (V2-P0B-007).

Two hard boundaries, mirroring `config.py`'s `.env`-loading precedent exactly:

- **Library code never configures.** Every module in this package that logs does so
  with a plain `logging.getLogger(__name__)` and nothing else -- no handler, no level,
  no `logging.basicConfig()`, ever, at import time or otherwise. `configure_logging()`
  below is called exactly once by each of this package's two real entry points,
  `cli.py::main()` and `api/app.py::create_app()` -- never by a library module, never
  as an import-time side effect. A host process embedding this package as a library
  (via `OpenAlphaSDK` or by importing `create_app()` without ever calling it) is never
  hijacked: `logging.basicConfig()` configures the *root* logger, which is why this
  module never calls it. `configure_logging()` only ever touches this package's own
  logger namespace, `PACKAGE_LOGGER_NAME` ("openalpha_cn") -- exactly the namespace
  `logging.getLogger(__name__)` resolves to for every module inside this package,
  since Python's logger names are dotted and inherit from their parent.

- **A caught exception's `str()` never becomes a log field.** This project already
  shipped one credential leak through exactly this path (Task 3: `_probe_report`
  caught only `ProviderFailure`, so a plain `Exception`'s message -- potentially
  carrying a token or URL query string -- reached a bare Python traceback on stderr).
  Logging is the same hazard, wider: every call site in this package that logs a
  caught exception passes only pre-vetted, non-secret fields (an error *type* name,
  a provider's declared `category`, a migration's `version`/`name`, ...) via `extra=`
  -- never the exception object itself, never `exc_info=True`, never an f-string that
  interpolates `str(exc)`. `JsonLinesFormatter` below reflects this discipline: even
  if a future call site broke it and passed `exc_info=True`, the formatter records
  only the exception's *type* name, never its message.

- **The formatter trusts nothing it wasn't explicitly told is safe.** Call-site
  discipline (the bullet above) is necessary but not sufficient: a reviewer proved
  that with no whitelist, `JsonLinesFormatter` would happily dump any `extra=` field
  a *future* call site passed -- `extra={"error": some_exception}` (full exception
  message via `default=str`), `extra={"config": {"tushare_token": "..."}}` (a nested
  dict serialized verbatim), or `extra={"url": "https://...?token=..."}` (a
  plausible-looking plain string) would all have leaked, even though today's 10 call
  sites are clean. `JsonLinesFormatter` now enforces two independent bars on every
  `extra=` field: its *name* must be in `_ALLOWED_EXTRA_FIELDS` (an explicit,
  call-site-by-call-site inventory -- a name-only check is required because the `url`
  case above is a perfectly ordinary `str`, so a type-only check would have missed
  it), and its *value* must be a plain scalar (`_SCALAR_EXTRA_TYPES` -- catches a
  future call site that reuses an allowed name for the wrong shape of value). A field
  that fails either bar is dropped, not stringified -- and the drop is never silent:
  the field's name (never its value) is recorded under `_dropped_fields` in the same
  JSON line, so a reviewer grepping logs sees evidence that something was refused
  instead of the field just vanishing.

Why a package-local logger instead of the root logger: attaching a permanent, inert
`NullHandler` to this package's own logger namespace (installed from
`openalpha_cn/__init__.py`, guaranteed to run for *any* import of this package -- see
that module -- by importing this module for its side effect below) is the standard
library-author pattern (Python's own `logging` HOWTO recommends it) for suppressing
the "handler of last resort" -- a bare `WARNING:...` line Python prints straight to
real stderr for any `WARNING`+ record that reaches a logger with *no* handler
anywhere in its chain, including the root logger. Without this, a `logger.warning(...)`
call made before either entry point has configured anything (e.g. `_probe_report()`
invoked through the bare `Typer` `app` object, the way `typer.testing.CliRunner`
drives it in every test in this repository -- or a `logger.error(...)` reached purely
through `OpenAlphaSDK.__init__`, with neither `cli.py` nor `api/app.py` ever
imported) would print directly to stderr, bypassing whatever stream a caller --
`CliRunner`, a subprocess harness, or a host process embedding this package as a
library -- thinks it is capturing.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

PACKAGE_LOGGER_NAME = "openalpha_cn"

__all__ = ["PACKAGE_LOGGER_NAME", "JsonLinesFormatter", "configure_logging"]

# Every attribute a bare `logging.LogRecord` carries -- computed once, from a real
# record, rather than hand-copied from the `logging` docs (which drift across Python
# versions, e.g. `taskName` added in 3.12). Used by `JsonLinesFormatter` to find the
# *extra* fields a call site added via `extra=`, without hand-maintaining a second list
# that could silently miss one and leak an internal `LogRecord` attribute instead.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
}

# Every `extra=` field name this package's 10 audited, clean call sites actually pass
# today (see `logging_setup.py`'s module docstring for the full call-site inventory).
# `JsonLinesFormatter` refuses to emit *any* other name -- a reviewer proved the old,
# whitelist-free formatter would happily dump an exception's `str()`, a nested dict
# carrying a raw credential, or a URL with a token in its query string, the moment a
# future call site passed one under a plausible-looking key such as `"error"`,
# `"config"`, or `"url"`. Adding a field here is therefore a deliberate, reviewable act
# -- exactly the point: a call site that wants to log something new must prove the
# field is safe by naming it here, not merely by looking reasonable at the call site.
#   cli.py#_probe_report            -> provider_id, category, dataset
#   runtime/batch.py#BatchResearchService
#                                    -> batch_id, item_count, max_concurrency, retry, status
#   storage/batch.py#SQLiteBatchTaskStore.recover_interrupted -> batch_id
#   runtime/composition.py#build_storage -> runtime_dir, schema_version
#   storage/migrations.py#run_migrations -> backup_path, from_version, migration_version,
#                                            migration_name
_ALLOWED_EXTRA_FIELDS = frozenset(
    {
        "provider_id",
        "category",
        "dataset",
        "batch_id",
        "item_count",
        "max_concurrency",
        "retry",
        "status",
        "runtime_dir",
        "schema_version",
        "backup_path",
        "from_version",
        "migration_version",
        "migration_name",
    }
)

# The only value shapes a JSON-lines log line should ever carry for an `extra=` field:
# plain scalars that `json.dumps` renders exactly, with no `default=str` fallback
# needed and no risk of silently unrolling an object's `__dict__`/`__str__`. Checked in
# addition to, not instead of, the name allowlist above: a name allowlist alone would
# wave through a *future* call site that reused an allowed key (e.g. `batch_id`) for a
# differently-shaped value by mistake.
_SCALAR_EXTRA_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class JsonLinesFormatter(logging.Formatter):
    """Render each log record as one JSON object per line.

    These logs are consumed by grepping/`jq`-ing a file after something has already
    gone wrong, not read live by a human -- see the task brief. Every line carries a
    UTC ISO-8601 timestamp, the level, the logger name, an `event` (the short event
    name a call site passes as its log message, e.g. `"migration_applied"`), and
    whatever structured fields that call site passed via `extra=` -- restricted to
    `_ALLOWED_EXTRA_FIELDS`, each required to hold a plain scalar (`_SCALAR_EXTRA_TYPES`).

    A field that fails either bar is dropped, never stringified and never silently
    discarded: its *name* (never its value -- the value is exactly what might be unsafe)
    is recorded under `_dropped_fields` in the same line, so the drop leaves evidence a
    reviewer can grep for instead of vanishing without a trace.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        dropped: list[str] = []
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            if key in _ALLOWED_EXTRA_FIELDS and isinstance(value, _SCALAR_EXTRA_TYPES):
                payload[key] = value
            else:
                dropped.append(key)
        if dropped:
            payload["_dropped_fields"] = sorted(dropped)
        if record.exc_info is not None:
            # Deliberately the exception's *type name only* -- never `str(exc)` (see
            # the module docstring). No call site in this package passes
            # `exc_info=True` today; this branch exists so the formatter stays safe
            # even if one did in the future, rather than relying solely on call-site
            # discipline.
            exc_type = record.exc_info[0]
            payload["exc_type"] = exc_type.__name__ if exc_type is not None else None
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def configure_logging(level: str) -> None:
    """Attach one structured handler to this package's logger namespace, idempotently.

    Never `logging.basicConfig()` and never touches the root logger -- only
    `logging.getLogger(PACKAGE_LOGGER_NAME)` (see module docstring). Safe to call more
    than once in the same process: a second call, even with a different `level`, is a
    no-op. This matters because both real entry points that call this
    (`cli.py::main()`, `api/app.py::create_app()`) can each run more than once per
    process -- every test that builds its own `create_app()` instance does -- and
    "each entry point configures once" must mean once *ever*, not once per call, or
    every such test would stack another real `StreamHandler`.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    if any(not isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLinesFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)


# Library-safe by default, from the moment this module (and therefore the package: see
# module docstring) is imported -- before either entry point has called
# `configure_logging()`. Mirrors the pre-existing `dotenv.main` `NullHandler` in
# `config.py`. A `NullHandler` never suppresses propagation (unlike `propagate =
# False`), so a test using pytest's `caplog` fixture -- which attaches its own handler
# at the root logger -- still observes every record normally; it only prevents
# Python's "handler of last resort" from ever firing for this package's loggers.
logging.getLogger(PACKAGE_LOGGER_NAME).addHandler(logging.NullHandler())
