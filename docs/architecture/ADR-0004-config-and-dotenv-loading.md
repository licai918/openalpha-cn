# ADR-0004: Typed runtime config and in-process `.env` loading

Date: 2026-08-07
Status: Accepted

## Context

Before this decision, OpenAlpha CN had no configuration object. `OPENALPHA_*`
settings were read with 9 scattered `os.getenv`/`os.environ` calls spread across
`api/app.py` and `cli.py`, with real, confirmed defects:

- `api/app.py:243` converted `OPENALPHA_MAX_REQUEST_BYTES` with a bare
  `int(os.getenv(...))`, and `api/app.py` executes `app = create_app()` at module
  import time -- a non-numeric value crashed the process with a raw traceback the
  moment the module was imported, not a readable configuration error.
- `api/app.py:539-540` read `OPENALPHA_WEB_DIR`, but `.env.example` never declared
  it.
- `OPENALPHA_LOG_LEVEL`, `OPENALPHA_HOST`, and `OPENALPHA_PORT` were declared in
  `.env.example` but never read by any code path that actually ran: `cli.py`'s
  `serve` command hardcoded `host="127.0.0.1"`/`port=8000` as Typer option
  defaults with no environment fallback at all.
- `Dockerfile` set `ENV OPENALPHA_HOST=0.0.0.0` / `OPENALPHA_PORT=8000`, but its
  `CMD` hardcoded `--host 0.0.0.0 --port 8000` on the `uvicorn` invocation itself,
  so those two `ENV` declarations could never take effect -- the CMD silently
  overrode them.
- There was no in-process `.env` loading anywhere. The README's "方式二: Python
  源码环境" setup flow told the user to `Copy-Item .env.example .env`, then run
  `openalpha doctor` / `openalpha serve` -- but `.env` was never read outside
  Docker Compose, so `doctor` reported every credential missing regardless of
  what the user had just written into `.env`. P0.A's end-to-end acceptance review
  rated this Important. Task 3 fixed the README's wording (documenting the gap
  honestly) and left a canary test,
  `tests/unit/test_repository_assets.py::test_no_dotenv_dependency_or_usage_exists_yet`,
  that pinned "no `.env` parser exists yet" and was explicitly written to break
  the moment this task implemented one.

## Decision

### One new runtime dependency: `pydantic-settings`

Runtime dependencies were exactly 7 (`duckdb`, `fastapi`, `pydantic`, `pytz`,
`typer`, `tzdata`, `uvicorn`) -- a number this project guards deliberately. This
task adds exactly one: `pydantic-settings>=2.15,<3`, bringing the count to 8. It
was chosen over hand-rolling a config dataclass because `pydantic` is already a
dependency and `pydantic-settings` gives typed fields, coercion (`str` ->
`Path`/`int`), and `pydantic.ValidationError`'s structured per-field error
reporting in one package, which is exactly the "clear error naming the variable"
requirement below needs. Its own transitive dependency, `python-dotenv`
(confirmed in `uv.lock`), is *not* imported directly anywhere in this project's
own code -- see the next section for why -- but its presence on the dependency
tree is a direct, understood consequence of choosing `pydantic-settings`, not an
unrelated addition.

### `OpenAlphaConfig` (`openalpha_cn/config.py`)

A single typed settings object, `OpenAlphaConfig(BaseSettings)`, with
`env_prefix="OPENALPHA_"`:

| Field | Env var | Default | Replaces |
|---|---|---|---|
| `runtime_dir: Path` | `OPENALPHA_RUNTIME_DIR` | `Path("./runtime")` | `api/app.py:241` |
| `web_dir: Path \| None` | `OPENALPHA_WEB_DIR` | `None` | `api/app.py:539-540` |
| `max_request_bytes: int` | `OPENALPHA_MAX_REQUEST_BYTES` | `8 * 1024 * 1024` | `api/app.py:243` |
| `host: str` | `OPENALPHA_HOST` | `"127.0.0.1"` | `cli.py`'s `serve` default |
| `port: int` | `OPENALPHA_PORT` | `8000` | `cli.py`'s `serve` default |

Every default matches the value the code it replaces hardcoded, so a process
with no `OPENALPHA_*` variables set behaves identically to before this task.
`max_request_bytes` keeps its pre-existing "`>= 1`" validation, now as a
`field_validator` instead of a hand-written `if` in `create_app()`. `web_dir`
gets a `mode="before"` validator that treats an empty/whitespace string as
unset, preserving `api/app.py`'s old truthiness check (`Path("")` would
otherwise silently resolve to `Path(".")`, turning static file serving on with
the wrong root).

