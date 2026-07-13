# Ordo-Publish

**Ordo-Publish** is a local multi-platform publishing engine for Markdown-first Chinese-language creators. It prepares and distributes one article to WeChat, Zhihu, Toutiao, Jianshu, Yidian, Bilibili columns, and similar platforms with less repeated formatting and copy-paste work.

The project is now **terminal / CLI first**. The historical desktop shell has been removed, and this repository no longer targets a macOS / Windows desktop product. Some internal modules still use the `workbench` name because the terminal TUI and engine reuse those import, planning, preflight, and recovery utilities.

Automation boundary: Ordo assumes the user already has valid platform sessions. It handles article loading, content transformation, editor injection, draft or publish actions, result recording, and failure recovery. It does not promise automatic login, CAPTCHA handling, or anti-risk bypass behavior.

[中文说明](README.md)

## Why It Is Useful

- One Markdown source for multiple platforms
- WeChat themes are random by default, so articles do not all share the same visual style
- Covers are random by default from the cover pool
- Cover assignment supports WeChat, Zhihu, Toutiao, Yidian, and Bilibili columns
- Browser platforms reuse an Ordo-managed Chrome profile after first login
- Browser-platform tasks can choose `cover / AI declaration`: `auto` / `force_on` / `force_off`
- Toutiao publish mode supports scheduled publishing
- VPS-managed publishing supports a task queue, platform-limit deferral, and daemon-based auto resume
- Comment auto-reply remains an independent tool

## Included Tools

- `ordo`: Homebrew-style fullscreen terminal publishing entry
- `scripts/terminal_wizard.py`: source-tree compatibility launcher
- `publish.py`: unified multi-platform entry
- `ordo_engine/`: core local publishing engine
- `wechat_publisher.py`: WeChat publishing
- `zhihu_publisher.py`: Zhihu publishing
- `toutiao_publisher.py`: Toutiao publishing
- `jianshu_publisher.py`: Jianshu publishing
- `yidian_publisher.py`: Yidian publishing
- `bilibili_publisher.py`: Bilibili column publishing
- `ordo_worker.py`: VPS task queue, deferred publishing, daemon, and status report
- `scripts/format.py`: WeChat formatting, preview, and theme gallery
- `scripts/publish.py`: push formatted output to WeChat drafts
- `scripts/generate.py`: AI image generation
- `reply_comments.py`: WeChat comment auto-reply
- `live_cdp.mjs`: CDP browser bridge

## Install

Install the terminal command:

```bash
bash scripts/install_ordo.sh
```

or directly:

```bash
brew install --formula ./Formula/ordo.rb
```

For source-tree development:

```bash
python3 -m pip install -r requirements.txt
```

Chrome or Chromium is required for browser-platform publishing.

## Configuration

WeChat credentials:

```bash
cp secrets.env.example secrets.env
```

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
```

AI and toolchain:

```bash
cp config.example.json config.json
```

Managed browser session defaults:

- profile: `.ordo/browser-session/profile`
- debug port: `9333`
- state file: `.ordo/browser-session/state.json`

Override in `config.json`:

```json
{
  "browser_session": {
    "enabled": true,
    "remind_after_days": 5,
    "profile_dir": ".ordo/browser-session/profile",
    "debug_port": 9333
  }
}
```

## Random Themes And Covers

Default publishing behavior:

- WeChat theme: random by default; use `--wechat-theme-mode fixed --wechat-theme chinese` to pin it
- Cover: use only `assets/<article_id>/cover.png` from the `ordo-scribe` publication package; all six platforms use the same file
- The file must be PNG, embedded sRGB, exactly `2538x1080` at `2.35:1`, and no larger than `5 MB`
- The centered `1920x1080` region is the 16:9 safe area; the centered `1600x800` region is the core safe area; each `309 px` side margin is crop-safe, and the main subject stays at least `350 px` from either edge
- No title, logo, watermark, numbers, letters, or any visible text; never upscale a low-resolution image, and only crop/downsample a larger source
- Cover-capable platforms: WeChat, Zhihu, Toutiao, Yidian, Bilibili
- `--cover-mode force_off` skips cover setup
- `--cover-mode force_on` fails early when the canonical cover is missing or invalid
- `--cover PATH` accepts only a compliant file named `cover.png`

The legacy `covers/` pool is no longer the default publication path. Preflight rejects noncompliant covers and never stretches or swaps them automatically.

## Quick Start

Launch the TUI:

```bash
ordo
```

Publish one file:

```bash
python3 publish.py "./my_articles/example.md" --platform all --mode draft
```

Batch publish a directory:

```bash
python3 publish.py "./my_articles" --platform all --mode publish --continue-on-error
```

VPS-managed publishing:

```bash
python3 publish.py "./my_articles" --platform all --mode publish --remote vps --vps-host 203.0.113.10 --vps-user root --vps-path /root/ordo-publish
```

VPS task status and auto-resume:

```bash
.venv/bin/python ordo_worker.py status
.venv/bin/python ordo_worker.py daemon-status
.venv/bin/python ordo_worker.py resume
```

When a platform returns daily-limit text such as “publish limit reached” or “try again tomorrow”, the VPS worker records that platform task as `deferred_limit`, stores `next_run_at`, and lets the daemon resume it when due.

To skip articles already marked as published in `Ordo_Scribe_AI创作看板.md`, opt in explicitly:

```bash
python3 publish.py "./my_articles" --platform all --mode publish --skip-published
```

Pin a WeChat theme:

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode fixed --wechat-theme chinese
```

