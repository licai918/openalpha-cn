"""Tests for `openalpha_cn.config`: typed `OPENALPHA_*` settings and `.env` loading.

Every test that touches `.env` loading points `load_dotenv(env_file=...)` at an
explicit `tmp_path` file, or `monkeypatch.chdir`s into a `tmp_path` before calling
`discover_dotenv()`/`load_dotenv()` with no arguments. Neither pattern can ever read
this repository's real, gitignored root `.env` -- see
`test_load_dotenv_default_discovery_is_cwd_based_and_never_touches_the_real_repo_env`
below, which is the isolation proof itself: it deliberately runs from a `tmp_path`
that mirrors the repository's own directory *name* pattern but contains no `.env` at
all, and asserts the credential key never appears anywhere.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from openalpha_cn.config import (
    ConfigError,
    OpenAlphaConfig,
    discover_dotenv,
    load_config,
    load_dotenv,
    load_log_level,
)

_ENV_EXAMPLE_PATH = Path(__file__).resolve().parents[2] / ".env.example"
_DECLARED_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def _declared_env_var_names() -> tuple[str, ...]:
    """Every variable name `.env.example` declares -- `OPENALPHA_*` settings *and*
    every credential/provider variable (`TUSHARE_TOKEN`, `CHAINLIN_API_KEY`,
    `OPENAI_API_KEY`, ...) -- derived from the file itself rather than a second,
    hand-maintained list that can silently drift out of sync with it.
    """
    names: list[str] = []
    for raw_line in _ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DECLARED_KEY_LINE.match(line)
        if match is not None:
            names.append(match.group(1))
    return tuple(names)


_ISOLATED_ENV_VARS = _declared_env_var_names()


@pytest.fixture(autouse=True)
def _isolated_environ() -> Iterator[None]:
    """Every test starts with none of `.env.example`'s declared variables set
    (regardless of what the invoking shell happened to export), and the *entire*
    real process environment is restored byte-for-byte afterward.

    This covers every `OPENALPHA_*` setting *and* every credential variable this
    file's `.env`-parsing tests can observe (`TUSHARE_TOKEN`, `CHAINLIN_API_KEY`,
    `OPENAI_API_KEY`, ...) -- not just the five `OPENALPHA_*` names. A developer
    who follows this repository's own real root `.env` header comment
    (`set -a; source .env; set +a`) exports every one of those credential
    variables into their shell; without clearing them here, running this file's
    suite from such a shell reproducibly fails
    `test_load_dotenv_tolerates_an_empty_declared_value`,
    `test_load_dotenv_return_value_never_contains_a_credential_value`, and
    `test_dotenv_example_file_itself_parses_without_error` -- each of those tests
    writes a `tmp_path` `.env` declaring `TUSHARE_TOKEN`, and `load_dotenv()`'s
    real-env-wins precedence rule means an already-exported `TUSHARE_TOKEN`
    silently makes the file's value a no-op, so `newly_set` no longer contains it.

    `load_dotenv()` mutates `os.environ` directly (`os.environ[key] = value`), not
    through `monkeypatch.setenv` -- a plain `monkeypatch` fixture would not know to
    undo that. A full snapshot/restore here means no test in this file can ever
    leak a declared value -- including a credential key merged in by a
    `.env`-parsing test -- into any test that runs after it, in this file or any
    other.
    """
    snapshot = dict(os.environ)
    for name in _ISOLATED_ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


# --- OpenAlphaConfig / load_config: defaults, real-env overrides, clear errors -----------


def test_load_config_defaults_match_the_pre_existing_hardcoded_values() -> None:
    """`max_request_bytes` is 32 MiB and was 8 MiB until `V2-P4-043`.

    Raised because 8 MiB was below this service's own declared ceilings: a batch at exactly
    `MAX_BATCH_ITEMS` is 9,840,054 bytes and was refused `413`, so the item ceiling `V2-P4-019`
    set on purpose could not be reached through the route that declares it. See that field's
    docstring in `config.py` for the three measurements.
    """
    config = load_config()

    assert config.runtime_dir == Path("./runtime")
    assert config.web_dir is None
    assert config.max_request_bytes == 32 * 1024 * 1024
    assert config.host == "127.0.0.1"
    assert config.port == 8000


def test_load_config_reads_real_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALPHA_RUNTIME_DIR", "/custom/runtime")
    monkeypatch.setenv("OPENALPHA_WEB_DIR", "/custom/web")
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "1024")
    monkeypatch.setenv("OPENALPHA_HOST", "0.0.0.0")
    monkeypatch.setenv("OPENALPHA_PORT", "9100")

    config = load_config()

    assert config.runtime_dir == Path("/custom/runtime")
    assert config.web_dir == Path("/custom/web")
    assert config.max_request_bytes == 1024
    assert config.host == "0.0.0.0"
    assert config.port == 9100


def test_load_config_treats_a_blank_web_dir_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins the pre-existing `api/app.py` behavior: an empty `OPENALPHA_WEB_DIR`
    must not silently enable static file serving from `Path(".")`."""
    monkeypatch.setenv("OPENALPHA_WEB_DIR", "")

    assert load_config().web_dir is None


