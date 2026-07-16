from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chinese_monitor_examples_are_portable():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "monitor_publish.py --once --watch-dir /path/to/polished" in readme
    assert "monitor_publish.py --daemon --watch-dir /path/to/polished" in readme


def test_readmes_require_wechat_vps_fixed_ip():
    chinese = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README_EN.md").read_text(encoding="utf-8")

    assert "微信公众号必须通过 VPS 固定公网 IP" in chinese
    assert "本机不得直接调用微信 API" in chinese
    assert "WeChat must use the VPS fixed public IP" in english
    assert "must never call the WeChat API directly" in english


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
