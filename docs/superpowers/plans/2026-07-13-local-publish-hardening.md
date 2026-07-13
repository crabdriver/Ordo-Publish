# Local Publish Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduled publication local, isolated, mode-aware, durable, and fail-closed while keeping VPS publication only behind explicit manual CLI selection.

**Architecture:** `scripts/monitor_publish.py` becomes a thin local orchestrator with one whole-run lock and two mode groups: WeChat API draft plus one shared-browser publish group. `ordo_engine/run_state.py` becomes the single atomic state authority keyed by article identity, platform, and mode. Browser and result layers reject ambiguity instead of falling back or manufacturing success.

**Tech Stack:** Python 3.12, unittest/pytest, Playwright-compatible synchronous API, POSIX `fcntl` locking, atomic JSON writes.

---

### Task 1: Durable mode-aware publication state

**Files:**
- Create: `tests/test_run_state.py`
- Modify: `ordo_engine/run_state.py`

- [ ] **Step 1: Write failing state tests**

```python
def test_state_file_is_repo_local():
    assert run_state.state_file_for(BASE_DIR) == BASE_DIR / ".ordo" / "publish-state.json"

def test_draft_does_not_suppress_publish(tmp_path):
    run_state.mark_done("a1", "zhihu", "draft_saved", "draft", state_file=state)
    assert run_state.is_done("a1", "zhihu", "draft", state_file=state)
    assert not run_state.is_done("a1", "zhihu", "publish", state_file=state)

def test_corrupt_state_blocks(tmp_path):
    state.write_text("{")
    with pytest.raises(run_state.StateCorruptionError):
        run_state.load_state(state)

def test_atomic_save_uses_replace(tmp_path, monkeypatch):
    monkeypatch.spy(os, "replace")
    run_state.save_state(state, {"a1": {}})
    os.replace.assert_called_once()
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_run_state.py`

Expected: failures because injectable state path, mode nesting, corruption error, and atomic save do not exist.

- [ ] **Step 3: Implement minimal state API**

```python
class StateCorruptionError(RuntimeError):
    pass

def state_file_for(base_dir: Path) -> Path:
    return Path(base_dir) / ".ordo" / "publish-state.json"

def is_done(identity, platform, mode, *, state_file=STATE_FILE):
    rec = load_state(state_file).get(identity, {}).get(platform, {}).get(mode)
    return bool(rec and rec.get("status") in DONE_BY_MODE[mode])

def save_state(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, path)
```

Use frontmatter `article_id` when present; use content hash only when missing. Add `get_record()` and `record_step()` with the same nested key.

- [ ] **Step 4: Verify GREEN**

Run: `.venv312/bin/python -m pytest -q tests/test_run_state.py tests/test_engine_pipeline.py`

Expected: all selected tests pass; no write outside repository.

- [ ] **Step 5: Commit**

```bash
git add ordo_engine/run_state.py tests/test_run_state.py
git commit -m "fix: make publish state durable and mode-aware"
```

### Task 2: Fail-closed publication outcomes

**Files:**
- Create: `tests/test_playwright_results.py`
- Modify: `ordo_engine/platforms/playwright/_common.py`
- Modify: `ordo_engine/platforms/playwright/base_publisher.py`
- Modify: `ordo_engine/platforms/playwright/adapters.py`
- Modify: `ordo_engine/platforms/playwright_zhihu/publisher.py`
- Modify: `ordo_engine/results/errors.py`

- [ ] **Step 1: Write failing outcome tests**

```python
def test_management_navigation_without_title_is_unverified(fake_page):
    result = verify_result_common(
        fake_page,
        "头条号",
        "publish",
        r"/article/\\d+",
        ["发布成功"],
        ["草稿已保存"],
        ["发布上限"],
        "https://example.test/manage",
        "https://example.test/drafts",
        expected_title="目标标题",
    )
    assert result.status == "submitted_unverified"
    assert result.page_state == "submitted_unverified"

@pytest.mark.parametrize("status", ["limit_reached", "submitted_unverified", "unknown"])
def test_nonterminal_status_has_nonzero_returncode(status):
    result = adapter_result(status)
    assert result["returncode"] != 0
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_playwright_results.py`

