# Ordo-Publish

**Ordo-Publish** is a locally orchestrated multi-platform publishing engine. Markdown is the single content source. WeChat must use the VPS fixed public IP for its official API; Zhihu, Toutiao, Jianshu, Yidian, and Bilibili columns use the repository-isolated local browser. The local machine must never call the WeChat API directly.

The project is terminal / CLI first. Automation loads content, injects it into platforms, saves drafts or publishes, records typed outcomes, and supports safe recovery. It does not automate login, CAPTCHAs, or risk-control bypasses.

[中文说明](README.md)

## Safety Boundary

- Local browser jobs use only `.ordo/automation-profile`; they never read the user's primary Chrome profile.
- Headless is the default. One run creates one browser context, with one page per browser platform.
- Local jobs do not scan `DevToolsActivePort`, connect through CDP, or fall back to primary Chrome or another normal browser session.
- An uninitialized profile or expired login stops the run. It never triggers a browser fallback.
- `.ordo/publish.lock` prevents overlapping runs and profile contention.
- WeChat always goes through the dedicated SSH/SCP adapter and VPS worker. Missing VPS configuration blocks the task; there is no local fallback.
- `--remote vps` controls emergency whole-batch hosting only. It does not change the VPS-only WeChat rule.

## Entry Points

- `ordo`: terminal TUI
- `scripts/terminal_wizard.py`: source-tree compatibility launcher
- `publish.py`: unified single-file or directory entry
- `scripts/monitor_publish.py`: local scheduled-run entry
- `wechat_publisher.py`: WeChat official-API publisher for VPS workers only
- `ordo_worker.py`: manual VPS emergency queue tool

Supported platforms: WeChat, Zhihu, Toutiao, Jianshu, Yidian, and Bilibili columns. Jianshu remains experimental because `403 Forbidden` and platform risk controls may block access.

## Install

Python 3.12+:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The project declares one browser runtime: `patchright==1.61.2`. Install Patchright Chromium only when the machine has no usable Chrome / Chromium:

```bash
.venv/bin/python -m patchright install chromium
```

Install the terminal command if wanted:

```bash
bash scripts/install_ordo.sh
```

or:

```bash
brew install --formula ./Formula/ordo.rb
```

## Configuration

Copy the credential template:

```bash
cp secrets.env.example secrets.env
```

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
VPS_IP=your_fixed_public_ip
VPS_USER=root
VPS_PATH=/root/ordo-publish
```

Optional project configuration:

```bash
cp config.example.json config.json
```

`config.json` is local configuration, not a security-default source. A fresh clone defaults to local standalone, headless, and the isolated profile. `wechat_theme_mode=console` is disabled; use `fixed` or `random`:

```bash
python3 publish.py article.md --platform wechat --mode draft \
  --wechat-theme-mode fixed --wechat-theme chinese
