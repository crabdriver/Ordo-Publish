import sqlite3
import tempfile
import time
import os
import unittest
import json
import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import ordo_worker
from ordo_engine.runner import db as db_helper


class TestVpsTaskPersistenceDaemon(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_tasks.db"
        db_helper.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_daily_published_count(self):
        # Create a job
        db_helper.create_job(
            self.db_path, "job_1", "publish", "/tmp/bundle.zip", "hash1", "{}"
        )

        # Create tasks: one published today, one yesterday, one failed
        db_helper.create_task(
            self.db_path, "t1", "job_1", "art1", "Title 1", "article1.md",
            "zhihu", "publish", "LocalSubprocessExecutor", "published", "2026-06-18 10:00:00"
        )
        db_helper.create_task(
            self.db_path, "t2", "job_1", "art2", "Title 2", "article2.md",
            "zhihu", "publish", "LocalSubprocessExecutor", "failed", "2026-06-18 10:00:00"
        )
        
        # Override the updated_at time for t1 to be today
        conn = db_helper.get_db_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE tasks SET updated_at = datetime('now') WHERE task_id = 't1'"
            )
            # Create a task updated yesterday
            conn.execute(
                """INSERT INTO tasks (
                    task_id, job_id, article_id, title, article_path, platform,
                    target_action, executor, status, scheduled_at, updated_at
                ) VALUES ('t3', 'job_1', 'art3', 'Title 3', 'article3.md', 'zhihu',
                          'publish', 'LocalSubprocessExecutor', 'published', datetime('now'),
                          datetime('now', '-2 days'))"""
            )
            conn.commit()
        finally:
            conn.close()

        # Count daily published for zhihu
        count = ordo_worker.get_daily_published_count(self.db_path, "zhihu")
        self.assertEqual(count, 1) # Only t1 is published today

    @patch("ordo_worker.BASE_DIR")
    def test_is_daemon_running_and_lock(self, mock_base_dir):
        mock_base_dir.return_value = self.temp_path
        # Setup mocked path in ordo_worker BASE_DIR
        with patch("ordo_worker.BASE_DIR", self.temp_path):
            pid_file = self.temp_path / "data" / "ordo_daemon.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)

            self.assertFalse(ordo_worker.is_daemon_running())

            # Write invalid PID
            pid_file.write_text("9999999")
            self.assertFalse(ordo_worker.is_daemon_running())

            # Write current PID
            pid_file.write_text(str(os.getpid()))
            self.assertTrue(ordo_worker.is_daemon_running())

    @patch("ordo_worker.is_daemon_running", return_value=False)
    @patch("subprocess.Popen")
    @patch("ordo_worker.BASE_DIR")
    def test_start_daemon_background(self, mock_base_dir, mock_popen, mock_running):
        # Override BASE_DIR to temp directory
        with patch("ordo_worker.BASE_DIR", self.temp_path):
            ordo_worker.start_daemon_background()
            self.assertTrue(mock_popen.called)
            args = mock_popen.call_args[0][0]
            self.assertIn("daemon", args)

    @patch("ordo_engine.runner.pipeline.run_platform_task")
    @patch("ordo_worker.start_daemon_background")
    @patch("ordo_worker.get_daily_published_count", return_value=5)
    def test_execute_tasks_loop_limit_reached_triggers_daemon(
        self, mock_count, mock_start_daemon, mock_run_task
    ):
        db_helper.create_job(
            self.db_path, "job_limit", "publish", "/tmp/bundle.zip", "hash_limit", "{}"
        )
        db_helper.create_task(
            self.db_path, "task_limit", "job_limit", "art_1", "Title Limit", "article.md",
            "zhihu", "publish", "LocalSubprocessExecutor", "pending", "2026-06-18 10:00:00"
        )

        mock_run_task.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "达到发布上限，请明天再来",
        }
        
        # Setup mock adapter registry
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.status = "limit_reached"
        mock_result.summary = "达到发布上限，请明天再来"
        mock_result.retryable = False
        mock_adapter.collect_result.return_value = mock_result
        
        tasks_to_run = [{
            "task_id": "task_limit",
            "platform": "zhihu",
            "article_path": "article.md",
            "article_id": "art_1",
            "attempts": 0,
            "max_attempts": 3,
        }]
        
        with patch("ordo_engine.platforms.registry.build_platform_registry", return_value={"zhihu": mock_adapter}):
            ordo_worker.execute_tasks_loop(
                db_path=self.db_path,
                job_id="job_limit",
                tasks_to_run=tasks_to_run,
                job_dir=self.temp_path,
                mode="publish",
                cover_mappings={}
            )
            
        # Verify task is updated to deferred_limit
        conn = db_helper.get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = 'task_limit'").fetchone()
            task = dict(row)
        finally:
            conn.close()
            
        self.assertEqual(task["status"], "deferred_limit")
        self.assertIsNotNone(task["next_run_at"])
        # Verify background daemon is triggered
        self.assertTrue(mock_start_daemon.called)

    @patch("ordo_worker.resume_jobs")
    def test_run_daemon_tick_resumes_only_when_due_tasks_exist(self, mock_resume):
        db_helper.create_job(
            self.db_path, "job_daemon", "publish", "/tmp/bundle.zip", "hash", "{}"
        )
        future = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        db_helper.create_task(
            self.db_path, "future_task", "job_daemon", "art1", "Future", "future.md",
            "zhihu", "publish", "LocalSubprocessExecutor", "deferred_limit", "2026-06-21 01:00:00"
        )
        db_helper.update_task(self.db_path, "future_task", "deferred_limit", next_run_at=future)

        self.assertEqual(ordo_worker.run_daemon_tick(self.db_path), 0)
        mock_resume.assert_not_called()

        db_helper.create_task(
            self.db_path, "past_task", "job_daemon", "art2", "Past", "past.md",
            "toutiao", "publish", "LocalSubprocessExecutor", "deferred_limit", "2026-06-21 01:00:00"
        )
        db_helper.update_task(self.db_path, "past_task", "deferred_limit", next_run_at=past)

        self.assertEqual(ordo_worker.run_daemon_tick(self.db_path), 1)
        mock_resume.assert_called_once()


if __name__ == "__main__":
    unittest.main()
