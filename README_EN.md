# 🚀 Ordo-Publish

**Ordo-Publish** is a local multi-platform publishing engine of the Ordo Creator Suite for Chinese-language creators. It treats Markdown as the single source of truth, then prepares and distributes the same article to WeChat, Zhihu, Toutiao, Jianshu, Yidian, and similar platforms with far less repeated login, formatting, and copy-paste work.

The current repository already includes a runnable desktop workbench MVP on top of the local CLI and engine workflow, and the official direction remains to harden that core into a native desktop application for `macOS` and `Windows`.

The current automation boundary is explicit: `ordo` assumes the user already has valid platform login sessions, and the system handles article loading, content transformation, editor injection, draft or publish actions, result recording, and failure recovery. It does not promise automatic login, CAPTCHA handling, or anti-risk bypass behavior.

Compatibility note: some internal paths and cache directories still use `.tiandidistribute/` for backward compatibility with the existing workflow and stored state, while the public project is now **Ordo-Publish** (invoked via CLI as `ordo`).

[中文说明](README.md)

## Why It Is Useful

- One Markdown source for multiple platforms
- A full WeChat theme system with local preview and gallery mode
- AI cover generation is enabled by default when configured
- Browser platforms now default to an Ordo-managed browser session: log in once, then keep reusing it with expiry reminders
- Browser-platform tasks can now explicitly choose `cover / AI declaration` mode: `auto` / `force_on` / `force_off`
- Comment auto-reply stays as an independent tool instead of polluting the main publishing flow
- The repository has been cleaned for public release: no real keys, no personal paths, no local scheduler setup

## Included Tools

- `ordo`: Homebrew-style fullscreen terminal publishing entry, now the recommended primary entry
- `scripts/terminal_wizard.py`: compatibility launcher kept for source-tree usage
- `publish.py`: unified multi-platform entry
- `tiandi_engine/`: core local publishing engine package for tasks, config, state, results, adapters, and runner
- `wechat_publisher.py`: WeChat-focused publishing flow
- `zhihu_publisher.py`
- `toutiao_publisher.py`
- `jianshu_publisher.py`
- `yidian_publisher.py`
- `scripts/format.py`: formatting, preview, and theme gallery
- `scripts/publish.py`: publish formatted output to WeChat drafts
- `scripts/generate.py`: AI image generation
- `reply_comments.py`: WeChat comment auto-reply entry
- `themes/`: theme library
- `templates/`: preview templates
- `live_cdp.mjs`: CDP browser bridge

## Project Structure

- `publish.py`: public CLI entry, now routed through the shared `tiandi_engine` runner
- `tiandi_engine/`: engine core for task models, config, state recovery, platform adapters, and orchestration
- `wechat_publisher.py`: compatibility CLI entry for the WeChat publishing flow
- `scripts/`: standalone tools for formatting, WeChat draft publishing, image generation, and auxiliary workflows
- `themes/`: WeChat theme library
- `templates/`: local preview and gallery templates
- `markdown_utils.py`: shared Markdown transformation utilities

## Install

If you want the end-user `ordo` command, use the Homebrew-style terminal install path described below.

The command below is only for source-tree development or debugging:

```bash
python3 -m pip install -r requirements.txt
```

You also need Chrome or Chromium for browser-platform publishing.

## Configuration

### Browser managed session

By default, Ordo now prefers its managed browser session:

- profile directory: `.tiandidistribute/browser-session/profile`
- fixed debugging port: `9333`
- session state file: `.tiandidistribute/browser-session/state.json`

You can override it in `config.json`:

```json
{
  "browser_session": {
    "enabled": true,
    "remind_after_days": 5,
    "profile_dir": ".tiandidistribute/browser-session/profile",
    "debug_port": 9333
  }
}
```

### 1. Main WeChat publishing flow

```bash
cp secrets.env.example secrets.env
```

