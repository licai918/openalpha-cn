import ast
import hashlib
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_verify_publication() -> ModuleType:
    """Import `scripts/verify_publication.py` by path (it has no package `__init__.py`,
    so it can't be imported as `scripts.verify_publication`)."""
    spec = importlib.util.spec_from_file_location(
        "verify_publication", ROOT / "scripts" / "verify_publication.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_job_block(workflow: str, job_name: str) -> str:
    """Return the body of a top-level `jobs.<job_name>` block.

    This is a deliberately narrow, structural extraction (not a literal
    substring match against the whole file) so that assertions built on top
    of it survive unrelated edits to the workflow and only break when the
    named job's own steps change.
    """
    match = re.search(
        rf"\n  {re.escape(job_name)}:\n(.*?)(?=\n  \w[\w-]*:\n|\Z)",
        workflow,
        re.DOTALL,
    )
    assert match is not None, f"quality.yml has no top-level job named {job_name!r}"
    return match.group(1)


_RUN_LINE = re.compile(r"^(?P<indent>[ \t]*)run:[ \t]*(?P<rest>.*)$")
_BLOCK_SCALAR = re.compile(r"^[|>][+\-0-9]*$")


def _iter_step_run_commands(job_block: str) -> list[str]:
    """Yield every step `run:` command in `job_block`, in step order.

    Handles both single-line `run: <command>` steps and YAML block-scalar
    steps (`run: |` / `run: >`, with the actual command on subsequent, more
    indented lines) so converting a step to block-scalar style doesn't
    produce a false negative for callers matching on keyword.
    """
    lines = job_block.splitlines()
    commands: list[str] = []
    index = 0
    while index < len(lines):
        match = _RUN_LINE.match(lines[index])
        if match is None:
            index += 1
            continue
        rest = match.group("rest").strip()
        indent = len(match.group("indent"))
        if _BLOCK_SCALAR.match(rest):
            body_lines: list[str] = []
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip() == "":
                    index += 1
                    continue
                line_indent = len(line) - len(line.lstrip(" \t"))
                if line_indent <= indent:
                    break
                body_lines.append(line.strip())
                index += 1
            commands.append(" ".join(body_lines))
            continue
        commands.append(rest)
        index += 1
    return commands


def _step_run_command(job_block: str, keyword: str) -> str | None:
    """Return the first step `run:` command in `job_block` containing `keyword`.

    This only locates a *candidate* command by substring; it says nothing
    about whether the command actually executes what it claims to. Callers
    that need that guarantee must additionally check the returned command
    with `_is_live_pytest_coverage_gate` / `_is_live_type_check` (or an
    equivalent shape check) before trusting it.
    """
    for command in _iter_step_run_commands(job_block):
        if keyword in command:
            return command
    return None


def _executed_program(command: str) -> str | None:
    """Return the program a shell `run:` command actually executes.

    Tokenizes with `shlex` (so a command hidden inside a quoted string
    argument, e.g. `echo "would run: pytest ..."`, resolves to `echo`, not
    `pytest` -- the quoted text is one token, not a sequence of executed
    words) and strips a leading `uv run` wrapper so `uv run pytest ...`
    resolves to `pytest`.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    if tokens[0] == "uv" and len(tokens) > 1 and tokens[1] == "run":
        tokens = tokens[2:]
    return tokens[0] if tokens else None


def _is_live_pytest_coverage_gate(command: str) -> bool:
    """True iff `command` genuinely executes pytest with the coverage gate live.

    Guards against two reviewer-demonstrated bypasses that keep every
    substring a naive check looks for (`pytest`, `--cov`,
    `--cov-fail-under=80`) while never running a single test body:

    - Wrapping the real command inside `echo "..."` -- a pure no-op that
      only prints the string; `_executed_program` resolves this to `echo`.
    - Adding `--collect-only`, which makes pytest gather tests without
      running them. This is worse than a no-op: pytest-cov silently
      suppresses the `--cov-fail-under` exit code under `--collect-only`, so
      this mutation exits 0 in real CI too, not only in a substring-based
      test.
    """
    if _executed_program(command) != "pytest":
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return "--collect-only" not in tokens


def _is_live_type_check(command: str) -> bool:
    """True iff `command` genuinely executes mypy as the run command.

    Same discipline as `_is_live_pytest_coverage_gate`: a step whose `run:`
    line merely mentions `mypy` inside an `echo` string, or otherwise never
    hands control to mypy, must not read as a live type-check step.
    """
    return _executed_program(command) == "mypy"


def _try_brace_block(text: str, key: str) -> str | None:
    """Return the contents of a `<key>: { ... }` block via balanced-brace
    matching, or `None` if no such block is present.

    Balanced-brace matching (rather than a greedy/non-greedy regex spanning
    to the "next" brace) so extraction is correct regardless of how the
    object literal is formatted or nested -- and so a deleted block is
    reported as absent instead of silently matching some unrelated block.
    """
    marker = re.search(rf"\b{re.escape(key)}:\s*\{{", text)
    if marker is None:
        return None
    start = marker.end()
    depth = 1
    index = start
    while depth > 0:
        if index >= len(text):
            return None
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    return text[start : index - 1]


def _vite_coverage_thresholds(vite_config: str) -> dict[str, int] | None:
    """Return the frontend coverage gate's per-metric thresholds, or `None`.

    `None` means the coverage gate is not actually live: either the
    `coverage` block is missing entirely (bare `vitest run`, nothing
    enforced), `enabled: true` is missing, or `thresholds` (or one of its
    four metrics) is missing.
    """
    test_block = _try_brace_block(vite_config, "test")
    if test_block is None:
        return None
    coverage_block = _try_brace_block(test_block, "coverage")
    if coverage_block is None:
        return None
    if re.search(r"\benabled\s*:\s*true\b", coverage_block) is None:
        return None
    thresholds_block = _try_brace_block(coverage_block, "thresholds")
    if thresholds_block is None:
        return None

    thresholds: dict[str, int] = {}
    for metric in ("statements", "branches", "functions", "lines"):
        match = re.search(rf"\b{metric}\s*:\s*(\d+)", thresholds_block)
        if match is None:
            return None
        thresholds[metric] = int(match.group(1))
    return thresholds


def test_readme_prioritizes_chainlin_and_explains_installation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## ✨ 核心特性" in readme
    assert "### 🇨🇳 A 股原生证据体系" in readme
    assert "### 🤖 可验证的多智能体决策" in readme
    assert "### 🔁 同路径回放与归因" in readme
    assert "### 🔌 开放的数据与使用接口" in readme
    assert "### 🛡️ 可复现的工程底座" in readme
    assert "## 核心独特优势" in readme
    assert "更适合 A 股涨停量化分析" in readme
    assert "强在角色编排与社区影响力" not in readme
    assert "## 两种使用方式" not in readme
    assert "### 1. 自托管 OpenAlpha CN" not in readme
    assert "## 不想本地部署" in readme
    assert "## 安装说明" in readme
    assert "### 安装链邻涨停复盘策略软件" in readme
    assert "### 安装 OpenAlpha CN" in readme
    assert "链邻涨停复盘策略软件" in readme
    assert "chainlin-desktop-v1.0.9" in readme
    assert "当前版本" not in readme
    assert "assets/brand/wechat-contact-qr.jpg" in readme

    brain_end = readme.index("openalpha-brain-05-replay-interfaces.svg")
    ready_to_use = readme.index("## 不想本地部署")
    installation = readme.index("## 安装说明")
    data_advantage = readme.index("## 数据优势如何体现")
    assert brain_end < ready_to_use < installation < data_advantage


def test_readme_python_source_setup_documents_automatic_dotenv_loading_and_precedence() -> None:
    """V2-P0B-006 implements in-process `.env` loading for the CLI, which flips the
    premise the old `test_readme_python_source_setup_does_not_imply_env_is_auto_loaded`
    pinned: back then the Python-source flow genuinely never auto-loaded `.env`, so
    that test asserted the README said so plainly. It now does auto-load `.env`
    (see `openalpha_cn/config.py::load_dotenv`, invoked once by `cli.py::main`), so
    this asserts the corrected, positive claim instead -- plus the precedence rule
    (a real exported variable always wins over the same name in `.env`) and the
    Docker-Compose-is-a-different-`.env`-entirely caveat a user actually needs.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    setup_start = readme.index("方式二")
    setup_end = readme.index("## 数据优势如何体现")
    assert setup_start < setup_end
    section = readme[setup_start:setup_end]

    # The corrected, positive claim -- and the old "not implemented yet" wording
    # this replaces must not linger.
    assert "自动加载" in section or "自动读取" in section
    assert "已列入后续计划" not in section

    # Real env vars still win over `.env` -- the precedence this task exists to fix.
    assert "优先" in section
    assert "$env:" in section or "export " in section

    # Docker Compose's own `.env` auto-load is a different directory/mechanism.
    assert "deploy/" in section
    assert "Compose" in section or "compose" in section

    # Host/port are genuinely configurable now, not the dead variables they were.
    assert "OPENALPHA_HOST" in section
    assert "OPENALPHA_PORT" in section

    assert "docs/data/providers.zh-CN.md" in section

    # The copy-to-.env step must come strictly before `doctor`/`serve`.
    copy_step = section.index(".env")
    doctor_step = section.index("openalpha doctor")
    serve_step = section.index("openalpha serve")
    assert copy_step < doctor_step < serve_step


def test_dotenv_loading_is_a_single_deliberate_module_not_scattered_parsing() -> None:
    """Supersedes `test_no_dotenv_dependency_or_usage_exists_yet`, which used to pin
    the opposite premise: that no `.env` parser existed anywhere in `src/` (in-process
    `.env` loading was deliberately deferred to V2-P0B-006). It is implemented now;
    this pins the shape of that implementation instead of only its absence:

    - `pydantic-settings` and `python-dotenv` are both direct runtime dependencies
      (recorded with their rationale in ADR-0004 and its amendment, not merely a
      version bump);
    - both are imported in exactly one module, `openalpha_cn/config.py` -- `.env`
      parsing is not scattered across the tree the way the 9 original `os.getenv`/
      `os.environ` call sites this task replaced were.

    A hand-written regex parser lived here first (see ADR-0004's original
    "Decision" section) and was replaced by a direct `python-dotenv` import once a
    code review found it silently corrupted a value with a trailing inline comment
    (`KEY=value # comment` -> `"value # comment"`) -- ADR-0004's amendment records
    that switch and why. `python-dotenv` was already a mandatory transitive
    dependency of `pydantic-settings` (confirmed in `uv.lock`) even before this, so
    this task declares it explicitly in `pyproject.toml` rather than continuing to
    rely on it staying present only as someone else's transitive pin.
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pydantic-settings" in pyproject
    assert "python-dotenv" in pyproject

    src_root = ROOT / "src"
    pydantic_settings_importers = sorted(
        path.relative_to(ROOT).as_posix()
        for path in src_root.rglob("*.py")
        if "pydantic_settings" in path.read_text(encoding="utf-8")
    )
    assert pydantic_settings_importers == ["src/openalpha_cn/config.py"]

    dotenv_package_importers = sorted(
        path.relative_to(ROOT).as_posix()
        for path in src_root.rglob("*.py")
        if re.search(
            r"^\s*(import dotenv\b|from dotenv\b)",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert dotenv_package_importers == ["src/openalpha_cn/config.py"]

    adr = (ROOT / "docs" / "architecture" / "ADR-0004-config-and-dotenv-loading.md").read_text(
        encoding="utf-8"
    )
    assert "pydantic-settings" in adr
    assert "python-dotenv" in adr
    assert "Amendment" in adr


def test_wechat_contact_qr_is_the_owner_provided_jpeg() -> None:
    qr_image = ROOT / "assets" / "brand" / "wechat-contact-qr.jpg"
    content = qr_image.read_bytes()

    assert content.startswith(b"\xff\xd8\xff")
    assert hashlib.sha256(content).hexdigest().upper() == (
        "A619D0051CE6BD1B836C91C445B527F67B505940CB3092DF11DDC5CA93B06B15"
    )
    assert len(content) > 100_000


def test_readme_brain_map_series_is_complete_and_ordered() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    diagrams = [
        "openalpha-brain-01-overview.svg",
        "openalpha-brain-02-evidence.svg",
        "openalpha-brain-03-agents.svg",
        "openalpha-brain-04-decision.svg",
        "openalpha-brain-05-replay-interfaces.svg",
    ]

    references = [f"./assets/diagrams/{name}" for name in diagrams]
    positions = [readme.index(reference) for reference in references]
    assert positions == sorted(positions)
    assert "以下五图在信息表达上参考" not in readme
    assert "下面五张图按实际使用顺序展示系统如何工作" in readme

    combined_content = ""
    for name in diagrams:
        content = (ROOT / "assets" / "diagrams" / name).read_text(encoding="utf-8")
        combined_content += content
        assert content.startswith("<svg")
        ET.fromstring(content)
        assert 'width="1440"' in content
        assert 'height="900"' in content
        assert "OPENALPHA" in content
        assert "研究闭环导航" in content
        assert "Geist" in content
        assert "Inter" not in content
        assert "Arial" not in content

    assert "链邻数据接口 API" in combined_content
    assert "已实现 · 统一替代入口" in combined_content
    assert "规划目标" not in combined_content
    assert "目标\uff1a实时获取" not in combined_content
    assert "持久批量任务中心" in combined_content
    assert "Bull / Bear 辩论" in combined_content
    assert "不可变组合转移账本" in combined_content
    assert "CAR · t 统计量" in combined_content
    assert "结构化研究筛选" in combined_content
    assert "自动执行 / 持久化" in combined_content
    assert "显式组合 / 人工反馈" in combined_content
    assert "EvidenceSnapshot" in combined_content
    assert "ResearchEngine.run_cycle" in combined_content
    assert "ResearchRunResult" in combined_content
    assert "DeliberationOutcome" in combined_content
    assert "PortfolioTransition" in combined_content
    assert "ValidationResult" in combined_content
    assert "四时钟 PIT" in combined_content
    assert "显式弃权" in combined_content
    assert "不自动下单" in combined_content
    assert "不自动训练模型" in combined_content
    assert "Tushare" not in combined_content
    assert "AKShare" not in combined_content


def test_readme_api_relationship_map_series_is_complete_and_source_grounded() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    diagrams = [
        "openalpha-api-01-landscape.svg",
        "openalpha-api-02-evidence-dataflow.svg",
        "openalpha-api-03-research-orchestration.svg",
        "openalpha-api-04-decision-products.svg",
        "openalpha-api-05-validation-loop.svg",
    ]

    references = [f"./assets/diagrams/{name}" for name in diagrams]
    positions = [readme.index(reference) for reference in references]
    assert positions == sorted(positions)
    assert positions[-1] < readme.index("## 公开 API")
    assert "## 🧭 五张 API 数据与功能关系图" in readme
    assert "实线表示服务端自动调用" in readme
    assert "虚线表示调用方显式组合" in readme

    combined_content = ""
    for name in diagrams:
        content = (ROOT / "assets" / "diagrams" / name).read_text(encoding="utf-8")
        combined_content += content
        assert content.startswith("<svg")
        ET.fromstring(content)
        assert 'width="1440"' in content
        assert 'height="900"' in content
        assert "OPENALPHA · API" in content
        assert "API 关系导航" in content

    required_source_terms = [
        "POST /api/v1/evidence/build",
        "ProviderMetadata + ProviderBatch",
        "EvidenceSnapshot",
        "四时钟 PIT",
        "POST /api/v1/research/run",
        "ResearchEngine.run_cycle",
        "POST /api/v1/research/batches",
        "POST /api/v1/research/deliberate",
        "POST /api/v1/portfolio/execute",
        "不可变 PortfolioTransition",
        "POST /api/v1/backtests/replay",
        "POST /api/v1/backtests/event-study",
        "POST /api/v1/backtests/validate",
        "不连接实盘券商",
    ]
    assert all(term in combined_content for term in required_source_terms)
    assert "不自动训练模型" in combined_content
    assert "服务端自动抓取数据" not in combined_content


def test_marketing_pack_contains_100_distinct_source_grounded_plans() -> None:
    content = (ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md").read_text(
        encoding="utf-8"
    )
    pattern = re.compile(
        r"(?ms)^###\s+(\d{3})\uff5c.*?"
        r"^\*\*开场钩子\uff1a\*\*\s+(.*?)\r?\n\r?\n"
        r"\*\*推广正文\uff1a\*\*\s+(.*?)\r?\n\r?\n"
        r"\*\*建议渠道\uff1a\*\*"
    )
    plans = pattern.findall(content)
    ids = [int(plan_id) for plan_id, _, _ in plans]
    hooks = [hook.strip() for _, hook, _ in plans]
    body_lengths = [len("".join(body.split())) for _, _, body in plans]

    assert ids == list(range(1, 101))
    assert len(set(hooks)) == 100
    assert min(body_lengths) >= 300
    assert max(body_lengths) <= 420
    assert all("TradingAgents" in body for _, _, body in plans)
    assert all("AI Hedge Fund" in body for _, _, body in plans)
    assert "链邻 Provider 已实现客户端合同" in content
    assert "不承诺收益" in content


def test_public_repository_metadata_is_present() -> None:
    required = [
        "Dockerfile",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        ".env.example",
    ]

    assert [name for name in required if not (ROOT / name).is_file()] == []


def test_container_delivery_has_persistence_and_recovery_verification() -> None:
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")
    verification = ROOT / "scripts" / "verify_compose_recovery.py"

    assert "openalpha-runtime:/data" in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert verification.is_file()


def test_dockerfile_pins_blas_and_openmp_thread_counts_for_deterministic_reductions() -> None:
    """ADR-0003's determinism hazard: without pinning `OMP_NUM_THREADS`/
    `OPENBLAS_NUM_THREADS`, BLAS/OpenMP floating-point reduction order changes with
    thread count -- a direct reproducibility hazard for a content-addressed system. The
    numerical stack (numpy/pandas) is not a runtime dependency yet, but the pinning
    mechanism must exist ahead of it (V2-P0B-009) so P4 does not have to remember to add
    it: `runtime/seeding.py#seed_everything` pins these at the Python level on every run,
    and the container image pins them at the process-environment level from container
    start, before any Python code (including `seed_everything`) ever runs.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for env_var in (
        "OMP_NUM_THREADS=1",
        "OPENBLAS_NUM_THREADS=1",
        "MKL_NUM_THREADS=1",
        "VECLIB_MAXIMUM_THREADS=1",
        "NUMEXPR_NUM_THREADS=1",
    ):
        assert env_var in dockerfile


def test_the_containers_working_directory_is_the_one_filesystem_it_can_write_to() -> None:
    """`V2-P4-015`: `read_only: true` plus a cwd on the image layer is a live spill failure.

    DuckDB defaults an in-memory connection's `temp_directory` to the **relative** path
    `.tmp`, and `panel/store.py` opens `duckdb.connect(":memory:")` on every
    `write_panel_batch`. Reproduced in the shipped image at one 200 MB memory limit: with the
    working directory on the read-only layer the query dies with
    `IO Error: Failed to create directory ".tmp": Read-only file system`, and with it on the
    runtime volume the query completes.

    The assertion is ordered rather than a pair of `in` checks, because the fix is entirely
    about *which* `WORKDIR` is last: the builder-side `WORKDIR /app` is still needed for the
    two `COPY` targets, and a change that added `/data` above it would read as done and be
    exactly as broken.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")

    runtime_stage = dockerfile.split("AS runtime", 1)[1]
    working_dirs = re.findall(r"^WORKDIR (\S+)$", runtime_stage, re.MULTILINE)

    assert working_dirs[-1] == "/data", (
        "the runtime stage's last WORKDIR is what the process inherits, and a DuckDB spill "
        "resolves its default temp_directory against it"
    )
    assert "openalpha-runtime:/data" in compose


def test_the_duckdb_spill_directory_default_is_relative_and_that_is_why_the_workdir_moved() -> None:
    """The executable half of the argument above, held against the library rather than prose.

    The whole reason a working directory is load-bearing is that DuckDB's default is a
    *relative* path. If a future DuckDB resolved it to an absolute one -- a temp dir, the
    database's directory, anything -- the `WORKDIR /data` above would stop being the fix and
    would be left standing as a change nobody could explain. This goes red on that day.
    """
    import duckdb

    with duckdb.connect(":memory:") as connection:
        row = connection.execute("SELECT current_setting('temp_directory')").fetchone()

    assert row is not None
    assert not Path(str(row[0])).is_absolute(), (
        f"duckdb {duckdb.__version__} resolves an in-memory temp_directory to {row[0]!r}, "
        "which is absolute; the Dockerfile's WORKDIR /data was chosen because it was relative"
    )


def test_the_container_does_not_put_its_writable_volume_on_the_import_path() -> None:
    """`PYTHONSAFEPATH` is what makes the working directory above safe rather than worse.

    `python -m uvicorn` prepends the process's cwd to `sys.path`, ahead of site-packages, so
    a working directory that is a user-writable volume is a module-shadowing surface. Measured
    in the shipped image: `python -m site` under `-w /data` prints `/data` as `sys.path[0]`
    without this variable and starts at the stdlib with it.

    Read out of the `ENV` block rather than as a substring of the whole file, because the
    substring version was a mutant's meal: this Dockerfile explains `PYTHONSAFEPATH` in a comment
    directly above the block, so deleting the actual `ENV` line left the name in the file and the
    check green. That is `test_known_limitation_registries.py`'s "prose does not satisfy the
    binding" arriving in a Dockerfile.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    runtime_stage = dockerfile.split("AS runtime", 1)[1]
    settings = [
        line.strip().removeprefix("ENV ").strip()
        for line in runtime_stage.splitlines()
        if not line.lstrip().startswith("#")
    ]
    declared = {
        setting.split("=", 1)[0] for setting in settings if re.match(r"^[A-Z][A-Z0-9_]*=", setting)
    }

    assert "PYTHONSAFEPATH" in declared, (
        "the runtime stage's ENV block does not declare PYTHONSAFEPATH; a mention in a comment "
        "is not a setting"
    )


def _env_example_section_vars(env_example: str, header: str) -> list[str]:
    """Return the `NAME=` variables declared directly under a `# <header>` comment.

    Only variables belonging to one specific `.env.example` section, not the whole
    file, so an unrelated future addition under a different header (e.g. another
    `OPENALPHA_*` runtime-path setting, which Compose deliberately hardcodes to a
    container path instead of passing through) can never be swept into this check.
    """
    match = re.search(
        rf"^# {re.escape(header)}\n((?:[A-Z][A-Z0-9_]*=.*\n?)+)",
        env_example,
        re.MULTILINE,
    )
    assert match is not None, f".env.example has no {header!r} section"
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", match.group(1), re.MULTILINE)
    assert names, f".env.example {header!r} section declares no variables"
    return names


def test_compose_passes_through_declared_provider_credentials() -> None:
    """Docker Compose must forward every user-owned credential declared in
    `.env.example` into the container environment, with a safe `:-` default so an
    unset variable can never break `docker compose config`.

    Reviewer-reproduced gap: with a real `TUSHARE_TOKEN` exported in the host shell,
    `docker compose up -d --wait` followed by `docker compose exec openalpha python -m
    openalpha_cn.cli doctor` reported the credential missing *inside the container*,
    because `environment:` never referenced `TUSHARE_TOKEN` (or any other credential)
    at all -- Compose has no implicit passthrough, so a Compose file that only ever
    references `OPENALPHA_*` settings can never see a Provider or model-provider
    credential set outside it, no matter how the user set it.

    This is a structural check only: it confirms each declared credential variable is
    referenced with a safe default, which is verifiable without Docker. It cannot
    confirm a *real* credential value actually reaches a running container -- that
    requires Docker itself, exercised only when available (see this phase's manual
    `docker compose config` verification and `scripts/verify_compose_recovery.py`).
    """
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    credential_vars = _env_example_section_vars(
        env_example, "User-owned data provider credentials"
    ) + _env_example_section_vars(env_example, "Optional model providers")

    for name in credential_vars:
        assert f"{name}: ${{{name}:-}}" in compose, (
            f"deploy/compose.yml does not pass {name} through with a safe default"
        )


def _override_names(pnpm_workspace: str) -> set[str]:
    """Which packages `web/pnpm-workspace.yaml` pins, without reading which version.

    This assertion used to be `"brace-expansion: 5.0.8" in pnpm_workspace`, and that literal
    became the defect it was written to prevent. 5.0.8 was the *patched* version when the pin
    was added; the advisory later moved to `>=5.0.9`, so the pin was holding the vulnerable
    version in place and `pnpm audit --audit-level high` -- CI's own `web` gate -- was exiting
    1 on an untouched checkout. A test asserting the literal then refused the security fix.

    A version is a claim about a moment and nothing re-reads it. What is durable is that the
    override *mechanism* is in place for the three packages that need it, and that the gate
    which can actually see a new advisory is still wired into CI. The gate is not run here:
    it needs the network, and `tests/conftest.py` refuses that outside `tests/e2e`.
    """
    return {
        match.group(1)
        for match in re.finditer(r"^  ([A-Za-z0-9@._-]+):", pnpm_workspace, re.MULTILINE)
    }


PATH_PRODUCING_NAMES: Final[frozenset[str]] = frozenset(
    {"relative_to", "parent", "joinpath", "with_name", "with_suffix"}
)
"""The `Path` members that derive a path used as a *value*, where `/` is the only right spelling.

`relative_to` is what `V2-P5-060` found; `parent` is what it missed, and one Windows run later
`Counter(str(Path(path).parent) for path in ...)` had keyed a whole ledger by `tests\\e2e`.

`resolve` and `absolute` are deliberately absent, and the distinction is the rule rather than an
exemption: they answer "where is this on *this* machine", which is exactly the question whose
answer must carry the platform's separator -- all seven sites spelling `str(x.resolve())` in this
tree feed a prefix replacement against a message holding a real local path, and `.as_posix()`
there would break them. What the members above produce is a path used as a key, an allowlist
entry or an identifier, and those are compared against literals written with `/`.
"""


def _stringifies_a_repository_path(tree: ast.AST) -> bool:
    """Whether `tree` spells `str(<a Path expression>)` as an executable call.

    Read from the syntax tree and not with a regex over the source, because the first draft of
    this audit *was* a regex and it flagged its own docstring -- the "the code says it twice, one
    of them in prose" mistake `test_known_limitation_registries.py` was built to stop counting.
    Prose that quotes the idiom in order to forbid it is not the idiom.
    """

    def _is_a_path_expression(node: ast.expr) -> bool:
        if isinstance(node, ast.Call):
            return isinstance(node.func, ast.Attribute) and node.func.attr in PATH_PRODUCING_NAMES
        return isinstance(node, ast.Attribute) and node.attr in PATH_PRODUCING_NAMES

    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and _is_a_path_expression(node.args[0])
        for node in ast.walk(tree)
    )


