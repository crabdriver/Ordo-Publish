#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
import hashlib
import datetime
from pathlib import Path

# Ensure codebase root is in Python load path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ordo_engine.runner.pipeline import run_publish_pipeline
from ordo_engine.runner import db as db_helper
from ordo_engine.results.errors import ErrorType


class DummyArgs:
    def __init__(self, mode="publish", continue_on_error=True):
        self.mode = mode
        self.continue_on_error = continue_on_error


def get_file_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def get_now_str() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def get_tomorrow_9am_str() -> str:
    now_local = datetime.datetime.now()
    tomorrow_local = now_local + datetime.timedelta(days=1)
    tomorrow_9am_local = tomorrow_local.replace(hour=9, minute=0, second=0, microsecond=0)
    tomorrow_9am_utc = tomorrow_9am_local.astimezone(datetime.timezone.utc)
    return tomorrow_9am_utc.strftime("%Y-%m-%d %H:%M:%S")


def update_overall_job_status(db_path: Path, job_id: str):
    tasks = db_helper.get_job_tasks(db_path, job_id)
    if not tasks:
        return
    all_success = True
    any_deferred = False
    for t in tasks:
        status = t["status"]
        if status not in ("published", "draft_saved", "scheduled"):
            all_success = False
        if status == "deferred_limit":
            any_deferred = True
            
    if all_success:
        new_status = "completed"
    elif any_deferred:
        new_status = "deferred"
    else:
        new_status = "failed"
        
    db_helper.update_job_status(
        db_path=db_path,
        job_id=job_id,
        status=new_status,
        finished_at=get_now_str()
    )


def execute_tasks_loop(db_path: Path, job_id: str, tasks_to_run, job_dir: Path, mode: str, cover_mappings: dict, theme_mappings: dict = None):
    from ordo_engine.runner.pipeline import run_platform_task
    from ordo_engine.platforms.registry import build_platform_registry
    
    registry = build_platform_registry(BASE_DIR)
    
    # Inject CDP port to environment
    os.environ.setdefault("LIVE_CDP_PORT", "9333")
    os.environ["ORDO_WORKER"] = "1"
    os.environ["ORDO_WECHAT_VPS_WORKER"] = "1"
    
    blocked_platforms = set()
    
    for task in tasks_to_run:
        task_id = task["task_id"]
        platform = task["platform"]
        
        if platform in blocked_platforms:
            print(f"[INFO] Skipping task {task_id} on {platform} because login is required.")
            db_helper.update_task(
                db_path, task_id, status="failed", last_error="Skipped because login is required on this platform."
            )
            continue
            
        article_path = job_dir / task["article_path"]
        article_id = task["article_id"]
        
        published_count = get_daily_published_count(db_path, platform)
        print(f"[INFO] Platform {platform} daily published count so far: {published_count}")
        print(f"[INFO] Running task {task_id} on {platform} for {task['article_path']}...")
        
        # Update task status to running and increment attempts
        attempts = task["attempts"] + 1
        db_helper.update_task(
            db_path, task_id, status="running", attempts=attempts
        )
        
        try:
            cover_path = None
            art_name = Path(article_path).name
            if art_name in cover_mappings and platform in cover_mappings[art_name]:
                cover_path = cover_mappings[art_name][platform]
            theme_name = None
            if theme_mappings and art_name in theme_mappings and platform in theme_mappings[art_name]:
                theme_name = theme_mappings[art_name][platform]
                
            result = run_platform_task(
                base_dir=BASE_DIR,
                platform=platform,
                markdown_file=str(article_path),
                mode=mode,
                theme_name=theme_name,
                template_mode="custom" if theme_name else None,
                cover_path=cover_path,
                article_id=article_id,
            )
            
            adapter = registry[platform]
            execution_result = adapter.collect_result(result, mode)
            status = execution_result.status
            summary = execution_result.summary
            retryable = execution_result.retryable
            
            # Print execution outcome to stdout
            print(f"[RESULT] {platform} -> {status} (Exit Code: {result.get('returncode')})")
            if result.get("stderr"):
                print(f"  Error details:\n{result.get('stderr')}")
            
            # Immediate retry loop for transient error
            max_attempts = task["max_attempts"]
            while status == "failed" and retryable and attempts < max_attempts:
                print(f"[WARN] Task {task_id} failed with transient error: {summary}. Retrying immediately (attempt {attempts + 1}/{max_attempts})...")
                time.sleep(5)
                attempts += 1
                db_helper.update_task(
                    db_path, task_id, status="running", attempts=attempts
                )
                result = run_platform_task(
                    base_dir=BASE_DIR,
                    platform=platform,
                    markdown_file=str(article_path),
                    mode=mode,
                    theme_name=theme_name,
                    template_mode="custom" if theme_name else None,
                    cover_path=cover_path,
                    article_id=article_id,
                )
                execution_result = adapter.collect_result(result, mode)
                status = execution_result.status
                summary = execution_result.summary
                retryable = execution_result.retryable
                print(f"[RESULT] {platform} -> {status} (Exit Code: {result.get('returncode')})")
                
            if status in ("published", "draft_only", "scheduled"):
                final_status = {"published": "published", "draft_only": "draft_saved", "scheduled": "scheduled"}[status]
                db_helper.update_task(
                    db_path, task_id, status=final_status,
                    attempts=attempts,
                    raw_result_json=json.dumps(result, ensure_ascii=False)
                )
                published_count = get_daily_published_count(db_path, platform)
                print(f"[SUCCESS] Task {task_id} completed: {final_status}. Platform {platform} daily published count: {published_count}")
            elif status == "limit_reached":
                next_run = get_tomorrow_9am_str()
                db_helper.update_task(
                    db_path, task_id, status="deferred_limit",
                    attempts=attempts,
                    next_run_at=next_run,
                    last_error=summary,
                    raw_result_json=json.dumps(result, ensure_ascii=False)
                )
                published_count = get_daily_published_count(db_path, platform)
                print(f"[INFO] Task {task_id} rate limited, deferred until {next_run}. Platform {platform} daily published count: {published_count}")
                start_daemon_background()
            else:
                db_helper.update_task(
                    db_path, task_id, status="failed",
                    attempts=attempts,
                    last_error=summary,
                    raw_result_json=json.dumps(result, ensure_ascii=False)
                )
                print(f"[ERROR] Task {task_id} failed after all retries: {summary}")
                
                # Check for login required error to display tunnel instructions
                error_type = execution_result.error_type
                if error_type == ErrorType.LOGIN_REQUIRED:
                    blocked_platforms.add(platform)
                    print("\n" + "="*80)
                    print(f"[WARNING] 平台 {platform} 登录已失效或需要人工安全验证！")
                    print("请按照以下步骤在本地进行接管登录与安全校验：")
                    print("1. 在本地终端执行 SSH 隧道命令建立端口映射（替换为您的 VPS 用户与 IP）：")
                    print("   ssh -N -L 9999:127.0.0.1:9333 <Your-VPS-User>@<Your-VPS-IP>")
                    print("2. 在本地 Chrome 浏览器打开开发者页面：")
                    print("   chrome://inspect")
                    print("3. 点击 'Configure...'，添加 'localhost:9999'，保存并等待 Remote Target 加载。")
                    print("4. 在显示的列表下，找到对应平台的网页，点击 'Inspect' 打开互动调试面板，完成扫码登录或滑块验证。")
                    print("5. 完成后，在远端或本地运行 resume 命令续跑任务。")
                    print("="*80 + "\n")
                
        except Exception as e:
            import traceback
            error_msg = f"Exception: {str(e)}\n{traceback.format_exc()}"
            db_helper.update_task(
                db_path, task_id, status="failed",
                attempts=attempts,
                last_error=error_msg
            )
            print(f"[ERROR] Task {task_id} raised an exception: {e}")