Fill:

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
```

### 2. AI and formatting toolchain

```bash
cp config.example.json config.json
```

Fill the real values for:

- `settings.base_url`
- `settings.model`
- `secrets.api_key`
- `ai.url`
- `ai.api_key`
- `ai.model`

### 3. AI cover default behavior

Default strategy:

1. Try AI cover generation first
2. Fallback to local `covers/cover_*.png`

If you want to disable AI-first behavior:

```json
"cover": {
  "prefer_ai_first": false
}
```

## Quick Start

### Homebrew-style Terminal TUI (Recommended)

The terminal flow is now the preferred primary entry. The desktop workbench remains in the repository, but new feature work is paused there and the focus moves to the terminal-first workflow.

Install it with:

```bash
bash scripts/install_ordo.sh
```

or directly:

```bash
brew install --formula ./Formula/ordo.rb
```

Then run:

```bash
ordo
```

The command launches a fullscreen TUI for article path, platform selection, `draft/publish`, cover mode, AI declaration mode, failure continuation, and default persistence.

On first launch, the command syncs its runtime template into `~/Library/Application Support/com.ordo.cli/runtime/repo`. Credentials stay in `secrets.env`, defaults stay in `config.json` under `terminal_wizard.defaults`, and runtime state continues under `.tiandidistribute/` inside that runtime repo. Use `ORDO_HOME` to override the base directory.

If preflight blocks the task or the run ends partially failed, the TUI prints the retry-queue path and tells you to re-run `ordo` using the saved defaults and the generated queue as your narrowing guide.

This terminal-first flow is currently macOS-first. Windows is only kept as a compatibility target for future follow-up, not as a full first-class delivery in this round.

### Desktop Workbench MVP

The desktop workbench lives in `desktop/` and uses a `Tauri + Rust` shell on top of the Python publishing engine in the repository root.

First start:

```bash
cd desktop
npm install
npm run tauri:dev
```

If your Python executable is not `python3`, set it before launch:

```bash
export ORDO_PYTHON=/path/to/python
cd desktop
npm run tauri:dev
```

Current desktop workbench coverage:

- visual WeChat settings (`AppID` / `Secret` / `Author`)
- top-level WeChat readiness status and blocking hint when credentials are missing
- paste / single-file / folder import, now covering `Markdown` / `TXT` / `DOCX`
- theme-pool and cover-pool discovery
- per-article theme override and non-WeChat cover override
- Python bridge planning and streaming publish execution
- `continue_on_error` passed through to publish planning
- task-level `cover / AI declaration` options passed into the Zhihu / Toutiao / Yidian scripts
- retry-only-failed-items flow after a failed run, even when the failed item itself is not marked `retryable`
- last-plan and last-result snapshots, so the desktop workbench can restore the latest task or latest failed subset after restart
- restore buttons are disabled with an explicit prompt when the staged Markdown files from the latest plan are missing
- the header now shows the effective `Repo Root` and `Python` path, making `ORDO_REPO_ROOT` / `ORDO_PYTHON` diagnosis much more direct
- structured result details and recent history refresh after publishing, with clearer hints for login loss, environment issues, and page-structure changes
- broken `config.json` is now surfaced as an explicit warning in the desktop workbench and CLI preflight, instead of silently degrading
- the header now shows both WeChat status and browser-session status, including managed mode, fallback mode, expiring-soon reminders, and relogin-required state

Cover-pool note:

- non-WeChat platforms read the default local cover pool from `covers/`
- you can override that path with `assignment.cover_dir` in `config.json`
- the desktop UI now shows an explicit target directory when the cover pool is missing or empty

Publish-option note:

- `Cover: auto` keeps the current platform-default behavior
- `Cover: force_on` asks for usable cover resources as early as planning / preflight
- `Cover: force_off` skips custom cover setup; Yidian may still fall back to the platform default cover in direct-publish mode, and the UI now says so explicitly
- `AI declaration: force_off` skips declaration setup on Zhihu / Toutiao / Yidian
- `AI declaration: force_on` keeps declaration as a required step and fails loudly when the site structure no longer matches

### Desktop Packaging Preview

The current desktop shell can already build a development preview bundle:

```bash
cd desktop
npm run tauri:build
```

If you want the built shell to point to an explicit engine checkout, set:

```bash
export ORDO_REPO_ROOT=/path/to/tiandidistribute
export ORDO_PYTHON=/path/to/python
cd desktop
npm run tauri:dev
```

Notes:

- the current bundle is best treated as a developer preview, not a fully standalone installer with an embedded Python engine
- `ORDO_REPO_ROOT` lets the desktop shell find `scripts/workbench_bridge.py` outside the original source launch layout
- if repository-root or Python resolution fails, the desktop shell now tells you how to set `ORDO_REPO_ROOT` / `ORDO_PYTHON`
- `tauri.conf.json` is still oriented toward `.app`-level output; Windows installer packaging remains a later step

Preview a WeChat article locally:

```bash
python3 wechat_publisher.py "./my_articles/example.md" --dry-run --theme chinese
```

Publish one file to all platforms:

```bash
python3 publish.py "./my_articles/example.md" --platform all --mode draft
```

Batch publish a directory:

```bash
python3 publish.py "./my_articles" --platform all --mode publish --continue-on-error
```

Open the theme gallery:

```bash
python3 scripts/format.py --input "./my_articles/example.md" --gallery
```

Publish through the formatting pipeline:

```bash
python3 scripts/publish.py --input "./my_articles/example.md" --theme newspaper --cover "./covers/cover_01.png"
```

Dry-run comment replies:

```bash
python3 reply_comments.py --dry-run
```

## Browser Workflow

Recommended workflow:

1. On first use, let Ordo launch its managed browser session, or open the same managed profile yourself
2. Log in to Zhihu, Toutiao, Jianshu, and Yidian in that managed session
3. Reuse that same profile afterward instead of re-authorizing each run
4. Start with `--mode draft`
4. Then switch to `--mode publish`
5. Record real smoke results against `docs/manual-validation/2026-03-28-browser-smoke.md`

The main entry tries to:

- connect to the existing browser session
- open missing platform tabs
- warm up the workbench tabs
- reuse the same bound targets
- run preflight checks before publishing
- assign covers for **non-WeChat** browser platforms from a local cover pool (separate from WeChat’s `covers/cover_*.png` / AI cover behavior; see `tiandi_engine` config)
- prefer the Ordo-managed browser instance first, then fall back to existing system Chrome / `DevToolsActivePort` hints if needed

Managed browser-session notes:

- the first login still happens in the managed browser profile, but that profile is then reused by default
- the latest health-check timestamps are stored in `.tiandidistribute/browser-session/state.json`
- if the session has not been revalidated for a while, the desktop workbench shows an expiring-soon reminder
- if preflight already lands on a login or verification page, the workbench explicitly tells you to log in again

The desktop workbench now also shows explicit execution-area hints for:

- Chrome remote debugging being required for browser platforms
- existing platform login sessions being required
- whether the current tab is already sitting on a writable editor page
- DOM-dependent browser automation still being vulnerable to site changes
- `config.json` parse failures

### Browser Smoke Checklist

The minimal smoke checklist and this round's validation record live at:

- `docs/manual-validation/2026-03-28-browser-smoke.md`
- `docs/manual-validation/2026-03-28-browser-session.md`

Latest real smoke entry-point attempt: `2026-03-28`, blocked before site-level draft save because no remote-debuggable Chrome tabs were available in the current environment.

### Structured results for GUI consumers

After each platform step, the CLI prints one JSON line prefixed with `[META]`, containing: `article_id`, `theme_name`, `template_mode`, `cover_path`, `platform`, `status`, `error_type`, `current_url`, `page_state`, and `smoke_step`. A future desktop GUI or automation can parse this without scraping unstructured logs.

`publish_records.csv` includes the same columns. If you still have an older 8-column file, the first append under the new logic **migrates** the file to the wider schema (back up the CSV before upgrading).

### Browser cover support (current behavior)

- **Zhihu, Toutiao, Yidian**: publisher scripts accept a cover path from the engine and attempt custom cover upload (DOM-dependent; site changes may break it).
- **Jianshu**: the editor is restrictive; when custom cover cannot be applied, the flow fails with an **explicit diagnostic**—do not read this as full Jianshu custom-cover support.

## Not Done Yet

- Standalone installer: the desktop bundle still depends on a local Python runtime and engine checkout
- Production Windows distribution: the current closing phase is explicitly `macOS` first; Windows browser adaptation, installer, signing, and distribution stay in a later phase
- Product-grade resume: the current flow is a minimal loop built from latest plan/result snapshots plus failed-item retry, not a full checkpoint-resume system
- Secret and local-data hardening: `secrets.env`, `config.json`, and `publish_records.csv` are still local engineering-style storage and need a dedicated security pass before wider distribution
- Real external smoke on logged-in accounts: the repository now includes a checklist plus one blocked real entry-point attempt, but a true authenticated draft-save pass still needs to be completed in the user's own browser environment
- Bundled browser runtime and UI redesign: both remain explicitly postponed; the current product line stays with managed system browsers and a small-footprint desktop shell

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

## Who It Is For

- creators who write in Markdown first
- people who care about stable WeChat formatting
- teams or individuals who publish the same content to multiple platforms
- builders who want a scriptable content workflow instead of a manual one

## Disclaimer

This project is for publishing workflow automation and technical experimentation. Platform rules, review policies, and login behavior may change at any time. Use it at your own risk.
