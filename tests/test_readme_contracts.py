from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chinese_monitor_examples_are_portable():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "monitor_publish.py --once --watch-dir /path/to/polished" in readme
    assert "monitor_publish.py --daemon --watch-dir /path/to/polished" in readme


def test_english_readme_matches_local_runtime_contract():
    readme = (ROOT / "README_EN.md").read_text(encoding="utf-8")

    required = (
        ".ordo/automation-profile",
        "one browser context",
        "headless",
        "primary Chrome",
        "--watch-dir /path/to/polished",
        "run_id",
        ".ordo/publish.lock",
        "submitted_unverified",
        "--remote vps",
        "wechat_theme_mode=console",
        "patchright==1.61.2",
    )
    for phrase in required:
        assert phrase in readme

    forbidden = (
        ".ordo/browser-session/profile",
        "recommended path for production use",
        "## Useful CDP Commands",
        "Let Ordo launch its managed browser profile",
    )
    for phrase in forbidden:
        assert phrase not in readme