`load_config()` wraps construction and turns any `pydantic.ValidationError` into
`ConfigError`, a plain `RuntimeError` whose message always names the specific
`OPENALPHA_*` variable (e.g. `"OPENALPHA_MAX_REQUEST_BYTES: must be a positive
integer"`), never a raw pydantic traceback. This is the direct fix for the
import-time crash: `api/app.py`'s `create_app()` now raises `ConfigError` (a
readable message) instead of letting a bare `int()` `ValueError` propagate out
of module-level code.

**`OpenAlphaConfig` deliberately never parses a `.env` file itself** -- no
`env_file` is set in its `SettingsConfigDict`. It reads the real process
environment only, exactly like the `os.getenv` calls it replaces. This is a
safety-critical design choice, not an oversight: see the isolation section
below.

### `.env` loading (`load_dotenv()`, `discover_dotenv()`)

`load_dotenv(env_file: Path | None = None)` is the *only* function in this
project that parses a `.env` file. It merges `KEY=VALUE` pairs into
`os.environ`, refusing to overwrite any key already present -- so **a real,
already-exported environment variable always wins over the same name in
`.env`**, which in turn wins over a field's compiled-in default.

> **Superseded by the amendment below.** The parser was originally a small,
> hand-written regex (`config.py::_parse_dotenv_text`), deliberately not a
> direct call into `python-dotenv`'s own API, on the theory that
> `.env.example`'s format needed nothing `python-dotenv` offered beyond what
> the regex already did. That parser had two real bugs (silent inline-comment
> corruption and silently-dropped `export`-prefixed lines) found in a later
> review; `load_dotenv()` now delegates directly to `python-dotenv`. See "Amendment (2026-08-07)" at the end of this document for the full account.

`discover_dotenv()` resolves the file to load when no explicit path is given:
always `<cwd>/.env` -- the process's current working directory, never a
directory walk-up search. This matches the documented setup flow exactly
(`git clone` -> `cd` into the repo -> `Copy-Item .env.example .env` -> `uv run
openalpha doctor`, all issued from the repository root) and keeps behavior
fully predictable.

**This is a deliberately different rule from Docker Compose's own `.env`
auto-load.** Compose's automatic `.env` loading is keyed off its *project
directory*, which defaults to wherever the primary Compose file lives --
`deploy/` in this repository, since deployment always runs `docker compose -f
deploy/compose.yml ...` (confirmed by Task 3's investigation, and documented in
`docs/deployment/production.zh-CN.md`). A `.env` created at the repository root
for the CLI/SDK flow this ADR describes is therefore invisible to that Compose
invocation, and vice versa -- the two `.env` files, and the two loading
mechanisms, are unrelated and must be maintained separately. The README and
`.env.example` both call this out explicitly so a user does not create one
`.env` and wonder why only one of the two setup paths sees it.

### Test isolation is structural, not best-effort

The repository's real, gitignored root `.env` -- when present, on any
developer's machine -- must never be read by the test suite; a config loader
that silently reads it would make test outcomes depend on one developer's local
secrets ("green locally, red in CI", or worse). This is enforced by where
`load_dotenv()` is called from, not by a runtime guard:

- `cli.py::main()` -- the real console-script entry point registered as
  `openalpha` in `pyproject.toml` -- calls `load_dotenv()` once, before
  dispatching to any subcommand. This is the **only** call site with no explicit
  `env_file` argument anywhere in this project's own source.
- `create_app()` (`api/app.py`) and `OpenAlphaSDK.__init__` (`sdk.py`) call
  `load_config()` (real-environment-only) but never `load_dotenv()`. `app =
  create_app()` runs at module import time in `api/app.py`; if it triggered
  `.env` parsing, merely *importing* that module -- which pytest does for
  virtually every integration test -- would read a real repository-root `.env`
  whenever one exists. It cannot: `load_config()` never touches the filesystem
  for `.env`, only `os.environ`.
  the Typer `app` object driven directly by `typer.testing.CliRunner`
  (as every test in `tests/unit/test_cli.py` does) is never routed through
  `main()`, so exercising the CLI in tests cannot trigger `.env` loading either.

