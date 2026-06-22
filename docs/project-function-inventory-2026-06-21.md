# Ordo-Publish 功能清单（2026-06-21）

## 结论

项目主体已成熟到可以准备 GitHub 发布，但发布前要先做一次仓库清理和文档同步。核心发布链路、浏览器自动化、封面、模板、结果记录、VPS 队列、限额延期补发都有代码和测试；桌面端历史文件、未跟踪 worker 文件、README 旧描述、明文配置风险需要处理。

## 已实现

### 1. 多平台发布

支持平台：

- 微信公众号：草稿写入、封面、主题排版、VPS IP 发送约束。
- 知乎：浏览器注入、草稿/发布、封面、AI 声明。
- 头条号：草稿/发布、封面、AI 声明、定时发布。
- 一点号：草稿/发布、封面、AI 声明。
- B 站专栏：草稿/发布、封面。
- 简书：草稿/发布流程存在，但当前实测受 `403 Forbidden` 阻挡。

入口：

- `publish.py`：本地统一发布入口。
- `ordo` / `scripts/terminal_wizard.py`：终端 TUI 入口。
- 平台脚本：`wechat_publisher.py`、`zhihu_publisher.py`、`toutiao_publisher.py`、`jianshu_publisher.py`、`yidian_publisher.py`、`bilibili_publisher.py`。

### 2. VPS-First 发布与自动补发

已实现：

- `publish.py --remote vps`：本地打包文章和封面，上传 VPS，远端执行。
- `ordo_engine/runner/bundle.py`：生成任务包 `manifest.json`。
- `ordo_worker.py run-job`：VPS 解包并执行任务。
- `data/ordo_tasks.db`：记录每篇文章、每个平台任务状态。
- 平台日限额识别：返回 `limit_reached` 后写入 `deferred_limit`。
- 次日自动补发：写入 `next_run_at`，VPS daemon 到点自动 `resume`。
- `ordo_worker.py status`：查看已发、延期、失败、下次执行时间。
- `ordo_worker.py daemon-status`：查看 VPS daemon 状态。

状态字段：

- `published`
- `draft_saved`
- `scheduled`
- `deferred_limit`
- `failed`
- `pending`
- `running`

### 3. 浏览器自动化

已实现：

- `live_cdp.mjs`：CDP 桥接，支持列表、导航、点击、输入、截图、文件上传。
- 固定工作台标签页绑定。
- 自动打开缺失平台标签页。
- 自动预热平台页面。
- 托管浏览器配置：profile、debug port、状态文件。
- VPS 图形栈：Xvfb、x11vnc、noVNC、websockify。

限制：

- 不处理自动登录、验证码、滑块、风控绕过。
- 平台 DOM 改版会导致发布器失效，需要维护选择器。

### 4. 封面与模板

已实现：

- 微信主题随机/固定/控制台确认。
- 封面池随机分配。
- 封面重复窗口。
- 支持手动封面 `--cover`。
- 支持封面策略 `random / auto / force_on / force_off`。
- 支持平台：微信、知乎、头条号、一点号、B 站。
- 已修正横版封面优先与 macOS `._*` 元数据文件过滤。

### 5. AI 声明

已实现：

- 浏览器平台支持 `--ai-declaration-mode auto / force_on / force_off`。
- 文章内容包含 AI 相关关键词时可自动声明。
- 各平台按自身 UI 处理声明项。

### 6. 导入与预处理

已实现：

- Markdown / TXT 导入。
- DOCX 文本导入。
- PDF 文本导入（依赖 `pypdf`）。
- 图片 OCR 导入（依赖系统 `tesseract`）。
- 粘贴文本归一化。
- 标题提取、正文归一化、字数统计。

### 7. 微信排版与主题画廊

已实现：

- Markdown 转微信兼容 HTML。
- 内联样式处理。
- 主题 JSON。
- 主题画廊与预览 HTML。
- Callout、表格、代码块、脚注等基础排版。

### 8. 结果记录和恢复

已实现：

- `publish_records.csv`：记录平台执行结果。
- `[META]` 结构化输出。
- 旧 CSV 自动迁移。
- TUI session 状态。
- 操作矩阵 `operations_matrix`：可构建失败项重试队列。
- VPS SQLite 任务库：正式支持延期补发。

### 9. 评论自动回复

已实现：

