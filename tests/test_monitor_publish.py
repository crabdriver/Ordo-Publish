"""monitor_publish 测试 —— v2 coordinator 架构。"""
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordo_engine.results.publish_records import PUBLISH_RECORD_FIELDNAMES
from ordo_engine.run_lock import run_lock
from ordo_engine.run_state import (
    ArticleRecord, PlatformRecord, PlatformStage, ArticleStage,
    stable_article_id, save_v2_state,
)
from ordo_engine.runner.pipeline import BatchCoordinator


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "monitor_publish.py"
SPEC = importlib.util.spec_from_file_location("monitor_publish", MODULE_PATH)
monitor_publish = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor_publish
SPEC.loader.exec_module(monitor_publish)


def append_record(
    path, *, article, platform, mode, status, returncode=0, error_type="",
    run_id="", article_id="", article_key="",
):
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=PUBLISH_RECORD_FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-07-13 06:00:00",
                "article": str(article),
                "article_id": article_id,
                "article_key": article_key,
                "platform": platform,
                "mode": mode,
                "status": status,
                "error_type": error_type,
                "returncode": returncode,
                "run_id": run_id,
            }
        )


def _mock_coordinator_summary(*, succeeded=True):
    """构造 mock coordinator 返回的摘要。"""
    platforms = {}
    if succeeded:
        platforms = {
            "wechat:draft": {"stage": "draft_saved"},
            "zhihu:publish": {"stage": "published"},
            "toutiao:publish": {"stage": "published"},
            "jianshu:publish": {"stage": "published"},
            "yidian:publish": {"stage": "published"},
            "bilibili:publish": {"stage": "published"},
        }
    return {"articles": {"test": {"platforms": platforms}}}