def get_daily_published_count(db_path: Path, platform: str) -> int:
    conn = db_helper.get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as count FROM tasks WHERE platform = ? AND status = 'published' AND date(updated_at) = date('now')",
            (platform,)
        ).fetchone()
        return row["count"] if row else 0
    except Exception as e:
        print(f"[WARN] Failed to query daily published count: {e}")
        return 0
    finally:
        conn.close()


def is_daemon_running() -> bool:
    lock_file = BASE_DIR / "data" / "ordo_daemon.pid"
    if not lock_file.exists():
        return False
    try:
        pid = int(lock_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def start_daemon_background():
    if is_daemon_running():
        print("[INFO] Background daemon is already running.")
        return
    
    import subprocess
    log_file = BASE_DIR / "data" / "ordo_daemon.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(log_file, "a") as f:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "daemon"],
            stdout=f,
            stderr=f,
            start_new_session=True
        )
    print("[INFO] Background daemon launched.")


def stop_daemon():
    lock_file = BASE_DIR / "data" / "ordo_daemon.pid"
    if not lock_file.exists():
        print("[INFO] No daemon PID file found. Daemon is not running.")
        return
    try:
        pid = int(lock_file.read_text().strip())
        os.kill(pid, 15) # SIGTERM
        print(f"[INFO] Sent termination signal to daemon process {pid}.")
        time.sleep(1)
        if not is_daemon_running():
            lock_file.unlink(missing_ok=True)
    except (ValueError, OSError) as e:
        print(f"[ERROR] Failed to stop daemon: {e}")
        lock_file.unlink(missing_ok=True)


def daemon_status():
    lock_file = BASE_DIR / "data" / "ordo_daemon.pid"
    log_file = BASE_DIR / "data" / "ordo_daemon.log"
    
    running = is_daemon_running()
    print(f"Daemon Status: {'RUNNING' if running else 'STOPPED'}")
    if lock_file.exists():
        print(f"PID File: {lock_file} (PID: {lock_file.read_text().strip()})")
    if log_file.exists():
        print(f"Log File: {log_file}")
        print("Last 10 log lines:")
        try:
            lines = log_file.read_text().splitlines()
            for line in lines[-10:]:
                print(f"  {line}")
        except Exception as e:
            print(f"  Failed to read log: {e}")


