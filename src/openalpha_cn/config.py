"""Centralized, typed runtime configuration and predictable `.env` loading.

Two independent pieces live here, deliberately kept apart:

- `OpenAlphaConfig` / `load_config()` -- typed `OPENALPHA_*` settings (runtime
  directory, web asset directory, max request body size, serve host/port),
  read from the **real process environment only**. Building one never
  touches the filesystem for a `.env` file, so constructing it anywhere --
  including `api/app.py`'s module-scope `app = create_app()`, which runs on
  every import of that module -- can never read a developer's real `.env`.
- `load_dotenv()` -- the *only* function in this package that parses a
  `.env` file and merges it into `os.environ`, real environment variables
  always winning. It is invoked exactly once, by `cli.main()` (the real
  console-script entry point registered as `openalpha` in `pyproject.toml`)
  -- never by module import, `create_app()`, or `OpenAlphaSDK.__init__` -- so
  importing this package, or driving the Typer `app` object directly the way
  `typer.testing.CliRunner` does in tests, never touches a real `.env` as a
  side effect.

Precedence, for every `OPENALPHA_*` setting and every provider/model
credential env var alike (`TUSHARE_TOKEN`, `CHAINLIN_API_KEY`, ...): an
already-exported real environment variable always wins over the same key in
`.env`, which in turn wins over a field's compiled-in default.
`load_dotenv()` implements the first half by refusing to overwrite a key
already present in `os.environ`; `OpenAlphaConfig` (and the scattered
`os.getenv`/`os.environ` reads in `providers/`, `models/`, and `cli.py` that
this module deliberately leaves untouched -- see the module docstring's
"credential env vars" note in the P0.B task report) implements the second
half simply by reading `os.environ` *after* `load_dotenv()` has already run.

`.env` discovery here (`discover_dotenv()`) is deliberately **not** the same
rule Docker Compose uses. Compose's automatic `.env` loading is keyed off the
Compose *project directory*, which defaults to wherever its primary
`docker-compose.yml`/`compose.yml` lives -- `deploy/` in this repository,
since deployment always runs `docker compose -f deploy/compose.yml ...`. A
`.env` created at the repository root (as the README's Python-source setup
instructs) is therefore invisible to that Compose invocation, and a `.env`
placed under `deploy/` is invisible to this loader. See
`docs/deployment/production.zh-CN.md` for the Compose side of that split.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `python-dotenv` logs a warning (via the stdlib `logging` module, on the
# `dotenv.main` logger) for any line it cannot parse -- unconditionally, not
# gated behind the `verbose` argument `dotenv_values()` takes. With no handler
# configured, Python's logging "handler of last resort" prints that warning
# straight to stderr. This project's own discipline is that a `.env` line, valid
# or malformed, never produces unannounced output on a stream a caller might be
# capturing (see the leak-check tests in `tests/unit/test_cli.py`), so this
# module owns silencing that default once, rather than letting a transitive
# dependency's logging configuration leak into this project's own CLI output.
logging.getLogger("dotenv.main").addHandler(logging.NullHandler())
logging.getLogger("dotenv.main").propagate = False

__all__ = [
    "ConfigError",
    "OpenAlphaConfig",
    "discover_dotenv",
    "load_config",
    "load_dotenv",
    "load_log_level",
]


_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_DEFAULT_LOG_LEVEL = "INFO"


class ConfigError(RuntimeError):
    """Invalid `OPENALPHA_*` environment configuration.

    Always names the offending `OPENALPHA_*` variable and pydantic's own
    validation message; never a raw `pydantic.ValidationError` traceback.
    Before this existed, a non-numeric `OPENALPHA_MAX_REQUEST_BYTES` raised
    exactly that bare traceback at *import* time, because `api/app.py`
    executes `app = create_app()` at module scope.
    """


class OpenAlphaConfig(BaseSettings):
    """Typed `OPENALPHA_*` runtime settings, read from the real process
    environment only -- never from a `.env` file directly (see module
    docstring for why that split exists). Every field default matches the
    value `api/app.py` and `cli.py` hardcoded before this module existed, so
    a process with no `OPENALPHA_*` variables set at all behaves exactly as
    before.
    """

    model_config = SettingsConfigDict(env_prefix="OPENALPHA_", extra="ignore")

    runtime_dir: Path = Path("./runtime")
    web_dir: Path | None = None
    max_request_bytes: int = 32 * 1024 * 1024
    """Largest declared request body this service accepts. Raised from 8 MiB by `V2-P4-043`.

    8 MiB was smaller than the ceilings this same service declares elsewhere, which made two of
    its own limits contradict each other. Measured on this repository:

        POST /api/v1/research/batches at MAX_BATCH_ITEMS (10,000)   9,840,054 bytes -> 413
        POST /api/v1/screen           at MAX_BATCH_ITEMS names     14,770,051 bytes -> 413
        POST /api/v1/screen           at the measured market (5,545) 8,190,016 bytes -> 200

    The first line is the one that decided this. `MAX_BATCH_ITEMS` is 10,000 *deliberately* --
    `V2-P4-019` raised it because "the market is a moving number" -- and
    `tests/integration/test_batch_whole_market_scale.py` proves the durable contract holds a
    batch that size. It was nonetheless **unreachable through the only surface that can express
    it**: every test at that scale built the task in process rather than posting it, so nothing
    noticed that the transport refused what the contract advertised. The third line shows how
    little headroom the market had left -- 198,592 bytes, about 134 more listings.

    32 MiB rather than a number fitted to those two measurements: both are taken with a *single*
    evidence snapshot per request, and a real caller sends more, so a ceiling at 15 MiB would be
    this same defect on a delay. It is still a bound, and deliberately: the check is made against
    `Content-Length` before any body is read, so this is also the largest single allocation one
    request can commit this process to. `tests/integration/test_request_body_ceiling.py` asserts
    the default clears both declared ceilings with a factor of two and that a body one byte over
    a configured ceiling is still refused.
    """
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = _DEFAULT_LOG_LEVEL

    @field_validator("log_level", mode="before")
    @classmethod
    def _log_level_must_be_a_known_name(cls, value: object) -> object:
        """Accept only the stdlib `logging` level names, case-insensitively.

        Normalizes to uppercase so `logging_setup.configure_logging()` can hand the
        result straight to `Logger.setLevel()` without re-validating it. An unknown
        name (a typo, or a numeric level string `logging` would also accept) is
        rejected here with a named error instead, matching every other field in this
        class -- see `_max_request_bytes_must_be_positive`.
        """
        if not isinstance(value, str):
            return value
        candidate = value.strip().upper()
        if candidate not in _VALID_LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(_VALID_LOG_LEVELS)}")
        return candidate

    @field_validator("web_dir", mode="before")
    @classmethod
    def _blank_web_dir_means_unset(cls, value: object) -> object:
        """An empty/whitespace-only `OPENALPHA_WEB_DIR` means "not configured".

        Matches the pre-existing `api/app.py` behavior, which only treated
        `OPENALPHA_WEB_DIR` as set when `os.getenv(...)` returned a truthy
        string. Without this, pydantic would coerce `""` to `Path(".")` --
        a real, existing directory -- silently turning static file serving
        on with the wrong root.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("max_request_bytes")
    @classmethod
    def _max_request_bytes_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be a positive integer")
        return value


