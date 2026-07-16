# 头条号与一点号提交证据修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阻止头条号和一点号把无效点击记为已提交，并让真实可观察的提交流程继续进入后台核验。

**Architecture:** 保留其他平台现有通用点击路径。新增仅供头条号和一点号使用的证据型点击函数；按钮必须可交互，点击必须产生确认弹窗、URL、反馈或按钮状态变化。新增专用异常，使“按钮无效果”回到明确失败路径，而不是被基类升级成 `submitted_unverified`。

**Tech Stack:** Python 3.12、Patchright/Playwright 同步 API、pytest、unittest.mock。

---

## 文件边界

- `ordo_engine/platforms/playwright/base_publisher.py`：定义 `PublishClickNoEffect`，在发布状态机中把它作为未形成提交证据的明确失败处理。
- `ordo_engine/platforms/playwright/_common.py`：新增证据型点击函数；现有 `click_publish_common()` 保持不变。
- `ordo_engine/platforms/playwright_toutiao/locators.py`：限定头条发布确认弹窗和精确确认文本。
- `ordo_engine/platforms/playwright_yidian/locators.py`：限定一点号发布确认弹窗和精确确认文本。
- `ordo_engine/platforms/playwright_toutiao/publisher.py`：切换到证据型点击。
- `ordo_engine/platforms/playwright_yidian/publisher.py`：切换到证据型点击。
- `ordo_engine/results/errors.py`、`ordo_engine/platforms/base.py`：对外返回 `publish_click_no_effect`。
- `ordo_engine/runner/pipeline.py`：把该失败映射为需人工核验，防止自动重投。
- `tests/test_playwright_results.py`、`tests/test_playwright_cover_uploads.py`：失败语义和点击行为回归测试。

### Task 1: 固化“无点击效果不是已提交”语义

**Files:**
- Modify: `tests/test_playwright_results.py`
- Modify: `ordo_engine/platforms/playwright/base_publisher.py`
- Modify: `ordo_engine/results/errors.py`
- Modify: `ordo_engine/platforms/base.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_playwright_results.py` 增加：

```python
def test_publish_click_no_effect_is_failed_not_submitted_unverified(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    engine = MagicMock(base_dir=tmp_path)
    engine.screenshot.return_value = None
    publisher = StatefulPublisher(
        engine,
        submit_error=PublishClickNoEffect("头条号发布按钮点击后页面无变化"),
    )

    result = publisher.publish(payload(article), "publish")

    assert result.status == "failed"
    assert result.page_state == "error"
    assert "页面无变化" in result.error
    assert get_record(
        article_key(article), "stub", "publish",
        state_file=state_file_for(tmp_path),
    )["last_step"] == "publish_click_no_effect"
```

并增加错误类型测试：

```python
def test_publish_click_no_effect_has_specific_error_type():
    result = infer_error_type("failed", {
        "returncode": 1,
        "stderr": "publish_click_no_effect: 页面无变化",
    })
    assert result == ErrorType.PUBLISH_CLICK_NO_EFFECT
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
.venv312/bin/python -m pytest tests/test_playwright_results.py -k 'click_no_effect' -q
```

Expected: FAIL，原因是 `PublishClickNoEffect` 和 `ErrorType.PUBLISH_CLICK_NO_EFFECT` 尚不存在。

- [ ] **Step 3: 最小实现异常和错误分类**

在 `base_publisher.py` 增加：

```python
class PublishClickNoEffect(RuntimeError):
    """发布控件没有产生任何可观察提交过渡。"""
```

在 `publish()` 的异常处理中，先于 `_submission_started` 分支处理：

```python
except PublishClickNoEffect as exc:
    self.state = PublishState.ERROR
    message = f"publish_click_no_effect: {exc}"
    self._emit_state("publish_click_no_effect", page_state="error", error=message)
    self._take_screenshot(f"error_{self.state.value}")
    return PublishResult(
        platform=self.platform,
        status="failed",
        page_state="error",
        current_url=self.page.url if self.page else "",
        smoke_step=self.state.value,
        error=message,
        screenshots=list(self._screenshots),
    )
```

在 `errors.py` 增加：

```python
PUBLISH_CLICK_NO_EFFECT = "publish_click_no_effect"
```

在 `infer_error_type()` 的平台变化判定前增加：

```python
if "publish_click_no_effect" in output:
    return ErrorType.PUBLISH_CLICK_NO_EFFECT
```