def test_load_config_rejects_a_non_numeric_max_request_bytes_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the import-time crash: `int(os.getenv(...))` used to
    raise a bare `ValueError` traceback when `api/app.py` executed `app =
    create_app()` at module scope. `load_config()` must instead raise `ConfigError`
    naming the offending variable."""
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    with pytest.raises(ConfigError) as excinfo:
        load_config()

    assert "OPENALPHA_MAX_REQUEST_BYTES" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, Exception)


def test_load_config_rejects_a_non_positive_max_request_bytes_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "0")

    with pytest.raises(ConfigError) as excinfo:
        load_config()

    assert "OPENALPHA_MAX_REQUEST_BYTES" in str(excinfo.value)


def test_load_config_error_never_includes_a_raw_pydantic_traceback_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ConfigError` must be the caller-facing exception type, not a leaked
    `pydantic_core.ValidationError` (whose message includes pydantic's own
    "For further information visit https://errors.pydantic.dev" footer)."""
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    with pytest.raises(ConfigError) as excinfo:
        load_config()

    assert "errors.pydantic.dev" not in str(excinfo.value)


# --- OpenAlphaConfig.log_level: V2-P0B-007 structured logging -----------------------------


def test_load_config_default_log_level_is_info() -> None:
    assert load_config().log_level == "INFO"


def test_load_config_reads_log_level_from_the_real_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "DEBUG")

    assert load_config().log_level == "DEBUG"


def test_load_config_normalizes_a_lowercase_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "warning")

    assert load_config().log_level == "WARNING"


def test_load_config_rejects_an_unknown_log_level_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigError) as excinfo:
        load_config()

    assert "OPENALPHA_LOG_LEVEL" in str(excinfo.value)


# --- load_log_level: resolves OPENALPHA_LOG_LEVEL alone, independent of every other field -


def test_load_log_level_default_is_info() -> None:
    assert load_log_level() == "INFO"


def test_load_log_level_reads_the_real_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "DEBUG")

    assert load_log_level() == "DEBUG"


def test_load_log_level_normalizes_a_lowercase_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "warning")

    assert load_log_level() == "WARNING"


def test_load_log_level_rejects_an_unknown_value_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALPHA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigError) as excinfo:
        load_log_level()

    assert "OPENALPHA_LOG_LEVEL" in str(excinfo.value)


