# WeChat Nightly Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make nightly WeChat drafts survive VPS SSH connection pressure, report only current-run outcomes, and stop rescanning protected historical records.

**Architecture:** Keep the VPS-only worker boundary. Add one lazily-created SSH ControlMaster per WeChat batch, reuse its socket for SSH/SCP, and retry only pre-worker transport operations. Track platform records touched by the current `BatchCoordinator` run, derive summaries from that delta, and reconcile article completion with the existing v2 state helpers.

**Tech Stack:** Python 3.12, stdlib `subprocess`/`tempfile`/`time`, pytest/unittest, existing v2 run-state model.

---

### Task 1: Reuse one owned SSH connection and preserve no-duplicate boundary

**Files:**
- Modify: `tests/test_platform_contracts.py`
- Modify: `ordo_engine/platforms/wechat/publisher.py`
- Modify: `ordo_engine/runner/pipeline.py`

- [ ] **Step 1: Write failing tests for connection reuse, pre-worker retry, and no worker retry**

Add tests that patch `ordo_engine.platforms.wechat.publisher.subprocess.run`. Two `adapter.publish()` calls inside one batch must start one `ControlMaster`; all transfer commands must contain the same `ControlPath`. A first master-start failure containing `Connection closed` must retry. A worker command returning 255 must run once and return `remote_started=True`.

```python
def test_wechat_batch_reuses_one_control_master(self):
    calls = []
    def fake_run(command, **_kwargs):
        calls.append(command)
        stdout = "[OK] 已写入微信公众号草稿: media-id" if "ORDO_WECHAT_VPS_WORKER=1" in command[-1] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        first = repo / "first.md"
        second = repo / "second.md"
        first.write_text("# first\n", encoding="utf-8")
        second.write_text("# second\n", encoding="utf-8")
        (repo / "secrets.env").write_text(
            "VPS_IP=203.0.113.10\nVPS_USER=root\nVPS_PATH=/root/ordo-publish\n",
            encoding="utf-8",
        )
        adapter = WeChatPlatformAdapter(repo)
        with patch("ordo_engine.platforms.wechat.publisher.subprocess.run", side_effect=fake_run):
            adapter.publish(adapter.prepare(markdown_file=first, mode="draft"))
            adapter.publish(adapter.prepare(markdown_file=second, mode="draft"))
            adapter.close_batch()

    masters = [cmd for cmd in calls if cmd[0] == "ssh" and "-M" in cmd]
    control_paths = {
        arg for cmd in calls for arg in cmd
        if isinstance(arg, str) and arg.startswith("ControlPath=")
    }
    self.assertEqual(len(masters), 1)
    self.assertEqual(len(control_paths), 1)

def test_wechat_master_start_retries_transport_close(self):
    calls = []
    def fake_run(command, **_kwargs):
        calls.append(command)
        master_count = sum(1 for cmd in calls if cmd[0] == "ssh" and "-M" in cmd)
        if command[0] == "ssh" and "-M" in command and master_count == 1:
            raise subprocess.CalledProcessError(255, command, stderr="Connection closed by host")
        stdout = "[OK] 已写入微信公众号草稿: media-id" if "ORDO_WECHAT_VPS_WORKER=1" in command[-1] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        article = repo / "article.md"
        article.write_text("# article\n", encoding="utf-8")
        (repo / "secrets.env").write_text("VPS_IP=203.0.113.10\n", encoding="utf-8")
        adapter = WeChatPlatformAdapter(repo)
        with patch("ordo_engine.platforms.wechat.publisher.subprocess.run", side_effect=fake_run), \
             patch("ordo_engine.platforms.wechat.publisher.time.sleep") as sleep:
            adapter.publish(adapter.prepare(markdown_file=article, mode="draft"))

    masters = [cmd for cmd in calls if cmd[0] == "ssh" and "-M" in cmd]
    self.assertEqual(len(masters), 2)
    sleep.assert_called_once()

def test_wechat_worker_connection_loss_is_not_retried(self):
    calls = []
    def fake_run(command, **_kwargs):
        calls.append(command)
        if "ORDO_WECHAT_VPS_WORKER=1" in command[-1]:
            return subprocess.CompletedProcess(command, 255, stdout="", stderr="Connection closed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        article = repo / "article.md"
        article.write_text("# article\n", encoding="utf-8")
        (repo / "secrets.env").write_text("VPS_IP=203.0.113.10\n", encoding="utf-8")
        adapter = WeChatPlatformAdapter(repo)
        with patch("ordo_engine.platforms.wechat.publisher.subprocess.run", side_effect=fake_run):
            result = adapter.publish(adapter.prepare(markdown_file=article, mode="draft"))

    workers = [cmd for cmd in calls if "ORDO_WECHAT_VPS_WORKER=1" in cmd[-1]]
    self.assertEqual(len(workers), 1)
    self.assertTrue(result["remote_started"])
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv312/bin/python -m pytest tests/test_platform_contracts.py -k 'control_master or master_start or worker_connection_loss' -q
```