def test_no_repository_path_is_compared_in_the_running_platforms_own_separator() -> None:
    """`V2-P5-060`. `str(path.relative_to(root))` spells the separator the platform runs on.

    `a\\b` on Windows, `a/b` everywhere else.

    Every one of these nine sites fed the result to a comparison against a literal written with
    `/` -- an allowlist of modules, a ledger of documented command lines, a set of offenders --
    so on Windows each compared a string that could not match and reported the whole set as
    unlisted. Seven neighbouring sites already spelled `.as_posix()`; the tree was half converted,
    which is the shape this repository keeps finding: a fix applied to some of the copies.

    `windows-latest` is in `quality.yml`'s matrix and asserted by the test just below, so this is
    a claim the repository makes rather than a platform it merely tolerates -- and the claim had
    never been tested, because CI had never run on this branch (`V2-P5-054`).
    """
    offenders = sorted(
        path.relative_to(ROOT).as_posix()
        for path in [
            *(ROOT / "src").rglob("*.py"),
            *(ROOT / "tests").rglob("*.py"),
            *(ROOT / "scripts").rglob("*.py"),
        ]
        if _stringifies_a_repository_path(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
    )

    assert offenders == []


def test_quality_workflow_covers_supported_platforms_and_locked_dependencies() -> None:
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    pnpm_workspace = (ROOT / "web" / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ubuntu-latest" in workflow
    assert "windows-latest" in workflow
    assert '"3.11"' in workflow
    assert '"3.12"' in workflow
    assert "uv sync --locked --all-extras --dev" in workflow
    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "pnpm install --frozen-lockfile" in workflow
    assert "pip-audit" in workflow
    assert "verify_publication.py" in workflow
    assert "verify_compose_recovery.py" in workflow
    assert "pnpm audit --audit-level high" in workflow
    assert _override_names(pnpm_workspace) >= {"brace-expansion", "nanoid", "undici"}
    assert "web/pnpm-workspace.yaml" in dockerfile


def test_quality_workflow_python_job_has_a_type_check_step() -> None:
    """The `python` job must run mypy somewhere, however the step is worded.

    Mutation: delete the "Type check" step from `.github/workflows/quality.yml`.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    command = _step_run_command(python_job, "mypy")
    assert command is not None, "quality.yml's python job has no step invoking mypy"
    assert _is_live_type_check(command), (
        f"mypy step does not actually execute mypy as the run command: {command!r}"
    )


def test_quality_workflow_type_check_step_covers_scripts_as_well_as_src() -> None:
    """The type-check step must type-check `scripts`, not just `src`.

    `scripts/build_feature_coverage.py` carries real logic (AST symbol
    resolution, acceptance_kind validation) that CI must type-check or
    regressions there go unnoticed.

    Mutation: revert the mypy step's `run:` line to `uv run mypy src`
    (dropping `scripts`).
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    command = _step_run_command(python_job, "mypy")
    assert command is not None, "quality.yml's python job has no step invoking mypy"
    assert _is_live_type_check(command), (
        f"mypy step does not actually execute mypy as the run command: {command!r}"
    )
    checked_paths = set(shlex.split(command))
    assert {"src", "scripts"} <= checked_paths, (
        f"mypy step does not type-check both src and scripts: {command!r}"
    )


def _ci_mypy_paths() -> set[str]:
    """The path arguments `quality.yml`'s type-check step passes to mypy.

    Everything after the `mypy` word that is not a flag. Shared by the two tests below so
    that "what CI checks" has one reading, not two that can drift apart.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    command = _step_run_command(_workflow_job_block(workflow, "python"), "mypy")
    assert command is not None, "quality.yml's python job has no step invoking mypy"
    argv = shlex.split(command)
    return {arg for arg in argv[argv.index("mypy") + 1 :] if not arg.startswith("-")}


def test_a_bare_mypy_checks_exactly_what_ci_passes_on_the_command_line() -> None:
    """`uv run mypy` with no arguments must type-check what `quality.yml` type-checks.

    This was measured, not assumed. With `[tool.mypy] packages = ["openalpha_cn"]` and this
    repository's `src/` layout, a bare `uv run mypy` does not read `src/` at all: it resolves
    the *installed* distribution inside `.venv`, finds no `py.typed` marker there, and exits
    **2** with "Package 'openalpha_cn' cannot be type checked due to missing py.typed marker".
    Nothing gets checked and the command fails.

    CI stayed green through all of it because CI never used that config -- its step spells
    `uv run mypy src scripts`, and an explicit path list overrides `files`/`packages`
    entirely. So the type gate was live in exactly one invocation and broken in the obvious
    one, which is the shape this repository keeps finding: a declared safety property that
    holds only on the path someone happened to test.

    The two readings are pinned equal here rather than the config simply being fixed, because
    a fix with no assertion is one edit away from returning -- and the edit that returns it
    (`packages = [...]`, which is what mypy's own docs reach for first) looks correct.

    Mutation: restore `packages = ["openalpha_cn"]` in place of `files`.
    """
    mypy_config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "mypy"
    ]

    assert "packages" not in mypy_config, (
        "[tool.mypy] declares `packages`, which resolves the installed distribution rather "
        "than `src/` and exits 2 on this repository's layout; declare `files` instead"
    )
    assert set(mypy_config.get("files", ())) == _ci_mypy_paths(), (
        f"a bare `uv run mypy` checks {mypy_config.get('files', ())!r} but CI checks "
        f"{sorted(_ci_mypy_paths())!r}; the two must be the same set"
    )


def test_a_green_run_leaves_no_tmp_path_behind() -> None:
    """`tmp_path` survives a run only for the tests that failed.

    The number that motivates this is measured, not estimated: a full run writes **4.9 GB**
    of `tmp_path` across 3,790 tests, and no single test is the problem -- the largest is
    30 MB and the median is orders of magnitude smaller. pytest's default
    `retention_policy = "all"` keeps the directory of every *passing* test as well, and the
    companion default `retention_count = 3` only bounds that for runs that reach their own
    exit path. A run killed part-way -- competing suites SIGTERMing each other, or the
    kernel's ENOSPC/OOM killer -- prunes nothing and orphans its numbered directory forever.
    On this machine those orphans reached **106 GB** and took the volume to zero bytes free,
    at which point no test can run, `git` cannot write, and the failure presents as an
    unrelated red build.

    So this is a disk-shaped bug with a config-shaped fix, and the assertion is here rather
    than nowhere because the fix is a single line whose absence is invisible for weeks: a
    green suite looks identical either way until a volume fills.

    Mutation: delete `tmp_path_retention_policy` from `[tool.pytest.ini_options]`, or set it
    back to `"all"`.
    """
    ini_options = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "pytest"
    ]["ini_options"]

    assert ini_options.get("tmp_path_retention_policy") == "failed", (
        '[tool.pytest.ini_options] must set tmp_path_retention_policy = "failed"; '
        f"got {ini_options.get('tmp_path_retention_policy')!r}. pytest's default keeps every "
        "passing test's tmp_path, which this suite grows by 4.9 GB per run."
    )


def test_quality_workflow_python_job_has_a_coverage_gate_step() -> None:
    """The `python` job must run pytest with coverage collection enabled.

    Mutation: delete the "Test with coverage" step from quality.yml.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    command = _step_run_command(python_job, "--cov")
    assert command is not None, "quality.yml's python job has no step collecting coverage (--cov)"
    assert _is_live_pytest_coverage_gate(command), (
        f"coverage step does not actually execute pytest as the run command: {command!r}"
    )


def test_quality_workflow_coverage_gate_threshold_is_at_least_80() -> None:
    """Whatever the CI coverage gate's threshold is, it must not regress below 80.

    Mutation: lower `--cov-fail-under=80` to any value under 80 in quality.yml.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    command = _step_run_command(python_job, "--cov-fail-under")
    assert command is not None, "quality.yml's python job has no --cov-fail-under gate"
    assert _is_live_pytest_coverage_gate(command), (
        f"coverage step does not actually execute pytest as the run command: {command!r}"
    )
    match = re.search(r"--cov-fail-under[= ](\d+)", command)
    assert match is not None, f"could not parse --cov-fail-under value from {command!r}"
    assert int(match.group(1)) >= 80


def test_backend_coverage_fail_under_is_configured_in_pyproject() -> None:
    """`pyproject.toml` must enforce the same 80% floor CI enforces, locally.

    Without `[tool.coverage.report].fail_under`, running `pytest --cov`
    locally collects coverage but never fails on a low number - only CI's
    `--cov-fail-under=80` flag would catch a regression.

    Mutation: delete `fail_under` from `[tool.coverage.report]` in
    pyproject.toml, or set it below 80.
    """
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    fail_under = config["tool"]["coverage"]["report"]["fail_under"]
    assert isinstance(fail_under, int)
    assert fail_under >= 80


def test_coverage_gate_bypass_collect_only_is_rejected() -> None:
    """Reviewer-demonstrated bypass: `--collect-only` keeps every substring a
    naive check looks for (`pytest`, `--cov`, `--cov-fail-under=80`) while
    pytest never executes a single test body. Worse, pytest-cov silently
    suppresses the `--cov-fail-under` exit code under `--collect-only`, so
    this mutation exits 0 in real CI too, not just in a substring-based test.
    """
    bypass = (
        "uv run pytest --collect-only --cov=openalpha_cn "
        "--cov-report=term-missing --cov-fail-under=80"
    )
    assert not _is_live_pytest_coverage_gate(bypass)


def test_coverage_gate_bypass_echo_wrapper_is_rejected() -> None:
    """Reviewer-demonstrated bypass: wrapping the real command in `echo`
    keeps every required substring while being a pure no-op that never
    invokes pytest at all.
    """
    bypass = (
        'echo "would run: uv run pytest --cov=openalpha_cn '
        '--cov-report=term-missing --cov-fail-under=80"'
    )
    assert not _is_live_pytest_coverage_gate(bypass)


def test_type_check_bypass_echo_wrapper_is_rejected() -> None:
    """Same discipline as the coverage gate: wrapping the mypy invocation in
    `echo` must not read as a live type-check step.
    """
    bypass = 'echo "would run: uv run mypy src scripts"'
    assert not _is_live_type_check(bypass)


def test_quality_workflow_coverage_gate_step_survives_both_demonstrated_bypasses() -> None:
    """End-to-end: substitute each demonstrated bypass into the real
    quality.yml `python` job's coverage-gate `run:` line and confirm the
    extraction-plus-shape-check pipeline used by the structural tests above
    still rejects it, exactly as a reviewer mutating the real file would.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")
    original = _step_run_command(python_job, "--cov-fail-under")
    assert original is not None

    bypasses = (
        "uv run pytest --collect-only --cov=openalpha_cn "
        "--cov-report=term-missing --cov-fail-under=80",
        'echo "would run: uv run pytest --cov=openalpha_cn '
        '--cov-report=term-missing --cov-fail-under=80"',
    )
    for bypass in bypasses:
        mutated_job = python_job.replace(f"run: {original}", f"run: {bypass}")
        assert mutated_job != python_job, "fixture no longer matches the real run: line"
        command = _step_run_command(mutated_job, "--cov-fail-under")
        assert command is not None
        assert not _is_live_pytest_coverage_gate(command)


def test_step_run_command_tolerates_block_scalar_style() -> None:
    """Converting a step to YAML block-scalar style (`run: |`, command on the
    next line) must not produce a false red: the coverage-gate step must
    resolve to the same live command whether written as `run: <cmd>` or as
    `run: |\\n  <cmd>`.
    """
    inline = (
        "  - name: Test with coverage\n"
        "    run: uv run pytest --cov=openalpha_cn --cov-fail-under=80\n"
    )
    block = (
        "  - name: Test with coverage\n"
        "    run: |\n"
        "      uv run pytest --cov=openalpha_cn --cov-fail-under=80\n"
    )
    inline_command = _step_run_command(inline, "--cov-fail-under")
    block_command = _step_run_command(block, "--cov-fail-under")
    assert inline_command is not None
    assert block_command is not None
    assert _is_live_pytest_coverage_gate(inline_command)
    assert _is_live_pytest_coverage_gate(block_command)


def test_step_run_command_block_scalar_style_does_not_reopen_the_bypass() -> None:
    """Block-scalar tolerance must not let the `--collect-only` bypass
    through just because it is written across two lines instead of one.
    """
    block = (
        "  - name: Test with coverage\n"
        "    run: |\n"
        "      uv run pytest --collect-only --cov=openalpha_cn --cov-fail-under=80\n"
    )
    command = _step_run_command(block, "--cov-fail-under")
    assert command is not None
    assert not _is_live_pytest_coverage_gate(command)


def test_vite_config_declares_frontend_coverage_gate_with_per_metric_thresholds() -> None:
    """`web/vite.config.ts` must declare a live `test.coverage` gate with
    per-metric thresholds, mirroring the backend's `fail_under` guard.

    Mutation: delete the `coverage` block from `test: { ... }` (or delete
    `enabled: true`, or delete `thresholds`) -- `pnpm test` then falls back
    to bare `vitest run`, exits 0, prints nothing, and no test anywhere
    notices.
    """
    vite_config = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    thresholds = _vite_coverage_thresholds(vite_config)
    assert thresholds is not None, (
        "web/vite.config.ts has no live test.coverage gate "
        "(missing coverage block, enabled: true, or thresholds)"
    )

    floors = {"statements": 68, "branches": 60, "functions": 64, "lines": 70}
    for metric, floor in floors.items():
        assert thresholds[metric] >= floor, (
            f"{metric} threshold regressed below {floor}: {thresholds[metric]}"
        )


def test_vite_config_coverage_gate_bypass_deleted_coverage_block_is_rejected() -> None:
    """Reviewer-demonstrated bypass: delete the `coverage` block entirely.

    `pnpm test` then falls back to bare `vitest run` -- exits 0, prints
    nothing, and (before this test existed) nothing anywhere noticed.
    """
    vite_config = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    test_block = _try_brace_block(vite_config, "test")
    assert test_block is not None
    coverage_block = _try_brace_block(test_block, "coverage")
    assert coverage_block is not None

    mutated_vite_config = vite_config.replace(f"coverage: {{{coverage_block}}}", "", 1)
    assert mutated_vite_config != vite_config, "fixture no longer contains the real coverage block"

    assert _vite_coverage_thresholds(mutated_vite_config) is None


def test_publication_gate_accepts_tracked_release_sources() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify_publication.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_publication_gate_survives_a_nested_checkout_and_says_it_skipped_it(
    tmp_path: Path,
) -> None:
    """An agent worktree under `.claude/worktrees/` used to kill this gate outright.

    `git ls-files --others` reports a directory carrying its own `.git` as one entry rather
    than descending into it, and the scanner called `read_text` on it: `IsADirectoryError`,
    exit 1, on a repository whose tracked content was untouched. That made the gate report on
    whether a sibling directory happened to exist -- and it exists exactly while this project's
    own tooling is running, so the gate failed when it was needed most.

    Both halves are asserted here. Skipping a nested checkout is correct, because its contents
    are published by *its* repository and scanned by *its* run. Saying so is not optional: a
    scan that quietly declines to read something and still answers "ok" is the failure mode
    every registry in this repository exists to prevent.

    The probe sits at the repository root and **not** under `.claude/`, which is where the
    harness actually puts its worktrees. That directory is ignored now, so `git ls-files
    --others --exclude-standard` never mentions it and there is nothing for this test to
    observe -- it went red the moment the ignore landed, which is the correct signal and the
    reason the probe moved rather than the assertion weakening. An ignored directory is not a
    skip; it is not a candidate. What still has to hold is the case the ignore does not cover:
    somebody clones a repository into the working tree, git lists that directory and nothing
    else about it, and the scan must survive it and say so.
    """
    nested = ROOT / "publication-gate-probe"
    subprocess.run(["git", "init", "-q", str(nested)], check=True, capture_output=True)
    try:
        (nested / "decoy.parquet").write_bytes(b"a blocked suffix the outer scan must not see")
        result = subprocess.run(
            [sys.executable, "scripts/verify_publication.py", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert "publication-gate-probe" in report["nested_checkouts"]
        assert report["blockers"] == []
    finally:
        shutil.rmtree(nested, ignore_errors=True)


def test_publication_gate_blocks_a_listed_directory_that_is_not_a_checkout(
    tmp_path: Path,
) -> None:
    """The skip above is bounded by `.git`, so it cannot become a way to hide a directory.

    Without this, "skip directories" would be a hole wide enough to drive an unscanned tree
    through. Git only lists a bare directory when it cannot enumerate its contents, so this
    branch should never fire in practice -- which is exactly why it must be a blocker rather
    than a `continue`: an unreachable refusal costs nothing, and a silent one costs everything.
    """
    module = _load_verify_publication()

    plain = tmp_path / "not-a-checkout"
    plain.mkdir()
    assert module._is_nested_checkout(plain) is False

    checkout = tmp_path / "a-checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    assert module._is_nested_checkout(checkout) is True


def test_publication_gate_blocks_sqlite_backup_files() -> None:
    """`storage/migrations.py` backs up `state.sqlite3` under a `.bak` suffix before every
    migration attempt (see `_take_backup`). `.bak` is a SQLite binary dump exactly like
    `.sqlite`/`.sqlite3`/`.db`/`.duckdb`, all of which are already blocked -- but `.bak`
    itself was missing from `BLOCKED_SUFFIXES`, so an untracked `.bak` file that escaped
    `.gitignore` (e.g. under a custom `--runtime-dir` outside the ignored `/runtime/` tree)
    would publish clean. `.gitignore` path matching is not a substitute for this: it stops
    covering the file the moment someone points `--runtime-dir` elsewhere.
    """
    module = _load_verify_publication()

    assert ".bak" in module.BLOCKED_SUFFIXES


def test_feature_coverage_artifacts_are_reconciled() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_feature_coverage.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert '"unreviewed": 0' in result.stdout
    assert '"unknown": 0' in result.stdout


# --- ADR-0003's nine runtime dependencies (P3 acceptance) ------------------------------------

RUNTIME_DEPENDENCIES = (
    "duckdb>=1.5,<2",
    "fastapi>=0.135,<1",
    "pydantic>=2.12,<3",
    "pydantic-settings>=2.15,<3",
    "python-dotenv>=1.1,<2",
    "pytz>=2025.2",
    "typer>=0.21.0,<1",
    "tzdata>=2025.2",
    "uvicorn>=0.41,<1",
)
"""`[project].dependencies`, written out, because ADR-0003 says this file pins them.

That ADR's whole decision is that this repository ships **no numerical stack**, argued over eight
measured updates during P3, and it says: "The nine runtime dependencies are pinned by
`tests/unit/test_repository_assets.py`, which is what would go red if this update were wrong."
The P3 technical acceptance added a tenth dependency to `[project].dependencies` and ran this
file together with `test_adr_consistency.py`: **34 passed**. Nothing in the repository read
`[project].dependencies` at all -- `domain-purity` forbids `domain/` from importing numpy or
pandas, which is a different claim about different code, and ruff's `NPY`/`PD` rule sets are not
enabled.

Full requirement strings rather than bare names, so a widened floor (`pydantic>=2.12` relaxed to
`pydantic>=2.0`) is as visible as a new package. `[project.optional-dependencies]` and
`[dependency-groups]` are deliberately outside this tuple;
`test_the_optional_and_development_dependency_tables_are_not_a_way_around_the_nine` is why that
is not a hole.
"""

NUMERICAL_STACK_PACKAGES = frozenset(
    {"numpy", "pandas", "scipy", "scikit-learn", "sklearn", "polars", "pyarrow", "statsmodels"}
)
"""The distributions ADR-0003 exists to keep out of an install.

Checked separately from the tuple equality above even though that equality already catches them,
because the two fail with different messages: the equality says "the dependency list changed",
and this one says which decision the change reverses.
"""

_REQUIREMENT_NAME = re.compile(r"[<>=!~\[;\s]")


def _requirement_name(requirement: str) -> str:
    """The distribution name at the head of a PEP 508 requirement string."""
    return _REQUIREMENT_NAME.split(requirement, maxsplit=1)[0].strip().lower()


def _pyproject_tables() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_nine_runtime_dependencies_are_exactly_what_adr_0003_says_they_are() -> None:
    """The pin ADR-0003 claims exists. It did not until this test did.

    An exact tuple equality rather than a set comparison or a count, because the three ways this
    list goes wrong are different and a weaker assertion separates only some of them: a tenth
    entry, a renamed entry (nine names, one of them different -- `Task 38`'s shape, where a table
    and its implementation drift while the count still matches), and a relaxed specifier on one
    of the nine.
    """
    project = _pyproject_tables()["project"]
    assert isinstance(project, dict)

    assert tuple(project["dependencies"]) == RUNTIME_DEPENDENCIES, (
        "[project].dependencies is not what ADR-0003 says it is. If the change is deliberate, "
        "update RUNTIME_DEPENDENCIES here and the ADR's own count with it -- that ADR argues "
        "over eight measured updates that this repository ships no numerical stack, and this "
        "list is the only place that claim can be falsified"
    )
    assert len(RUNTIME_DEPENDENCIES) == 9


def test_no_numerical_stack_package_can_be_installed_by_default() -> None:
    """ADR-0003's decision, stated as the thing it forbids rather than as the list it allows.

    `domain-purity` forbids `domain/` from *importing* numpy, and the two `backtest` study
    contracts forbid the study modules from importing it too. None of them stops it being
    *installed*, and an installed numerical stack is what an ADR arguing about wheel size, BLAS
    thread pinning and reproducibility is actually about.
    """
    project = _pyproject_tables()["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    assert isinstance(dependencies, list)

    numeric = sorted(
        str(requirement)
        for requirement in dependencies
        if _requirement_name(str(requirement)) in NUMERICAL_STACK_PACKAGES
    )

    assert not numeric, (
        f"{numeric} is a numerical-stack package in [project].dependencies. ADR-0003 decided "
        "against exactly this and re-measured the decision eight times during P3; reversing it "
        "is an ADR edit, not a dependency edit"
    )


def test_the_optional_and_development_dependency_tables_are_not_a_way_around_the_nine() -> None:
    """The nine are a floor on what an install pulls in, so the other two tables need a rule too.

    **Correction, `V2-P4-015`: the first sentence below overstates what this test does, and the
    correction is measured.** This is a check over *declared names*, and the eight names it knows
    are `NUMERICAL_STACK_PACKAGES`. The `akshare` extra has been in this table the whole time and
    reaches `pandas` and therefore `numpy`, and this test has always passed -- so "forbidden
    outright" was true of the four names and false of the install.
    `test_a_numerical_stack_cannot_arrive_through_an_extra_that_only_names_its_wheel` is the check
    over the resolved graph, and `EXTRAS_THAT_CARRY_A_NUMERICAL_STACK` is where `akshare` is
    written down rather than exempted. This test is kept as it stands because it fails with a
    different message -- the direct one, naming the decision reversed.

    `[project.optional-dependencies]` ships to users behind an extra, so a numerical stack there
    is the same install by another name and is forbidden outright. `[dependency-groups].dev`
    never reaches a wheel, so it is pinned as a list instead: adding `numpy` there for one test
    would not violate ADR-0003, but it would make `import numpy` succeed in CI and turn every
    "this repository has no numpy" measurement into one about the developer's own environment --
    `tests/unit/runtime/test_seeding.py` currently skips on exactly that import.
    """
    tables = _pyproject_tables()
    project = tables["project"]
    assert isinstance(project, dict)
    optional = project.get("optional-dependencies", {})
    assert isinstance(optional, dict)

    for extra, requirements in sorted(optional.items()):
        assert isinstance(requirements, list)
        numeric = sorted(
            str(requirement)
            for requirement in requirements
            if _requirement_name(str(requirement)) in NUMERICAL_STACK_PACKAGES
        )
        assert not numeric, f"extra {extra!r} would install {numeric}"

    groups = tables["dependency-groups"]
    assert isinstance(groups, dict)
    assert tuple(groups["dev"]) == (
        "httpx2>=2.9.0",
        "import-linter>=2.13,<3",
        "mypy>=1.19.0,<2",
        "pytest>=9.0.0,<10",
        "pytest-cov>=7.0.0,<8",
        "ruff>=0.15.0,<1",
    )


EXTRAS_THAT_CARRY_A_NUMERICAL_STACK = {"akshare": ("numpy", "pandas")}
"""Which extras pull a numerical stack **transitively**, and what each pulls.

`V2-P4-015` measured that the guard above is a **name** check and that it was already false.
`test_the_optional_and_development_dependency_tables_are_not_a_way_around_the_nine`'s own
docstring says an extra shipping a numerical stack "is the same install by another name and is
forbidden outright" -- and `akshare` has been in that table the whole time, requiring `pandas`,
which requires `numpy`. Neither name is in `NUMERICAL_STACK_PACKAGES`, so the guard passed. It
would have passed a `lightgbm` extra for exactly the same reason: `lightgbm` is not one of the
eight names either, and its wheel requires `numpy` and `scipy`.

This mapping is therefore a pin over the **resolved** graph rather than over the declared names,
and `akshare` is written into it rather than exempted from it. The distinction it draws is the one
ADR-0003 actually argues: `akshare` is an optional *data provider* whose pandas dependency is the
provider's own, reached by `providers/`, and ADR-0003's Context item 2 already records that CI
installs both because of it. A model that needed a numerical stack would be this repository's own
code depending on one, which is the decision that ADR requires an ADR edit to reverse.
"""


def _locked_packages() -> dict[str, dict[str, object]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = lock["package"]
    assert isinstance(packages, list)
    return {str(entry["name"]): entry for entry in packages}


def _reachable(names: list[str], packages: dict[str, dict[str, object]]) -> set[str]:
    """Every distribution an install of `names` would pull in, per the lock file."""
    seen: set[str] = set()
    stack = list(names)
    while stack:
        current = stack.pop()
        if current in seen or current not in packages:
            continue
        seen.add(current)
        dependencies = packages[current].get("dependencies", [])
        assert isinstance(dependencies, list)
        stack.extend(str(dependency["name"]) for dependency in dependencies)
    return seen


def test_a_numerical_stack_cannot_arrive_through_an_extra_that_only_names_its_wheel() -> None:
    """The hole `V2-P4-015` found in the guard above, closed over the resolved graph.

    Two assertions and they are different claims. The first is the one ADR-0003 has re-argued
    nine times and has never actually measured: that a **default** install reaches no numerical
    stack at all -- 25 distributions in the lock, none of them numeric. The nine-name pin cannot
    say that, because a numerical stack can arrive as somebody else's dependency.

    The second pins which extras carry one and what each carries, so an extra named after a
    wheel this repository has never heard of -- `lightgbm`, `xgboost`, anything -- fails here
    instead of passing a check that only knows eight distribution names.
    """
    packages = _locked_packages()
    root = packages["openalpha-cn"]

    dependencies = root.get("dependencies", [])
    assert isinstance(dependencies, list)
    default = _reachable([str(entry["name"]) for entry in dependencies], packages)

    assert sorted(default & NUMERICAL_STACK_PACKAGES) == [], (
        "a default `uv sync` would install a numerical stack transitively. ADR-0003's decision "
        "is about what an install pulls in, not about what nine strings say"
    )

    optional = root.get("optional-dependencies", {})
    assert isinstance(optional, dict)
    measured = {
        extra: tuple(
            sorted(
                _reachable([str(entry["name"]) for entry in requirements], packages)
                & NUMERICAL_STACK_PACKAGES
            )
        )
        for extra, requirements in optional.items()
    }
    carriers = {extra: names for extra, names in measured.items() if names}

    assert carriers == EXTRAS_THAT_CARRY_A_NUMERICAL_STACK, (
        f"the extras table's transitive numerical carry is {carriers} and this file pins "
        f"{EXTRAS_THAT_CARRY_A_NUMERICAL_STACK}. Adding an extra that reaches numpy, pandas, "
        "scipy or scikit-learn is ADR-0003's decision being reversed one indirection out, "
        "which is exactly how it would arrive"
    )


def test_adr_0003_still_names_this_file_and_the_count_it_pins() -> None:
    """The citation loop, closed in the direction that was open.

    ADR-0003 named this file as the thing that would go red; this is the other half, so renaming
    the pin or changing the count without touching the ADR fails here rather than leaving a
    second sentence that a measurement disproves.
    """
    adr = (ROOT / "docs" / "architecture" / "ADR-0003-numerical-stack-boundary.md").read_text(
        encoding="utf-8"
    )

    assert "tests/unit/test_repository_assets.py" in adr
    assert "nine runtime dependencies" in adr, (
        "ADR-0003 no longer says 'nine runtime dependencies'; if the count changed, "
        "RUNTIME_DEPENDENCIES above and the ADR have to change together"
    )
    assert len(RUNTIME_DEPENDENCIES) == 9


CONFLICT_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:<{7}|\|{7}|>{7})(?: .*)?$", re.MULTILINE
)
"""The three markers no legitimate line of text can be, as a pattern over the tracked tree.

Committed conflict markers reached `feat/v2-p0a` **twice** in one day and both times a
sibling agent found them by reading the file, not by any gate. Nothing else here can see
them: `verify_publication.py` walks 520 files without looking, `ruff` and `mypy` never open
`CHANGELOG.md`, and a marker inside a Markdown bullet list changes no rendered output that
anything asserts on. The pytest suite is the only thing that reads the whole tracked tree,
so this is where the check belongs.

Scoped to `git ls-files` rather than a walk, because the untracked world holds agent
scratch space, `.venv` and half-applied patches, none of which is this repository's claim
about itself. Binary files are skipped by the decode guard rather than by an extension
list, so a new text format is covered on arrival instead of when somebody remembers it.

## What `V2-P5-036` changed, and it is three things

The pattern was `^(?:<<<<<<< |=======$|>>>>>>> )`, and measured:

    Summary / =======    (7-char setext H1 underline)  -> DETECTED   <- false positive
    Summary / =========  (9-char setext H1 underline)  -> missed
    `||||||| merged common ancestors`                  -> missed
    `<<<<<<<` with no label                            -> missed
    `>>>>>>>` with no label                            -> missed

**The trailing space was load-bearing and should not have been.** `git` writes a label after
`<<<<<<<` and `>>>>>>>` when it has one, and writes the bare marker when it does not -- which
is what `merge-file` and several editors' resolvers emit. So the two least ambiguous strings in
the whole problem were being missed for want of a space.

**`|||||||` was absent entirely.** It is the base section `merge.conflictStyle = diff3` and
`zdiff3` write, and a half-resolved diff3 conflict can leave it behind with nothing else.

**`=======` moved to `AMBIGUOUS_SEPARATOR` below**, because it is the one marker that is also
ordinary text: a Markdown setext `<h1>` underline of exactly seven `=` is indistinguishable
from a conflict separator line-for-line. `=======$` therefore made correct Markdown into a red
build, and this repository is written in Markdown. Nothing in the tree triggered it today,
which is the only reason it was never paid.

Exactly seven, and the run length needs no lookahead: an eighth `<` is neither a space nor the
end of the line, so `(?: .*)?$` already refuses it. A mutation sweep showed a `(?!<)` guard here
made no difference to any of the twelve driven shapes, so it is not carried.
"""

AMBIGUOUS_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"^={7}$", re.MULTILINE)
"""`=======`, which is a conflict separator **and** a seven-character setext heading rule.

Flagged only in a file that also carries one of the unambiguous markers above, because that is
the difference between the two readings: a conflict separator never occurs alone -- `git` writes
it between a `<<<<<<<` and a `>>>>>>>` -- and a setext underline always does.

The direction this gives up is stated rather than discovered: a merge resolved by hand-deleting
the opening and closing markers and leaving the separator is not caught. That resolution is a
person editing the file, which is the case this gate was never able to reason about anyway; the
case it exists for is a marker nobody looked at.
"""


@pytest.mark.parametrize(
    ("shape", "line", "detected"),
    [
        ("labelled open", "<<<<<<< HEAD", True),
        ("labelled close", ">>>>>>> feat/v2-p0a", True),
        ("bare open", "<<<<<<<", True),
        ("bare close", ">>>>>>>", True),
        ("diff3 base, labelled", "||||||| merged common ancestors", True),
        ("diff3 base, bare", "|||||||", True),
        ("eight opens is not a marker", "<<<<<<<<", False),
        ("six opens is not a marker", "<<<<<<", False),
        ("a shell heredoc arrow", "cat <<EOF", False),
        ("a quoted marker inside prose", "the `<<<<<<< HEAD` line", False),
        ("a seven-character setext rule", "=======", False),
        ("a nine-character setext rule", "=========", False),
    ],
)
def test_the_marker_pattern_reads_every_shape_git_writes_and_no_shape_it_does_not(
    shape: str, line: str, detected: bool
) -> None:
    """`V2-P5-036`. Each row is a string this gate either must or must not treat as a marker.

    Four of these were wrong before the row. `git` writes `<<<<<<<` and `>>>>>>>` **without** a
    label when it has none, and the pattern required a trailing space; `|||||||` is what
    `merge.conflictStyle = diff3` writes for the base section and the pattern had never heard of
    it; and `=======` was matched unconditionally, so a seven-character Markdown setext heading
    rule -- correct Markdown, in a repository written in Markdown -- was a red build.

    `=======` is `False` here because it is not a marker **on its own**;
    `test_a_separator_counts_only_beside_a_marker_that_cannot_be_anything_else` is the other
    half of that answer and the one that keeps a real conflict caught.
    """
    assert bool(CONFLICT_MARKER_PATTERN.match(line)) is detected, shape


def test_a_separator_counts_only_beside_a_marker_that_cannot_be_anything_else() -> None:
    """The `=======` rule, driven on the two documents it has to tell apart.

    They are line-for-line identical on the separator itself, so nothing about that line
    decides it. What decides it is the company it keeps: `git` never writes a separator without
    an opening and a closing marker, and a setext underline never comes with either.
    """
    conflicted = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> other\n"
    markdown = "Summary\n=======\n\nProse under a setext heading.\n"

    assert conflict_marker_lines(conflicted) == [1, 3, 5]
    assert conflict_marker_lines(markdown) == []

    # A wider separator pattern would start reading ordinary rules as markers, so the widening
    # is refused here as well as in the file that is clean of them.
    assert conflict_marker_lines("Summary\n=========\n") == []
    assert conflict_marker_lines("<<<<<<<\na\n=========\nb\n>>>>>>>\n") == [1, 5]


def conflict_marker_lines(text: str) -> list[int]:
    """The 1-based lines of `text` that are committed conflict markers, and none that are not.

    The combination rule lives here rather than inside the tree walk so that it can be driven
    on documents built for the purpose -- the tree is (correctly) clean, so the walk alone
    cannot tell a rule that works from one that never fires. A mutation sweep made that
    concrete: counting the separator unconditionally, and widening it to `^={3,}$`, both left
    the walk green.

    A separator counts only in the company of a marker that cannot be anything else. On its own
    it is a Markdown setext heading rule, and this repository is written in Markdown.
    """
    numbered = list(enumerate(text.splitlines(), start=1))
    markers = [number for number, line in numbered if CONFLICT_MARKER_PATTERN.match(line)]
    if not markers:
        return []
    separators = [number for number, line in numbered if AMBIGUOUS_SEPARATOR.match(line)]
    return sorted(set(markers + separators))


def test_no_tracked_file_carries_a_committed_merge_conflict_marker() -> None:
    """The whole tracked tree, as an equality against the empty set.

    Deliberately not "no `.py` file" or "no source file". Both real occurrences were in
    `CHANGELOG.md`, which is exactly the kind of file a source-scoped audit skips and a
    human skims. The two other merges that day left markers in `pyproject.toml` and a test
    module -- one of which made the TOML unparseable, so `lint-imports` could not report
    `8 kept, 0 broken` at all, and that was silent until `ruff` refused to read its config.
    """
    root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")

    offenders: dict[str, list[int]] = {}
    for raw in tracked:
        if not raw:
            continue
        path = root / raw.decode("utf-8")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = conflict_marker_lines(text)
        if lines:
            offenders[raw.decode("utf-8")] = lines

    assert offenders == {}, f"committed merge conflict markers: {offenders}"