同时把 `publish_click_no_effect` 加入 `publish()` 开头的危险步骤集合。返回值和持久化步骤都保留明确名称；直接调用和 BatchCoordinator 下次运行都只能核验，不能自动重投。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run:

```bash
.venv312/bin/python -m pytest tests/test_playwright_results.py -k 'click_no_effect' -q
```

Expected: PASS。

### Task 2: 头条号和一点号采用证据型点击

**Files:**
- Modify: `tests/test_playwright_cover_uploads.py`
- Modify: `ordo_engine/platforms/playwright/_common.py`
- Modify: `ordo_engine/platforms/playwright_toutiao/locators.py`
- Modify: `ordo_engine/platforms/playwright_yidian/locators.py`
- Modify: `ordo_engine/platforms/playwright_toutiao/publisher.py`
- Modify: `ordo_engine/platforms/playwright_yidian/publisher.py`

- [ ] **Step 1: 写失败测试**

增加四组完整测试；沿用本文件现有 `Locator`、`MappingPage`：

```python
def test_evidence_click_rejects_disabled_publish_button():
    publish = Locator(text="发布", enabled=False)
    page = MappingPage({'button:visible:has-text("发布")': publish})

    with pytest.raises(PublishClickNoEffect, match="不可交互"):
        click_publish_with_evidence(
            page, ["发布"], ["确认发布"], "测试平台",
            confirm_scope_selector='[role="dialog"]:visible',
            timeout_seconds=0,
        )


def test_evidence_click_rejects_click_without_page_change():
    publish = Locator(text="发布")
    page = MappingPage({'button:visible:has-text("发布")': publish})

    with pytest.raises(PublishClickNoEffect, match="页面无变化"):
        click_publish_with_evidence(
            page, ["发布"], ["确认发布"], "测试平台",
            confirm_scope_selector='[role="dialog"]:visible',
            timeout_seconds=0,
        )


def test_toutiao_confirmation_does_not_accept_generic_confirm():
    publish = Locator(text="预览并发布")
    generic = Locator(text="确定")
    page = MappingPage({
        'button:visible:has-text("预览并发布")': publish,
        'button:visible:has-text("确定")': generic,
    })

    with pytest.raises(PublishClickNoEffect, match="页面无变化"):
        click_publish_with_evidence(
            page,
            ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
            ToutiaoLocators.CONFIRM_PUBLISH_TEXTS,
            "头条号",
            confirm_scope_selector=ToutiaoLocators.CONFIRM_DIALOG_SELECTOR,
            timeout_seconds=0,
        )

    assert generic.clicked == 0


def test_evidence_click_accepts_scoped_confirm_then_transition():
    publish = Locator(text="发布")
    confirm = Locator(text="确认发布")
    dialog = Locator(count=0)
    dialog.children['button:visible:has-text("确认发布")'] = confirm
    page = MappingPage({
        'button:visible:has-text("发布")': publish,
        '[role="dialog"]:visible': dialog,
    })
    publish.on_click = lambda: setattr(dialog, "_count", 1)
    confirm.on_click = lambda: setattr(page, "url", "https://example.test/manage")

    click_publish_with_evidence(
        page, ["发布"], ["确认发布"], "测试平台",
        confirm_scope_selector='[role="dialog"]:visible',
        timeout_seconds=0,
    )

    assert publish.clicked == 1
    assert confirm.clicked == 1
```

测试 fake locator 的 `on_click` 分别模拟：无变化、出现确认弹窗、确认弹窗关闭及 URL 变化。断言无变化和禁用按钮抛 `PublishClickNoEffect`；有效过渡正常返回；普通“确定”不被点击。

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
.venv312/bin/python -m pytest tests/test_playwright_cover_uploads.py -k 'evidence_click or confirmation_does_not_accept' -q
```

Expected: FAIL，原因是证据型点击函数尚不存在，两个 publisher 仍调用旧通用函数。

- [ ] **Step 3: 实现最小证据型点击函数**

在 `_common.py` 新增：

```python
def click_publish_with_evidence(
    page: Page,
    publish_texts: list,
    confirm_texts: list,
    platform: str,
    *,
    confirm_scope_selector: str,
    timeout_seconds: float = 10,
):
    publish_btn = find_visible_button(page, publish_texts)
    if not publish_btn or not publish_btn.is_visible() or not publish_btn.is_enabled():
        raise PublishClickNoEffect(f"{platform}发布按钮不可交互")

    pre_url = page.url or ""
    pre_feedback = _feedback_text(page)
    print(f"[INFO] 点击{platform}发布按钮...")
    publish_btn.click()

    changed, confirm_btn = _wait_for_submit_effect(
        page, publish_btn, pre_url, pre_feedback,
        confirm_texts, confirm_scope_selector, timeout_seconds,
    )
    if not changed:
        raise PublishClickNoEffect(f"{platform}发布按钮点击后页面无变化")
    if confirm_btn is None:
        return

    confirm_pre_url = page.url or ""
    confirm_pre_feedback = _feedback_text(page)
    print(f"[INFO] 点击{platform}确认发布...")
    confirm_btn.click()
    if not _wait_for_confirm_effect(
        page, publish_btn, confirm_btn,
        confirm_pre_url, confirm_pre_feedback, timeout_seconds,
    ):
        raise PublishClickNoEffect(f"{platform}确认发布后页面无变化")