Expected: failures because no ControlMaster lifecycle or transport retry exists.

- [ ] **Step 3: Implement minimum ControlMaster lifecycle**

In `WeChatPlatformAdapter`, add lazy batch-owned socket state and these operations:

```python
def start_batch(self) -> None:
    self._ensure_master()

def close_batch(self) -> None:
    if not self._control_path:
        return
    subprocess.run(
        ["ssh", *self._base_ssh_options, "-S", self._control_path,
         "-O", "exit", self._target],
        capture_output=True, text=True,
    )
    Path(self._control_path).unlink(missing_ok=True)
    self._control_path = None

def _ensure_master(self) -> None:
    if self._control_path:
        return
    control_path = str(Path(tempfile.gettempdir()) / f"ordo-wechat-{os.getpid()}-{uuid.uuid4().hex[:8]}.sock")
    command = [
        "ssh", *self._base_ssh_options,
        "-M", "-N", "-f",
        "-o", "ControlMaster=yes",
        "-o", "ControlPersist=60",
        "-o", f"ControlPath={control_path}",
        self._target,
    ]
    for attempt in range(3):
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            self._control_path = control_path
            return
        except subprocess.CalledProcessError as exc:
            if attempt == 2 or not self._is_transport_error(exc):
                raise
            time.sleep(2 ** attempt)

@staticmethod
def _is_transport_error(exc: subprocess.CalledProcessError) -> bool:
    output = f"{exc.stdout or ''}\n{exc.stderr or ''}".lower()
    return any(marker in output for marker in (
        "connection closed", "connection reset", "broken pipe",
        "control socket connect", "mux_client_request_session",
    ))
```

Build SSH/SCP slave options with `-o ControlMaster=no` and the exact `ControlPath`. Route `mkdir` and uploads through a helper that retries recognized transport failures only before the worker command. Do not wrap the worker command in that retry helper. Set `remote_started=True` as soon as worker execution is attempted.

