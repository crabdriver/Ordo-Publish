import json
import tempfile
import unittest
from pathlib import Path

from tiandi_engine.workbench.operations_matrix import build_button_matrix, build_retry_queue, write_operations_matrix


class OperationsMatrixTests(unittest.TestCase):
    def test_build_button_matrix_covers_publish_and_recovery_actions(self):
        entries = build_button_matrix()
        action_ids = {item["action"] for item in entries}
        self.assertIn("start-publish", action_ids)
        self.assertIn("retry-failed-publish", action_ids)
        self.assertIn("save-wechat-settings", action_ids)

    def test_build_retry_queue_includes_preflight_blockers(self):
        queue = build_retry_queue(
            preflight_report={
                "blockers": [
                    "微信公众号缺少 `WECHAT_APPID` 或 `WECHAT_SECRET`，请先配置 `secrets.env`",
                    "知乎预检未通过：当前标签页仍处于登录或校验状态. 当前页面：https://www.zhihu.com/signin",
                ]
            }
        )
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["platform"], "wechat")
        self.assertEqual(queue[0]["status"], "blocked_preflight")
        self.assertEqual(queue[1]["platform"], "zhihu")

    def test_write_operations_matrix_persists_json_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = write_operations_matrix(
                base,
                bundle_id="ops-1",
                preflight_report={"blockers": ["未找到 `toutiao` 的可用标签页，请先在当前远程调试 Chrome 中打开并登录"]},
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["bundle_id"], "ops-1")
        self.assertEqual(payload["retry_queue"][0]["platform"], "toutiao")


if __name__ == "__main__":
    unittest.main()
