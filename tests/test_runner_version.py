import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordo_engine.runner import version


class VersionTests(unittest.TestCase):
    def test_fingerprint_changes_with_runtime_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "publish.py").write_text("one", encoding="utf-8")
            first = version.get_local_code_fingerprint(root)
            (root / "publish.py").write_text("two", encoding="utf-8")
            self.assertNotEqual(first, version.get_local_code_fingerprint(root))

    def test_verify_falls_back_to_source_fingerprint_without_remote_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "publish.py").write_text("one", encoding="utf-8")
            fingerprint = version.get_local_code_fingerprint(root)
            with patch.object(version, "get_local_git_commit", return_value="abc"), patch.object(
                version, "get_remote_git_commit", return_value=None
            ), patch.object(version, "get_remote_code_fingerprint", return_value=fingerprint):
                self.assertEqual(
                    version.verify_codebase_version("host", remote_path="/root/ordo-publish", local_repo_path=str(root)),
                    (True, fingerprint, fingerprint),
                )