def run_daemon_tick(db_path: Path) -> int:
    pending = db_helper.get_pending_tasks(db_path)
    if not pending:
        return 0
    print(f"[DAEMON] {get_now_str()} - Found {len(pending)} pending/deferred tasks. Executing resume...")
    resume_jobs()
    return len(pending)


def run_daemon(interval=60):
    if is_daemon_running():
        print("[ERROR] Another daemon instance is already running.")
        sys.exit(1)
        
    pid_file = BASE_DIR / "data" / "ordo_daemon.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    
    print(f"[INFO] Daemon process started (PID: {os.getpid()}). Checking every {interval}s...")
    
    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    db_helper.init_db(db_path)
    
    try:
        while True:
            run_daemon_tick(db_path)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[INFO] Daemon stopped by user.")
    finally:
        pid_file.unlink(missing_ok=True)


def run_job(bundle_zip_path: str):
    zip_path = Path(bundle_zip_path).resolve()
    if not zip_path.exists():
        print(f"[ERROR] Bundle file not found: {zip_path}")
        sys.exit(1)

    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    db_helper.init_db(db_path)

    # 1. Prepare job directory
    jobs_dir = BASE_DIR / "data" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Read manifest to extract Job ID
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            manifest_data = zip_ref.read("manifest.json")
            manifest = json.loads(manifest_data.decode("utf-8"))
    except Exception as e:
        print(f"[ERROR] Failed to read manifest.json from bundle: {e}")
        sys.exit(1)

    job_id = manifest.get("job_id", f"job_{int(time.time())}")
    job_dir = jobs_dir / job_id
    
    # Calculate bundle hash
    try:
        bundle_hash = get_file_sha256(zip_path)
    except Exception as e:
        print(f"[ERROR] Failed to compute bundle SHA256: {e}")
        sys.exit(1)

    # Check database for existing job/hash
    existing_job = db_helper.get_job(db_path, job_id)
    if not existing_job:
        existing_job = db_helper.get_job_by_hash(db_path, bundle_hash)

    # Prepare directories
    if job_dir.exists():
        print(f"[INFO] Cleaning up existing job directory: {job_dir}")
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True)

    # Extract contents
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(job_dir)
        print(f"[INFO] Unpacked bundle successfully to {job_dir}")
    except Exception as e:
        print(f"[ERROR] Failed to unpack zip bundle: {e}")
        sys.exit(1)

    # Setup database records
    platforms = manifest.get("platforms", [])
    mode = manifest.get("mode", "publish")
    
    if existing_job:
        job_id = existing_job["job_id"]
        print(f"[INFO] Job {job_id} already exists in DB. Status: {existing_job['status']}")
        if existing_job["status"] == "completed":
            print(f"[INFO] Job {job_id} is already completed. Skipping execution.")
            sys.exit(0)
            
        # Check if bundle hash is different
        if existing_job["bundle_hash"] != bundle_hash:
            print(f"[INFO] Bundle hash changed from {existing_job['bundle_hash']} to {bundle_hash}. Recreating tasks.")
            # Update job details
            db_helper.update_job_status(db_path, job_id, "running", started_at=get_now_str())
            
            conn = db_helper.get_db_connection(db_path)
            try:
                conn.execute(
                    "UPDATE jobs SET bundle_hash = ?, source_bundle = ?, policy_json = ? WHERE job_id = ?",
                    (bundle_hash, str(zip_path), json.dumps(manifest.get("policy", {})), job_id)
                )
                conn.commit()
            finally:
                conn.close()
                
            db_helper.delete_job_tasks(db_path, job_id)
            
            # Create new tasks
            for art in manifest.get("articles", []):
                article_id = art["article_id"]
                title = art.get("title", "")
                art_rel_path = art["markdown_path"]
                
                for platform in platforms:
                    task_id = f"{job_id}_{article_id}_{platform}"
                    db_helper.create_task(
                        db_path=db_path,
                        task_id=task_id,
                        job_id=job_id,
                        article_id=article_id,
                        title=title,
                        article_path=art_rel_path,
                        platform=platform,
                        target_action=mode,
                        executor="LocalSubprocessExecutor",
                        status="pending",
                        scheduled_at=get_now_str(),
                        max_attempts=3
                    )
        else:
            # We will resume the pending/failed tasks
            db_helper.update_job_status(db_path, job_id, "running", started_at=get_now_str())
    else:
        policy_json = json.dumps(manifest.get("policy", {}))
        db_helper.create_job(db_path, job_id, mode, str(zip_path), bundle_hash, policy_json)
        db_helper.update_job_status(db_path, job_id, "running", started_at=get_now_str())
        
        # Create tasks
        for art in manifest.get("articles", []):
            article_id = art["article_id"]
            title = art.get("title", "")
            art_rel_path = art["markdown_path"]
            
            for platform in platforms:
                task_id = f"{job_id}_{article_id}_{platform}"
                db_helper.create_task(
                    db_path=db_path,
                    task_id=task_id,
                    job_id=job_id,
                    article_id=article_id,
                    title=title,
                    article_path=art_rel_path,
                    platform=platform,
                    target_action=mode,
                    executor="LocalSubprocessExecutor",
                    status="pending",
                    scheduled_at=get_now_str(),
                    max_attempts=3
                )

    # Prepare cover mappings
    cover_mappings = {}
    theme_mappings = {}
    for art in manifest.get("articles", []):
        art_rel_path = art["markdown_path"]
        art_abs_path = job_dir / art_rel_path
        art_name = art_abs_path.name
        cover_mappings[art_name] = {}
        for platform, cov_rel in art.get("covers", {}).items():
            if cov_rel:
                cover_mappings[art_name][platform] = job_dir / cov_rel
        theme_mappings[art_name] = dict(art.get("themes", {}))

    # Retrieve tasks that are not yet successful for this job
    all_job_tasks = db_helper.get_job_tasks(db_path, job_id)
    tasks_to_run = [t for t in all_job_tasks if t["status"] not in ("published", "draft_saved", "scheduled")]

    print(f"[INFO] Starting execution for Job: {job_id} ({len(tasks_to_run)} tasks to run)")
    try:
        execute_tasks_loop(db_path, job_id, tasks_to_run, job_dir, mode, cover_mappings, theme_mappings)
    finally:
        # Update job status
        update_overall_job_status(db_path, job_id)
    
    # Check final job status
    final_job = db_helper.get_job(db_path, job_id)
    print(f"[INFO] Job {job_id} execution completed. Status: {final_job['status']}")
    if final_job["status"] == "failed":
        sys.exit(1)
    else:
        sys.exit(0)


