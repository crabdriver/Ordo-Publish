import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class OrdoCliBootstrapTests(unittest.TestCase):
    def _write_template_repo(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.example.json").write_text('{"settings": {}}\n', encoding="utf-8")
        (root / "publish.py").write_text("print('publish')\n", encoding="utf-8")
        (root / "live_cdp.mjs").write_text("console.log('cdp');\n", encoding="utf-8")
        (root / "themes").mkdir(parents=True)
        (root / "themes" / "default.json").write_text('{"name": "Default"}\n', encoding="utf-8")
        (root / "templates").mkdir(parents=True)
        (root / "templates" / "preview.html").write_text("<html></html>\n", encoding="utf-8")
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "format.py").write_text("def noop():\n    return 'ok'\n", encoding="utf-8")
        (root / "tiandi_engine" / "workbench").mkdir(parents=True)
        (root / "tiandi_engine" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tiandi_engine" / "workbench" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tiandi_engine" / "workbench" / "bridge.py").write_text("BRIDGE = True\n", encoding="utf-8")

    def test_seed_runtime_repo_copies_runtime_files_and_preserves_user_config(self):
        from tiandi_engine.cli.runtime import seed_runtime_repo

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template"
            home = root / "home"
            self._write_template_repo(template)

            runtime_repo = home / "runtime" / "repo"
            runtime_repo.mkdir(parents=True)
            (runtime_repo / "config.json").write_text('{"saved": true}\n', encoding="utf-8")
            (runtime_repo / "secrets.env").write_text("WECHAT_APPID=keep-me\n", encoding="utf-8")
            (runtime_repo / "publish.py").write_text("old\n", encoding="utf-8")

            result = seed_runtime_repo(template_root=template, app_home=home)

            self.assertEqual(result.resolve(), runtime_repo.resolve())
            self.assertEqual((runtime_repo / "publish.py").read_text(encoding="utf-8"), "print('publish')\n")
            self.assertEqual((runtime_repo / "config.json").read_text(encoding="utf-8"), '{"saved": true}\n')
            self.assertEqual((runtime_repo / "secrets.env").read_text(encoding="utf-8"), "WECHAT_APPID=keep-me\n")
            self.assertTrue((runtime_repo / "config.example.json").is_file())
            self.assertTrue((runtime_repo / "themes" / "default.json").is_file())
            self.assertTrue((runtime_repo / "templates" / "preview.html").is_file())

    def test_main_bootstraps_runtime_and_dispatches_to_repo_entrypoint(self):
        from tiandi_engine.cli import app

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            template = root / "template"
            home = root / "ordo-home"
            self._write_template_repo(template)

            with patch.object(app, "run_repo_entrypoint", return_value=7) as mock_run:
                exit_code = app.main(
                    argv=["--demo"],
                    environ={
                        "ORDO_REPO_TEMPLATE_ROOT": str(template),
                        "ORDO_HOME": str(home),
                    },
                )

        self.assertEqual(exit_code, 7)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["argv"], ["--demo"])
        self.assertEqual(kwargs["runtime_repo_root"].resolve(), (home / "runtime" / "repo").resolve())

    def test_pyproject_exposes_ordo_console_script(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file())
        text = pyproject_path.read_text(encoding="utf-8")
        self.assertIn("[project.scripts]", text)
        self.assertIn('ordo = "tiandi_engine.cli.app:main"', text)

    def test_resolve_app_home_uses_xdg_config_on_linux(self):
        from tiandi_engine.cli import runtime

        with patch.object(runtime.sys, "platform", "linux"):
            path = runtime.resolve_app_home({"XDG_CONFIG_HOME": "/tmp/xdg"})

        self.assertEqual(path.resolve(), (Path("/tmp/xdg") / "ordo").resolve())

    def test_run_repo_entrypoint_dispatches_to_terminal_tui(self):
        from tiandi_engine.cli import app

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_repo = Path(tmpdir)
            with patch.object(app, "ensure_runtime_importable") as mock_importable, patch(
                "tiandi_engine.workbench.terminal_tui.main",
                return_value=5,
            ) as mock_tui_main:
                exit_code = app.run_repo_entrypoint(runtime_repo_root=runtime_repo, argv=["--demo"])

        self.assertEqual(exit_code, 5)
        mock_importable.assert_called_once_with(runtime_repo)
        mock_tui_main.assert_called_once_with(base_dir=runtime_repo)


if __name__ == "__main__":
    unittest.main()
