# 🚀 Ordo-Publish

**Ordo-Publish** 是 Ordo Creator Suite 中的本地多平台自动发布引擎。它以 Markdown 为单一内容源，负责把同一篇文章整理并分发到微信、知乎、头条号、简书、一点号等平台，目标是减少重复登录、重复排版和重复复制粘贴。

当前仓库已经包含一个可运行的桌面工作台 MVP，同时仍保留本地 CLI 工作流；项目的正式演进方向是继续把这套本地发布内核打磨稳定，并最终封装成可在 `macOS` 和 `Windows` 上一键安装的原生桌面软件。

自动化边界目前定义为：默认依赖用户已经存在的登录态，系统负责文章装载、内容转换、平台注入、草稿或发布、结果记录和失败恢复；不承诺自动登录、验证码处理或风控绕过。

兼容性说明：当前仓库中的部分内部路径与缓存目录仍沿用 `.tiandidistribute/` 命名，以兼容现有工作流和历史数据；对外项目及命令名称为 **Ordo-Publish** (命令行调用为 `ordo`)。

[English](README_EN.md)

## 为什么用它

- 一次写作，多平台分发
- 微信有完整主题系统、画廊预览和本地 dry-run
- AI 封面默认优先启用，配置好后会先尝试生成，再回退到本地封面
- 浏览器平台默认复用 Ordo 托管浏览器会话，首次登录一次即可，并支持临近失效提醒
- 浏览器平台的本轮任务可显式选择 `封面 / AI 声明`：`auto` / `force_on` / `force_off`
- 头条号 `publish` 模式支持任务级定时发布时间，成功后会回传 `scheduled` 状态
- 评论自动回复可独立运行，不强绑定主发布流程
- 仓库已做公开化整理，不包含真实密钥、个人路径和本机调度配置

## 当前支持

- `ordo`: Homebrew 风格全屏终端发布入口，当前推荐主入口
- `scripts/terminal_wizard.py`: 源码仓库内的兼容启动脚本
- `publish.py`: 多平台统一入口
- `tiandi_engine/`: 本地发布引擎核心包，承载任务、配置、状态、结果、平台适配与 runner
- `wechat_publisher.py`: 微信主发布链路
- `zhihu_publisher.py`: 知乎发布
- `toutiao_publisher.py`: 头条号发布
- `jianshu_publisher.py`: 简书发布
- `yidian_publisher.py`: 一点号发布
- `scripts/format.py`: 微信排版、主题画廊、预览输出
- `scripts/publish.py`: 基于排版结果推送微信草稿
- `scripts/generate.py`: AI 生图
- `reply_comments.py`: 微信评论自动回复入口
- `themes/`: 主题库
- `templates/`: 预览模板
- `live_cdp.mjs`: CDP 浏览器桥接

## 项目结构

- `publish.py`: 对外 CLI 入口，当前主流程已通过 `tiandi_engine` 统一调度
- `tiandi_engine/`: 引擎内核，包含任务模型、配置模型、状态恢复、平台适配与统一 runner
- `wechat_publisher.py`: 微信兼容入口，保留现有 CLI 使用方式
- `scripts/`: 独立工具链
- `themes/`: 微信样式主题
- `templates/`: 本地预览与主题画廊模板
- `markdown_utils.py`: Markdown 公共处理逻辑

## 安装

如果你只是想安装并使用 `ordo` 命令，优先走上面的 Homebrew 终端版路径。

下面这条只适合在源码仓库里做开发或调试：

```bash
python3 -m pip install -r requirements.txt
```

还需要：

- 本机安装 Chrome 或 Chromium
- 浏览器平台使用前启用远程调试

## 配置

### 1. 微信主流程配置

```bash
cp secrets.env.example secrets.env
```

### 2. 浏览器托管会话配置

默认情况下，Ordo 会启用托管浏览器会话，并优先复用：

- 专用资料目录：`.tiandidistribute/browser-session/profile`
- 固定调试端口：`9333`
- 会话状态文件：`.tiandidistribute/browser-session/state.json`