Expected: false-success behavior returns `published`; limit/unverified return code is 0.

- [ ] **Step 3: Implement strict verification and return-code mapping**

```python
SUCCESS_STATUSES = {"published", "scheduled", "draft_only", "draft_saved", "skipped_existing"}

returncode = 0 if result.status in SUCCESS_STATUSES else 1

if management_url:
    page.goto(management_url, wait_until="domcontentloaded", timeout=30000)
    lines = {normalize_title(line) for line in page.inner_text("body").splitlines()}
    if normalize_title(expected_title) in lines:
        return terminal_result(mode)
return PublishResult(
    platform=platform,
    status="submitted_unverified",
    page_state="submitted_unverified",
    current_url=page.url,
    smoke_step="verify",
    message="提交结果无法确认",
)
```

Store current `ArticlePayload` on the base publisher so platform verifiers receive `expected_title`. Navigation failure returns unverified, never success. Mark `RATE_LIMITED` retryable for the next scheduled run.

- [ ] **Step 4: Verify GREEN**

Run: `.venv312/bin/python -m pytest -q tests/test_playwright_results.py tests/test_platform_contracts.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_playwright_results.py ordo_engine/platforms/playwright ordo_engine/results/errors.py
git commit -m "fix: reject unverified publish outcomes"
```

### Task 3: Shared isolated browser lifecycle

**Files:**
- Modify: `tests/test_engine_pipeline.py`
- Modify: `tests/test_playwright_engine.py`
- Modify: `ordo_engine/runner/pipeline.py`
- Modify: `ordo_engine/platforms/playwright/engine.py`
- Modify: `ordo_engine/platforms/playwright/adapters.py`
- Modify: `publish.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_wechat_only_pipeline_never_builds_engine():
    with TemporaryDirectory() as tmp:
        article = Path(tmp) / "a.md"
        article.write_text("# A", encoding="utf-8")
        registry = {"wechat": DummyAdapter(Path(tmp), "wechat", stdout="ok")}
        run_publish_pipeline(
            base_dir=Path(tmp),
            args=Namespace(mode="draft", continue_on_error=False),
            article_paths=[article],
            platforms=["wechat"],
            registry=registry,
            engine_factory=lambda **kwargs: (_ for _ in ()).throw(AssertionError("browser started")),
        )

def test_shared_engine_start_failure_aborts_browser_pipeline():
    with TemporaryDirectory() as tmp, pytest.raises(RuntimeError, match="独立浏览器"):
        article = Path(tmp) / "a.md"
        article.write_text("# A", encoding="utf-8")
        registry = build_platform_registry(Path(tmp))
        run_publish_pipeline(
            base_dir=Path(tmp),
            args=Namespace(mode="publish", continue_on_error=False, headed=False),
            article_paths=[article],
            platforms=["zhihu"],
            registry=registry,
            engine_factory=lambda **kwargs: FailingEngine(),
        )

def test_headless_missing_profile_fails_without_headed_fallback(tmp_path):
    with pytest.raises(RuntimeError, match="初始化"):
        PlaywrightEngine(profile_dir=tmp_path / "new", headless=True).connect()
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_engine_pipeline.py tests/test_playwright_engine.py`

Expected: pipeline launches browser for WeChat, falls back after startup failure, and engine test still asserts obsolete CDP behavior.

- [ ] **Step 3: Implement bounded lifecycle**

```python
browser_platforms = [p for p in platforms if isinstance(registry[p], PlaywrightPlatformAdapter)]
if browser_platforms:
    shared_engine = engine_factory(
        mode="standalone",
        headless=not getattr(args, "headed", False),
        base_dir=Path(base_dir),
    )
    shared_engine.connect()  # exception propagates

if self.headless and not self._has_existing_profile:
    raise RuntimeError("自动发布 profile 尚未初始化；请运行 --bootstrap-browser")
```