def resume_jobs(job_id_filter: str = None):
    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    db_helper.init_db(db_path)
    
    pending_tasks = db_helper.get_pending_tasks(db_path)
    if job_id_filter:
        pending_tasks = [t for t in pending_tasks if t["job_id"] == job_id_filter]
        
    if not pending_tasks:
        print("[INFO] No pending, failed, or deferred tasks require execution.")
        return
        
    from collections import defaultdict
    tasks_by_job = defaultdict(list)
    for t in pending_tasks:
        tasks_by_job[t["job_id"]].append(t)
        
    for job_id, tasks in tasks_by_job.items():
        print(f"[INFO] Resuming {len(tasks)} tasks for Job {job_id}...")
        
        job_dir = BASE_DIR / "data" / "jobs" / job_id
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"[ERROR] Manifest not found for job {job_id} at {manifest_path}. Cannot resume tasks.")
            db_helper.update_job_status(db_path, job_id, "failed")
            continue
            
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load manifest for job {job_id}: {e}")
            continue
            
        mode = manifest.get("mode", "publish")
        
        cover_mappings = {}
        theme_mappings = {}
        for art in manifest.get("articles", []):
            art_name = Path(art["markdown_path"]).name
            cover_mappings[art_name] = {}
            for platform, cov_rel in art.get("covers", {}).items():
                if cov_rel:
                    cover_mappings[art_name][platform] = job_dir / cov_rel
            theme_mappings[art_name] = dict(art.get("themes", {}))
                    
        db_helper.update_job_status(db_path, job_id, "running", started_at=get_now_str())
        
        try:
            execute_tasks_loop(db_path, job_id, tasks, job_dir, mode, cover_mappings, theme_mappings)
        finally:
            update_overall_job_status(db_path, job_id)
 
 
def list_jobs():
    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    db_helper.init_db(db_path)
    
    jobs = db_helper.get_recent_jobs(db_path, limit=20)
    if not jobs:
        print("No jobs found in database.")
        return
        
    print(f"{'Job ID':<30} | {'Mode':<8} | {'Status':<10} | {'Created At':<19} | {'Tasks (P/R/S/D/F)'}")
    print("-" * 90)
    
    job_ids = [job["job_id"] for job in jobs]
    task_counts = db_helper.get_job_task_counts(db_path, job_ids)
    
    for job in jobs:
        job_id = job["job_id"]
        counts = task_counts.get(job_id, {})
        
        pending = counts.get("pending", 0)
        running = counts.get("running", 0)
        success = counts.get("published", 0) + counts.get("draft_saved", 0) + counts.get("scheduled", 0)
        deferred = counts.get("deferred_limit", 0)
        failed = counts.get("failed", 0)
        
        status_str = f"{pending}/{running}/{success}/{deferred}/{failed}"
        print(f"{job_id:<30} | {job['mode']:<8} | {job['status']:<10} | {job['created_at']:<19} | {status_str}")