如需覆盖，可在 `config.json` 里加入：

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

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
```

### 2. AI 与工具链配置

```bash
cp config.example.json config.json
```

需要按实际情况填写：

- `settings.base_url`: AI 生图接口地址
- `settings.model`: AI 生图模型
- `secrets.api_key`: AI 生图密钥
- `ai.url`: 评论回复模型接口地址
- `ai.api_key`: 评论回复密钥
- `ai.model`: 评论回复模型
- `vault_root`: 可选的素材根目录

### 3. AI 封面默认策略

默认策略是：

1. 优先尝试 AI 封面
2. AI 不可用时，回退到 `covers/cover_*.png`

如果你不想使用 AI 封面，可以在 `config.json` 里把：

```json
"cover": {
  "prefer_ai_first": false
}
```

## 快速开始

### Homebrew 风格终端版（当前推荐）

终端版现在是主线入口，适合在不打开 GUI 的情况下直接完成导入、预检、发布和失败续跑；桌面工作台暂时只保留 bug 修复与已有能力维护，不再承载新功能主线。

安装方式：

```bash
bash scripts/install_ordo.sh
```

或者直接：

```bash
brew install --formula ./Formula/ordo.rb
```

安装后启动：

```bash
ordo
```

当前终端版会以全屏 TUI 形式带出这些配置项：

- 文章来源目录或单文件
- 目标平台
- `draft / publish`
- 封面策略 `auto / force_on / force_off`
- AI 声明策略 `auto / force_on / force_off`
- 封面目录覆盖
- 是否在单个平台失败后继续
- 是否把本次配置保存为默认值

运行时目录与状态文件：

- 首次运行会把运行模板同步到 `~/Library/Application Support/com.ordo.cli/runtime/repo`
- 微信凭据保存在该目录下的 `secrets.env`
- 终端默认配置写入该目录下 `config.json` 的 `terminal_wizard.defaults`
- 计划、结果、续跑队列继续写入该目录下的 `.tiandidistribute/`
- 如需改目录，可设置环境变量 `ORDO_HOME`

遇到问题时，终端版会直接输出：

- 预检阻塞项和建议动作
- 逐篇、逐平台执行状态
- 自动生成的续跑队列路径
- 如果任务被阻塞或部分失败，会直接提示下一步终端命令：重新执行 `ordo`

更多细节见 `docs/terminal-wizard.md`。

### 桌面工作台 MVP

桌面工作台位于 `desktop/`，采用 `Tauri + Rust` 桌面壳，底层继续调用仓库根目录下的 Python 发布引擎。

首次启动：

```bash
cd desktop
npm install
npm run tauri:dev
```

如果你的 Python 不在 `python3`，可在启动前指定：

```bash
export ORDO_PYTHON=/path/to/python
cd desktop
npm run tauri:dev
```

桌面工作台当前已打通的主链路：

- 顶部 `设置` 弹窗可直接填写微信 `AppID` / `Secret` / `Author`
- 微信配置状态会在顶部即时显示，未配置时会阻止微信发布并给出明确提示
- 粘贴 / 单文件 / 文件夹导入，当前支持 `Markdown` / `TXT` / `DOCX`
- 主题池 / 封面池发现
- 逐篇模板覆盖与非微信封面覆盖
- 调用 Python bridge 创建发布计划
- `遇错继续` 开关会透传到引擎侧发布计划
- 本轮任务级 `封面 / AI 声明 / 头条号定时发布时间` 会透传到对应平台脚本与结果对象
- 失败后可切换到“仅重试失败项”的最小续跑计划，即使失败项不是 `retryable` 也能恢复成新的最小计划
- 会保存最近一次工作台计划与结果快照，桌面重开后可恢复上次任务或上次失败项
- 如果最近计划里的 staged Markdown 已缺失，恢复按钮会禁用并明确提示需要重新规划
- 顶部会显示当前实际 `Repo Root` / `Python` 路径，定位 `ORDO_REPO_ROOT` / `ORDO_PYTHON` 更直接
- 发起发布并实时回看结构化日志
- 发布后可直接查看平台级结果详情与失败摘要，登录失效 / 环境未就绪 / 页面结构变动会显示更明确的提示
- 读取最近发布记录与最近 session 快照
- `config.json` 解析失败会作为显式 warning 暴露到桌面资源提示与 CLI 预检，而不是静默降级
- 顶部状态区会并列显示微信状态和浏览器会话状态；当前是托管模式、回退系统 Chrome、临近失效还是需要重新登录，都会直接展示

封面接入补充说明：

- 非微信平台默认从仓库根目录 `covers/` 读取本地封面池
- 如果你想改目录，可在 `config.json` 里设置 `assignment.cover_dir`
- 当前工作台会在封面池不可用时直接提示目标目录，而不是静默跳过

发布选项补充说明：

- `封面: 自动` 表示沿用当前平台默认逻辑
- `封面: 强制开启` 会在规划或预检阶段尽早要求可用封面资源
- `封面: 关闭` 会跳过封面设置；但一点号在直接发布模式下仍可能回退为平台默认封面，工作台会明确提示
- `AI 声明: 关闭` 会让知乎 / 头条号 / 一点号跳过声明设置
- `AI 声明: 强制开启` 会继续把平台声明视为必达项，失败时直接暴露错误
- `头条号定时发布时间` 仅在 `头条号 + publish` 时展示，格式为 `YYYY-MM-DDTHH:MM`
- 头条号若成功设置预约发布时间，平台结果会记为 `scheduled`，而不是伪装成普通 `published`

推荐首次使用顺序：

1. 打开桌面工作台
2. 点击顶部 `设置`
3. 填写微信 `AppID`、`Secret`、`Author`
4. 保存后再开始导入文章并发布

### 桌面独立打包

当前桌面壳已经支持构建内嵌最小 Python 与 Node 运行时的独立 `.app`：

```bash
cd desktop
npm run tauri:build
```

说明：

- `tauri:build` 之前会自动执行 `npm run prepare:runtime`，将精简后的 Python 和 Node 运行时打包到 `desktop/runtime-dist/ordo-runtime`。
- 构建出的 `.app` 在首次启动时，会自动将内置的 `ordo-runtime` 解压到用户的 `~/Library/Application Support/com.ordo.workbench/ordo-runtime` 目录下。
- 这是一个**真正独立的桌面应用**，不再依赖你本机的 Python 环境或源码仓库路径。
- 如果你是开发者，希望在开发模式下指向本地源码仓库，可在启动前设置环境变量：

```bash
export ORDO_REPO_ROOT=/path/to/tiandidistribute
export ORDO_PYTHON=/path/to/python
cd desktop
npm run tauri:dev
```

### 微信本地预览

```bash
python3 wechat_publisher.py "./my_articles/example.md" --dry-run --theme chinese
```

### 发布单篇到多个平台

```bash
python3 publish.py "./my_articles/example.md" --platform all --mode draft
```

### 批量发布目录

```bash
python3 publish.py "./my_articles" --platform all --mode publish --continue-on-error
```

### 指定微信主题模式

固定主题：

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode fixed --wechat-theme chinese
```

