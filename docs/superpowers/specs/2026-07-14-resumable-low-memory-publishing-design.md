# 可恢复低内存发布编排设计

## 目标

每日批次在不调用主力浏览器的前提下，以固定且有上界的内存完成发布。任何平台失败不得吞掉文章：必须留下经核验的平台草稿，或留下明确不可恢复原因与本地检查点。

## 非目标

- 不常驻浏览器。
- 不改动润色目录、源 Markdown、封面或发布包。
- 不通过 `--force` 规避待核验、限额或失败状态。
- 不让飞书报告决定、修复或重跑发布。

## 批次所有权

`scripts/monitor_publish.py` 是唯一批次协调器和唯一锁持有者。它不再为每篇文章启动一个继承锁的 `publish.py` 子进程。

协调器不使用 CDP。每个平台独立启动一次 owned browser，处理该平台全部文章后彻底关闭并释放内存，再启动下一个平台。任意时刻最多一个 browser context、一个活动 page。

```text
定时任务 -> BatchCoordinator(唯一 publish.lock)
          -> 微信专用 worker 草稿（不经过 publish.py，不重复获取锁）
          -> zhihu:  启动 owned browser → 全部文章 → 彻底关闭
          -> jianshu:启动 owned browser → 全部文章 → 彻底关闭
          -> toutiao:启动 owned browser → 全部文章 → 彻底关闭
          -> yidian: 启动 owned browser → 全部文章 → 彻底关闭
          -> bilibili:启动 owned browser → 全部文章 → 彻底关闭
          -> 生成只读报告
```

每个平台 browser 使用 `.ordo/automation-profile` 隔离 profile。系统 Chrome binary 可以保留，但必须通过精确 `--user-data-dir` 命令行识别 owned 进程并确认无其他进程使用同一 profile。

## 状态与恢复

`.ordo/auto_publish_state.json` 是唯一恢复依据。`publish_records.csv` 是追加审计日志，不参与恢复决策。

每个 `article_key + platform` 保存模式维度（draft/publish），自动任务固定读取 wechat.draft / browser.publish。手动路径的 draft 和 publish 互不覆盖。

- `package_hash`：Markdown 内容身份；浏览器平台暂停封面。微信封面单独验证。
- `stage`：`pending`、`preflight_ok`、`draft_prepared`、`draft_saved`、`publish_attempted`、`published`、`limited_after_draft`、`blocked_no_draft`、`manual_verify`、`failed_before_draft`、`not_executed`。
- `draft_ref`：平台草稿 ID 或编辑 URL；无可靠平台草稿时为空。
- `published_ref`：平台公开 URL；只有可验证时写入。
- `error`、`retry_after`、`updated_at`。

恢复规则：

- `completed` 整篇跳过。
- `published` 该平台跳过。
- `draft_saved` 禁止重新创建草稿；平台恢复路径完成并验证前保持跳过。
- `limited_after_draft` 在 `retry_after` 前不再尝试正式发布。
- `blocked_no_draft` 不自动重试；只有包或平台能力改变后才重新预检。
- `manual_verify` 不自动重投，等待平台后台人工确认。
- `not_executed` 允许正常执行。
- `failed_before_draft` 按错误可重试性决定。
- `needs_review`（文章级）不进入任何平台发布。

## 平台检查点协议

浏览器平台必须实现四项能力，不能确认的能力返回明确阻断：

1. `prepare_draft`：填写标题、正文、封面与必填设置。
2. `save_draft`：显式保存或触发平台自动保存。
3. `verify_draft`：通过平台草稿 ID、编辑 URL 或官方草稿列表精确核验。
4. `publish_from_draft`：只对已核验草稿执行正式发布，并以公开 URL 或官方已发布列表核验。

若封面、权限、登录、选择器或限额在第 1-3 步失败，协调器不得尝试正式发布。若平台没有本地封面上传权限，例如一点号，写入 `blocked_no_draft`，并报告所缺的平台能力。

正式发布失败后，协调器先重新核验草稿：存在则写 `draft_saved` 或 `limited_after_draft`；不存在则写 `manual_verify`，保留本地包身份与失败现场。

## 故障隔离

单个平台失败不停止同一篇的其他平台，也不停止后续文章。只有以下情况停止整个批次：隔离浏览器无法启动、状态文件损坏、唯一锁无法获得、或发布包预检出现全局不可读错误。

封面预检只读：不符合 PNG、sRGB、2538x1080、5MB 上限时写 `blocked_no_draft`。自动化不得修图、改封面或重写文章。

## 资源上限

- 每个平台一个独立 browser 生命周期；平台之间彻底关闭并释放内存。
- 任意时刻最多一个 browser context、一个活动 page。
- 无待处理浏览器工作时不启动浏览器。
- 每篇、每平台有独立超时；超时先保存/核验草稿，再释放页面。
- 浏览器清理失败时停止启动后续平台，剩余任务标记 `not_executed`。

## 报告契约

项目输出终端文本或结构化报告，逐篇逐平台报告下列之一。外部自动化软件负责推送飞书：

- `已发表`
- `草稿已核验，待正式发布`
- `限额，草稿已保留`
- `未执行`
- `需人工核验`
- `阻断：具体原因`

禁止将未执行写成失败，禁止将 toast、按钮点击或编辑器 URL 写成已发表，禁止在报告阶段修改文件或再次运行发布。

## 验收标准

1. 八篇文章批次中，每个平台独立启动并关闭 owned browser；平台间主进程完全不保留浏览器进程，主力浏览器零访问。
2. 第一篇的平台错误不会阻断第二篇进入编辑器。
3. 封面或权限失败后，状态明确为 `draft_saved` 或 `blocked_no_draft`，不存在无解释的 `failed`。
4. 已核验草稿重跑时不得创建重复草稿；未接入可靠恢复路径的平台保持跳过。
5. 限额只阻断对应平台的正式发布，其他平台和文章继续。
6. 报告与 `auto_publish_state.json` 的阶段逐项一致。
