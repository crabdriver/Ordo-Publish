# Ordo-Publish

**Ordo-Publish** 是本地多平台发布引擎。Markdown 是单一内容源；微信走官方 API，知乎、头条号、简书、一点号、B 站专栏走仓库隔离浏览器。默认执行位置是本机，VPS 只保留为显式手工应急路径。

项目以终端 / CLI 为主。自动化负责内容装载、平台注入、草稿或发布、结果记录和失败恢复；不负责自动登录、验证码处理或风控绕过。

[English](README_EN.md)

## 安全边界

- 本地浏览器任务只用 `.ordo/automation-profile`，不读取用户主力 Chrome profile。
- 默认 headless；一次发布运行只创建一个 browser context，各浏览器平台复用此 context 的独立 page。
- 本地流程不连接主力 Chrome，不扫描 `DevToolsActivePort`，不回退 CDP 或普通 Chrome 会话。
- profile 未初始化或登录失效时直接停止。不会临时改用用户浏览器。
- `.ordo/publish.lock` 阻止两个发布任务重叠，避免重复投稿和 profile 争用。
- `--remote vps` 只在手工显式传入时启用；自动 monitor 和普通 CLI 默认本地。

## 支持入口

- `ordo`：终端 TUI
- `scripts/terminal_wizard.py`：源码仓库兼容入口
- `publish.py`：单篇或目录统一入口
- `scripts/monitor_publish.py`：本地定时任务入口
- `wechat_publisher.py`：微信公众号 API 发布
- `ordo_worker.py`：手工 VPS 应急队列工具

支持平台：微信公众号、知乎、头条号、简书、一点号、B 站专栏。简书可能受 `403 Forbidden` 或平台风控影响，仍按实验性能力处理。

## 安装

Python 3.12+：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

项目只声明一个浏览器自动化 runtime：`patchright==1.61.2`。机器没有可用 Chrome / Chromium 时，再安装 Patchright Chromium：

```bash
.venv/bin/python -m patchright install chromium
```

也可安装终端命令：

```bash
bash scripts/install_ordo.sh
```

或：

```bash
brew install --formula ./Formula/ordo.rb
```

## 配置

复制凭据模板：

```bash
cp secrets.env.example secrets.env
```

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
```

可选项目配置：

```bash
cp config.example.json config.json
```

`config.json` 是本地文件，不是安全默认值来源。fresh clone 默认使用本地 standalone、headless、隔离 profile。`wechat_theme_mode=console` 已禁用；使用 `fixed` 或 `random`。例如：

```bash
python3 publish.py article.md --platform wechat --mode draft \
  --wechat-theme-mode fixed --wechat-theme chinese
```

## 首次登录

浏览器平台第一次使用前，必须显式启动临时有头隔离浏览器：

```bash
python3 publish.py --bootstrap-browser --platform zhihu,toutiao,jianshu,yidian,bilibili
```

此命令只打开 `.ordo/automation-profile`。在临时浏览器完成登录后，按终端提示输入 `YES`；后续自动任务使用同一 profile 的 headless context。登录失效时重新运行此命令。自动发布不会自行拉起普通 Chrome。

微信使用 API，不需要浏览器登录。

## 手工本地发布

保存草稿：

```bash
python3 publish.py article.md --platform all --mode draft
```

正式发布：

```bash
python3 publish.py article.md --platform all --mode publish --continue-on-error
```

本地是默认值；以下两条等价：

```bash
python3 publish.py article.md --platform zhihu --mode publish
python3 publish.py article.md --platform zhihu --mode publish --remote local
```

`limit_reached`、`submitted_unverified`、`unknown`、登录失效都不是成功。命令返回非零并保留状态，避免自动重投。只有与 mode 匹配的明确终态才返回成功：draft 对应 `draft_only` / `draft_saved`，publish 对应 `published` / `scheduled`；`skipped_existing` 是明确跳过。

## 本地定时发布

单次扫描：

```bash
.venv/bin/python scripts/monitor_publish.py --once --template-theme sspai
```

指定单篇：

```bash
.venv/bin/python scripts/monitor_publish.py --article /path/to/article.md --template-theme sspai
```

循环扫描：

```bash
.venv/bin/python scripts/monitor_publish.py --daemon --interval 300 --template-theme sspai
```

每篇待处理文章最多拆成两组：

1. 微信 API `draft` 组，不启动浏览器。
2. 全部待处理浏览器平台组成一个 `publish` 组，共享一个隔离 browser context。

monitor 固定传 `--remote local`。每组命令生成独立 `run_id`，只读取该 `run_id` 对应的 `publish_records.csv` 行，防止旧记录或并发记录冒充本轮结果。`.ordo/auto_publish_state.json` 和 `publish_records.csv` 共同用于幂等判断；`[INFO] 没有未处理文章` 表示安全 no-op。

输出按五类汇总：

- 成功：明确终态且 return code 为 0
- 跳过：`skipped_existing` 或已有明确终态
- 限流：`limit_reached` / `rate_limited`，留待下次计划重试
- 待核验：`submitted_unverified`，禁止自动重投，需要人工核验
- 失败：其余错误、缺失记录或非零终态

`--force` 会显式重发，风险高，只用于人工确认后的窄范围恢复。正常定时任务不要使用。

## 统一封面

`ordo-scribe` 发布包只使用 `assets/<article_id>/cover.png`，六个平台复用同一张图：

- PNG、sRGB、精确 `2538x1080`、`2.35:1`、不超过 `5 MB`
- 中央 `1920x1080` 是 16:9 安全区，中央约 `1600x800` 是核心安全区
- 左右各 `309 px` 只放可裁切背景，核心主体距左右边缘至少 `350 px`
- 禁止标题、Logo、水印及任何可见文字
- 禁止放大低分辨率旧图；只从更大的高分辨率源图裁切和降采样

`--cover-mode force_off` 跳过封面；`force_on` 在缺少合格统一封面时失败。`--cover PATH` 只接受符合契约且文件名为 `cover.png` 的文件。

## 结构化结果

每个平台输出 `[META]` JSON，并写入 `publish_records.csv`。关键字段包括：

- `run_id`、`article`、`article_id`、`platform`、`mode`
- `status`、`error_type`、`returncode`
- `theme_name`、`template_mode`、`cover_path`
- `current_url`、`page_state`、`smoke_step`

状态判断只信 typed outcome 和与本轮匹配的记录，不以“进程结束”或普通日志文本冒充发布成功。

## 手工 VPS 应急模式

VPS 能力仍保留，但不是默认流程，也不由自动 monitor 触发。只在人工确认远端版本、浏览器/CDP、登录态都可用时显式执行：

```bash
python3 publish.py article.md --platform all --mode publish \
  --remote vps --vps-host 203.0.113.10 --vps-user root \
  --vps-path /root/ordo-publish
```

应急 worker 命令：

```bash
.venv/bin/python ordo_worker.py status
.venv/bin/python ordo_worker.py daemon-status
.venv/bin/python ordo_worker.py resume
```

远端路径失败不会回退本地主力浏览器。先修复远端环境，再人工重试。

## 不承诺

- 自动登录、验证码/滑块处理、风控绕过
- 产品级 Web 控制台、桌面 App、多账号托管、队列并发
- 真实账号平台规则永久稳定
- 明文 `secrets.env`、`config.json`、`publish_records.csv` 的额外密钥管理

## 免责声明

本项目仅用于内容工作流自动化与技术研究。不同平台审核、风控、登录和接口规则可能变化，请自行评估风险。
