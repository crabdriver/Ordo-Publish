import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ordo_engine.run_lock import InvalidInheritedLock, RunAlreadyActive, run_lock


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

    def test_stale_lock_file_does_not_block_new_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"
            path.write_text("crashed", encoding="utf-8")

            with run_lock(path) as lock_fd:
                self.assertEqual(os.fstat(lock_fd).st_ino, path.stat().st_ino)

    def test_inherited_fd_for_same_lock_is_accepted_without_relocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"
            script = (
                "import os, sys\n"
                "from pathlib import Path\n"
                "from ordo_engine.run_lock import run_lock\n"
                "fd = int(os.environ['ORDO_PUBLISH_LOCK_FD'])\n"
                "with run_lock(Path(sys.argv[1]), inherited_fd=fd):\n"
                "    pass\n"
            )
            with run_lock(path) as lock_fd:
                env = dict(os.environ, ORDO_PUBLISH_LOCK_FD=str(lock_fd))
                result = subprocess.run(
                    [sys.executable, "-c", script, str(path)],
                    cwd=Path(__file__).resolve().parents[1],
                    env=env,
                    pass_fds=(lock_fd,),
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_inherited_fd_for_other_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"
            other = Path(tmp) / "not-the-lock"
            other.touch()
            with other.open("r") as handle:
                with self.assertRaises(InvalidInheritedLock):
                    with run_lock(path, inherited_fd=handle.fileno()):
                        pass

    def test_closed_inherited_fd_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publish.lock"
            handle = (Path(tmp) / "closed").open("w")
            fd = handle.fileno()
            handle.close()
            with self.assertRaises(InvalidInheritedLock):
                with run_lock(path, inherited_fd=fd):
                    pass


if __name__ == "__main__":
    unittest.main()
