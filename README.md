# Ordo-Publish

**Ordo-Publish** 是 Ordo Creator Suite 中的本地多平台自动发布引擎。它以 Markdown 为单一内容源，把同一篇文章整理并分发到微信、知乎、头条号、简书、一点号、B站专栏等平台，减少重复排版、复制粘贴和人工切换页面。

项目当前方向是 **终端 / CLI 优先**。历史桌面端壳已移除，不再以 macOS / Windows 桌面产品为目标。仓库仍保留部分内部 `workbench` 命名模块，因为终端 TUI 和发布引擎还复用这些导入、规划、预检和结果恢复能力。

自动化边界：默认依赖用户已有平台登录态，系统负责文章装载、内容转换、平台注入、草稿或发布、结果记录和失败恢复；不承诺自动登录、验证码处理或风控绕过。

[English](README_EN.md)

## 为什么用它

- 一次写作，多平台分发
- 微信主题默认随机分配，让每篇文章排版更有变化
- 封面默认从封面池随机分配；支持微信、知乎、头条号、一点号、B站专栏
- 浏览器平台复用 Ordo 托管浏览器会话，首次登录一次后长期复用
- 浏览器平台可设置 `封面 / AI 声明`：`auto` / `force_on` / `force_off`
- 头条号 `publish` 模式支持任务级定时发布时间，成功后回传 `scheduled`
- VPS 托管发布支持任务队列、平台限额延期、daemon 到期自动补发
- 评论自动回复可独立运行，不强绑定主发布流程

## 当前支持

- `ordo`: Homebrew 风格全屏终端发布入口，当前推荐主入口
- `scripts/terminal_wizard.py`: 源码仓库内的兼容启动脚本
- `publish.py`: 多平台统一入口
- `ordo_engine/`: 本地发布引擎核心包
- `wechat_publisher.py`: 微信发布
- `zhihu_publisher.py`: 知乎发布
- `toutiao_publisher.py`: 头条号发布
- `jianshu_publisher.py`: 简书发布
- `yidian_publisher.py`: 一点号发布
- `bilibili_publisher.py`: B站专栏发布
- `ordo_worker.py`: VPS 任务队列、自动延期补发和状态查询
- `scripts/format.py`: 微信排版、主题画廊、预览输出
- `scripts/publish.py`: 基于排版结果推送微信草稿
- `scripts/generate.py`: AI 生图
- `reply_comments.py`: 微信评论自动回复入口
- `live_cdp.mjs`: CDP 浏览器桥接

## 安装

终端版安装：

```bash
bash scripts/install_ordo.sh
```

或直接：

```bash
brew install --formula ./Formula/ordo.rb
```

源码开发 / 调试：

```bash
python3 -m pip install -r requirements.txt
```

还需要本机安装 Chrome 或 Chromium。浏览器平台发布前需要启用或让 Ordo 拉起远程调试浏览器。

## 配置

微信凭据：

```bash
cp secrets.env.example secrets.env
```

```env
WECHAT_APPID=your_appid
WECHAT_SECRET=your_secret
WECHAT_AUTHOR=your_author_name
```

AI 与工具链：

```bash
cp config.example.json config.json
```

按需填写：

- `settings.base_url`
- `settings.model`
- `secrets.api_key`
- `ai.url`
- `ai.api_key`
- `ai.model`
- `vault_root`

托管浏览器会话默认配置：

- 资料目录：`.ordo/browser-session/profile`
- 调试端口：`9333`
- 状态文件：`.ordo/browser-session/state.json`

可在 `config.json` 中覆盖：

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

## 随机主题与封面

默认发布行为：

- 微信主题：默认随机；如需固定，使用 `--wechat-theme-mode fixed --wechat-theme chinese`
- 封面：默认从 `covers/` 封面池随机分配，并尽量避开最近使用记录
- 支持封面的目标平台：微信、知乎、头条号、一点号、B站专栏
- `--cover-mode force_off` 会跳过封面设置
- `--cover-mode force_on` 会在缺少可用封面时尽早失败
- `--cover PATH` 会对本轮任务使用指定封面

封面池目录可在 `config.json` 里设置：

```json
{
  "assignment": {
    "cover_dir": "covers",
    "cover_repeat_window": 8
  }
}
```

## 快速开始

启动终端 TUI：