Two tests prove this holds, not just assert it by convention:
`tests/unit/test_cli.py::test_cli_runner_invocation_never_auto_loads_dotenv_unlike_the_real_entrypoint`
drives `doctor` through `CliRunner` from a `tmp_path` containing a `.env` with a
sentinel credential and confirms it never reaches `os.environ` or the report;
`tests/unit/test_cli.py::test_main_entrypoint_loads_dotenv_before_dispatching_to_doctor`
proves the positive half in a fresh subprocess (own `cwd`, own environment):
`cli.main()` really does load a `.env` sitting in its working directory. This
was additionally verified by hand with the repository's actual root `.env`
present (`uv run pytest -q`, full suite green, credential value absent from all
captured output) -- see the P0.B task report for the exact commands and output.

### Host/port and the Dockerfile

`cli.py`'s `serve` command now resolves `host`/`port` with precedence
`--host`/`--port` (Typer option, if given) > `OPENALPHA_HOST`/`OPENALPHA_PORT`
(via `OpenAlphaConfig`, including any value `.env` supplied) > the
`127.0.0.1:8000` default -- making the two previously dead `.env.example`
variables genuinely effective for the first time.

`Dockerfile`'s `CMD` no longer hardcodes `--host 0.0.0.0 --port 8000`; it now
reads the same `OPENALPHA_HOST`/`OPENALPHA_PORT` the image's own `ENV`
declarations set (`sh -c 'exec python -m uvicorn ... --host "${OPENALPHA_HOST:-0.0.0.0}" --port
"${OPENALPHA_PORT:-8000}" ...'`), so the two no longer silently undermine each
other. `deploy/compose.yml` does not change: it never set `OPENALPHA_HOST` in
the container `environment:` block (correctly -- the container must always bind
`0.0.0.0` internally for Docker's own port mapping to work; only the *host-side*
port is meant to be user-configurable, via `OPENALPHA_PORT` in the `ports:`
mapping, which was already correct), so this fix only changes behavior for
someone running the built image directly (`docker run -e OPENALPHA_PORT=... `)
outside Compose, where it previously had no effect at all.

### The three dead/undeclared variables

- `OPENALPHA_HOST` / `OPENALPHA_PORT`: no longer dead -- see above.
- `OPENALPHA_WEB_DIR`: added to `.env.example` (it was read by `api/app.py` but
  never declared).
- `OPENALPHA_LOG_LEVEL`: left declared in `.env.example`, explicitly marked
  `# Reserved for structured logging (V2-P0B-007). Not read by any code yet.`
  rather than deleted or silently wired to a no-op field -- structured logging
  is out of this task's scope by design (a separate P0.B item), and adding an
  unused `log_level` field to `OpenAlphaConfig` now would be exactly the kind of
  half-implemented surface this project's scope discipline avoids.

## Consequences

Positive:

- `openalpha doctor` / `openalpha serve` now genuinely see values that live only
  in `.env`, closing the Important P0.A finding, with real environment variables
  always taking precedence over `.env`.
- A non-numeric `OPENALPHA_MAX_REQUEST_BYTES`/`OPENALPHA_PORT` now fails with a
  named, readable `ConfigError` instead of crashing import with a bare
  traceback.
- `OPENALPHA_HOST`/`OPENALPHA_PORT`/`OPENALPHA_WEB_DIR` are no longer
  documented-but-inert; `OPENALPHA_LOG_LEVEL` is documented as reserved instead
  of silently doing nothing with no explanation.
- `.env` loading's blast radius is structurally limited to the real CLI
  entrypoint, so it can never destabilize test determinism -- verified both by
  two dedicated tests and by a manual full-suite run with a real root `.env`
  present.

Negative:

- Runtime dependency count goes from 7 to 8, a deliberate, recorded exception to
  the project's usual guard on that number. (See the amendment below: a second,
  later change brings this to 9.)
- Directly instantiating `OpenAlphaSDK` (bypassing the CLI) still does not
  auto-load `.env` by design -- a library caller is expected to already manage
  its own process environment, exactly as before this task; only the `openalpha`
  command line gained this behavior. A future task that wants SDK-level
  `.env` loading must make that an explicit, opt-in constructor argument rather
  than a default, to preserve the isolation guarantee above.

## Amendment (2026-08-07): adopt `python-dotenv` directly, drop the hand-written parser

A follow-up review of this task found two real defects in the hand-written
`_parse_dotenv_text` regex parser described above:

- **Silent value corruption on inline comments.** `KEY=value # comment` parsed
  to the value `"value # comment"` -- the trailing comment was never stripped.
  For a credential, this is silent: the token simply stops authenticating with
  no error pointing at `.env` as the cause. Reproduced directly:
  `TUSHARE_TOKEN=realtoken123 # trailing note` merged as
  `"realtoken123 # trailing note"`.
