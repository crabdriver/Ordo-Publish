import sqlite3
import tempfile
import time
import unittest
import json
import datetime
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import ordo_worker
from ordo_engine.runner import db as db_helper


class TestVpsTaskPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_tasks.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_db_init_and_wal_mode(self):
        db_helper.init_db(self.db_path)
        self.assertTrue(self.db_path.exists())
        
        # Verify WAL mode
        conn = db_helper.get_db_connection(self.db_path)
        cursor = conn.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        conn.close()

    def test_job_and_task_crud(self):
        db_helper.init_db(self.db_path)
        
        # Create job
        res = db_helper.create_job(
            self.db_path, "job_123", "publish", "/tmp/bundle.zip", "hash123", '{"limit": "defer"}'
        )
        self.assertTrue(res)
        
        job = db_helper.get_job(self.db_path, "job_123")
        self.assertEqual(job["job_id"], "job_123")
        self.assertEqual(job["status"], "pending")
        
        # Update job status
        db_helper.update_job_status(self.db_path, "job_123", "running", started_at="2026-06-16 12:00:00")
        job = db_helper.get_job(self.db_path, "job_123")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["started_at"], "2026-06-16 12:00:00")

        # Create task
        res = db_helper.create_task(
            self.db_path, "job_123_art1_wechat", "job_123", "art1", "Article Title", "articles/art1.md",
            "wechat", "publish", "LocalSubprocessExecutor", "pending", "2026-06-16 12:00:00", 3
        )
        self.assertTrue(res)
        
        tasks = db_helper.get_job_tasks(self.db_path, "job_123")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "job_123_art1_wechat")
        
        # Update task status
        db_helper.update_task(
            self.db_path, "job_123_art1_wechat", status="published", attempts=1, raw_result_json='{"status": "ok"}'
        )
        tasks = db_helper.get_job_tasks(self.db_path, "job_123")
        self.assertEqual(tasks[0]["status"], "published")
        self.assertEqual(tasks[0]["attempts"], 1)
        self.assertEqual(tasks[0]["raw_result_json"], '{"status": "ok"}')

    def test_get_pending_tasks(self):
        db_helper.init_db(self.db_path)
        db_helper.create_job(self.db_path, "job_1", "publish", "bundle.zip", "hash1", "{}")
        
        # Task 1: pending
        db_helper.create_task(
            self.db_path, "task_1", "job_1", "art1", "T1", "art1.md", "wechat", "publish", "Executor", "pending", "2026-06-16 12:00:00", 3
        )
        # Task 2: failed, attempts = 1, max_attempts = 3 (retryable)
        db_helper.create_task(
            self.db_path, "task_2", "job_1", "art1", "T2", "art1.md", "zhihu", "publish", "Executor", "failed", "2026-06-16 12:00:00", 3
        )
        db_helper.update_task(self.db_path, "task_2", "failed", attempts=1)
        # Task 3: failed, attempts = 3, max_attempts = 3 (not retryable)
        db_helper.create_task(
            self.db_path, "task_3", "job_1", "art1", "T3", "art1.md", "jianshu", "publish", "Executor", "failed", "2026-06-16 12:00:00", 3
        )
        db_helper.update_task(self.db_path, "task_3", "failed", attempts=3)
        # Task 4: deferred_limit, next_run_at = past
        past_str = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        db_helper.create_task(
            self.db_path, "task_4", "job_1", "art1", "T4", "art1.md", "toutiao", "publish", "Executor", "deferred_limit", "2026-06-16 12:00:00", 3
        )
        db_helper.update_task(self.db_path, "task_4", "deferred_limit", next_run_at=past_str)
        # Task 5: deferred_limit, next_run_at = future
        future_str = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        db_helper.create_task(
            self.db_path, "task_5", "job_1", "art1", "T5", "art1.md", "yidian", "publish", "Executor", "deferred_limit", "2026-06-16 12:00:00", 3
        )
        db_helper.update_task(self.db_path, "task_5", "deferred_limit", next_run_at=future_str)

        pending = db_helper.get_pending_tasks(self.db_path)
        pending_ids = {t["task_id"] for t in pending}
        
        self.assertIn("task_1", pending_ids)
        self.assertIn("task_2", pending_ids)
        self.assertIn("task_4", pending_ids)
        self.assertNotIn("task_3", pending_ids)
        self.assertNotIn("task_5", pending_ids)

    def test_build_status_report(self):
        db_helper.init_db(self.db_path)
        db_helper.create_job(self.db_path, "job_report", "publish", "/tmp/bundle.zip", "hash", "{}")
        db_helper.create_task(
            self.db_path, "t_pub", "job_report", "art1", "Title 1", "articles/a1.md",
            "toutiao", "publish", "Executor", "published", "2026-06-21 01:00:00", 3
        )
        db_helper.create_task(
            self.db_path, "t_defer", "job_report", "art2", "Title 2", "articles/a2.md",
            "yidian", "publish", "Executor", "deferred_limit", "2026-06-21 01:00:00", 3
        )
        db_helper.update_task(
            self.db_path,
            "t_defer",
            "deferred_limit",
            next_run_at="2026-06-22 01:00:00",
            last_error="发布上限，请明天再来",
        )
        db_helper.create_task(
            self.db_path, "t_fail", "job_report", "art3", "Title 3", "articles/a3.md",
            "zhihu", "publish", "Executor", "failed", "2026-06-21 01:00:00", 3
        )
        db_helper.update_task(self.db_path, "t_fail", "failed", attempts=3, last_error="登录已失效")

        report = ordo_worker.build_status_report(self.db_path)

        self.assertIn("job_report", report)
        self.assertIn("published: 1", report)
        self.assertIn("deferred_limit: 1", report)
        self.assertIn("failed: 1", report)
        self.assertIn("yidian", report)
        self.assertIn("2026-06-22 01:00:00", report)
        self.assertIn("发布上限，请明天再来", report)
        self.assertIn("登录已失效", report)

    @patch("ordo_worker.start_daemon_background")
    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_run_job_with_rate_limiting_and_retries(self, mock_run, mock_start_daemon):
        # 1. Create a dummy bundle zip
        bundle_zip = self.temp_path / "bundle.zip"
        with zipfile.ZipFile(bundle_zip, "w") as z:
            manifest = {
                "job_id": "job_test_run",
                "mode": "publish",
                "platforms": ["wechat", "zhihu"],
                "articles": [
                    {
                        "article_id": "art_001",
                        "title": "My Article",
                        "markdown_path": "articles/art_001.md",
                        "covers": {},
                    }
                ],
                "policy": {"max_attempts": 3}
            }
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("articles/art_001.md", "# My Article\nHello")
            
        # Mock run_platform_task returns
        # 1st call (wechat): success (returncode=0)
        # 2nd call (zhihu): rate limited (returncode=1, stdout="达到发布上限")
        mock_run.side_effect = [
            {"returncode": 0, "stdout": "已发布到微信公众号", "stderr": ""},
            {"returncode": 1, "stdout": "达到发布上限", "stderr": ""},
        ]
        
        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                ordo_worker.run_job(str(bundle_zip))
            
            # The exit code should be 0 since deferred is not a hard failure
            self.assertEqual(cm.exception.code, 0)
            
            db_path = self.temp_path / "data" / "ordo_tasks.db"
            job = db_helper.get_job(db_path, "job_test_run")
            self.assertEqual(job["status"], "deferred")
            
            tasks = db_helper.get_job_tasks(db_path, "job_test_run")
            tasks_dict = {t["platform"]: t for t in tasks}
            
            self.assertEqual(tasks_dict["wechat"]["status"], "published")
            self.assertEqual(tasks_dict["zhihu"]["status"], "deferred_limit")
            self.assertIsNotNone(tasks_dict["zhihu"]["next_run_at"])
            mock_start_daemon.assert_called_once()

    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_run_job_with_transient_retry(self, mock_run):
        bundle_zip = self.temp_path / "bundle.zip"
        with zipfile.ZipFile(bundle_zip, "w") as z:
            manifest = {
                "job_id": "job_retry_test",
                "mode": "publish",
                "platforms": ["zhihu"],
                "articles": [
                    {
                        "article_id": "art_001",
                        "title": "My Article",
                        "markdown_path": "articles/art_001.md",
                        "covers": {},
                    }
                ],
            }
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("articles/art_001.md", "# My Article\nHello")
            
        # Mock run_platform_task transient failure (timeout) then success
        # 1st call: transient error
        # 2nd call: success
        mock_run.side_effect = [
            {"returncode": 124, "stdout": "", "stderr": "Process timed out after 180 seconds", "timed_out": True},
            {"returncode": 0, "stdout": "已发布到知乎", "stderr": ""},
        ]
        
        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with patch("time.sleep", MagicMock()):  # Speed up test
                with self.assertRaises(SystemExit) as cm:
                    ordo_worker.run_job(str(bundle_zip))
                
                self.assertEqual(cm.exception.code, 0)
                
                db_path = self.temp_path / "data" / "ordo_tasks.db"
                job = db_helper.get_job(db_path, "job_retry_test")
                self.assertEqual(job["status"], "completed")
                
                tasks = db_helper.get_job_tasks(db_path, "job_retry_test")
                self.assertEqual(tasks[0]["status"], "published")
                self.assertEqual(tasks[0]["attempts"], 2)

    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_run_job_passes_theme_from_manifest(self, mock_run):
        bundle_zip = self.temp_path / "bundle.zip"
        with zipfile.ZipFile(bundle_zip, "w") as z:
            manifest = {
                "job_id": "job_theme_test",
                "mode": "publish",
                "platforms": ["zhihu"],
                "articles": [
                    {
                        "article_id": "art_001",
                        "title": "My Article",
                        "markdown_path": "articles/art_001.md",
                        "covers": {},
                        "themes": {"zhihu": "sspai"},
                    }
                ],
            }
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("articles/art_001.md", "# My Article\nHello")

        mock_run.return_value = {"returncode": 0, "stdout": "已发布到知乎", "stderr": ""}

        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                ordo_worker.run_job(str(bundle_zip))

        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(mock_run.call_args.kwargs["theme_name"], "sspai")
        self.assertEqual(mock_run.call_args.kwargs["template_mode"], "custom")

    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_run_job_treats_scheduled_as_success(self, mock_run):
        bundle_zip = self.temp_path / "bundle.zip"
        with zipfile.ZipFile(bundle_zip, "w") as z:
            manifest = {
                "job_id": "job_scheduled_test",
                "mode": "publish",
                "platforms": ["toutiao"],
                "articles": [
                    {
                        "article_id": "art_001",
                        "title": "My Article",
                        "markdown_path": "articles/art_001.md",
                        "covers": {},
                    }
                ],
            }
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("articles/art_001.md", "# My Article\nHello")

        mock_run.return_value = {
            "returncode": 0,
            "stdout": "已设置头条号定时发布",
            "stderr": "",
            "page_state": "scheduled",
        }

        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                ordo_worker.run_job(str(bundle_zip))

        self.assertEqual(cm.exception.code, 0)
        db_path = self.temp_path / "data" / "ordo_tasks.db"
        job = db_helper.get_job(db_path, "job_scheduled_test")
        self.assertEqual(job["status"], "completed")
        tasks = db_helper.get_job_tasks(db_path, "job_scheduled_test")
        self.assertEqual(tasks[0]["status"], "scheduled")

    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_resume_command(self, mock_run):
        db_path = self.temp_path / "data" / "ordo_tasks.db"
        db_helper.init_db(db_path)
        
        # Prepare a job and a deferred task in the DB
        db_helper.create_job(db_path, "job_resume", "publish", str(self.temp_path / "bundle.zip"), "hash_res", "{}")
        # next_run_at is in the past
        past_str = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        db_helper.create_task(
            db_path, "job_resume_art001_zhihu", "job_resume", "art001", "My Article", "articles/art001.md",
            "zhihu", "publish", "LocalSubprocessExecutor", "deferred_limit", "2026-06-16 12:00:00", 3
        )
        db_helper.update_task(db_path, "job_resume_art001_zhihu", "deferred_limit", next_run_at=past_str)
        
        # Mock manifest file and unpacked job directory
        job_dir = self.temp_path / "data" / "jobs" / "job_resume"
        job_dir.mkdir(parents=True)
        manifest = {
            "job_id": "job_resume",
            "mode": "publish",
            "platforms": ["zhihu"],
            "articles": [
                {
                    "article_id": "art001",
                    "title": "My Article",
                    "markdown_path": "articles/art001.md",
                    "covers": {},
                }
            ]
        }
        with open(job_dir / "manifest.json", "w") as f:
            json.dump(manifest, f)
            
        mock_run.return_value = {"returncode": 0, "stdout": "已发布到知乎", "stderr": ""}
        
        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            ordo_worker.resume_jobs()
            
            # The task should now be published
            tasks = db_helper.get_job_tasks(db_path, "job_resume")
            self.assertEqual(tasks[0]["status"], "published")
            self.assertEqual(tasks[0]["attempts"], 1)
            
            # Job status should be updated to completed
            job = db_helper.get_job(db_path, "job_resume")
            self.assertEqual(job["status"], "completed")


    @patch("socket.socket")
    @patch("subprocess.run")
    def test_check_login_status_chrome_offline(self, mock_sub_run, mock_socket):
        # Setup socket connection to fail (Chrome offline)
        mock_socket_inst = MagicMock()
        mock_socket_inst.connect.side_effect = Exception("offline")
        mock_socket.return_value = mock_socket_inst
        
        # Call check_login_status
        import io
        import sys
        captured_output = io.StringIO()
        sys.stdout = captured_output
        try:
            ordo_worker.check_login_status()
        finally:
            sys.stdout = sys.__stdout__
            
        output_str = captured_output.getvalue()
        self.assertIn("Chrome is offline", output_str)
        self.assertIn("CDP port 9333 is not listening", output_str)


    @patch("socket.socket")
    @patch("subprocess.run")
    def test_check_login_status_online_with_platforms(self, mock_sub_run, mock_socket):
        # Setup socket connection to succeed (Chrome online)
        mock_socket_inst = MagicMock()
        mock_socket_inst.connect.return_value = None
        mock_socket.return_value = mock_socket_inst
        
        # Mock node process execution results:
        # First call (list tabs): zhihu and jianshu active tabs, toutiao and yidian missing
        # Second call (inspect zhihu): editor_ready
        # Third call (inspect jianshu): login_required
        mock_list_output = "tab1\tZhihu Title\thttps://zhuanlan.zhihu.com/write\ntab2\tJianshu Title\thttps://www.jianshu.com\n"
        
        mock_list_res = MagicMock()
        mock_list_res.stdout = mock_list_output
        
        mock_zhihu_res = MagicMock()
        mock_zhihu_res.stdout = '{"page_state": "editor_ready", "detail": "写作编辑器已就绪"}'
        
        mock_jianshu_res = MagicMock()
        mock_jianshu_res.stdout = '{"page_state": "login_required", "detail": "当前标签页仍处于登录或校验状态"}'
        
        mock_sub_run.side_effect = [
            mock_list_res,
            mock_zhihu_res,
            mock_jianshu_res
        ]
        
        # Call check_login_status
        import io
        import sys
        captured_output = io.StringIO()
        sys.stdout = captured_output
        try:
            ordo_worker.check_login_status()
        finally:
            sys.stdout = sys.__stdout__
            
        output_str = captured_output.getvalue()
        # Verify zhihu healthy state
        self.assertIn("zhihu    : [HEALTHY] 登录状态正常 - 写作编辑器已就绪", output_str)
        # Verify jianshu login_required state
        self.assertIn("jianshu  : [LOGIN_REQUIRED] 登录已失效或需要滑块验证！", output_str)
        # Verify toutiao/yidian offline state
        self.assertIn("toutiao  : [OFFLINE] 暂无打开的标签页", output_str)
        self.assertIn("yidian   : [OFFLINE] 暂无打开的标签页", output_str)


    @patch("ordo_engine.runner.pipeline.run_platform_task")
    def test_run_job_shows_login_assistance_instruction(self, mock_run):
        bundle_zip = self.temp_path / "bundle.zip"
        with zipfile.ZipFile(bundle_zip, "w") as z:
            manifest = {
                "job_id": "job_login_assist_test",
                "mode": "publish",
                "platforms": ["zhihu"],
                "articles": [
                    {
                        "article_id": "art_001",
                        "title": "My Article",
                        "markdown_path": "articles/art_001.md",
                        "covers": {},
                    }
                ],
            }
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("articles/art_001.md", "# My Article\nHello")
            
        # Mock run_platform_task to return login_required failure
        mock_run.return_value = {
            "returncode": 1, 
            "stdout": "请确认当前 Chrome 已登录知乎", 
            "stderr": ""
        }
        
        import io
        import sys
        captured_output = io.StringIO()
        
        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                try:
                    sys.stdout = captured_output
                    ordo_worker.run_job(str(bundle_zip))
                finally:
                    sys.stdout = sys.__stdout__
                    
            self.assertEqual(cm.exception.code, 1)
            
            output_str = captured_output.getvalue()
            # Verify that the SSH tunnel guide was printed
            self.assertIn("登录已失效或需要人工安全验证", output_str)
            self.assertIn("ssh -N -L 9999:127.0.0.1:9333", output_str)
            self.assertIn("chrome://inspect", output_str)
