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
        str(path.relative_to(ROOT))
        for path in src_root.rglob("*.py")
        if "pydantic_settings" in path.read_text(encoding="utf-8")
    )
    assert pydantic_settings_importers == ["src/openalpha_cn/config.py"]

    dotenv_package_importers = sorted(
        str(path.relative_to(ROOT))
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
    assert "brace-expansion: 5.0.8" in pnpm_workspace
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
    """
    nested = ROOT / ".claude" / "worktrees" / "publication-gate-probe"
    nested.parent.mkdir(parents=True, exist_ok=True)
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
        assert ".claude/worktrees/publication-gate-probe" in report["nested_checkouts"]
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