def build_status_report(db_path: Path) -> str:
    db_helper.init_db(db_path)
    jobs = db_helper.get_recent_jobs(db_path, limit=100)
    tasks = db_helper.get_all_tasks(db_path)
    if not jobs:
        return "No jobs found."

    tasks_by_job = {}
    for task in tasks:
        tasks_by_job.setdefault(task["job_id"], []).append(task)

    lines = ["=== Ordo Publish Status ==="]
    for job in jobs:
        job_tasks = tasks_by_job.get(job["job_id"], [])
        counts = {}
        for task in job_tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        count_text = ", ".join(f"{status}: {counts[status]}" for status in sorted(counts)) or "no tasks"
        lines.append(f"{job['job_id']} | {job['mode']} | {job['status']} | {count_text}")
        for task in job_tasks:
            if task["status"] in {"published", "draft_saved", "scheduled"}:
                continue
            next_run = f" | next: {task['next_run_at']}" if task.get("next_run_at") else ""
            error = f" | {str(task.get('last_error') or '').splitlines()[0]}" if task.get("last_error") else ""
            lines.append(
                f"  - {task['platform']} | {task['status']} | {task['article_path']}{next_run}{error}"
            )
    return "\n".join(lines)


def print_status_report():
    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    print(build_status_report(db_path))


def job_status(job_id: str):
    db_path = BASE_DIR / "data" / "ordo_tasks.db"
    db_helper.init_db(db_path)
    
    job = db_helper.get_job(db_path, job_id)
    if not job:
        print(f"[ERROR] Job {job_id} not found in database.")
        return
        
    print(f"=== Job Details: {job_id} ===")
    print(f"  Mode:         {job['mode']}")
    print(f"  Status:       {job['status']}")
    print(f"  Created At:   {job['created_at']}")
    print(f"  Started At:   {job['started_at'] or 'N/A'}")
    print(f"  Finished At:  {job['finished_at'] or 'N/A'}")
    print(f"  Source ZIP:   {job['source_bundle']}")
    print(f"  Bundle Hash:  {job['bundle_hash']}")
    
    tasks = db_helper.get_job_tasks(db_path, job_id)
    print(f"\n=== Tasks ({len(tasks)}): ===")
    for t in tasks:
        print(f"  Task ID:      {t['task_id']}")
        print(f"    Platform:   {t['platform']}")
        print(f"    Article:    {t['article_path']} (ID: {t['article_id']})")
        print(f"    Status:     {t['status']}")
        print(f"    Attempts:   {t['attempts']}/{t['max_attempts']}")
        if t['next_run_at']:
            print(f"    Next Run:   {t['next_run_at']}")
        if t['last_error']:
            err_lines = t['last_error'].splitlines()
            err_summary = err_lines[0] if err_lines else ""
            print(f"    Last Error: {err_summary}")
            if len(err_lines) > 1:
                print("    Error Details:")
                for line in err_lines[:5]:
                    print(f"      {line}")
                if len(err_lines) > 5:
                    print("      ...")
        print("-" * 40)