Inject `engine_factory` for tests. Remove per-platform fallback. Close each shared publisher page after collecting its result. Add `--bootstrap-browser` that starts only `.ordo/automation-profile` headed, opens selected platform login pages, waits for explicit terminal confirmation, then closes.

- [ ] **Step 4: Verify GREEN**

Run: `.venv312/bin/python -m pytest -q tests/test_engine_pipeline.py tests/test_playwright_engine.py tests/test_publish_preflight.py`

Expected: all selected tests pass; unit tests launch no real Chrome.

- [ ] **Step 5: Commit**

```bash
git add publish.py ordo_engine/runner/pipeline.py ordo_engine/platforms/playwright tests/test_engine_pipeline.py tests/test_playwright_engine.py tests/test_publish_preflight.py
git commit -m "fix: enforce isolated shared browser lifecycle"
```

### Task 4: Whole-run lock

**Files:**
- Create: `ordo_engine/run_lock.py`
- Create: `tests/test_run_lock.py`
- Modify: `scripts/monitor_publish.py`

- [ ] **Step 1: Write failing lock tests**

```python
def test_second_lock_is_rejected(tmp_path):
    with publication_lock(tmp_path / "publish.lock"):
        with pytest.raises(RunAlreadyActive):
            with publication_lock(tmp_path / "publish.lock"):
                pass

def test_overlap_exits_before_queue_scan(monkeypatch):
    monkeypatch.setattr(monitor, "publication_lock", reject_lock)
    assert monitor.scan_once(tmp_path) == "skipped_overlap"
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_run_lock.py tests/test_monitor_publish.py`

Expected: missing lock module/API.

- [ ] **Step 3: Implement POSIX non-blocking lock**

```python
@contextmanager
def publication_lock(path):
    fp = path.open("a+")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fp.close()
        raise RunAlreadyActive(str(path)) from exc
    try:
        yield
    finally:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        fp.close()
```

Acquire lock before state/queue reads. Catch `RunAlreadyActive` only at monitor boundary and return `skipped_overlap`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv312/bin/python -m pytest -q tests/test_run_lock.py tests/test_monitor_publish.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add ordo_engine/run_lock.py scripts/monitor_publish.py tests/test_run_lock.py tests/test_monitor_publish.py
git commit -m "fix: prevent overlapping publish runs"
```

### Task 5: Local automatic orchestration and typed summary

**Files:**
- Modify: `scripts/monitor_publish.py`
- Modify: `tests/test_monitor_publish.py`
- Modify: `publish.py`
- Modify: `tests/test_publish_preflight.py`

- [ ] **Step 1: Write failing local-orchestration tests**

```python
def test_publish_mode_defaults_local():
    assert publish.parse_args(["a.md", "--mode", "publish"]).remote == "local"

def test_monitor_never_runs_vps_preflight(monkeypatch):
    commands = capture_commands(monkeypatch)
    monitor.publish_article(article, state=empty_state)
    assert all(cmd[cmd.index("--remote") + 1] == "local" for cmd in commands)
    assert not any("ssh" in cmd for cmd in commands)

def test_browser_platforms_use_one_command(monkeypatch):
    commands = capture_commands(monkeypatch)
    monitor.publish_article(article, state=empty_state)
    browser = [cmd for cmd in commands if "zhihu,toutiao,yidian,bilibili,jianshu" in cmd]
    assert len(browser) == 1