Force random WeChat themes:

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode random
```

Open theme gallery:

```bash
python3 scripts/format.py --input "./my_articles/example.md" --gallery
```

Dry-run comment replies:

```bash
python3 reply_comments.py --dry-run
```

## Browser Workflow

Recommended flow:

1. Let Ordo launch its managed browser profile, or open the same profile yourself
2. Log in to Zhihu, Toutiao, Jianshu, Yidian, and Bilibili there
3. Reuse the same profile afterward
4. Start with `--mode draft`
5. Switch to `--mode publish`

The main entry tries to connect to Chrome, open missing tabs, reuse bound targets, run preflight checks, use the publication-package cover first, fall back to random covers for supported platforms, and then run each platform adapter.

Smoke records:

- `docs/manual-validation/2026-03-28-browser-smoke.md`
- `docs/manual-validation/2026-03-28-browser-session.md`
- `docs/manual-validation/2026-03-28-functional-freeze-checklist.md`

## Structured Results

Each platform step prints one `[META]` JSON line with `article_id`, `theme_name`, `template_mode`, `cover_path`, `platform`, `status`, `error_type`, `current_url`, `page_state`, and `smoke_step`.

`publish_records.csv` writes the same fields. Older 8-column CSV files migrate on first append.

## VPS Mode

VPS mode keeps publishing traffic on the VPS IP and is the recommended path for production use.

Common commands:

```bash
python3 ordo_worker.py start-browser --xvfb
python3 ordo_worker.py run-job /root/ordo-publish/data/inbox/bundle.zip
python3 ordo_worker.py daemon
python3 ordo_worker.py status
```

Deployment helper:

```bash
bash scripts/deploy_vps.sh
```

## Not Done / Not Promised

- Jianshu is experimental because real access may be blocked by `403 Forbidden` or platform risk controls
- Local `publish.py` mode records CSV results but does not auto-resume the next day; automatic deferral belongs to the VPS job queue
- Product-grade web console, desktop app, multi-account hosting, and queue concurrency are not implemented
- Automatic login, CAPTCHA/slider handling, and risk-control bypass are out of scope
- Secret and local-data hardening: `secrets.env`, `config.json`, and `publish_records.csv` are still local plaintext
- Long soak and batch stress testing with real logged-in accounts still need more time

## Useful CDP Commands

```bash
node live_cdp.mjs list
node live_cdp.mjs warmall
node live_cdp.mjs eval <target> "document.title"
node live_cdp.mjs pastehtml <target> "<p>Hello</p>"
node live_cdp.mjs setfile <target> "<css-selector>" "/path/to/local/file"
node live_cdp.mjs snap <target>
node live_cdp.mjs stop
```

## Disclaimer

This project is for content workflow automation and technical research. Platform review, risk control, login, and API behavior can change at any time. Use it at your own discretion.
