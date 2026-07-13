from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from ordo_engine.platforms.base import is_terminal_outcome

WORKBENCH_ROOT = Path(".ordo") / "workbench"
OPERATIONS_ROOT = WORKBENCH_ROOT / "operations"

BUTTON_MATRIX = (
    {
        "action": "import-file",
        "category": "real_side_effect",
        "label": "导入单文件",
        "verification_mode": "ui+manifest",
        "expected": "生成 1 篇导入记录或失败条目",
    },
    {
        "action": "import-folder",
        "category": "real_side_effect",
        "label": "导入文件夹",
        "verification_mode": "ui+manifest",
        "expected": "生成批量导入记录与失败清单",
    },
    {
        "action": "confirm-paste-import",
        "category": "real_side_effect",
        "label": "确认粘贴导入",
        "verification_mode": "ui",
        "expected": "创建 paste 来源稿件",
    },
    {
        "action": "save-wechat-settings",
        "category": "real_side_effect",
        "label": "保存微信配置",
        "verification_mode": "ui+file",
        "expected": "更新 secrets.env / 状态面板",
    },
    {
        "action": "start-publish",
        "category": "dangerous_recovery",
        "label": "开始发布",
        "verification_mode": "real_publish+lock",
        "expected": "创建发布任务并持有后端发布锁",
    },
    {
        "action": "retry-failed-publish",
        "category": "dangerous_recovery",
        "label": "续跑失败项",
        "verification_mode": "history+retry_queue",
        "expected": "只重建失败组合的发布计划",
    },
    {
        "action": "restore-latest-plan",
        "category": "dangerous_recovery",
        "label": "恢复上次任务",
        "verification_mode": "history",
        "expected": "恢复最近一次完整计划",
    },
    {
        "action": "restore-failed-plan",
        "category": "dangerous_recovery",
        "label": "恢复失败项",
        "verification_mode": "history",
        "expected": "恢复失败组合的重试计划",
    },
    {
        "action": "open-theme-modal",
        "category": "low_risk_state",
        "label": "打开模板批量设置",
        "verification_mode": "ui",
        "expected": "显示模板矩阵配置面板",
    },
    {
        "action": "open-settings-modal",
        "category": "low_risk_state",
        "label": "打开设置",
        "verification_mode": "ui",
        "expected": "显示微信/浏览器/指引设置弹窗",
    },
    {
        "action": "fetch-public-ip",
        "category": "low_risk_state",
        "label": "获取公网 IP",
        "verification_mode": "network+clipboard",
        "expected": "复制 IP 并给出白名单提示",
    },
    {
        "action": "open-external-url",
        "category": "low_risk_state",
        "label": "在浏览器打开",
        "verification_mode": "browser",
        "expected": "打开对应平台写作页",
    },
    {
        "action": "copy-url",
        "category": "low_risk_state",
        "label": "复制链接",
        "verification_mode": "clipboard",
        "expected": "复制平台写作页 URL",
    },
    {
        "action": "refresh",
        "category": "low_risk_state",
        "label": "刷新资源",
        "verification_mode": "ui+resources",
        "expected": "刷新主题池/封面池/登录态摘要",
    },
)


def build_button_matrix():
    return [dict(item) for item in BUTTON_MATRIX]


def _detect_platform(message: str) -> str:
    if "微信" in message:
        return "wechat"
    if "知乎" in message:
        return "zhihu"
    if "头条" in message:
        return "toutiao"
    if "简书" in message:
        return "jianshu"
    if "一点" in message:
        return "yidian"
    match = re.search(r"`([a-z]+)`", message)
    return match.group(1) if match else "unknown"


def build_retry_queue(*, preflight_report=None, publish_result=None):
    queue = []
    for message in (preflight_report or {}).get("blockers", []):
        queue.append(
            {
                "platform": _detect_platform(str(message)),
                "status": "blocked_preflight",
                "reason": str(message),
                "retryable": False,
            }
        )
    for result in (publish_result or {}).get("results", []):
        status = str(result.get("status") or "")
        mode = str(result.get("mode") or (publish_result or {}).get("mode") or "draft")
        if result.get("returncode") == 0 and status == "skipped_existing":
            continue
        if result.get("returncode") == 0 and is_terminal_outcome(status, mode):
            continue
        queue.append(
            {
                "platform": str(result.get("platform") or "unknown"),
                "article_id": result.get("article_id"),
                "status": "retryable_failed" if result.get("retryable") else "fatal_failed",
                "reason": str(result.get("summary") or result.get("stderr") or "publish failed"),
                "retryable": bool(result.get("retryable")),
            }
        )
    return queue


def write_operations_matrix(base_dir, *, bundle_id=None, preflight_report=None, publish_result=None) -> Path:
    root = Path(base_dir).expanduser().resolve()
    resolved_bundle_id = bundle_id or f"ops-{uuid.uuid4().hex}"
    payload = {
        "bundle_id": resolved_bundle_id,
        "button_matrix": build_button_matrix(),
        "retry_queue": build_retry_queue(preflight_report=preflight_report, publish_result=publish_result),
    }
    path = root / OPERATIONS_ROOT / f"{resolved_bundle_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
