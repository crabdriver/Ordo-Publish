from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "README_EN.md",
    ROOT / "docs" / "manual-validation" / "local-publish-engine-phase1.md",
)


def test_publisher_docs_share_the_canonical_cover_contract():
    required = (
        "cover.png",
        "PNG",
        "sRGB",
        "2538x1080",
        "2.35:1",
        "5 MB",
        "1920x1080",
        "1600x800",
        "309 px",
        "350 px",
    )

    for path in CONTRACT_FILES:
        text = path.read_text(encoding="utf-8")
        for token in required:
            assert token in text, f"{path.name} missing {token}"


def test_publisher_docs_forbid_text_upscaling_and_per_platform_images():
    for path in CONTRACT_FILES:
        text = path.read_text(encoding="utf-8")
        assert "任何可见文字" in text or "any visible text" in text
        assert "禁止放大" in text or "never upscale" in text
        assert "同一张" in text or "same file" in text