```bash
ordo
```

终端版会带出这些配置项：

- 文章来源目录或单文件
- 目标平台
- `draft / publish`
- 封面策略
- AI 声明策略
- 封面目录覆盖
- 是否在单个平台失败后继续
- 是否保存本次配置为默认值

源码内直接发布单篇：

```bash
python3 publish.py "./my_articles/example.md" --platform all --mode draft
```

批量发布目录：

```bash
python3 publish.py "./my_articles" --platform all --mode publish --continue-on-error
```

VPS 托管发布：

```bash
python3 publish.py "./my_articles" --platform all --mode publish --remote vps --vps-host 203.0.113.10 --vps-user root --vps-path /root/ordo-publish
```

VPS 任务状态与自动补发：

```bash
.venv/bin/python ordo_worker.py status
.venv/bin/python ordo_worker.py daemon-status
.venv/bin/python ordo_worker.py resume
```

当平台返回“发布上限 / 请明天再来”等日限额提示时，VPS worker 会把对应平台任务标记为 `deferred_limit`，写入 `next_run_at`，由 daemon 到点自动续跑。

如需按 `Ordo_Scribe_AI创作看板.md` 跳过已发表文章，显式加：

```bash
python3 publish.py "./my_articles" --platform all --mode publish --skip-published
```

固定微信主题：

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode fixed --wechat-theme chinese
```

显式随机微信主题：

```bash
python3 publish.py "./my_articles" --platform wechat --mode draft --wechat-theme-mode random
```

打开主题画廊：

```bash
python3 scripts/format.py --input "./my_articles/example.md" --gallery
```

评论自动回复：

```bash
python3 reply_comments.py --dry-run
```

## 浏览器平台工作流

推荐顺序：

1. 首次运行时，让 Ordo 自动拉起托管浏览器，或手动打开同一套托管 profile
2. 在托管浏览器里登录知乎、头条号、简书、一点号、B站
3. 后续复用这套资料目录
4. 先用 `--mode draft` 试跑
5. 再切到 `--mode publish`

主入口会：

- 自动连接浏览器
- 自动补开缺失平台标签页
- 复用固定页面目标
- 在正式执行前做预检
- 从本地封面池为支持平台随机分配封面
- 优先连接 Ordo 托管浏览器实例，再回退到现有系统 Chrome / `DevToolsActivePort`

真实 smoke 与功能冻结记录：

- `docs/manual-validation/2026-03-28-browser-smoke.md`
- `docs/manual-validation/2026-03-28-browser-session.md`
- `docs/manual-validation/2026-03-28-functional-freeze-checklist.md`

## 结构化结果

每条平台执行结束后，除普通日志外会输出一行 `[META]` 前缀 JSON，字段包括：`article_id`、`theme_name`、`template_mode`、`cover_path`、`platform`、`status`、`error_type`、`current_url`、`page_state`、`smoke_step`。

`publish_records.csv` 会写入一致列。旧 8 列 CSV 首次追加时会自动迁移为扩展表头。

## VPS 托管模式

VPS 模式用于固定从 VPS IP 执行发布，尤其适合微信和需要远端稳定登录态的平台。

常用命令：

```bash
python3 ordo_worker.py start-browser --xvfb
python3 ordo_worker.py run-job /root/ordo-publish/data/inbox/bundle.zip
python3 ordo_worker.py daemon
python3 ordo_worker.py status
```

部署辅助脚本：

```bash
bash scripts/deploy_vps.sh
```

## 当前仍未完成 / 不承诺

- 简书当前受 `403 Forbidden` 或平台风控影响，发布能力按实验性处理
- 普通本地 `publish.py` 模式只记录 CSV，不自动次日补发；自动补发属于 VPS job 队列能力
- 产品级 Web 控制台、桌面 App、多账号托管、队列并发尚未实现
- 自动登录、验证码/滑块处理、风控绕过不属于项目承诺
- 密钥与本地数据安全：`secrets.env`、`config.json`、`publish_records.csv` 仍是本地明文存储
- 长时间 soak / 批量压测：真实账号、真实登录态、长周期稳定性仍需继续积累

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

## 免责声明

本项目仅用于内容工作流自动化与技术研究。不同平台的审核、风控、登录和接口规则可能随时变化，请自行评估和承担使用风险。
