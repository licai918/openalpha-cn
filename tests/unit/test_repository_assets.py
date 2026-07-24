from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_exposes_self_host_and_chainlin_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "自托管 OpenAlpha CN" in readme
    assert "链邻涨停复盘策略软件" in readme
    assert "chainlin-desktop-v1.0.9" in readme
    assert "不构成任何投资建议" in readme


def test_wechat_banner_is_a_real_png_asset() -> None:
    banner = ROOT / "assets" / "brand" / "platform-wechat-banner.png"
    content = banner.read_bytes()

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(content) > 100_000


def test_public_repository_metadata_is_present() -> None:
    required = [
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        ".env.example",
    ]

    assert [name for name in required if not (ROOT / name).is_file()] == []
