import hashlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
        assert "五脑区导航" in content

    assert "链邻数据接口 API" in combined_content
    assert "规划目标" in combined_content
    assert "Tushare" not in combined_content
    assert "AKShare" not in combined_content


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