def _format_validation_error(error: ValidationError) -> str:
    """Render a `pydantic.ValidationError` as one line per `OPENALPHA_*` variable."""
    parts: list[str] = []
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "?"
        env_var = f"OPENALPHA_{field.upper()}"
        parts.append(f"{env_var}: {item['msg']}")
    return "invalid OpenAlpha CN environment configuration -- " + "; ".join(parts)


def load_config() -> OpenAlphaConfig:
    """Build `OpenAlphaConfig` from the real process environment.

    Raises `ConfigError` -- never a raw `pydantic.ValidationError` -- naming
    the specific `OPENALPHA_*` variable that failed validation, e.g. a
    non-numeric `OPENALPHA_MAX_REQUEST_BYTES` or a negative one.
    """
    try:
        return OpenAlphaConfig()
    except ValidationError as error:
        raise ConfigError(_format_validation_error(error)) from error


def load_log_level() -> str:
    """Resolve `OPENALPHA_LOG_LEVEL` alone, from the real process environment --
    independent of every other `OPENALPHA_*` field's validity.

    `cli.py::main()` needs a validated log level before it dispatches to any
    subcommand (see `logging_setup.configure_logging()`), but that is the *only*
    config value it genuinely needs at that point. `load_config()` builds the whole
    `OpenAlphaConfig` object atomically -- pydantic-settings validates every field
    together -- so calling it here would mean an unrelated invalid field (e.g. a
    non-numeric `OPENALPHA_MAX_REQUEST_BYTES`) aborts logging setup, and therefore
    dispatch to *every* command, including `doctor` (whose entire job is diagnosing
    exactly this kind of broken environment) and `version` (which has no
    relationship to config at all). This was a real regression, found by review: see
    the P0.B task report for Finding 2.

    Applies the identical validation `OpenAlphaConfig.log_level`'s field validator
    does (case-insensitive, restricted to `_VALID_LOG_LEVELS`) and raises the same
    named `ConfigError` naming `OPENALPHA_LOG_LEVEL` on an unknown value -- callers
    that need the rest of the config still call `load_config()` themselves and get
    the same error at the point they need it (see `cli.py::serve`, `doctor`).
    """
    raw = os.environ.get("OPENALPHA_LOG_LEVEL", _DEFAULT_LOG_LEVEL)
    candidate = raw.strip().upper()
    if candidate not in _VALID_LOG_LEVELS:
        raise ConfigError(
            "invalid OpenAlpha CN environment configuration -- "
            f"OPENALPHA_LOG_LEVEL: must be one of {', '.join(_VALID_LOG_LEVELS)}"
        )
    return candidate


