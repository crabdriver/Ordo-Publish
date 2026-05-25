import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DummyAdapter:
    def __init__(self, base_dir, platform, returncode=0, stdout="", stderr=""):
        self.base_dir = Path(base_dir)
        self.platform = platform
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def prepare(
        self,
        markdown_file,
        mode,
        theme_name=None,
        cover_path=None,
        template_mode=None,
        article_id=None,
        cover_mode=None,
        ai_declaration_mode=None,
        scheduled_publish_at=None,
    ):
        return {
            "platform": self.platform,
            "markdown_file": str(markdown_file),
            "mode": mode,
        }

    def publish(self, prepared_context):
        return {
            "platform": self.platform,
            "command": f"dummy {self.platform} {prepared_context['markdown_file']}",
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def verify(self, process_result, mode):
        if process_result["returncode"] != 0:
            return "failed"
        return "draft_only" if mode == "draft" else "published"

    def collect_result(self, process_result, mode):
        from tiandi_engine.results.record import ExecutionResult

        return ExecutionResult(
            platform=self.platform,
            stage="publish",
            status=self.verify(process_result, mode),
            summary=process_result["stderr"] or process_result["stdout"] or "ok",
            stdout=process_result["stdout"],
            stderr=process_result["stderr"],
            retryable=process_result["returncode"] != 0,
        )


class TerminalServiceTests(unittest.TestCase):
    def test_read_terminal_defaults_from_service_module(self):
        from tiandi_engine.workbench.terminal_service import read_terminal_defaults

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "config.json").write_text(
                json.dumps({"terminal_wizard": {"defaults": {"source_path": "/tmp/articles"}}}, ensure_ascii=False),
                encoding="utf-8",
            )

            settings = read_terminal_defaults(base)

        self.assertEqual(settings.source_path, "/tmp/articles")

    def test_save_terminal_defaults_from_service_module(self):
        from tiandi_engine.workbench.terminal_service import TerminalWizardSettings, save_terminal_defaults

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            save_terminal_defaults(
                base,
                TerminalWizardSettings(
                    source_path="/tmp/articles",
                    platforms=("wechat", "toutiao"),
                    mode="publish",
                ),
            )
            payload = json.loads((base / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["terminal_wizard"]["defaults"]["mode"], "publish")

    def test_execute_publish_flow_from_service_module(self):
        from tiandi_engine.workbench.terminal_service import TerminalWizardSettings, execute_publish_flow

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            article_dir = base / "articles"
            article_dir.mkdir()
            (article_dir / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
            output = io.StringIO()

            with patch(
                "tiandi_engine.workbench.terminal_service.publish.run_preflight_checks",
                return_value=([], []),
            ):
                result = execute_publish_flow(
                    base,
                    TerminalWizardSettings(
                        source_path=str(article_dir),
                        platforms=("wechat",),
                        mode="draft",
                        cover_mode="force_off",
                    ),
                    registry={"wechat": DummyAdapter(base, "wechat", returncode=0, stdout="ok")},
                    output=lambda text="": print(text, file=output),
                )

        self.assertEqual(result["status"], "completed")
        self.assertIn("成功: 1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