In `BatchCoordinator._run_wechat_batch()`, resolve `adapter = self.wechat_adapter or self.registry.get(WECHAT_PLATFORM)` before its existing article loop, wrap that loop in `try/finally`, and call `adapter.close_batch()` in `finally` when the method exists. This keeps current per-article cover and state behavior unchanged while guaranteeing batch-owned transport cleanup.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
.venv312/bin/python -m pytest tests/test_platform_contracts.py tests/test_batch_coordinator_safety.py -q
```

Expected: all tests pass; no network access occurs.

- [ ] **Step 5: Commit Task 1**

```bash
git add tests/test_platform_contracts.py ordo_engine/platforms/wechat/publisher.py ordo_engine/runner/pipeline.py
git commit -m "fix(wechat): reuse VPS SSH connection safely"
```

### Task 2: Build reports from current-run state changes only

**Files:**
- Modify: `tests/test_batch_coordinator_safety.py`
- Modify: `ordo_engine/runner/pipeline.py`

- [ ] **Step 1: Write failing tests for historical-state exclusion**

```python
def test_batch_summary_excludes_untouched_historical_success(tmp_path):
    article = _article(tmp_path / "a.md")
    coordinator = _coordinator(tmp_path)
    coordinator._articles["article-1"] = ArticleRecord(
        article_id="article-1",
        platforms={
            "wechat": {"draft": PlatformRecord(stage=PlatformStage.draft_saved, draft_ref="old")},
            "zhihu": {"publish": PlatformRecord(stage=PlatformStage.published, published_ref="old-url")},
        },
    )
    coordinator._batch_identities = {"article-1"}
    assert coordinator._build_summary()["articles"] == {}

def test_batch_summary_includes_only_touched_platform(tmp_path):
    coordinator = _coordinator(tmp_path)
    coordinator._articles["article-1"] = ArticleRecord(
        article_id="article-1",
        platforms={
            "zhihu": {"publish": PlatformRecord(stage=PlatformStage.published)},
        },
    )
    coordinator._batch_identities = {"article-1"}
    coordinator._record_error("article-1", "wechat", "draft", "transport failed")
    platforms = coordinator._build_summary()["articles"]["article-1"]["platforms"]
    assert set(platforms) == {"wechat:draft"}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py -k 'summary_excludes or summary_includes' -q
```

Expected: old records appear in `_build_summary()`.

- [ ] **Step 3: Track touched records through one wrapper**

Add `self._touched_records: set[tuple[str, str, str]]` and reset it at `run_batch()` start. Replace coordinator calls to `set_platform_record()` with:

```python
def _set_platform_record(self, identity, platform, mode, record):
    set_platform_record(self._articles, identity, platform, mode, record)
    self._touched_records.add((identity, platform, mode))
```

Change `_build_summary()` to omit identities with no touched records and include only touched platform/mode keys. Preserve article metadata and current error/reference fields.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py tests/test_report.py -q
```

Expected: all pass; old draft references never appear in a new empty summary.

- [ ] **Step 5: Commit Task 2**

```bash
git add tests/test_batch_coordinator_safety.py ordo_engine/runner/pipeline.py
git commit -m "fix(report): isolate current batch outcomes"
```

### Task 3: Reconcile completion and skip protected historical work

**Files:**
- Modify: `tests/test_batch_coordinator_safety.py`
- Modify: `tests/test_monitor_publish.py`
- Modify: `ordo_engine/runner/pipeline.py`
- Modify: `scripts/monitor_publish.py`

- [ ] **Step 1: Write failing completion and scan tests**

```python
def test_refresh_marks_fully_terminal_article_completed(tmp_path):
    coordinator = _coordinator(tmp_path)
    platforms = {
        "wechat": {"draft": PlatformRecord(stage=PlatformStage.draft_saved)},
        **{
            platform: {"publish": PlatformRecord(stage=PlatformStage.published)}
            for platform in pipeline_module.BROWSER_PLATFORMS_TUPLE
        },
    }
    coordinator._articles["article-1"] = ArticleRecord(
        article_id="article-1", platforms=platforms,
    )
    coordinator._batch_identities = {"article-1"}
    coordinator._refresh_article_stages()
    article = coordinator._articles["article-1"]
    assert article.article_stage == ArticleStage.completed
    assert article.completed_at

def test_scan_skips_pending_article_when_all_platforms_are_protected(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        article = root / "a.md"
        article.write_text("---\narticle_id: stable-a\ntitle: A\n---\nBody\n", encoding="utf-8")
        state_file = root / ".ordo" / "auto_publish_state.json"
        platforms = {
            "wechat": {"draft": PlatformRecord(stage=PlatformStage.draft_saved)},
            **{
                platform: {"publish": PlatformRecord(stage=PlatformStage.manual_verify)}
                for platform in monitor_publish.BROWSER_PLATFORMS
            },
        }
        save_v2_state({
            "stable-a": ArticleRecord(
                article_id="stable-a",
                source_path=str(article),
                article_stage=ArticleStage.pending,
                platforms=platforms,
            )
        }, state_file)
        with patch.object(monitor_publish, "STATE_FILE", state_file), \
             patch.object(monitor_publish, "PUBLISH_LOCK_FILE", root / ".ordo" / "publish.lock"), \
             patch.object(monitor_publish, "BatchCoordinator") as coordinator:
            coordinator.return_value.needs_any_processing.return_value = False
            self.assertEqual(monitor_publish.scan_once(root), [])
        coordinator.return_value.run_batch.assert_not_called()
```

