import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from ordo_engine.runner.executor import LocalSubprocessExecutor
from ordo_engine.runner.bundle import create_publish_bundle, extract_title_from_md
import ordo_worker


class TestVpsFirstSkeleton(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_local_subprocess_executor_success(self):
        executor = LocalSubprocessExecutor()
        res = executor.execute(["echo", "hello_world"])
        self.assertEqual(res["returncode"], 0)
        self.assertEqual(res["stdout"], "hello_world")
        self.assertFalse(res["timed_out"])

    def test_local_subprocess_executor_timeout(self):
        executor = LocalSubprocessExecutor()
        # Trigger an intentional timeout with sleep command
        res = executor.execute(["python3", "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertEqual(res["returncode"], 124)
        self.assertTrue(res["timed_out"])

    def test_extract_title_from_md(self):
        md_file = self.temp_path / "test.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# This is a Title\n\nSome body text here.")

        title = extract_title_from_md(md_file)
        self.assertEqual(title, "This is a Title")

        # Fallback test
        md_file_no_title = self.temp_path / "no_title.md"
        with open(md_file_no_title, "w", encoding="utf-8") as f:
            f.write("Some body text without title.")
        self.assertEqual(extract_title_from_md(md_file_no_title), "no_title")

    def test_create_publish_bundle(self):
        # Create dummy articles
        art1 = self.temp_path / "article_one.md"
        art1.write_text("# Article One\nContent 1", encoding="utf-8")

        art2 = self.temp_path / "article_two.md"
        art2.write_text("No header content here", encoding="utf-8")

        # Create dummy covers
        cov1 = self.temp_path / "cover1.png"
        cov1.write_text("dummy image content", encoding="utf-8")

        # Define mappings
        cover_mapping = {
            "article_one": {
                "zhihu": cov1
            }
        }

        zip_output = self.temp_path / "bundle.zip"

        # Execute bundling
        create_publish_bundle(
            article_paths=[art1, art2],
            cover_mapping=cover_mapping,
            platforms=["zhihu", "toutiao"],
            mode="draft",
            output_zip_path=zip_output,
            job_id="test_job_123",
            theme_mapping={"article_one": {"zhihu": "sspai"}},
        )

        self.assertTrue(zip_output.exists())

        # Inspect Zip Content
        with zipfile.ZipFile(zip_output, "r") as z:
            names = z.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("articles/art_000_article_one.md", names)
            self.assertIn("articles/art_001_article_two.md", names)
            self.assertIn("covers/cover_000_zhihu_cover1.png", names)

            # Check Manifest JSON content
            manifest_bytes = z.read("manifest.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))

            self.assertEqual(manifest["job_id"], "test_job_123")
            self.assertEqual(manifest["mode"], "draft")
            self.assertEqual(manifest["platforms"], ["zhihu", "toutiao"])
            self.assertEqual(len(manifest["articles"]), 2)

            art1_meta = manifest["articles"][0]
            self.assertEqual(art1_meta["title"], "Article One")
            self.assertEqual(art1_meta["markdown_path"], "articles/art_000_article_one.md")
            self.assertEqual(art1_meta["covers"]["zhihu"], "covers/cover_000_zhihu_cover1.png")
            self.assertEqual(art1_meta["themes"]["zhihu"], "sspai")

            art2_meta = manifest["articles"][1]
            self.assertEqual(art2_meta["title"], "article_two")

    @patch("ordo_worker.execute_tasks_loop")
    def test_ordo_worker_run_job(self, mock_execute_loop):
        # 1. Create a bundle ZIP
        art = self.temp_path / "post.md"
        art.write_text("# Hello Post\nContent", encoding="utf-8")

        zip_output = self.temp_path / "worker_bundle.zip"
        create_publish_bundle(
            article_paths=[art],
            cover_mapping={},
            platforms=["wechat"],
            mode="publish",
            output_zip_path=zip_output,
            job_id="job_worker_test"
        )

        with patch.object(ordo_worker, "BASE_DIR", self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                ordo_worker.run_job(str(zip_output))

            # Assertions
            self.assertEqual(cm.exception.code, 1)
            mock_execute_loop.assert_called_once()

            # Verify argument mapping
            args, kwargs = mock_execute_loop.call_args
            self.assertEqual(args[1], "job_worker_test")
            self.assertEqual(len(args[2]), 1)
            self.assertEqual(args[2][0]["platform"], "wechat")

    @patch("ordo_engine.runner.version.subprocess.run")
    def test_version_verification(self, mock_run):
        from ordo_engine.runner.version import verify_codebase_version

        # Mock git rev-parse output for local and remote
        local_mock = MagicMock()
        local_mock.stdout = "commit_abc_123\n"

        remote_mock = MagicMock()
        remote_mock.stdout = "commit_abc_123\n"

        mock_run.side_effect = [local_mock, remote_mock]

        # Test matching
        match, local_commit, remote_commit = verify_codebase_version("my-vps-host")
        self.assertTrue(match)
        self.assertEqual(local_commit, "commit_abc_123")
        self.assertEqual(remote_commit, "commit_abc_123")

        # Test mismatch
        remote_mock_diff = MagicMock()
        remote_mock_diff.stdout = "commit_diff_456\n"
        mock_run.side_effect = [local_mock, remote_mock_diff]
        match, local_commit, remote_commit = verify_codebase_version("my-vps-host")
        self.assertFalse(match)
        self.assertEqual(local_commit, "commit_abc_123")
        self.assertEqual(remote_commit, "commit_diff_456")

    @patch("ordo_worker.is_proxy_available")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_start_browser_with_proxy(self, mock_which, mock_popen, mock_proxy):
        mock_which.return_value = "/usr/bin/google-chrome"
        mock_proxy.return_value = True

        # Mock Popen return value
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc

        # Invoke start-browser
        try:
            ordo_worker.start_browser(9333, str(self.temp_path / "chrome-profile"))
        finally:
            pid_file = Path("/tmp/ordo-chrome-9333.pid")
            if pid_file.exists():
                pid_file.unlink()

        # Assertions
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd_args = args[0]
        self.assertIn("/usr/bin/google-chrome", cmd_args)
        self.assertIn("--headless=new", cmd_args)
        self.assertIn("--remote-debugging-port=9333", cmd_args)
        self.assertIn("--proxy-server=http://127.0.0.1:7890", cmd_args)

    @patch("ordo_worker.is_proxy_available")
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_start_browser_no_proxy(self, mock_which, mock_popen, mock_proxy):
        mock_which.return_value = "/usr/bin/google-chrome"
        mock_proxy.return_value = False

        # Mock Popen return value
        mock_proc = MagicMock()
        mock_proc.pid = 8888
        mock_popen.return_value = mock_proc

        # Invoke start-browser
        try:
            ordo_worker.start_browser(9333, str(self.temp_path / "chrome-profile"))
        finally:
            pid_file = Path("/tmp/ordo-chrome-9333.pid")
            if pid_file.exists():
                pid_file.unlink()

        # Assertions
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd_args = args[0]
        self.assertNotIn("--proxy-server=http://127.0.0.1:7890", cmd_args)

    @patch("ordo_engine.runner.version.verify_codebase_version")
    @patch("ordo_engine.runner.bundle.create_publish_bundle")
    @patch("ordo_engine.runner.bundle.upload_bundle_to_vps")
    @patch("ordo_engine.runner.executor.RemoteSubprocessExecutor")
    @patch("publish.collect_markdown_files")
    @patch("publish.filter_already_published_articles")
    @patch("publish.build_publication_cover_assignments")
    @patch("publish.time.time", return_value=1000)
    def test_publish_py_remote_delegation(
        self,
        mock_time,
        mock_build_covers,
        mock_filter,
        mock_collect,
        mock_executor_cls,
        mock_upload,
        mock_bundle,
        mock_verify,
    ):
        import sys
        mock_verify.return_value = (True, "commit123", "commit123")
        mock_collect.return_value = [Path("/tmp/my_article.md")]
        mock_build_covers.return_value = ()

        # Mock executor instance
        mock_executor = MagicMock()
        mock_executor.execute.return_value = {"returncode": 0, "stdout": "Job Done", "stderr": ""}
        mock_executor_cls.return_value = mock_executor

        # Simulate command line arguments
        test_args = [
            "publish.py",
            "/tmp/my_article.md",
            "--platform", "wechat",
            "--remote", "vps",
            "--vps-host", "1.2.3.4",
            "--vps-user", "myuser",
            "--vps-path", "/my/remote/repo"
        ]

        with patch("sys.argv", test_args):
            from publish import main
            # To avoid real config loading failures interrupting main
            with patch("publish.load_engine_config", MagicMock()):
                main()

        # Verify remote delegator sequence
        mock_verify.assert_called_once_with(
            ssh_host="1.2.3.4",
            ssh_user="myuser",
            remote_path="/my/remote/repo"
        )
        mock_bundle.assert_called_once()
        mock_upload.assert_called_once()
        self.assertEqual(mock_upload.call_args.kwargs["remote_path"], "/my/remote/data/inbox/bundle_1000.zip")
        mock_executor_cls.assert_called_once_with(
            ssh_host="1.2.3.4",
            ssh_user="myuser",
            remote_cwd="/my/remote/repo",
            proxy_tunnel="7890:127.0.0.1:7890"
        )
        self.assertEqual(mock_executor.execute.call_count, 3)
