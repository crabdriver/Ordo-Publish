import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ordo_engine.run_lock import RunAlreadyActive, run_lock


class RunLockTests(unittest.TestCase):
    def test_second_lock_in_same_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "publish.lock"

            with run_lock(path):
                with self.assertRaises(RunAlreadyActive):
                    with run_lock(path):
                        pass

    def test_lock_from_independent_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"
            script = (
                "from pathlib import Path\n"
                "from ordo_engine.run_lock import RunAlreadyActive, run_lock\n"
                "try:\n"
                "    with run_lock(Path(__import__('sys').argv[1])):\n"
                "        raise SystemExit(9)\n"
                "except RunAlreadyActive:\n"
                "    raise SystemExit(0)\n"
            )

            with run_lock(path):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(path)],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_lock_is_released_after_body_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"

            with self.assertRaisesRegex(RuntimeError, "boom"):
                with run_lock(path):
                    raise RuntimeError("boom")

            with run_lock(path):
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