def check_login_status():
    print("=== Platform Login Status ===")
    
    # 1. Check configured managed Chrome port.
    import socket
    port = int(os.environ.get("LIVE_CDP_PORT", "9333"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    chrome_running = False
    try:
        s.connect(("127.0.0.1", port))
        chrome_running = True
    except Exception:
        pass
    finally:
        s.close()
        
    if not chrome_running:
        print(f"[ERROR] Chrome is offline (CDP port {port} is not listening).")
        print("Please start Chrome first using: python3 ordo_worker.py start-browser")
        return

    # 2. Get active tabs using node live_cdp.mjs list
    from ordo_engine.platforms.browser.node_runtime import resolve_node_executable
    try:
        node_bin = resolve_node_executable()
    except Exception as e:
        print(f"[ERROR] Failed to resolve Node executable: {e}")
        return
        
    cdp_script = BASE_DIR / "live_cdp.mjs"
    
    try:
        res = subprocess.run(
            [node_bin, str(cdp_script), "list"],
            cwd=str(BASE_DIR),
            capture_output=True, text=True, check=True, timeout=15
        )
        output = res.stdout.strip()
    except Exception as e:
        print(f"[ERROR] Failed to list Chrome tabs: {e}")
        return
        
    tabs = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            tabs.append({"id": parts[0], "title": parts[1], "url": parts[2]})
            
    # Platform matches definition
    PLATFORM_MATCHES = {
        "zhihu": ["zhihu.com"],
        "toutiao": ["toutiao.com"],
        "jianshu": ["jianshu.com"],
        "yidian": ["yidianzixun.com", "yidian.com"],
    }
    
    # Expressions for state inspection
    EXPRESSIONS = {
        "zhihu": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = document.querySelector('.public-DraftEditor-content, .ProseMirror, [data-lexical-editor="true"], [contenteditable="true"]');
  if (titleEl && editor) {
    return JSON.stringify({ page_state: 'editor_ready', editor_ready: true, detail: '写作编辑器已就绪' });
  }
  if (text.includes('登录') || text.includes('验证码')) {
    return JSON.stringify({ page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('/write') && !href.includes('/creator')) {
    return JSON.stringify({ page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在知乎写作页' });
  }
  return JSON.stringify({ page_state: 'editor_missing', editor_ready: false, detail: '已进入知乎域名，但未检测到标题框或正文编辑器' });
})()
        """.strip(),
        "toutiao": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('textarea[placeholder="请输入文章标题（2～30个字）"]');
  const editor = document.querySelector('.ProseMirror');
  if (titleEl && editor) {
    return JSON.stringify({ page_state: 'editor_ready', editor_ready: true, detail: '图文编辑器已就绪' });
  }
  if (text.includes('登录') || text.includes('验证码')) {
    return JSON.stringify({ page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('mp.toutiao.com') && !href.includes('/graphic/publish')) {
    return JSON.stringify({ page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在头条号图文写作页' });
  }
  return JSON.stringify({ page_state: 'editor_missing', editor_ready: false, detail: '已进入头条号发文域，但未检测到标题框或正文编辑器' });
})()
        """.strip(),
        "yidian": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector("input.post-title");
  const editor = document.querySelector(".editor-content[contenteditable='true']");
  if (titleEl && editor) {
    return JSON.stringify({ page_state: 'editor_ready', editor_ready: true, detail: '一点号编辑器已就绪' });
  }
  if (text.includes('登录') || text.includes('验证码')) {
    return JSON.stringify({ page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('/Writing/articleEditor')) {
    return JSON.stringify({ page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在一点号发文编辑页' });
  }
  return JSON.stringify({ page_state: 'editor_missing', editor_ready: false, detail: '已进入一点号发文页，但未检测到标题框或正文编辑器' });
})()
        """.strip(),
        "jianshu": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('input._24i7u');
  const editor = document.querySelector('textarea._3swFR.source');
  if (titleEl && editor) {
    return JSON.stringify({ page_state: 'editor_ready', editor_ready: true, detail: '简书编辑器已就绪' });
  }
  if (text.includes('登录') || text.includes('验证码')) {
    return JSON.stringify({ page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('jianshu.com')) {
    return JSON.stringify({ page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在简书域名' });
  }
  return JSON.stringify({ page_state: 'editor_missing', editor_ready: false, detail: '已进入简书域名，但未检测到标题框或正文编辑器' });
})()
        """.strip(),
    }
    
    for platform, matches in PLATFORM_MATCHES.items():
        # Find if a tab matches
        target_tab = None
        for tab in tabs:
            if any(match in tab["url"] for match in matches):
                target_tab = tab
                break
                
        if not target_tab:
            print(f"  {platform:<8} : [OFFLINE] 暂无打开的标签页 (请访问平台页面完成首次登录)")
            continue
            
        # Inspect tab state
        expr = EXPRESSIONS[platform]
        try:
            eval_res = subprocess.run(
                [node_bin, str(cdp_script), "eval", target_tab["id"], expr],
                cwd=str(BASE_DIR),
                capture_output=True, text=True, check=True, timeout=5
            )
            state = json.loads(eval_res.stdout.strip())
            page_state = state.get("page_state", "unknown")
            detail = state.get("detail", "")
            
            if page_state == "editor_ready":
                print(f"  {platform:<8} : [HEALTHY] 登录状态正常 - {detail}")
            elif page_state == "login_required":
                print(f"  {platform:<8} : [LOGIN_REQUIRED] 登录已失效或需要滑块验证！")
            else:
                print(f"  {platform:<8} : [{page_state.upper()}] {detail} (URL: {target_tab['url']})")
        except Exception as e:
            print(f"  {platform:<8} : [ERROR] 无法查询状态 - {e}")


def run_doctor():
    print("=== ordo-worker Doctor System Status ===")
    print(f"Base Repository Directory: {BASE_DIR}")

    # 1. Python Check
    print("\n[1] Checking Python Engine Environment...")
    print(f"  Python Path: {sys.executable}")
    print(f"  Python Version: {sys.version.splitlines()[0]}")
    try:
        from ordo_engine.config import load_engine_config
        config = load_engine_config(BASE_DIR)
        print("  ordo_engine core: Load successful")
    except Exception as e:
        print(f"  ordo_engine core: Load FAILED ({e})")

    # 2. Node Check
    print("\n[2] Checking Node.js Environment...")
    from ordo_engine.platforms.browser.node_runtime import resolve_node_executable
    try:
        node_bin = resolve_node_executable()
        print(f"  Node Executable: Found ({node_bin})")
        res = subprocess.run([node_bin, "-v"], capture_output=True, text=True)
        print(f"  Node Version: {res.stdout.strip()}")
    except Exception as e:
        print(f"  Node Executable: FAILED to resolve ({e})")

    # 3. CDP / Chrome check
    print("\n[3] Checking Chrome Debugging Port status...")
    import socket
    port = 9333
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        print(f"  CDP Port {port}: Active (Chrome is running and listening on localhost)")
    except Exception:
        print(f"  CDP Port {port}: Inactive (Chrome is not running or listening elsewhere)")
    finally:
        s.close()

    print("\n=== System Status Doctor Check Finished ===")


def is_proxy_available(host="127.0.0.1", port=7890) -> bool:
    """Detect if the local proxy tunnel is open."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def start_browser(port: int, profile_dir: str, use_xvfb: bool = False):
    """Start Chrome in remote debugging mode, optionally inside Xvfb with VNC/noVNC."""
    import socket
    
    # 1. Check if port is already in use
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        print(f"[WARN] Port {port} is already active. Chrome might be running.")
        return
    except Exception:
        pass
    finally:
        s.close()

    # 2. Resolve Chrome binary path
    _CHROME_CANDIDATES = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ]
    chrome_bin = None
    for cand in _CHROME_CANDIDATES:
        path = shutil.which(cand)
        if path:
            chrome_bin = path
            break
            
    if not chrome_bin:
        print(f"[ERROR] Chrome/Chromium binary not found on VPS. Checked candidates: {_CHROME_CANDIDATES}")
        sys.exit(1)

    print(f"[INFO] Using Chrome binary: {chrome_bin}")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    # 3. Setup Xvfb graphics display environment if requested
    chrome_env = os.environ.copy()
    if use_xvfb:
        display = ":99"
        xvfb_pid_file = Path(f"/tmp/ordo-xvfb-{port}.pid")
        if not xvfb_pid_file.exists():
            xvfb_bin = shutil.which("Xvfb")
            if not xvfb_bin:
                print("[ERROR] Xvfb binary not found. Please install xvfb first.")
                sys.exit(1)
            xvfb_cmd = [xvfb_bin, display, "-ac", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"]
            print(f"[INFO] Starting Xvfb: {' '.join(xvfb_cmd)}")
            xvfb_log = open("/tmp/ordo-xvfb.log", "w")
            xvfb_proc = subprocess.Popen(xvfb_cmd, stdout=xvfb_log, stderr=xvfb_log, preexec_fn=os.setsid)
            xvfb_log.close()
            xvfb_pid_file.write_text(str(xvfb_proc.pid))
            time.sleep(2) # wait for display server boot
        
        chrome_env["DISPLAY"] = display

        # Start x11vnc
        vnc_pid_file = Path(f"/tmp/ordo-x11vnc-{port}.pid")
        if not vnc_pid_file.exists():
            vnc_bin = shutil.which("x11vnc")
            if vnc_bin:
                vnc_cmd = [vnc_bin, "-display", display, "-forever", "-shared", "-nopw", "-listen", "127.0.0.1", "-xkb"]
                print(f"[INFO] Starting x11vnc: {' '.join(vnc_cmd)}")
                vnc_log = open("/tmp/ordo-x11vnc.log", "w")
                vnc_proc = subprocess.Popen(vnc_cmd, stdout=vnc_log, stderr=vnc_log, preexec_fn=os.setsid)
                vnc_log.close()
                vnc_pid_file.write_text(str(vnc_proc.pid))
                time.sleep(1)

        # Start websockify for noVNC
        websockify_pid_file = Path(f"/tmp/ordo-websockify-{port}.pid")
        if not websockify_pid_file.exists():
            websockify_bin = shutil.which("websockify")
            if websockify_bin:
                novnc_web_dir = "/usr/share/novnc"
                if not os.path.exists(novnc_web_dir):
                    novnc_web_dir = "/tmp/mock-novnc"
                    os.makedirs(novnc_web_dir, exist_ok=True)
                websock_cmd = [websockify_bin, "--web", novnc_web_dir, "6080", "127.0.0.1:5900"]
                print(f"[INFO] Starting websockify: {' '.join(websock_cmd)}")
                websock_log = open("/tmp/ordo-websockify.log", "w")
                websock_proc = subprocess.Popen(websock_cmd, stdout=websock_log, stderr=websock_log, preexec_fn=os.setsid)
                websock_log.close()
                websockify_pid_file.write_text(str(websock_proc.pid))
                time.sleep(1)

    cmd = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if not use_xvfb:
        cmd.append("--headless=new")

    # 4. Detect Clash proxy tunnel
    if is_proxy_available():
        print("[INFO] Active local proxy tunnel found at 127.0.0.1:7890. Booting Chrome with proxy.")
        cmd.append("--proxy-server=http://127.0.0.1:7890")
    else:
        print("[INFO] Proxy tunnel offline. Chrome will use direct internet access.")

    # 5. Launch daemonized process
    log_file_path = "/tmp/ordo-chrome.log"
    log_file = open(log_file_path, "w")
    print(f"[INFO] Launching process: {' '.join(cmd)}")
    
    # os.setsid detaches process from terminal so it survives SSH logout
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        preexec_fn=os.setsid,
        env=chrome_env,
    )

    # Close the file handle in parent process; Chrome child already has its own FD copy
    log_file.close()

    pid_file = Path(f"/tmp/ordo-chrome-{port}.pid")
    pid_file.write_text(str(proc.pid))
    print(f"[SUCCESS] Chrome launched. PID: {proc.pid} | Logs: {log_file_path}")


def stop_browser(port: int):
    """Find and terminate Chrome running on the debugging port and stop graphic services."""
    pid_file = Path(f"/tmp/ordo-chrome-{port}.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
            print(f"[INFO] Terminated Chrome PID {pid} via pidfile")
            pid_file.unlink()
        except ProcessLookupError:
            pid_file.unlink()
        except Exception as e:
            print(f"[WARN] Pidfile terminate failed: {e}")
            pid_file.unlink()

    # Fallback to process listing search
    print("[INFO] Checking running processes for Chrome debug instances...")
    try:
        import psutil
        killed = False
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = proc.info.get("cmdline") or []
            if any(f"--remote-debugging-port={port}" in arg for arg in cmd):
                try:
                    proc.terminate()
                    print(f"[INFO] Terminated proc {proc.info['pid']} ({proc.info['name']})")
                    killed = True
                except Exception:
                    pass
        if killed:
            pass
    except ImportError:
        # Fallback without psutil: use safe subprocess (no shell=True)
        try:
            ps_result = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True, text=True
            )
            search_flag = f"--remote-debugging-port={port}"
            for line in ps_result.stdout.strip().splitlines():
                if search_flag in line and "grep" not in line:
                    parts = line.strip().split(None, 1)
                    if parts:
                        try:
                            pid = int(parts[0])
                            os.kill(pid, 15)
                            print(f"[INFO] Killed Chrome PID {pid} via process query")
                        except (ValueError, ProcessLookupError):
                            pass
        except Exception as e:
            print(f"[WARN] Process query fallback failed: {e}")

    # Stop graphic services if running
    for name, filepath in [
        ("websockify", Path(f"/tmp/ordo-websockify-{port}.pid")),
        ("x11vnc", Path(f"/tmp/ordo-x11vnc-{port}.pid")),
        ("Xvfb", Path(f"/tmp/ordo-xvfb-{port}.pid")),
    ]:
        if filepath.exists():
            try:
                pid = int(filepath.read_text().strip())
                os.kill(pid, 15)
                print(f"[INFO] Terminated {name} PID {pid}")
            except ProcessLookupError:
                pass
            except Exception as e:
                print(f"[WARN] Failed to terminate {name}: {e}")
            filepath.unlink(missing_ok=True)



def main():
    parser = argparse.ArgumentParser(description="Ordo Worker daemon & task execution interface")
    subparsers = parser.add_subparsers(dest="command")

    # run-job
    run_parser = subparsers.add_parser("run-job", help="Decompress and execute a publishing bundle ZIP")
    run_parser.add_argument("bundle_zip", type=str, help="Absolute path to the ZIP bundle")

    # doctor
    subparsers.add_parser("doctor", help="Run local VPS environmental checkups")

    # start-browser
    start_parser = subparsers.add_parser("start-browser", help="Start headless Chrome session on VPS")
    start_parser.add_argument("--port", type=int, default=9333, help="Remote debugging port")
    start_parser.add_argument("--profile-dir", type=str, default="/root/ordo-publish-runtime/browser/profile", help="User profile directory")
    start_parser.add_argument("--xvfb", action="store_true", help="Start Xvfb & virtual display environment")

    # stop-browser
    stop_parser = subparsers.add_parser("stop-browser", help="Stop running Chrome session on VPS")
    stop_parser.add_argument("--port", type=int, default=9333, help="Remote debugging port")

    # list-jobs
    subparsers.add_parser("list-jobs", help="List all jobs and task summaries from SQLite DB")

    # status
    subparsers.add_parser("status", help="Show publish task report with deferred/failed details")

    # job-status
    status_parser = subparsers.add_parser("job-status", help="Show details of a specific job and its tasks")
    status_parser.add_argument("job_id", type=str, help="Job ID to query")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume pending/deferred tasks from SQLite DB")
    resume_parser.add_argument("--job", type=str, help="Filter to resume only a specific Job ID")

    # login-status
    subparsers.add_parser("login-status", help="Check login status of all browser platforms on VPS Chrome")

    # daemon
    daemon_parser = subparsers.add_parser("daemon", help="Run background task execution daemon on VPS")
    daemon_parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds")

    # stop-daemon
    subparsers.add_parser("stop-daemon", help="Stop background task execution daemon")

    # daemon-status
    subparsers.add_parser("daemon-status", help="Check background daemon status and log")

    args = parser.parse_args()

    if args.command == "run-job":
        run_job(args.bundle_zip)
    elif args.command == "doctor":
        run_doctor()
    elif args.command == "start-browser":
        start_browser(args.port, args.profile_dir, args.xvfb)
    elif args.command == "stop-browser":
        stop_browser(args.port)
    elif args.command == "list-jobs":
        list_jobs()
    elif args.command == "status":
        print_status_report()
    elif args.command == "job-status":
        job_status(args.job_id)
    elif args.command == "resume":
        resume_jobs(args.job)
    elif args.command == "login-status":
        check_login_status()
    elif args.command == "daemon":
        run_daemon(args.interval)
    elif args.command == "stop-daemon":
        stop_daemon()
    elif args.command == "daemon-status":
        daemon_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
