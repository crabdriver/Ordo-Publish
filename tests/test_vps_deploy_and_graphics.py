import unittest
import tempfile
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import ordo_worker


class TestVpsDeployAndGraphics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean up any test PID files
        for name in ["xvfb", "x11vnc", "websockify", "chrome"]:
            p = Path(f"/tmp/ordo-{name}-9333.pid")
            if p.exists():
                p.unlink()

    @patch("ordo_worker.is_proxy_available", return_value=False)
    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_start_browser_with_xvfb(self, mock_which, mock_popen, mock_proxy):
        # Setup which mock
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"

        # Mock Popen instances
        mock_xvfb_proc = MagicMock()
        mock_xvfb_proc.pid = 1111
        mock_vnc_proc = MagicMock()
        mock_vnc_proc.pid = 2222
        mock_web_proc = MagicMock()
        mock_web_proc.pid = 3333
        mock_chrome_proc = MagicMock()
        mock_chrome_proc.pid = 4444

        mock_popen.side_effect = [
            mock_xvfb_proc,
            mock_vnc_proc,
            mock_web_proc,
            mock_chrome_proc
        ]

        # Call start_browser with xvfb enabled
        ordo_worker.start_browser(
            port=9333,
            profile_dir=str(self.temp_path / "chrome-profile"),
            use_xvfb=True
        )

        # Check that Xvfb was launched
        xvfb_pid_file = Path("/tmp/ordo-xvfb-9333.pid")
        self.assertTrue(xvfb_pid_file.exists())
        self.assertEqual(xvfb_pid_file.read_text().strip(), "1111")

        # Check that x11vnc was launched
        vnc_pid_file = Path("/tmp/ordo-x11vnc-9333.pid")
        self.assertTrue(vnc_pid_file.exists())
        self.assertEqual(vnc_pid_file.read_text().strip(), "2222")

        # Check that websockify was launched
        websock_pid_file = Path("/tmp/ordo-websockify-9333.pid")
        self.assertTrue(websock_pid_file.exists())
        self.assertEqual(websock_pid_file.read_text().strip(), "3333")

        # Check that Chrome was launched with DISPLAY env and WITHOUT --headless=new
        chrome_pid_file = Path("/tmp/ordo-chrome-9333.pid")
        self.assertTrue(chrome_pid_file.exists())
        self.assertEqual(chrome_pid_file.read_text().strip(), "4444")

        # Check subprocess.Popen arguments
        self.assertEqual(mock_popen.call_count, 4)
        
        # Verify chrome call
        chrome_call_args = mock_popen.call_args_list[3]
        cmd_args = chrome_call_args[0][0]
        kwargs = chrome_call_args[1]
        
        self.assertNotIn("--headless=new", cmd_args)
        self.assertIn("--remote-debugging-port=9333", cmd_args)
        self.assertEqual(kwargs.get("env", {}).get("DISPLAY"), ":99")

    @patch("os.kill")
    def test_stop_browser_graphics_cleanup(self, mock_kill):
        # Pre-create mock PID files
        Path("/tmp/ordo-chrome-9333.pid").write_text("4444")
        Path("/tmp/ordo-xvfb-9333.pid").write_text("1111")
        Path("/tmp/ordo-x11vnc-9333.pid").write_text("2222")
        Path("/tmp/ordo-websockify-9333.pid").write_text("3333")

        # Call stop_browser
        with patch.dict("sys.modules", {"psutil": None}):
            ordo_worker.stop_browser(9333)

        # Check that kill was called for all processes
        mock_kill.assert_any_call(4444, 15) # Chrome
        mock_kill.assert_any_call(1111, 15) # Xvfb
        mock_kill.assert_any_call(2222, 15) # x11vnc
        mock_kill.assert_any_call(3333, 15) # websockify

        # Check that all PID files were unlinked
        self.assertFalse(Path("/tmp/ordo-chrome-9333.pid").exists())
        self.assertFalse(Path("/tmp/ordo-xvfb-9333.pid").exists())
        self.assertFalse(Path("/tmp/ordo-x11vnc-9333.pid").exists())
        self.assertFalse(Path("/tmp/ordo-websockify-9333.pid").exists())


if __name__ == "__main__":
    unittest.main()