```

## First Login

Before the first browser-platform run, explicitly open a temporary headed isolated browser:

```bash
python3 publish.py --bootstrap-browser --platform zhihu,toutiao,jianshu,yidian,bilibili
```

This command opens only `.ordo/automation-profile`. Log in, then enter `YES` at the terminal prompt. Later automation reuses that profile in a headless context. Rerun bootstrap after login expires. Automatic publishing never launches normal Chrome.

WeChat uses its API from the VPS worker and needs no browser login. Direct local execution of `wechat_publisher.py` is rejected.

## Manual Local Publishing

Save drafts:

```bash
python3 publish.py article.md --platform all --mode draft
```

Publish:

```bash
python3 publish.py article.md --platform all --mode publish --continue-on-error
```

Local execution is the default for browser platforms, so these commands are equivalent:

```bash
python3 publish.py article.md --platform zhihu --mode publish
python3 publish.py article.md --platform zhihu --mode publish --remote local
```

`limit_reached`, `submitted_unverified`, `unknown`, and expired login are not success. They return nonzero and preserve state to prevent automatic resubmission. Only a mode-matching terminal outcome succeeds: `draft_only` / `draft_saved` for draft mode, `published` / `scheduled` for publish mode, and explicit `skipped_existing` for a skip.

## Local Scheduled Publishing

One portable scan:

```bash
.venv/bin/python scripts/monitor_publish.py --once --watch-dir /path/to/polished --template-theme sspai
```

One article:

```bash
.venv/bin/python scripts/monitor_publish.py --article /path/to/article.md --template-theme sspai
```

Daemon scan:

```bash
.venv/bin/python scripts/monitor_publish.py --daemon --watch-dir /path/to/polished --interval 300 --template-theme sspai
```

Each pending article uses at most two groups:

1. One WeChat VPS API `draft` group, with no browser. The local machine only packages and transfers files over SSH/SCP.
2. One `publish` group containing every pending browser platform, sharing one browser context.

Each group gets its own `run_id`; only matching `publish_records.csv` rows count for that group. `.ordo/auto_publish_state.json` and `publish_records.csv` jointly enforce idempotency. `[INFO] 没有未处理文章` is a safe no-op.

Monitor reports five typed categories:

- success: explicit terminal outcome and return code 0
- skipped: `skipped_existing` or an existing explicit terminal outcome
- rate-limited: `limit_reached` / `rate_limited`, retried by a later scheduled run
- pending verification: `submitted_unverified`, requiring human review and blocking resubmission
- failed: all other errors, missing records, and nonzero terminal mismatches

`--force` explicitly resends and is high risk. Use it only for narrow recovery after human verification, never for normal scheduled runs.

## Canonical Cover

An `ordo-scribe` publication package uses only `assets/<article_id>/cover.png`; all six platforms reuse the same file:

- PNG, embedded sRGB, exactly `2538x1080` at `2.35:1`, no larger than `5 MB`
- centered `1920x1080` 16:9 safe area and centered approximately `1600x800` core safe area
- each `309 px` side margin contains crop-safe background only; main subject stays at least `350 px` from either edge
- no title, logo, watermark, letters, numbers, or any visible text
- never upscale low-resolution images; crop and downsample only from a larger source

`--cover-mode force_off` skips the cover. `force_on` fails if the canonical cover is absent or invalid. `--cover PATH` accepts only a compliant file named `cover.png`.

## Structured Results

Each platform emits `[META]` JSON and writes `publish_records.csv`. Key fields include:

- `run_id`, `article`, `article_id`, `platform`, `mode`
- `status`, `error_type`, `returncode`
- `theme_name`, `template_mode`, `cover_path`
- `current_url`, `page_state`, `smoke_step`

Outcome handling trusts typed status and records for the current run. Process exit or generic log text alone never proves publication success.

## Manual VPS Emergency Mode

Whole-batch VPS hosting remains an emergency option and monitor never invokes it. It is separate from the dedicated WeChat VPS API route. Use whole-batch hosting only after confirming remote version, browser/CDP, and login state:

```bash
python3 publish.py article.md --platform all --mode publish \
  --remote vps --vps-host 203.0.113.10 --vps-user root \
  --vps-path /root/ordo-publish
```

Emergency worker commands:

```bash
.venv/bin/python ordo_worker.py status
.venv/bin/python ordo_worker.py daemon-status
.venv/bin/python ordo_worker.py resume
```

A remote failure never falls back to the user's primary browser. Repair the remote environment, verify it, then retry manually.

## Not Promised

- automatic login, CAPTCHA / slider handling, or risk-control bypass
- product-grade web console, desktop app, multi-account hosting, or concurrent queues
- permanent compatibility with live platform rules
- extra secret management for plaintext `secrets.env`, `config.json`, or `publish_records.csv`

## Disclaimer

This project is for content workflow automation and technical research. Platform review, risk controls, login behavior, and APIs may change. Evaluate account and publishing risk before use.
