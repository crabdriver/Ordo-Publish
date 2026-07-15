# WeChat VPS-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make every normal WeChat draft path execute on the configured VPS and make local API execution fail closed.

**Architecture:** Restore the dedicated SSH/SCP `WeChatPlatformAdapter`, route both CLI and `BatchCoordinator` through it, and guard API entry points with `ORDO_WORKER=1`. Browser platforms remain local standalone; WeChat never runs browser or CDP preflight.

**Tech Stack:** Python, subprocess SSH/SCP, pytest, existing v2 run state.

---

### Task 1: Lock API entry points to VPS worker

**Files:**
- Create: `ordo_engine/platforms/wechat/runtime.py`
- Modify: `wechat_publisher.py`
- Modify: `ordo_engine/platforms/wechat/api.py`
- Modify: `scripts/publish.py`
- Test: `tests/test_wechat_vps_only.py`

- [x] **Step 1: Write failing guard tests**

```python
def test_local_wechat_worker_is_blocked(monkeypatch):
    monkeypatch.delenv("ORDO_WORKER", raising=False)
    with pytest.raises(WechatVpsOnlyError, match="VPS"):
        require_vps_worker()

def test_vps_worker_is_allowed(monkeypatch):
    monkeypatch.setenv("ORDO_WORKER", "1")
    require_vps_worker()
```

- [x] **Step 2: Run tests and confirm RED**

Run: `.venv312/bin/python -m pytest tests/test_wechat_vps_only.py -q`
Expected: import failure because `runtime.py` does not exist.

- [x] **Step 3: Implement shared guard and call it before API work**

```python
class WechatVpsOnlyError(RuntimeError):
    pass

def require_vps_worker():
    if os.environ.get("ORDO_WORKER") != "1":
        raise WechatVpsOnlyError("微信公众号 API 仅允许在 VPS worker 中执行")
```

Remove `WECHAT_PROXY` mutation. Call guard at each executable main and at shared token acquisition.

- [x] **Step 4: Run guard tests and confirm GREEN**

Run: `.venv312/bin/python -m pytest tests/test_wechat_vps_only.py -q`
Expected: all tests pass.

### Task 2: Restore dedicated VPS adapter

**Files:**
- Modify: `ordo_engine/platforms/wechat/publisher.py`
- Modify: `tests/test_platform_contracts.py`

- [x] **Step 1: Replace local-success contract with failing VPS contracts**

```python
def test_wechat_publish_refuses_local_without_vps_ip():
    result = adapter.publish(prepared)
    assert result["returncode"] == 2
    assert "必须走 VPS" in result["stderr"]

def test_wechat_remote_command_marks_worker_and_clears_proxy():
    assert "export ORDO_WORKER=1" in ssh_command
    assert "unset WECHAT_PROXY HTTP_PROXY HTTPS_PROXY" in ssh_command
```

- [x] **Step 2: Run focused tests and confirm RED**

Run: `.venv312/bin/python -m pytest tests/test_platform_contracts.py -q`
Expected: local command currently executes and no SSH command exists.

- [x] **Step 3: Restore SSH/SCP adapter**

Load `VPS_IP`, `VPS_PORT`, `VPS_USER`, `VPS_SSH_KEY`, and `VPS_PATH` from `secrets.env`; upload article, cover, and referenced images; execute `wechat_publisher.py` remotely with `ORDO_WORKER=1`; never fall back locally.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run: `.venv312/bin/python -m pytest tests/test_platform_contracts.py -q`
Expected: all tests pass.

### Task 3: Route BatchCoordinator through adapter

**Files:**
- Modify: `ordo_engine/runner/pipeline.py`
- Modify: `tests/test_batch_coordinator_safety.py`

- [x] **Step 1: Write failing delegation test**

```python
def test_wechat_batch_uses_vps_adapter_not_local_subprocess():
    coordinator._run_wechat_subprocess(article, cover)
    adapter.prepare.assert_called_once()
    adapter.publish.assert_called_once()
```

Assert `subprocess.run` is never called by `BatchCoordinator`.

- [x] **Step 2: Run focused test and confirm RED**

Run: `.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py -q`
Expected: current coordinator directly invokes local `wechat_publisher.py`.

- [x] **Step 3: Delegate to `WeChatPlatformAdapter` and preserve state mapping**

Use adapter `prepare(... mode="draft")`, `publish()`, and `collect_result()`. Record `draft_saved` only for an explicit draft marker; ambiguous/timeout results become `manual_verify`.

- [x] **Step 4: Run focused test and confirm GREEN**

Run: `.venv312/bin/python -m pytest tests/test_batch_coordinator_safety.py -q`
Expected: all tests pass.

### Task 4: Remove stale local-route documentation and verify

**Files:**
- Modify: `README.md`
- Modify: `tests/test_readme_contracts.py`

- [x] **Step 1: Add README contract assertions**

```python
assert "微信公众号必须走 VPS" in readme
assert "微信走官方 API" not in local_default_paragraph
```

- [x] **Step 2: Update README to state exact split**

Document WeChat via VPS fixed IP and five browser platforms via local isolated owned browser.

- [x] **Step 3: Run focused and full verification**

Run: `.venv312/bin/python -m pytest tests/test_wechat_vps_only.py tests/test_platform_contracts.py tests/test_batch_coordinator_safety.py tests/test_readme_contracts.py -q`
Expected: all focused tests pass.

Run: `.venv312/bin/python -m pytest -q`
Expected: full suite passes.

- [x] **Step 4: Audit forbidden paths and commit**

Run: `rg -n "WECHAT_PROXY|Run WeChat official-API publisher in current execution environment|直接.*wechat_publisher" wechat_publisher.py ordo_engine scripts README.md tests`
Expected: no local WeChat publication route remains.

Commit: `fix(wechat): enforce VPS-only draft publishing`