def test_load_log_level_ignores_an_unrelated_invalid_openalpha_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason this function exists at all (Finding 2, `cli.py::main()`): `main()`
    needs a validated log level before it dispatches to any subcommand, and that is
    the *only* config value it genuinely needs at that point. `load_config()` builds
    the whole `OpenAlphaConfig` object atomically, so an unrelated invalid field (a
    non-numeric `OPENALPHA_MAX_REQUEST_BYTES`, here) would abort logging setup -- and
    therefore dispatch to every command, including ones like `doctor`/`version` that
    have nothing to do with request-body limits. `load_log_level()` must never be
    affected by any field but its own.
    """
    monkeypatch.setenv("OPENALPHA_MAX_REQUEST_BYTES", "not-a-number")

    assert load_log_level() == "INFO"


# --- discover_dotenv: predictable, cwd-based, never a walk-up search ---------------------


def test_discover_dotenv_resolves_to_dot_env_under_the_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert discover_dotenv() == tmp_path / ".env"


def test_discover_dotenv_does_not_walk_up_to_a_parent_directorys_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("OPENALPHA_HOST=parent-should-not-be-found\n", encoding="utf-8")
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)

    resolved = discover_dotenv()

    assert resolved == child / ".env"
    assert not resolved.exists()


# --- load_dotenv: precedence, parsing, and never-leak-values discipline ------------------


def test_load_dotenv_merges_a_value_present_only_in_the_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENALPHA_PORT=9001\n", encoding="utf-8")

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ("OPENALPHA_PORT",)
    assert load_config().port == 9001


def test_load_dotenv_never_overrides_a_real_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence rule this task exists to fix: a real, already-exported
    environment variable must win over the same key in `.env`."""
    monkeypatch.setenv("OPENALPHA_HOST", "10.0.0.5")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENALPHA_HOST=9.9.9.9\n", encoding="utf-8")

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ()
    assert load_config().host == "10.0.0.5"