- [ ] **Step 2: Run tests and verify RED**

```bash
.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py tests/test_monitor_publish.py -k 'fully_terminal or all_platforms_are_protected' -q
```

Expected: no refresh method exists and `scan_once()` includes the pending article.

- [ ] **Step 3: Add pure processing and completion checks**

In `BatchCoordinator`:

```python
def needs_any_processing(self, article: ArticleRecord) -> bool:
    if article.article_stage == ArticleStage.completed:
        return False
    pairs = [(WECHAT_PLATFORM, "draft"), *[(p, "publish") for p in BROWSER_PLATFORMS_TUPLE]]
    return any(self._needs_processing(article, platform, mode) for platform, mode in pairs)

def _refresh_article_stages(self) -> None:
    for identity in self._batch_identities:
        article = self._articles.get(identity)
        if article and is_article_completed(article, list(BROWSER_PLATFORMS_TUPLE)):
            article.article_stage = ArticleStage.completed
            article.completed_at = _now_iso()
```

Call `_refresh_article_stages()` before the final state save.

In `scan_once()`, create the coordinator before building `todo` and use `coordinator.needs_any_processing(existing)` instead of only checking `article_stage != completed`. Missing records remain eligible; protected `manual_verify`/`draft_saved`/`published` records remain out of the batch.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py tests/test_monitor_publish.py -q
```

Expected: all pass; no browser/VPS invocation.

- [ ] **Step 5: Commit Task 3**

```bash
git add tests/test_batch_coordinator_safety.py tests/test_monitor_publish.py ordo_engine/runner/pipeline.py scripts/monitor_publish.py
git commit -m "fix(monitor): stop rescanning protected outcomes"
```

### Task 4: Verify safety contracts and full suite

**Files:**
- Verify only: `ordo_engine/platforms/wechat/runtime.py`
- Verify only: `ordo_engine/platforms/wechat/publisher.py`
- Verify only: `scripts/monitor_publish.py`

- [ ] **Step 1: Verify no local WeChat API fallback**

```bash
rg -n "ORDO_WECHAT_VPS_WORKER|VPS_IP|ControlMaster|WECHAT_PROXY" ordo_engine/platforms/wechat wechat_publisher.py scripts/monitor_publish.py
```

Expected: API worker remains guarded; local coordinator only delegates through VPS; no proxy/local fallback.

- [ ] **Step 2: Run targeted regression suite**

```bash
.venv312/bin/python -m pytest tests/test_platform_contracts.py tests/test_batch_coordinator_safety.py tests/test_monitor_publish.py tests/test_report.py tests/test_wechat_vps_only.py -q
```

Expected: zero failures.

- [ ] **Step 3: Run complete suite**

```bash
.venv312/bin/python -m pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Inspect final diff and repository status**

```bash
git diff --check
git status --short
git log --oneline -5
```

Expected: no whitespace errors; unrelated existing untracked files remain untouched.

- [ ] **Step 5: Commit any final plan-only tracking update if needed**

No production changes are added in this step. Do not push.
