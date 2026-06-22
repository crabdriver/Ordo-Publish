# 终端版发布向导

当前推荐入口是安装后的 `ordo` 命令。它会启动一个全屏 TUI，在不打开 GUI 的情况下完成一次完整的导入、预检、发布和失败续跑。

## 当前定位

- macOS 优先可用
- 复用现有 Python 发布引擎与浏览器会话链路
- GUI 暂停新功能扩展，仅保留现有能力和后续 bug 修复
- Windows 本轮只保留兼容性设计，不单独交付完整终端产品体验

## 启动

```bash
bash scripts/install_ordo.sh
```

或直接：

```bash
brew install --formula ./Formula/ordo.rb
```

安装完成后：

```bash
ordo
```

源码仓库内仍保留 `scripts/terminal_wizard.py` 作为兼容启动脚本，但它现在也会走同一套 `ordo` bootstrap，而不是旧的逐行 Python 问答。

## TUI 会呈现什么

首版会在全屏界面里呈现这些配置项：

1. 文章来源目录或单文件
2. 目标平台
3. 发布模式：`draft` / `publish`
4. 封面策略：`auto` / `force_on` / `force_off`
5. AI 声明策略：`auto` / `force_on` / `force_off`
6. 封面目录覆盖值
7. 单个平台失败后是否继续
8. 是否把本次配置保存为默认值

TUI 会以全屏界面呈现：

- 顶部标题区
- 左侧当前配置摘要
- 中间配置区
- 右侧执行日志区
- 底部快捷键提示

常用快捷键：

- `R`: 执行发布
- `S`: 保存默认配置
- `L`: 重新载入默认配置
- `Q`: 退出

## 配置与状态

终端版会在首次运行时把运行模板同步到：

- `~/Library/Application Support/com.ordo.cli/runtime/repo`

此目录下继续沿用现有文件结构：

- `secrets.env`
  - 微信 `AppID / Secret / Author`
- `config.json`
  - 终端默认配置写在 `terminal_wizard.defaults`
- `.ordo/`
  - 最近计划、最近结果、续跑队列、浏览器会话状态

可通过环境变量 `ORDO_HOME` 覆盖基础目录。

说明：

- 运行模板文件会在每次启动时从安装模板刷新到 runtime 目录
- 因此不建议直接修改 `runtime/repo` 里的脚本文件；用户配置请放在 `config.json`、`secrets.env`、`.ordo/`

## 运行期行为

终端版会复用现有引擎能力：

- 导入：`ordo_engine.workbench.bridge.import_sources`
- 预检：`publish.run_preflight_checks`
- 规划：`ordo_engine.workbench.bridge.plan_publish_job`
- 执行：`ordo_engine.workbench.bridge.run_publish_job`
- 续跑队列：`ordo_engine.workbench.operations_matrix.write_operations_matrix`

## 出错时会看到什么

- 预检失败：直接打印阻塞项和 warning，并生成续跑队列文件
- 导入部分失败：保留成功文章，同时打印失败源文件和原因
- 发布中失败：逐篇逐平台输出状态、摘要和 `retryable` 标记
- 任务结束：打印成功数、失败数、跳过数，以及续跑队列文件路径
- 如果任务被阻塞或部分失败，终端会直接提示下一步命令：重新执行 `ordo`，并根据续跑队列缩小本轮范围

## Windows 预留

当前先不做完整 Windows 交付，但这版终端入口已经尽量避免绑定 Tauri/桌面运行时。后续如果要扩到 Windows，优先考虑：

- 继续复用同一套 `config.json` / `secrets.env` / `.ordo/`
- 在 PowerShell 中运行相同 `ordo` 入口
- 等 macOS 终端链路稳定后，再决定是否补独立脚本或单独可执行文件