def discover_dotenv() -> Path:
    """Return the `.env` path the real CLI looks for: `<cwd>/.env`.

    Always the process's current working directory joined with `.env` --
    never a directory walk-up search. This matches the documented setup flow
    (`git clone` -> `cd` into the repo -> `Copy-Item .env.example .env` ->
    `uv run openalpha doctor`, all issued from the repository root) and
    keeps behavior fully predictable: invoking the CLI from a different
    directory reads `.env` there, or nothing, never a file found by silently
    climbing the tree. Tests control this purely via `monkeypatch.chdir`,
    never by passing a path around -- see `tests/unit/test_config.py`.
    """
    return Path.cwd() / ".env"


def load_dotenv(env_file: Path | None = None) -> tuple[str, ...]:
    """Merge a `.env` file into `os.environ`; return the *names* of keys newly set.

    Real environment variables already present always win: a key already in
    `os.environ` is never overwritten. Never returns or logs *values* --
    only key names -- so a credential value can never reach a caller that
    prints this function's result, matching this project's existing
    "credential values never reach stdout/stderr/logs/exceptions" discipline
    (see `tests/unit/test_cli.py`'s leak-check tests).

    `env_file`, when omitted, resolves via `discover_dotenv()` (the
    process's real current working directory). **Tests must always pass an
    explicit `env_file`** pointing at a `tmp_path` -- never rely on the
    default -- so the test suite can never read a developer's real
    repository-root `.env`; see `tests/unit/test_config.py` and the
    isolation tests in `tests/unit/test_cli.py`.

    A missing file is a no-op, not an error: `.env` is documented as an
    optional convenience (`.env.example` -> `.env`), not a requirement --
    every variable it declares can also be exported directly.

    Parsing is delegated to `python-dotenv`'s `dotenv_values()` (see
    `docs/architecture/ADR-0004-config-and-dotenv-loading.md`'s amendment for
    why this replaced a hand-written regex parser: the hand-written version
    silently corrupted a value with a trailing inline comment, e.g.
    `KEY=value # comment` -> `"value # comment"`). `interpolate=False` keeps
    this project's original, deliberate "no variable interpolation" behavior
    -- a credential value that happens to contain a literal `${...}` must
    survive verbatim, not be silently rewritten.
    """
    path = env_file if env_file is not None else discover_dotenv()
    if not path.is_file():
        return ()
    newly_set: list[str] = []
    for key, value in dotenv_values(path, interpolate=False).items():
        if value is None:
            # A bare `KEY` line with no `=` at all -- python-dotenv still
            # reports the name, with no value to merge. Skip it exactly like
            # any other line with no key=value shape.
            continue
        if key not in os.environ:
            os.environ[key] = value
            newly_set.append(key)
    return tuple(newly_set)