- 微信公众号评论扫描。
- AI 生成回复。
- `--dry-run`。
- 回复状态记录，避免重复回复。

入口：

- `reply_comments.py`
- `scripts/comment_reply.py`

### 10. AI 生图

已实现：

- `scripts/generate.py`。
- 支持 prompt frontmatter。
- 支持第三方网关兼容 Google `generateContent` API。
- 支持图片输出格式转换。

### 11. 测试覆盖

已有测试覆盖：

- 平台适配器契约。
- VPS 任务持久化、daemon、延期补发。
- 封面和模板分配。
- 预检。
- 发布结果分类。
- TUI/terminal service。
- live CDP resolver。
- B 站发布器部分行为。
- 格式化画廊。

## 半实现 / 需要实机继续验证

### 1. 简书

代码存在，但当前真实访问是 `403 Forbidden`。发布前文档应标注为“实验性/受平台访问限制”。

### 2. VPS 发布部署

已能运行，daemon 已启动过，测试通过。还缺：

- 标准部署文档。
- systemd 服务文件。
- 开机自启动。
- 远端 repo / runtime 路径统一。

### 3. 断点续跑

VPS 队列支持任务级恢复；本地 `publish.py` 普通模式仍只是 CSV 记录，不自动恢复。

推荐发布口径：

- “VPS 模式支持任务级延期补发和恢复。”
- 不说“所有模式都有完整 checkpoint”。

### 4. GitHub 发布包装

Homebrew Formula 当前没有安装这些新增/相关文件：

- `ordo_worker.py`
- `bilibili_publisher.py`
- `ordo_engine/runner/db.py`
- `ordo_engine/runner/bundle.py`
- `ordo_engine/runner/executor.py`
- `ordo_engine/runner/version.py`

发布前要更新 Formula，否则安装版缺 VPS 功能和 B 站发布器。

## 未实现 / 不承诺

- 自动登录。
- 验证码/滑块/风控绕过。
- 平台审核结果长期跟踪。
- 删除已发文章。
- 产品级 Web 控制台。
- 多账号管理。
- 云端密钥管理。
- 队列并发执行。
- 平台配额主动配置（目前靠平台返回限额后延期）。
- 完整 GUI 桌面应用。

## 发布前必须处理

### 1. Git 仓库清理

当前工作区非常脏，包含大量历史桌面端删除、运行缓存、浏览器 profile、测试图片、未跟踪 worker 文件。发布 GitHub 前必须清理：

- 不提交 `data/local_browser_profile/`。
- 不提交 `data/ordo_tasks.db`。
- 不提交 `publish_records.csv`。
- 不提交 `secrets.env`、`config.json`。
- 不提交截图和临时测试图片。
- 处理或移除 `desktop/` 历史删除。
- 把实际需要发布的未跟踪代码纳入 git。

### 2. `.gitignore`

需要确认忽略：

- `.venv*`
- `__pycache__/`
- `.pytest_cache/`
- `.ordo/`
- `data/*.db`
- `data/local_browser_profile/`
- `data/jobs/`
- `data/inbox/`
- `publish_records.csv`
- `secrets.env`
- `config.json`
- `*.png` 测试截图（若不作为文档资产）

### 3. README 同步

README 现在仍写“远端 VPS 常驻发布未完成”，已经不准确。需要更新为：

- VPS 模式支持任务队列、限额延期、daemon 自动补发。
- 简书为实验性。
- 微信公众号必须走 VPS IP。
- 安装版支持文件清单同步。

### 4. 安全说明

必须明确：

- 平台账号、cookie、secrets 都是本地/VPS 明文文件。
- 用户自行负责平台协议和风控风险。
- 不提供自动登录或绕过验证能力。

## 建议 GitHub 首发范围

首发定位：

> 面向中文创作者的 Markdown 多平台发布 CLI，支持微信排版、浏览器平台发布、封面随机分配、VPS 托管发布和限额自动延期补发。

首发功能打勾：

- Markdown 一稿多发。
- 微信草稿/发布。
- 知乎、头条、一点号、B 站专栏发布。
- 简书实验性。
- 随机主题与封面。
- VPS 托管发布。
- 限额延期补发。
- 结构化结果记录。
- 终端 TUI。

首发不打勾：

- 桌面 App。
- 自动登录。
- 验证码处理。
- 企业级任务队列。
- 多账号托管。

