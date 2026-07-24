import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_exposes_self_host_and_chainlin_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## ✨ 核心特性" in readme
    assert "### 🇨🇳 A 股原生证据体系" in readme
    assert "### 🤖 可验证的多智能体决策" in readme
    assert "### 🔁 同路径回放与归因" in readme
    assert "### 🔌 开放的数据与使用接口" in readme
    assert "### 🛡️ 可复现的工程底座" in readme
    assert "自托管 OpenAlpha CN" in readme
    assert "链邻涨停复盘策略软件" in readme
    assert "chainlin-desktop-v1.0.9" in readme
    assert "当前版本" not in readme
    assert "assets/brand/wechat-contact-qr.jpg" in readme


def test_wechat_contact_qr_is_the_owner_provided_jpeg() -> None:
    qr_image = ROOT / "assets" / "brand" / "wechat-contact-qr.jpg"
    content = qr_image.read_bytes()

    assert content.startswith(b"\xff\xd8\xff")
    assert hashlib.sha256(content).hexdigest().upper() == (
        "A619D0051CE6BD1B836C91C445B527F67B505940CB3092DF11DDC5CA93B06B15"
    )
    assert len(content) > 100_000


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