def test_load_dotenv_leaves_defaults_alone_when_neither_env_nor_file_set_a_key(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENALPHA_PORT=9001\n", encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert load_config().host == "127.0.0.1"


def test_load_dotenv_is_a_noop_for_a_missing_file(tmp_path: Path) -> None:
    assert load_dotenv(env_file=tmp_path / "does-not-exist.env") == ()


def test_load_dotenv_ignores_comments_and_blank_lines_and_strips_quotes(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n"
        "# a full-line comment\n"
        'OPENALPHA_HOST="10.20.30.40"\n'
        "\n"
        "OPENALPHA_WEB_DIR='/quoted/single'\n",
        encoding="utf-8",
    )

    load_dotenv(env_file=env_file)

    config = load_config()
    assert config.host == "10.20.30.40"
    assert config.web_dir == Path("/quoted/single")


def test_load_dotenv_ignores_a_line_with_no_key_value_shape(tmp_path: Path) -> None:
    """A malformed line (no `=`, or a key that is not a valid identifier) is skipped
    rather than raising -- `.env` parsing must degrade gracefully, not crash the CLI
    on a stray line a user typed by hand."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "this line has no equals sign at all\nOPENALPHA_PORT=9002\n",
        encoding="utf-8",
    )

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ("OPENALPHA_PORT",)


def test_load_dotenv_strips_a_trailing_inline_comment_from_the_value(tmp_path: Path) -> None:
    """`KEY=value # comment` must yield `"value"`, not `"value # comment"`. For a
    credential, the corrupted form is silent: the token simply stops working with
    no error pointing at the cause."""
    env_file = tmp_path / ".env"
    env_file.write_text("TUSHARE_TOKEN=realtoken123 # trailing note\n", encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert os.environ["TUSHARE_TOKEN"] == "realtoken123"


def test_load_dotenv_preserves_a_hash_inside_a_quoted_value(tmp_path: Path) -> None:
    """A `#` inside quotes is data, not the start of a comment."""
    env_file = tmp_path / ".env"
    env_file.write_text('TUSHARE_TOKEN="token#with#hashes"\n', encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert os.environ["TUSHARE_TOKEN"] == "token#with#hashes"


def test_load_dotenv_preserves_an_embedded_equals_sign_in_the_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOM_MODEL_BASE_URL=https://x.example/v1?a=1&b=2\n", encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert os.environ["CUSTOM_MODEL_BASE_URL"] == "https://x.example/v1?a=1&b=2"


def test_load_dotenv_handles_crlf_line_endings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"OPENALPHA_HOST=10.1.2.3\r\nOPENALPHA_PORT=9003\r\n")

    newly_set = load_dotenv(env_file=env_file)

    assert set(newly_set) == {"OPENALPHA_HOST", "OPENALPHA_PORT"}
    assert load_config().host == "10.1.2.3"
    assert load_config().port == 9003


def test_load_dotenv_strips_a_leading_export_keyword(tmp_path: Path) -> None:
    """`set -a; source .env; set +a` (this repository's own real `.env` header
    comment) works regardless of an `export ` prefix; the loader must accept a
    file written the same way."""
    env_file = tmp_path / ".env"
    env_file.write_text("export OPENALPHA_HOST=10.5.5.5\n", encoding="utf-8")

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ("OPENALPHA_HOST",)
    assert load_config().host == "10.5.5.5"


def test_load_dotenv_last_duplicate_key_in_the_file_wins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENALPHA_PORT=1111\nOPENALPHA_PORT=2222\n", encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert load_config().port == 2222


def test_load_dotenv_does_not_interpolate_a_dollar_brace_reference_in_the_value(
    tmp_path: Path,
) -> None:
    """A credential value that happens to contain `${...}` must survive verbatim --
    this project's `.env` format has never supported variable interpolation (see
    `.env.example`'s format), and a parser that silently substitutes a reference
    would be exactly the kind of value corruption this task exists to close."""
    env_file = tmp_path / ".env"
    env_file.write_text("TUSHARE_TOKEN=abc${OPENALPHA_PORT}def\n", encoding="utf-8")

    load_dotenv(env_file=env_file)

    assert os.environ["TUSHARE_TOKEN"] == "abc${OPENALPHA_PORT}def"


def test_load_dotenv_tolerates_an_empty_declared_value(tmp_path: Path) -> None:
    """`.env.example` declares several credential variables with no value
    (`TUSHARE_TOKEN=`) for the user to fill in; parsing that line must not raise."""
    env_file = tmp_path / ".env"
    env_file.write_text("TUSHARE_TOKEN=\n", encoding="utf-8")

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ("TUSHARE_TOKEN",)


def test_load_dotenv_return_value_never_contains_a_credential_value(tmp_path: Path) -> None:
    secret = "sk-config-loader-leak-check-should-never-appear-4471"
    env_file = tmp_path / ".env"
    env_file.write_text(f"TUSHARE_TOKEN={secret}\n", encoding="utf-8")

    newly_set = load_dotenv(env_file=env_file)

    assert newly_set == ("TUSHARE_TOKEN",)
    assert secret not in newly_set
    assert secret not in repr(newly_set)


def test_load_dotenv_default_discovery_is_cwd_based_and_never_touches_the_real_repo_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hard isolation constraint, exercised directly: chdir into a fresh
    `tmp_path` containing no `.env` at all, call `load_dotenv()` with its default
    (discovery-based) argument, and confirm nothing was merged -- proving the
    default path is genuinely cwd-scoped rather than climbing to a real ancestor
    `.env` (this repository's real root `.env`, when present, is several directory
    levels above pytest's `tmp_path`)."""
    monkeypatch.chdir(tmp_path)

    newly_set = load_dotenv()

    assert newly_set == ()
    assert load_config() == OpenAlphaConfig()


def test_dotenv_example_file_itself_parses_without_error() -> None:
    """Round-trip proof against the real, checked-in `.env.example`: every line in
    the file this project ships must parse cleanly (comments, section headers,
    blank lines, and empty `NAME=` declarations alike)."""
    repo_root = Path(__file__).resolve().parents[2]
    env_example = repo_root / ".env.example"

    newly_set = load_dotenv(env_file=env_example)

    assert "OPENALPHA_RUNTIME_DIR" in newly_set
    assert "OPENALPHA_WEB_DIR" in newly_set
    assert "TUSHARE_TOKEN" in newly_set