- **`export `-prefixed lines were silently dropped, not "handled correctly".**
  The regex requires the key to start at the beginning of the (stripped) line,
  so `export OPENALPHA_HOST=10.5.5.5` matched nothing and the variable was
  never set -- with no error, since a non-matching line was (by design)
  treated the same as any other malformed line. This matters concretely: this
  repository's own real root `.env` recommends `set -a; source .env; set +a`
  for the SDK-direct workflow, and a developer who instead hand-writes
  `export KEY=value` lines into `.env` itself (a common enough convention that
  the file still sources correctly under plain `sh .env`) would have every
  such variable silently vanish from `openalpha`'s in-process load, with
  nothing in the CLI's output suggesting why.

Both defects were found in a project whose parser had exactly one job and was
deliberately kept minimal to avoid a second, unaudited `.env`-parsing surface.
That tradeoff no longer holds up: `python-dotenv` was **already** a mandatory
transitive dependency of `pydantic-settings` (present in `uv.lock` regardless
of this decision), so importing it directly costs this project nothing in
dependency-tree footprint, and it already implements the exact behavior this
project wants for every one of these cases -- including the two above, plus
the ones the original hand-written parser did get right (quoted values,
`#` inside quotes, an embedded `=` in the value, CRLF line endings, duplicate
keys with last-write-wins, blank lines, and `override=False`-equivalent
real-env-wins precedence).

**Decision: adopt `python-dotenv`'s `dotenv_values()` for parsing, removing
`_parse_dotenv_text` entirely.** `python-dotenv` is now declared explicitly as
a **direct** runtime dependency in `pyproject.toml`
(`python-dotenv>=1.1,<2`) rather than left as an unstated, merely-transitive
consequence of `pydantic-settings` -- the previous "not a second direct import
surface to keep in sync with upstream" argument is exactly backwards once the
alternative is a hand-written parser with two confirmed correctness bugs.
Runtime dependency count moves from 8 to 9, a second deliberate, recorded
exception to the project's usual guard on that number.

Two integration details, both load-bearing and both covered by tests in
`tests/unit/test_config.py`:

- `dotenv_values(path, interpolate=False)` is called with interpolation
  explicitly disabled. `python-dotenv` defaults to `interpolate=True`, which
  would rewrite a `${OTHER_VAR}`-shaped substring inside a value by
  substituting another variable's value (or empty, if undefined) -- exactly
  the kind of silent value corruption this amendment exists to close, just
  for a different trigger. This project's `.env` format has never supported
  interpolation (see the original "Decision" section above), so this keeps
  that behavior unchanged.
- `python-dotenv` logs a warning (via the stdlib `logging` module, on the
  `dotenv.main` logger) for any line it cannot parse, unconditionally --
  independent of the `verbose` argument `dotenv_values()` takes. With no
  handler configured, Python's logging "handler of last resort" would print
  that warning straight to stderr. `config.py` attaches a `NullHandler` to
  `dotenv.main` and disables propagation at import time so a malformed `.env`
  line degrades exactly as before (silently skipped, never a crash) instead of
  gaining new, unannounced console noise from a dependency's internal logging.

Also fixed while touching this: `tests/unit/test_config.py`'s autouse
`_isolated_environ` fixture previously cleared only the five `OPENALPHA_*`
variable names, hardcoded in the test file. It did not clear credential
variables such as `TUSHARE_TOKEN`, so a shell that had exported one --
exactly what this repository's own root `.env` header comment recommends via
`set -a; source .env; set +a` -- leaked into every test in the file, breaking
the real-env-wins precedence assertions in three tests
(`test_load_dotenv_tolerates_an_empty_declared_value`,
`test_load_dotenv_return_value_never_contains_a_credential_value`,
`test_dotenv_example_file_itself_parses_without_error`). The fixture now
derives the full set of variable names to clear directly from
`.env.example` -- every declared name, not only the `OPENALPHA_*` ones --
so it can never again drift out of sync with what the config layer and its
tests can actually observe.

Nothing else in this ADR's "Decision" or "Consequences" sections changes:
precedence (`--flag` > real env > `.env` > default), `discover_dotenv()`'s
cwd-based resolution, and the CLI-only `load_dotenv()` call boundary are all
unchanged and were re-verified after this amendment.
