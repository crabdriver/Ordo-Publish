# 发表阻断修复设计

## 目标

修复两个已定位的本地阻断：头条封面上传竞态，以及微信任务把本地幂等参数错误传给远端 worker。不得真实发表文章，不得引入 CDP，不得连接用户主力 Chrome。

## 范围

本轮只改：

- `ordo_engine/platforms/playwright_toutiao/publisher.py`
- `ordo_engine/runner/pipeline.py`
- 两处对应测试

VPS 的微信 `48001 api unauthorized` 独立处理，不与本轮本地改动混合。

## 头条封面上传

现有 DOM 证据显示，“本地上传”是 `<button class="... upload-btn">`，且抽屉打开后同时存在 `#upload-drag-input` 和按钮内部的 file input，不支持“按钮实际是 div”的判断。实机对照进一步证明：坐标式 `human_click()` 未打开抽屉，locator `click()` 立即生效；向 `#upload-drag-input` 设置文件不触发上传，按钮内部 `.btn-upload-handle input[type="file"]` 才触发 `/spice/image` 请求。

修复方案：使用 locator `click()` 打开封面抽屉，等待 `.btn-upload-handle input[type="file"]` attached，再调用 `set_input_files()`。删除“查找并点击本地上传按钮”步骤，减少一个易变 UI 依赖。后续仍保留上传成功项可见、选中图片、确认抽屉三项证据，保持 fail closed。

测试必须断言封面入口不走 `human_click()`、按钮内部 file input 需等待后才能使用，并断言不再依赖“本地上传”按钮文本选择器。

## 微信参数边界

`force_republish` 是本地编排层幂等控制参数，不是 `wechat_publisher.py` 的 CLI 参数。当前 `run_platform_task()` 为微信把它追加进 subprocess command，随后 VPS adapter 原样转发，导致远端 argparse 拒绝。

修复方案：删除 `run_platform_task()` 中微信专用的参数追加逻辑。`prepared["force_republish"]` 仍保留，供本地编排和结果上下文使用；远端命令只包含 worker 已声明支持的参数。对应测试改为断言上下文保留 `force_republish=True`，执行命令不含 `--force-republish`。

## 验证

按 TDD 执行：

1. 新增或修改聚焦测试，分别观察预期失败。
2. 实施每项最小修复，观察聚焦测试通过。
3. 运行相关测试文件，再运行完整 `pytest tests/ -q`。
4. 运行 `scripts/monitor_publish.py --dry-run`，确认调用接线未破坏。
5. 使用项目隔离 profile 运行不点击发表按钮的头条封面探针；真实发表必须另行确认。

## 非目标

- 不修改知乎、简书、一点号、B站现有实现。
- 不处理头条 `verify_result` 真发验证。
- 不修改 VPS 文件。
- 不清理现有工作区其他改动或未跟踪文件。