随机主题：

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode random
```

交互选择：

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode prompt
```

### 打开主题画廊

```bash
python3 scripts/format.py --input "./my_articles/example.md" --gallery
```

### 从排版结果推送微信草稿

```bash
python3 scripts/publish.py --input "./my_articles/example.md" --theme newspaper --cover "./covers/cover_01.png"
```

### 评论自动回复

```bash
python3 reply_comments.py --dry-run
```

## 浏览器平台工作流

推荐做法：

1. 首次运行时，让 Ordo 自动拉起托管浏览器，或手动打开同一套托管 profile
2. 在这套托管浏览器里登录知乎、头条号、简书、一点号
3. 后续继续复用这套资料目录，不必每次重新授权
4. 先用 `--mode draft` 试跑
5. 再切 `--mode publish`

真实 smoke 与功能冻结记录建议直接参考：

- `docs/manual-validation/2026-03-28-browser-smoke.md`
- `docs/manual-validation/2026-03-28-functional-freeze-checklist.md`

主入口默认会：

- 自动连接浏览器
- 自动补开缺失平台标签页
- 自动预热工作台标签页
- 复用固定 workbench
- 在正式执行前做预检
- 从本地封面池为**非微信平台**自动分配封面（与微信的 `covers/cover_*.png` / AI 封面策略相互独立，详见 `tiandi_engine` 配置）
- 优先连接 Ordo 托管浏览器实例，再回退到现有系统 Chrome / `DevToolsActivePort` 线索

