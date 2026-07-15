# 微信夜间发布可靠性修复设计

## 目标

修复 20:30 自动任务中三个已确认问题：VPS SSH 间歇断开导致微信 worker 未启动；批次报告混入旧状态；文章完成状态不收敛导致旧文章每天重新进入扫描。

本修复不真实发表文章，不修改润色目录，不修改已有发布记录，不改变五个浏览器平台行为。

## 已确认根因

1. VPS 正遭受持续 SSH 密码爆破，`sshd` 使用 `MaxStartups 10:30:100`。现有微信适配器为建目录、上传文章、上传封面、执行 worker、清理分别建立连接，连接可能在认证前被服务端丢弃。
2. `BatchCoordinator._build_summary()` 返回文章当前完整状态，而非本批次产生的结果。旧 `draft_saved` 因此会被报告成当前成功。
3. `BatchCoordinator` 写入平台记录后不重新计算 `ArticleStage.completed`。`scan_once()` 仅按文章级状态选取任务，导致终态文章再次进入批次并污染报告。

## 方案选择

### 采用：批次级 SSH ControlMaster

一个 `WeChatPlatformAdapter` 批次建立一个受控 SSH master，后续 SSH/SCP 通过专用 `ControlPath` 复用连接。连接建立失败时只在远端 worker 尚未启动前做有限退避重试。worker 一旦启动，无论输出丢失或超时，都不得自动重投，状态进入 `manual_verify`。

优点：改动集中；保留现有 SCP 和远端 worker；把每篇多次认证降为批次一次；不引入依赖。

### 不采用：每篇文章通过单条 tar-over-SSH 流执行

连接数最少，但需要重写上传、引用图片映射、远端清理和错误边界，回归风险大。

### 不采用：增加 `MaxStartups` 或为每条 SSH/SCP 独立重试

只缓解症状。独立重试仍会制造连接风暴；worker 阶段误重试可能产生重复草稿。

## 数据流

1. `BatchCoordinator` 生成本批次标识，并初始化本批次结果集合。
2. 微信适配器建立 owned ControlMaster；失败则记录 `wechat_vps_transport_failed`，不调用微信 API。
3. 文章和封面通过复用连接上传。
4. 远端 worker 开始前的连接错误允许有限重试；开始后的未知结果标记 `manual_verify`。
5. 每个平台处理后只把本批次发生的结果写入批次摘要。
6. 批次结束调用 `is_article_completed()`，仅当微信 `draft_saved` 且五个平台均 `published` 时写入 `ArticleStage.completed`；否则保持 `pending` 或 `needs_review`。
7. `scan_once()` 只把仍有可执行平台的文章送入协调器。`manual_verify`、`draft_saved`、`published` 等受保护状态不重投，也不作为本批次成功汇报。

## 错误边界

- ControlMaster 建立失败：`failed_before_draft` + `wechat_vps_transport_failed`。
- 上传失败且 worker 未启动：有限退避后失败；安全重试。
- worker 已启动后 SSH 中断或超时：`manual_verify`；禁止重试。
- ControlMaster 清理失败：记录诊断，不覆盖文章真实结果；只删除本任务创建的本地 socket。
- 封面合同失败：继续 `cover_preflight_failed`；发布项目不修改源封面。
- 报告不得将历史 `draft_ref`、`published_ref` 算入当前运行统计。

## 测试

1. 复现多次 SSH/SCP 调用未共享连接；修复后断言共享同一 `ControlPath`。
2. 复现认证前首次断开；断言 worker 未启动时有限重试。
3. 复现 worker 已启动后连接中断；断言不重试且进入 `manual_verify`。
4. 给状态文件预置旧 `draft_saved`；运行无新动作批次，断言摘要不报告当前成功。
5. 给文章写入全部所需终态；断言文章变成 `completed`，下次扫描不进入批次。
6. 给文章写入 `manual_verify`；断言不重投，也不伪报当前成功。
7. 运行相关测试和完整测试套件。全程使用 fake executor，不连接 VPS、不调用微信 API。

## 验收标准

- 自动任务不会因一篇文章产生多次独立 SSH 认证。
- 未证明创建草稿时绝不报告“草稿已建”。
- 当前报告只包含本次执行结果。
- 已完成文章不再进入下一次扫描。
- 不出现任何本地微信 API 路径。
- 测试期间零真实发布。