```

`_wait_for_submit_effect()` 返回 `(changed, confirm_btn)`；`_wait_for_confirm_effect()` 返回布尔值。两个函数仅检查 URL 变化、新反馈、主按钮消失/禁用、确认弹窗出现或确认按钮消失。它们不把普通页面文字当作结果证据。`timeout_seconds=0` 时仍执行一次状态采样，保证测试无需真实等待。

- [ ] **Step 4: 限定平台确认弹窗和文本**

头条号：

```python
CONFIRM_PUBLISH_TEXTS = ["确定并发布", "确认发布"]
CONFIRM_DIALOG_SELECTOR = '[role="dialog"]:visible, .byte-modal-wrapper:visible'
```

一点号：

```python
CONFIRM_PUBLISH_TEXTS = ["确认发布"]
CONFIRM_DIALOG_SELECTOR = (
    '[role="dialog"]:visible, .el-dialog__wrapper:visible, '
    '.el-message-box__wrapper:visible'
)
```

两个 publisher 的 `click_publish()` 改调 `click_publish_with_evidence()`。头条调用前继续关闭已知 AI 助手遮罩。

- [ ] **Step 5: 防止 BatchCoordinator 自动重投无效果提交**

在 `tests/test_batch_coordinator_safety.py` 增加：

```python
def test_publish_click_no_effect_requires_manual_review():
    assert _map_payload_stage({
        "status": "failed",
        "error_type": "publish_click_no_effect",
    }) == PlatformStage.manual_verify
```

在 `pipeline.py` 增加并使用：

```python
def _map_payload_stage(payload):
    if payload.get("error_type") == "publish_click_no_effect":
        return PlatformStage.manual_verify
    return _map_status(payload.get("status", "failed"))
```

`_run_one_browser_article()` 使用 `_map_payload_stage(payload)`，保证报告保留失败原因，但后续批次不重复提交。

- [ ] **Step 6: 运行定向测试并确认 GREEN**

Run:

```bash
.venv312/bin/python -m pytest \
  tests/test_playwright_cover_uploads.py \
  tests/test_playwright_results.py \
  tests/test_batch_coordinator_safety.py -q
```

Expected: 全部 PASS。

### Task 3: 回归验证和提交

**Files:**
- Verify only: all tracked files

- [ ] **Step 1: 运行静态检查**

```bash
git diff --check
```

Expected: 无输出。

- [ ] **Step 2: 运行全量测试**

```bash
.venv312/bin/python -m pytest -q
```

Expected: 全部 PASS；不访问真实平台。

- [ ] **Step 3: 检查变更范围**

```bash
git status --short
git diff --stat
```

Expected: 仅计划列出的代码和测试文件；保留用户原有未跟踪文件，不纳入提交。

- [ ] **Step 4: 提交代码**

```bash
git add \
  ordo_engine/platforms/base.py \
  ordo_engine/platforms/playwright/base_publisher.py \
  ordo_engine/platforms/playwright/_common.py \
  ordo_engine/platforms/playwright_toutiao/locators.py \
  ordo_engine/platforms/playwright_toutiao/publisher.py \
  ordo_engine/platforms/playwright_yidian/locators.py \
  ordo_engine/platforms/playwright_yidian/publisher.py \
  ordo_engine/results/errors.py \
  ordo_engine/runner/pipeline.py \
  tests/test_batch_coordinator_safety.py \
  tests/test_playwright_cover_uploads.py \
  tests/test_playwright_results.py
git commit -m "fix(publish): require observable submit effects"
```

Expected: 单个修复提交成功。真实平台验收不包含在本提交中。