class MonitorPublishTests(unittest.TestCase):
    """保持兼容的旧测试 + 新 coordinator 测试。"""

    # ── 旧测试：article matching / state / CLI ──────────────

    def test_mismatched_article_id_never_matches_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = Path(tmp) / "article.md"
            article.write_text("---\narticle_id: new-id\n---\n# 新稿", encoding="utf-8")
            row = {"article": str(article), "article_id": "old-id", "article_key": "old-id"}
            self.assertFalse(
                monitor_publish.record_matches_article(
                    row, article, article_id="new-id", identity="new-id"
                )
            )

    def test_content_hash_distinguishes_replaced_article_at_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = Path(tmp) / "article.md"
            article.write_text("# 旧稿", encoding="utf-8")
            old_key = monitor_publish.durable_article_key(article)
            row = {"article": str(article), "article_id": "", "article_key": old_key}
            article.write_text("# 新稿", encoding="utf-8")
            new_key = monitor_publish.durable_article_key(article)
            self.assertNotEqual(old_key, new_key)
            self.assertFalse(
                monitor_publish.record_matches_article(
                    row, article, article_id=None, identity=new_key
                )
            )

    def test_script_entrypoint_can_import_project_modules(self):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            cwd="/tmp",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_publish_command_defaults_to_local(self):
        cmd = monitor_publish.build_publish_cmd(
            Path("/tmp/a.md"), platforms="zhihu", mode="publish"
        )
        self.assertEqual(cmd[cmd.index("--remote") + 1], "local")

    # ── 新 coordinator 测试 ────────────────────────────────

    def test_publish_article_uses_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            state_file = root / "state.json"

            class MockCoordinator:
                def __init__(self, **kwargs):
                    pass
                def run_batch(self, paths):
                    return _mock_coordinator_summary(succeeded=True)

            with patch.object(monitor_publish, "BatchCoordinator", MockCoordinator), \
                 patch.object(monitor_publish, "STATE_FILE", state_file):
                result = monitor_publish.publish_article(article)
            self.assertEqual(result, "success")

    def test_publish_article_once_no_lock_fd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            with patch.object(monitor_publish, "PUBLISH_LOCK_FILE", root / ".ordo" / "publish.lock"), \
                 patch.object(monitor_publish, "publish_article", return_value="success") as publish:
                monitor_publish.publish_article_once(article)
            self.assertIsNone(publish.call_args.kwargs.get("lock_fd"))

    def test_coordinator_no_lock_fd_param(self):
        import inspect
        sig = inspect.signature(BatchCoordinator.__init__)
        params = list(sig.parameters.keys())
        self.assertNotIn("lock_fd", params)
        self.assertNotIn("inherited_fd", params)

    # ── 未提交/限流/跳过 行为测试 ──────────────────────────

    def test_csv_unverified_record_is_audit_only_after_v2_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(
                records, article=article, platform="zhihu", mode="publish",
                status="submitted_unverified", returncode=1,
            )
            for platform in ("toutiao", "jianshu", "yidian", "bilibili"):
                append_record(
                    records, article=article, platform=platform, mode="publish",
                    status="published", returncode=0,
                )
            append_record(
                records, article=article, platform="wechat", mode="draft",
                status="draft_only", returncode=0,
            )
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", root / "state.json"
            ), patch.object(monitor_publish, "PUBLISH_LOCK_FILE",
                            root / ".ordo" / "publish.lock"), patch.object(
                monitor_publish, "BatchCoordinator") as coordinator:
                coordinator.return_value.run_batch.return_value = {"articles": {}}
                self.assertEqual(monitor_publish.scan_once(root), [article.resolve()])
            coordinator.return_value.run_batch.assert_called_once_with([article.resolve()])

    def test_terminal_status_with_nonzero_returncode_is_not_success(self):
        summary = monitor_publish.PublishSummary()
        summary.add("zhihu", "published", "", 1)
        self.assertEqual(summary.succeeded, [])
        self.assertEqual(summary.failed, ["zhihu"])
        self.assertFalse(monitor_publish._is_terminal(
            "zhihu", {"mode": "publish", "status": "published", "returncode": 1}
        ))

    def test_historical_terminal_record_is_not_overwritten_by_later_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(records, article=article, platform="zhihu", mode="publish", status="published")
            append_record(records, article=article, platform="zhihu", mode="publish", status="failed", returncode=1)
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records):
                merged = monitor_publish.merge_record_successes({}, article)
            self.assertTrue(monitor_publish._is_terminal("zhihu", merged["platforms"]["zhihu"]))

    def test_newer_unverified_overrides_historical_terminal_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            identity = monitor_publish.durable_article_key(article)
            append_record(
                records, article=article, article_key=identity,
                platform="zhihu", mode="publish", status="published",
            )
            append_record(
                records, article=article, article_key=identity,
                platform="zhihu", mode="publish", status="submitted_unverified",
                returncode=1,
            )
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records):
                merged = monitor_publish.merge_record_successes({}, article)
            self.assertEqual(
                merged["platforms"]["zhihu"]["status"], "submitted_unverified"
            )

    def test_newer_terminal_resolves_historical_unverified_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            identity = monitor_publish.durable_article_key(article)
            append_record(
                records, article=article, article_key=identity,
                platform="zhihu", mode="publish", status="submitted_unverified",
                returncode=1,
            )
            append_record(
                records, article=article, article_key=identity,
                platform="zhihu", mode="publish", status="published",
            )
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records):
                merged = monitor_publish.merge_record_successes({}, article)
            self.assertEqual(merged["platforms"]["zhihu"]["status"], "published")

    def test_existing_state_terminal_is_not_overwritten_by_csv_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(records, article=article, platform="zhihu", mode="publish", status="failed", returncode=1)
            existing = {"platforms": {"zhihu": {
                "mode": "publish", "status": "published", "returncode": 0,
            }}}
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records):
                merged = monitor_publish.merge_record_successes(existing, article)
            self.assertEqual(merged["platforms"]["zhihu"]["status"], "published")

    def test_existing_empty_or_invalid_csv_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for content in ("", "platform,status\nzhihu,published\n"):
                records = root / "records.csv"
                records.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaisesRegex(RuntimeError, "CSV"):
                    monitor_publish.read_record_rows(records)

    def test_force_command_uses_narrow_force_republish_flag_and_not_skip_published(self):
        cmd = monitor_publish.build_publish_cmd(
            Path("/tmp/a.md"), platforms="zhihu", mode="publish", force_republish=True
        )
        self.assertIn("--force-republish", cmd)
        self.assertNotIn("--skip-published", cmd)

    def test_timeout_terminates_process_group_and_returns_124(self):
        process = unittest.mock.MagicMock(pid=4321)
        process.wait.side_effect = [subprocess.TimeoutExpired("publish", 900), 0]
        with patch.object(monitor_publish.subprocess, "Popen", return_value=process), patch.object(
            monitor_publish.os, "killpg"
        ) as killpg:
            result = monitor_publish.run_cmd(["publish"])
        self.assertEqual(result, 124)
        killpg.assert_called_once_with(4321, monitor_publish.signal.SIGTERM)

    def test_timeout_escalates_to_sigkill_when_group_does_not_exit(self):
        process = unittest.mock.MagicMock(pid=4321)
        process.wait.side_effect = [
            subprocess.TimeoutExpired("publish", 900),
            subprocess.TimeoutExpired("publish", 5),
            0,
        ]
        with patch.object(monitor_publish.subprocess, "Popen", return_value=process), patch.object(
            monitor_publish.os, "killpg"
        ) as killpg:
            self.assertEqual(monitor_publish.run_cmd(["publish"]), 124)
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list],
            [monitor_publish.signal.SIGTERM, monitor_publish.signal.SIGKILL],
        )

    def test_timeout_falls_back_to_process_terminate_when_killpg_unavailable(self):
        process = unittest.mock.MagicMock(pid=4321)
        process.wait.side_effect = [subprocess.TimeoutExpired("publish", 900), 0]
        with patch.object(monitor_publish.subprocess, "Popen", return_value=process), patch.object(
            monitor_publish.os, "killpg", side_effect=OSError("unsupported")
        ):
            self.assertEqual(monitor_publish.run_cmd(["publish"]), 124)
        process.terminate.assert_called_once_with()

    def test_current_run_does_not_claim_other_run_published_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(
                records, article=article, platform="zhihu", mode="publish",
                status="published", run_id="other-run",
            )
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records):
                outcomes = monitor_publish._new_command_outcomes(
                    article, ["zhihu"], "publish", "current-run", 1
                )
            self.assertEqual(outcomes["zhihu"]["status"], "unknown")

    def test_main_maps_attempted_to_nonzero_and_safe_results_to_zero(self):
        with patch.object(monitor_publish, "publish_article_once", return_value="attempted"):
            self.assertEqual(monitor_publish.main(["--article", "/tmp/a.md"]), 1)
        for status in ("success", "skipped", "dry_run"):
            with self.subTest(status=status), patch.object(
                monitor_publish, "publish_article_once", return_value=status
            ):
                self.assertEqual(monitor_publish.main(["--article", "/tmp/a.md"]), 0)
        with patch.object(monitor_publish, "publish_article_once", return_value="skipped_overlap"):
            self.assertEqual(monitor_publish.main(["--article", "/tmp/a.md"]), 1)
        with patch.object(
            monitor_publish, "scan_once", side_effect=monitor_publish.AutoPublishRunError("failed")
        ):
            self.assertEqual(monitor_publish.main(["--watch-dir", "/tmp"]), 1)

    def test_save_state_uses_fsync_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            with patch.object(monitor_publish.os, "fsync", wraps=monitor_publish.os.fsync) as fsync, patch.object(
                monitor_publish.os, "replace", wraps=monitor_publish.os.replace
            ) as replace:
                monitor_publish.save_state({"articles": {}}, target)
            fsync.assert_called_once()
            replace.assert_called_once()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"articles": {}})

    def test_csv_terminal_records_do_not_override_missing_v2_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(records, article=article, platform="wechat", mode="draft", status="draft_only")
            for platform in monitor_publish.BROWSER_PLATFORMS:
                append_record(records, article=article, platform=platform, mode="publish", status="published")
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", root / "state.json"
            ), patch.object(monitor_publish, "PUBLISH_LOCK_FILE",
                            root / ".ordo" / "publish.lock"), patch.object(
                monitor_publish, "BatchCoordinator") as coordinator:
                coordinator.return_value.run_batch.return_value = {"articles": {}}
                todo = monitor_publish.scan_once(root)
            self.assertEqual(todo, [article.resolve()])
            coordinator.return_value.run_batch.assert_called_once_with([article.resolve()])
            source = MODULE_PATH.read_text(encoding="utf-8")
            self.assertNotIn("require_vps_ready", source)
            self.assertNotIn("jianshu_dedicated_browser", source)

    def test_completed_v2_article_skips_without_csv_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text(
                "---\narticle_id: stable-a\ntitle: A\n---\nBody\n",
                encoding="utf-8",
            )
            state_file = root / ".ordo" / "auto_publish_state.json"
            record = ArticleRecord(
                article_id="stable-a",
                source_path=str(article),
                article_stage=ArticleStage.completed,
            )
            save_v2_state({"stable-a": record}, state_file)

            with patch.object(monitor_publish, "STATE_FILE", state_file), patch.object(
                monitor_publish, "PUBLISH_RECORDS_FILE", root / "missing.csv"
            ), patch.object(
                monitor_publish, "PUBLISH_LOCK_FILE", root / ".ordo" / "publish.lock"
            ), patch.object(monitor_publish, "BatchCoordinator") as coordinator:
                self.assertEqual(monitor_publish.scan_once(root), [])

            coordinator.assert_not_called()

    def test_scan_skips_pending_article_when_all_platforms_are_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text(
                "---\narticle_id: stable-a\ntitle: A\n---\nBody\n",
                encoding="utf-8",
            )
            state_file = root / ".ordo" / "auto_publish_state.json"
            platforms = {
                "wechat": {
                    "draft": PlatformRecord(stage=PlatformStage.draft_saved),
                },
                **{
                    platform: {
                        "publish": PlatformRecord(stage=PlatformStage.manual_verify),
                    }
                    for platform in monitor_publish.BROWSER_PLATFORMS
                },
            }
            save_v2_state(
                {
                    "stable-a": ArticleRecord(
                        article_id="stable-a",
                        source_path=str(article),
                        article_stage=ArticleStage.pending,
                        platforms=platforms,
                    )
                },
                state_file,
            )

            with patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(
                monitor_publish,
                "PUBLISH_LOCK_FILE",
                root / ".ordo" / "publish.lock",
            ), patch.object(monitor_publish, "BatchCoordinator") as coordinator:
                coordinator.return_value.needs_any_processing.return_value = False
                self.assertEqual(monitor_publish.scan_once(root), [])

            coordinator.return_value.run_batch.assert_not_called()

    def test_empty_queue_is_terminal_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            monitor_publish, "PUBLISH_LOCK_FILE", Path(tmp) / ".ordo" / "publish.lock"
        ), patch.object(monitor_publish, "run_cmd") as run:
            self.assertEqual(monitor_publish.scan_once(Path(tmp)), [])
            run.assert_not_called()

    def test_scan_overlap_skips_before_state_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / ".ordo" / "publish.lock"
            lock_path.parent.mkdir(parents=True)
            with run_lock(lock_path), patch.object(
                monitor_publish, "PUBLISH_LOCK_FILE", lock_path
            ), patch.object(monitor_publish, "load_state") as load_state:
                result = monitor_publish.scan_once(root)
            self.assertEqual(result, "skipped_overlap")
            load_state.assert_not_called()

    def test_main_maps_scan_overlap_to_nonzero(self):
        with patch.object(monitor_publish, "scan_once", return_value="skipped_overlap"):
            self.assertEqual(monitor_publish.main(["--once", "--watch-dir", "/tmp"]), 1)

    def test_force_is_rejected_before_any_publish_work(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            monitor_publish, "BatchCoordinator"
        ) as coordinator:
            with self.assertRaisesRegex(
                monitor_publish.AutoPublishRunError, "禁用 --force"
            ):
                monitor_publish.scan_once(Path(tmp), force=True)
        coordinator.assert_not_called()

    def test_daemon_reports_scan_exception_then_continues_to_sleep(self):
        with patch.object(
            monitor_publish, "scan_once", side_effect=[RuntimeError("boom"), KeyboardInterrupt]
        ) as scan, patch.object(monitor_publish.time, "sleep") as sleep, patch("builtins.print") as output:
            with self.assertRaises(KeyboardInterrupt):
                monitor_publish.run_daemon(Path("/tmp"), interval=7)
        self.assertEqual(scan.call_count, 2)
        sleep.assert_called_once_with(7)
        self.assertTrue(any("boom" in str(call) for call in output.call_args_list))

    # ── 新增：coordinator 关键行为测试 ─────────────────────

    def test_scan_once_acquires_lock_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("# A", encoding="utf-8")
            lock_path = root / ".ordo" / "publish.lock"
            lock_path.parent.mkdir(parents=True)

            lock_calls = []
            class _LockCM:
                def __init__(self, *a):
                    lock_calls.append(1)
                def __enter__(self):
                    return None
                def __exit__(self, *a):
                    pass

            with patch.object(monitor_publish, "PUBLISH_LOCK_FILE", lock_path), \
                 patch.object(monitor_publish, "run_lock", _LockCM), \
                 patch.object(monitor_publish, "STATE_FILE", root / "state.json"), \
                 patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"), \
                 patch.object(monitor_publish, "BatchCoordinator") as mock_cls:
                mock_cls.return_value.run_batch.return_value = _mock_coordinator_summary()
                monitor_publish.scan_once(root)

            self.assertEqual(len(lock_calls), 1, "scan_once 应只获取一次锁")

    def test_all_eligible_articles_enter_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.md", "b.md", "c.md"):
                (root / name).write_text(f"# {name}", encoding="utf-8")
            lock_path = root / ".ordo" / "publish.lock"
            lock_path.parent.mkdir(parents=True)

            with patch.object(monitor_publish, "PUBLISH_LOCK_FILE", lock_path), \
                 patch.object(monitor_publish, "STATE_FILE", root / "state.json"), \
                 patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"), \
                 patch.object(monitor_publish, "run_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = None
                mock_lock.return_value.__exit__.return_value = None

                with patch.object(monitor_publish, "BatchCoordinator") as mock_cls:
                    mock_cls.return_value.run_batch.return_value = _mock_coordinator_summary()
                    monitor_publish.scan_once(root)

                self.assertEqual(mock_cls.return_value.run_batch.call_count, 1)
                batch_args = mock_cls.return_value.run_batch.call_args[0][0]
                self.assertEqual(len(batch_args), 3, "三篇文章都应进入 coordinator")

    def test_scan_handles_coordinator_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("# A", encoding="utf-8")
            lock_path = root / ".ordo" / "publish.lock"
            lock_path.parent.mkdir(parents=True)

            with patch.object(monitor_publish, "PUBLISH_LOCK_FILE", lock_path), \
                 patch.object(monitor_publish, "STATE_FILE", root / "state.json"), \
                 patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"), \
                 patch.object(monitor_publish, "run_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = None
                mock_lock.return_value.__exit__.return_value = None

                with patch.object(monitor_publish, "BatchCoordinator") as mock_cls:
                    mock_cls.return_value.run_batch.return_value = {
                        "articles": {"test": {"platforms": {
                            "wechat:draft": {"stage": "draft_saved"},
                            "zhihu:publish": {"stage": "failed_before_draft"},
                        }}}
                    }
                    with self.assertRaises(monitor_publish.AutoPublishRunError):
                        monitor_publish.scan_once(root)


if __name__ == "__main__":
    unittest.main()
