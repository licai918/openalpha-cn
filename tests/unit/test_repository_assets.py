import hashlib
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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


def _step_run_command(job_block: str, keyword: str) -> str | None:
    """Return the first step `run:` command in `job_block` containing `keyword`."""
    for command in re.findall(r"^\s*run:\s*(.+)$", job_block, re.MULTILINE):
        if keyword in command:
            return command
    return None


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

    assert _step_run_command(python_job, "mypy") is not None, (
        "quality.yml's python job has no step invoking mypy"
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
    checked_paths = set(command.split())
    assert {"src", "scripts"} <= checked_paths, (
        f"mypy step does not type-check both src and scripts: {command!r}"
    )


def test_quality_workflow_python_job_has_a_coverage_gate_step() -> None:
    """The `python` job must run pytest with coverage collection enabled.

    Mutation: delete the "Test with coverage" step from quality.yml.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    assert _step_run_command(python_job, "--cov") is not None, (
        "quality.yml's python job has no step collecting coverage (--cov)"
    )


def test_quality_workflow_coverage_gate_threshold_is_at_least_80() -> None:
    """Whatever the CI coverage gate's threshold is, it must not regress below 80.

    Mutation: lower `--cov-fail-under=80` to any value under 80 in quality.yml.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    python_job = _workflow_job_block(workflow, "python")

    command = _step_run_command(python_job, "--cov-fail-under")
    assert command is not None, "quality.yml's python job has no --cov-fail-under gate"
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


def test_publication_gate_accepts_tracked_release_sources() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify_publication.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


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
