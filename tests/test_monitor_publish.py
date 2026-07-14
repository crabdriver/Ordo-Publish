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


class MonitorPublishTests(unittest.TestCase):
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

    def test_publish_article_runs_at_most_two_local_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            state_file = root / "state.json"

            def fake_run(cmd, **_kwargs):
                platforms = cmd[cmd.index("--platform") + 1].split(",")
                mode = cmd[cmd.index("--mode") + 1]
                run_id = cmd[cmd.index("--run-id") + 1]
                self.assertEqual(cmd[cmd.index("--remote") + 1], "local")
                for platform in platforms:
                    status = "draft_only" if mode == "draft" else "published"
                    append_record(records, article=article, platform=platform, mode=mode, status=status, run_id=run_id)
                return 0

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(monitor_publish, "run_cmd", side_effect=fake_run) as run:
                result = monitor_publish.publish_article(article)

            self.assertEqual(result, "success")
            self.assertEqual(run.call_count, 2)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0][commands[0].index("--platform") + 1], "wechat")
            self.assertEqual(
                commands[1][commands[1].index("--platform") + 1],
                "zhihu,toutiao,jianshu,yidian,bilibili",
            )

    def test_pending_browser_subset_uses_one_stable_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            for platform, mode, status in (
                ("wechat", "draft", "draft_saved"),
                ("zhihu", "publish", "published"),
                ("jianshu", "publish", "scheduled"),
                ("bilibili", "publish", "skipped_existing"),
            ):
                append_record(records, article=article, platform=platform, mode=mode, status=status)

            def fake_run(cmd, **_kwargs):
                run_id = cmd[cmd.index("--run-id") + 1]
                for platform in cmd[cmd.index("--platform") + 1].split(","):
                    append_record(
                        records, article=article, platform=platform, mode="publish", status="published", run_id=run_id
                    )
                return 0

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", root / "state.json"
            ), patch.object(monitor_publish, "run_cmd", side_effect=fake_run) as run:
                monitor_publish.publish_article(article)

            self.assertEqual(run.call_count, 1)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index("--platform") + 1], "toutiao,yidian")

    def test_typed_rate_limit_is_not_success_and_retries_next_run(self):
        self._assert_nonterminal_retries("limit_reached", "rate_limited")

    def test_unverified_is_not_success_but_is_protected_from_resubmission(self):
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
            ), patch.object(monitor_publish, "run_cmd") as run:
                self.assertEqual(monitor_publish.scan_once(root), [])
            run.assert_not_called()

    def _assert_nonterminal_retries(self, status, error_type):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            state_file = root / "state.json"
            calls = []

            def fake_run(cmd, **_kwargs):
                calls.append(cmd)
                platforms = cmd[cmd.index("--platform") + 1].split(",")
                mode = cmd[cmd.index("--mode") + 1]
                run_id = cmd[cmd.index("--run-id") + 1]
                for platform in platforms:
                    row_status = status if platform == "zhihu" else (
                        "draft_only" if mode == "draft" else "published"
                    )
                    append_record(
                        records,
                        article=article,
                        platform=platform,
                        mode=mode,
                        status=row_status,
                        returncode=1 if platform == "zhihu" else 0,
                        error_type=error_type if platform == "zhihu" else "",
                        run_id=run_id,
                    )
                return 1

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(monitor_publish, "run_cmd", side_effect=fake_run):
                first = monitor_publish.publish_article(article)
                second = monitor_publish.publish_article(article)

            self.assertEqual(first, "attempted")
            self.assertEqual(second, "attempted")
            self.assertIn("zhihu", calls[-1][calls[-1].index("--platform") + 1].split(","))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotEqual(
                state["articles"][monitor_publish.article_key(article)].get("status"),
                "success",
            )

    def test_missing_new_csv_row_fails_closed_even_when_command_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            state_file = root / "state.json"
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "missing.csv"), patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(monitor_publish, "run_cmd", return_value=0):
                result = monitor_publish.publish_article(article)

            self.assertEqual(result, "attempted")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            platforms = state["articles"][monitor_publish.article_key(article)]["platforms"]
            self.assertTrue(all(item["status"] == "unknown" for item in platforms.values()))

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

    def test_wechat_state_is_saved_before_browser_command_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            state_file = root / "state.json"
            calls = 0

            def fake_run(cmd, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    run_id = cmd[cmd.index("--run-id") + 1]
                    append_record(records, article=article, platform="wechat", mode="draft", status="draft_only", run_id=run_id)
                    return 0
                raise RuntimeError("browser crashed")

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(monitor_publish, "run_cmd", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "browser crashed"):
                    monitor_publish.publish_article(article)

            saved = json.loads(state_file.read_text(encoding="utf-8"))
            wechat = saved["articles"][monitor_publish.article_key(article)]["platforms"]["wechat"]
            self.assertEqual(wechat["status"], "draft_only")

    def test_run_cmd_has_finite_timeout(self):
        process = unittest.mock.MagicMock()
        process.wait.return_value = 0
        with patch.object(monitor_publish.subprocess, "Popen", return_value=process) as run:
            monitor_publish.run_cmd(["publish"])
        self.assertTrue(run.call_args.kwargs["start_new_session"])
        process.wait.assert_called_once_with(timeout=monitor_publish.PUBLISH_TIMEOUT_SECONDS)

    def test_run_cmd_passes_valid_parent_lock_to_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "publish.lock"
            script = (
                "import os, sys\n"
                "from pathlib import Path\n"
                "from ordo_engine.run_lock import run_lock\n"
                "with run_lock(Path(sys.argv[1]), inherited_fd=int(os.environ['ORDO_PUBLISH_LOCK_FD'])):\n"
                "    pass\n"
            )
            with run_lock(lock_path) as lock_fd:
                returncode = monitor_publish.run_cmd(
                    [sys.executable, "-c", script, str(lock_path)],
                    lock_fd=lock_fd,
                )
            self.assertEqual(returncode, 0)

    def test_scan_passes_held_lock_fd_to_publish_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            seen = []

            def fake_run(_cmd, **kwargs):
                fd = kwargs.get("lock_fd")
                seen.append(fd)
                self.assertEqual(os.fstat(fd).st_ino, monitor_publish.PUBLISH_LOCK_FILE.stat().st_ino)
                return 1

            with patch.object(
                monitor_publish, "PUBLISH_LOCK_FILE", root / ".ordo" / "publish.lock"
            ), patch.object(monitor_publish, "STATE_FILE", root / "state.json"), patch.object(
                monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"
            ), patch.object(monitor_publish, "run_cmd", side_effect=fake_run):
                with self.assertRaises(monitor_publish.AutoPublishRunError):
                    monitor_publish.scan_once(root)
            self.assertTrue(seen)

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

    def test_timeout_missing_records_is_persisted_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            state_file = root / "state.json"
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"), patch.object(
                monitor_publish, "STATE_FILE", state_file
            ), patch.object(monitor_publish, "run_cmd", return_value=124):
                self.assertEqual(monitor_publish.publish_article(article), "attempted")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            platform_state = state["articles"][monitor_publish.article_key(article)]["platforms"]
            self.assertTrue(all(item["error_type"] == "timeout" for item in platform_state.values()))

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

    def test_scan_processes_all_articles_then_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.md", "b.md"):
                (root / name).write_text(f"# {name}", encoding="utf-8")
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", root / "records.csv"), patch.object(
                monitor_publish, "STATE_FILE", root / "state.json"
            ), patch.object(monitor_publish, "publish_article", side_effect=["attempted", "success"]) as publish:
                with self.assertRaises(monitor_publish.AutoPublishRunError):
                    monitor_publish.scan_once(root)
            self.assertEqual(publish.call_count, 2)

    def test_rows_before_command_do_not_count_as_command_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            records = root / "records.csv"
            append_record(records, article=article, platform="zhihu", mode="publish", status="failed", returncode=1)
            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish, "STATE_FILE", root / "state.json"
            ), patch.object(monitor_publish, "run_cmd", return_value=0):
                result = monitor_publish.publish_article(article)
            self.assertEqual(result, "attempted")

    def test_corrupt_state_blocks_before_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "a.md"
            article.write_text("# A", encoding="utf-8")
            state_file = root / "state.json"
            state_file.write_text("{bad", encoding="utf-8")
            with patch.object(monitor_publish, "STATE_FILE", state_file), patch.object(
                monitor_publish, "run_cmd"
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "JSON"):
                    monitor_publish.publish_article(article)
            run.assert_not_called()

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

    def test_all_terminal_records_skip_without_command_or_vps(self):
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
            ), patch.object(monitor_publish, "run_cmd") as run:
                todo = monitor_publish.scan_once(root)
            self.assertEqual(todo, [])
            run.assert_not_called()
            source = MODULE_PATH.read_text(encoding="utf-8")
            self.assertNotIn("require_vps_ready", source)
            self.assertNotIn("jianshu_dedicated_browser", source)

    def test_empty_queue_is_terminal_noop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            monitor_publish, "run_cmd"
        ) as run:
            self.assertEqual(monitor_publish.scan_once(Path(tmp)), [])
            run.assert_not_called()

    def test_scan_overlap_skips_before_state_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "publish.lock"
            with run_lock(lock_path), patch.object(
                monitor_publish, "PUBLISH_LOCK_FILE", lock_path
            ), patch.object(monitor_publish, "load_state") as load_state:
                result = monitor_publish.scan_once(root)
            self.assertEqual(result, "skipped_overlap")
            load_state.assert_not_called()

    def test_main_maps_scan_overlap_to_nonzero(self):
        with patch.object(monitor_publish, "scan_once", return_value="skipped_overlap"):
            self.assertEqual(monitor_publish.main(["--once", "--watch-dir", "/tmp"]), 1)

    def test_daemon_reports_scan_exception_then_continues_to_sleep(self):
        with patch.object(
            monitor_publish, "scan_once", side_effect=[RuntimeError("boom"), KeyboardInterrupt]
        ) as scan, patch.object(monitor_publish.time, "sleep") as sleep, patch("builtins.print") as output:
            with self.assertRaises(KeyboardInterrupt):
                monitor_publish.run_daemon(Path("/tmp"), interval=7)
        self.assertEqual(scan.call_count, 2)
        sleep.assert_called_once_with(7)
        self.assertTrue(any("boom" in str(call) for call in output.call_args_list))


if __name__ == "__main__":
    unittest.main()