def test_summary_keeps_rate_limit_out_of_success():
    summary = classify_summary([{"platform": "zhihu", "status": "limit_reached"}])
    assert summary.rate_limited == ["zhihu"]
    assert summary.succeeded == []
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_monitor_publish.py tests/test_publish_preflight.py`

Expected: default is VPS, VPS preflight runs, browser platforms spawn separately, and summary collapses outcomes.

- [ ] **Step 3: Implement two local mode groups**

```python
wechat_cmd = build_publish_cmd(
    article,
    platforms="wechat",
    mode="draft",
    remote="local",
    cover=cover,
    wechat_theme=wechat_theme,
)
browser_cmd = build_publish_cmd(
    article,
    platforms=",".join(pending_browser_platforms),
    mode="publish",
    remote="local",
    cover=cover,
    template_theme=template_theme,
)
```

Delete automatic VPS preflight and Jianshu CDP wrapper. Read typed platform rows from `publish_records.csv` after each command; update unified state only for terminal outcomes. Print separate success, skip, rate-limit, unverified, and failure lists. Daemon catches scan exceptions, reports, and continues.

- [ ] **Step 4: Verify GREEN**

Run: `.venv312/bin/python -m pytest -q tests/test_monitor_publish.py tests/test_publish_preflight.py tests/test_engine_pipeline.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add publish.py scripts/monitor_publish.py tests/test_monitor_publish.py tests/test_publish_preflight.py
git commit -m "fix: run automatic publishing locally"
```

### Task 6: Reproducible dependencies and obsolete-contract cleanup

**Files:**
- Create: `tests/test_runtime_dependencies.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `README.md`
- Modify: `tests/test_platform_contracts.py`

- [ ] **Step 1: Write failing dependency/contract tests**

```python
def test_browser_runtime_is_declared():
    assert "patchright" in Path("requirements.txt").read_text()

def test_playwright_adapter_prepare_is_in_process_context():
    prepared = build_platform_registry(Path("/tmp/repo"))["zhihu"].prepare(
        markdown_file="/tmp/article.md",
        mode="draft",
        article_id="rev-1",
    )
    assert "command" not in prepared
    assert prepared["article_id"] == "rev-1"
```

- [ ] **Step 2: Verify RED**

Run: `.venv312/bin/python -m pytest -q tests/test_platform_contracts.py tests/test_runtime_dependencies.py`

Expected: runtime missing and old tests expect subprocess commands.

- [ ] **Step 3: Declare one runtime and update docs/contracts**

```text
patchright==1.61.2
```

Add the same pin to `pyproject.toml` and `requirements.txt`. Document isolated profile bootstrap, local default, explicit VPS emergency invocation, and automatic result categories. Update platform-contract tests for in-process prepared contexts.

- [ ] **Step 4: Verify GREEN and full suite**

Run: `.venv312/bin/python -m pytest -q`

Expected: exit 0; no test launches a real browser.

Run: `.venv312/bin/python -m compileall -q publish.py scripts/monitor_publish.py ordo_engine tests`

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt README.md tests/test_platform_contracts.py tests/test_runtime_dependencies.py
git commit -m "build: declare local browser runtime"
```

### Task 7: Final adversarial verification

**Files:**
- Review all changed files from `49c3897..HEAD`

- [ ] **Step 1: Run focused safety suite**

Run: `.venv312/bin/python -m pytest -q tests/test_run_state.py tests/test_run_lock.py tests/test_playwright_results.py tests/test_playwright_engine.py tests/test_engine_pipeline.py tests/test_monitor_publish.py tests/test_publish_preflight.py tests/test_platform_contracts.py tests/test_runtime_dependencies.py`

Expected: exit 0.

- [ ] **Step 2: Run full suite and static checks**

Run: `.venv312/bin/python -m pytest -q`

Expected: exit 0.

Run: `git diff --check 49c3897..HEAD`

Expected: no output, exit 0.

- [ ] **Step 3: Inspect forbidden automatic paths**

Run: `rg -n "require_vps_ready|remote=\"vps\"|jianshu_dedicated_browser|connect_over_cdp|DevToolsActivePort" scripts/monitor_publish.py ordo_engine/runner/pipeline.py ordo_engine/platforms/playwright`

Expected: no automatic-path matches; explicit manual VPS code in `publish.py` may remain.

- [ ] **Step 4: Dispatch independent code review**

Review `49c3897..HEAD` against the approved spec. Fix all Critical and Important findings, rerun Steps 1-3, then use `superpowers:finishing-a-development-branch`.