托管浏览器会话补充说明：

- 第一次需要在 Ordo 托管的浏览器资料目录里完成登录；之后默认长期复用
- 最近一次健康确认时间会写入 `.tiandidistribute/browser-session/state.json`
- 如果会话长时间未重新校验，桌面工作台会给出“临近失效”提醒
- 如果预检已经落到登录页或验证码态，桌面工作台会明确提示需要重新登录

桌面工作台现在也会在执行区直接提示：

- 浏览器平台需要 Chrome 远程调试
- 浏览器平台默认依赖现有登录态
- 浏览器平台现在会额外预检“当前页面是否已停留在可写编辑器态”
- 这些平台的页面结构仍可能随站点改版而失效
- 当前 `config.json` 是否损坏

### 浏览器平台 Smoke 清单

最小 smoke 清单、真实发布结果与本轮功能冻结记录见：

- `docs/manual-validation/2026-03-28-browser-smoke.md`
- `docs/manual-validation/2026-03-28-browser-session.md`
- `docs/manual-validation/2026-03-28-functional-freeze-checklist.md`

最近一轮真实验收记录：`2026-03-28`，已在托管浏览器登录态下完成：

- 知乎正式发布
- 头条号正式发布
- 头条号定时发布
- 一点号正式发布
- 简书正式发布

说明：

- 微信当前仍按产品边界维持“写入草稿”而非正式发布
- 简书当前不走封面分配，也不走 AI 声明设置
- 头条号封面池现会自动跳过明显无效的占位图（例如 `1x1` 测试图）

### 结构化结果与 GUI 消费

每条平台执行结束后，除原有日志外会额外输出一行 `[META]` 前缀的 JSON，字段包括：`article_id`、`theme_name`、`template_mode`、`cover_path`、`platform`、`status`、`error_type`、`current_url`、`page_state`、`smoke_step`。后续桌面 GUI 或外部脚本可直接解析，无需再从散落的 stdout 推断状态。

`publish_records.csv` 会写入与上述一致的列。若你仍在使用旧版仅有 8 列的 CSV，首次按新逻辑追加记录时会**自动迁移**为扩展表头（建议在版本升级后备份该文件）。

### 浏览器侧封面能力（当前实现）

- **知乎、头条号、一点号**：发布脚本支持通过引擎传入的封面路径设置自定义封面（依赖远程调试 Chrome 与页面 DOM；平台改版可能导致不稳定）。
- **简书**：编辑器对封面上传限制较多，当前实现会在无法完成自定义封面时给出**明确诊断失败**，请勿理解为已全面支持简书自定义封面。

## 当前仍未完成

- 独立安装包体验：当前已实现内嵌 Python/Node 的独立 `.app`，但尚未提供 `DMG` 安装器或代码签名/公证（Notarization），分发时可能触发 macOS Gatekeeper 拦截。
- 正式 Windows 分发：当前明确以 `macOS` 为优先收口平台；Windows 浏览器适配、安装器、签名和分发流程仍在后续阶段。
- 产品级恢复：当前是“最近计划/结果快照 + 失败项续跑”的最小闭环，还不是完整断点续跑系统。
- 密钥与本地数据安全：`secrets.env`、`config.json`、`publish_records.csv` 仍属于本地明文存储，后续如要正式分发还需要单独的加密与隐私治理。
- 长时间 soak / 批量压测：当前已完成功能主线真实验收，但更长时段的稳定性压测与风控边界观察仍可继续补。

## 常用 CDP 命令

```bash
node live_cdp.mjs list
node live_cdp.mjs warmall
node live_cdp.mjs eval <target> "document.title"
node live_cdp.mjs pastehtml <target> "<p>Hello</p>"
node live_cdp.mjs setfile <target> "<css-selector>" "/path/to/local/file"
node live_cdp.mjs snap <target>
node live_cdp.mjs stop
```

## 适合谁

- 习惯用 Markdown 写作的内容创作者
- 想把公众号排版做得更稳定的人
- 需要跨平台分发但不想手工复制粘贴的人
- 想把内容工作流脚本化、自动化的人

## 免责声明

本项目仅用于内容工作流自动化与技术研究。不同平台的审核、风控、登录和接口规则可能随时变化，请自行评估和承担使用风险。
