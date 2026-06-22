import sqlite3
import json
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# Use UTC consistently: all timestamps written by Python use datetime.utcnow(),
# and SQLite DEFAULT values also use CURRENT_TIMESTAMP (which is UTC).

def get_db_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for high concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection(db_path)
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            finished_at DATETIME,
            source_bundle TEXT NOT NULL,
            bundle_hash TEXT,
            policy_json TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            job_id TEXT REFERENCES jobs(job_id),
            article_id TEXT NOT NULL,
            title TEXT,
            article_path TEXT NOT NULL,
            platform TEXT NOT NULL,
            target_action TEXT NOT NULL,
            executor TEXT NOT NULL,
            status TEXT NOT NULL,
            scheduled_at DATETIME NOT NULL,
            next_run_at DATETIME,
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            last_error TEXT,
            raw_result_json TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
    finally:
        conn.close()

def create_job(db_path: Path, job_id: str, mode: str, source_bundle: str, bundle_hash: str, policy_json: str) -> bool:
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, mode, status, source_bundle, bundle_hash, policy_json) VALUES (?, ?, 'pending', ?, ?, ?)",
            (job_id, mode, source_bundle, bundle_hash, policy_json)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_job(db_path: Path, job_id: str) -> Optional[dict]:
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_job_by_hash(db_path: Path, bundle_hash: str) -> Optional[dict]:
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE bundle_hash = ?", (bundle_hash,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_job_status(db_path: Path, job_id: str, status: str, started_at: Optional[str] = None, finished_at: Optional[str] = None):
    conn = get_db_connection(db_path)
    try:
        query = "UPDATE jobs SET status = ?"
        params = [status]
        if started_at:
            query += ", started_at = ?"
            params.append(started_at)
        if finished_at:
            query += ", finished_at = ?"
            params.append(finished_at)
        query += " WHERE job_id = ?"
        params.append(job_id)
        conn.execute(query, tuple(params))
        conn.commit()
    finally:
        conn.close()

def create_task(
    db_path: Path,
    task_id: str,
    job_id: str,
    article_id: str,
    title: str,
    article_path: str,
    platform: str,
    target_action: str,
    executor: str,
    status: str,
    scheduled_at: str,
    max_attempts: int = 3
) -> bool:
    conn = get_db_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO tasks (
                task_id, job_id, article_id, title, article_path, platform,
                target_action, executor, status, scheduled_at, max_attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, job_id, article_id, title, article_path, platform, target_action, executor, status, scheduled_at, max_attempts)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def update_task(
    db_path: Path,
    task_id: str,
    status: str,
    attempts: Optional[int] = None,
    next_run_at: Optional[str] = None,
    last_error: Optional[str] = None,
    raw_result_json: Optional[str] = None
):
    conn = get_db_connection(db_path)
    try:
        query = "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP"
        params = [status]
        if attempts is not None:
            query += ", attempts = ?"
            params.append(attempts)
        if next_run_at is not None:
            query += ", next_run_at = ?"
            params.append(next_run_at)
        if last_error is not None:
            query += ", last_error = ?"
            params.append(last_error)
        if raw_result_json is not None:
            query += ", raw_result_json = ?"
            params.append(raw_result_json)
        query += " WHERE task_id = ?"
        params.append(task_id)
        conn.execute(query, tuple(params))
        conn.commit()
    finally:
        conn.close()

def get_job_tasks(db_path: Path, job_id: str) -> List[dict]:
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_tasks(db_path: Path) -> List[dict]:
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC, job_id, article_id, platform"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_pending_tasks(db_path: Path) -> List[dict]:
    """Retrieve tasks that need execution/retry.
    Includes tasks with:
    - status = 'pending' or 'failed' where attempts < max_attempts
    - status = 'deferred_limit' where next_run_at <= current UTC time
    """
    conn = get_db_connection(db_path)
    try:
        # Use SQLite's datetime('now') for UTC-consistent comparison
        rows = conn.execute(
            """SELECT * FROM tasks WHERE 
               (status IN ('pending', 'failed') AND attempts < max_attempts) OR
               (status = 'deferred_limit' AND next_run_at <= datetime('now'))"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_recent_jobs(db_path: Path, limit: int = 20) -> List[dict]:
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_job_task_counts(db_path: Path, job_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """Batch query: returns {job_id: {pending: N, running: N, success: N, deferred: N, failed: N}} for given job_ids.
    Eliminates N+1 queries in list_jobs.
    """
    if not job_ids:
        return {}
    conn = get_db_connection(db_path)
    try:
        placeholders = ",".join("?" for _ in job_ids)
        rows = conn.execute(
            f"""SELECT job_id, status, COUNT(*) as cnt
                FROM tasks WHERE job_id IN ({placeholders})
                GROUP BY job_id, status""",
            tuple(job_ids)
        ).fetchall()
        result: Dict[str, Dict[str, int]] = {jid: {} for jid in job_ids}
        for r in rows:
            result[r["job_id"]][r["status"]] = r["cnt"]
        return result
    finally:
        conn.close()


def delete_job_tasks(db_path: Path, job_id: str):
    """Delete all task records for a given job. Used when re-creating tasks from a new bundle."""
    conn = get_db_connection(db_path)
    try:
        conn.execute("DELETE FROM tasks WHERE job_id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
