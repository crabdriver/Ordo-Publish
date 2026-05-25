import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from tiandi_engine.results.errors import ErrorType, is_retryable_error
from tiandi_engine.results.record import ExecutionResult


LIMIT_MARKERS = [
    "达到发布上限",
    "发布上限",
    "次数上限",
    "每天最多",
    "请明天再来",
    "未来7天",
    "审核通过前你将无法继续编辑",
    "暂不发布",
    "时间限制",
    "排期",
]
ENVIRONMENT_MARKERS = [
    "无法连接 CDP",
    "远程调试",
    "browser not reachable",
    "chrome not reachable",
    "无法自动启动浏览器",
]
PLATFORM_CHANGED_MARKERS = [
    "未就绪",
    "not found",
    "未找到",
    "未检测到",
    "selector",
    "button-not-found",
    "cover-option-not-found",
    "entry-not-found",
]
SMOKE_STATE_PREFIX = "[SMOKE_STATE] "


def _extract_smoke_state(text):
    if not text:
        return "", None

    cleaned_lines = []
    state = None
    for line in text.splitlines():
        if line.startswith(SMOKE_STATE_PREFIX):
            payload = line[len(SMOKE_STATE_PREFIX) :].strip()
            try:
                state = json.loads(payload)
            except json.JSONDecodeError:
                cleaned_lines.append(line)
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip(), state


def classify_process_result(platform, mode, process_result):
    output = "\n".join(filter(None, [process_result.get("stdout", ""), process_result.get("stderr", "")]))
    page_state = str(process_result.get("page_state") or "").strip()

    if any(marker in output for marker in LIMIT_MARKERS):
        return "limit_reached"

    if process_result.get("returncode", 0) != 0:
        if "草稿" in output or page_state == "draft_saved":
            return "draft_only"
        return "failed"

    if platform == "wechat":
        if "已存在同标题文章" in output:
            return "skipped_existing"
        if "已发布到微信公众号" in output:
            return "published"
        if "已写入微信公众号草稿" in output:
            return "draft_only"
        return "success_unknown"

    if mode == "publish":
        if page_state == "scheduled":
            return "scheduled"
        if page_state == "published":
            return "published"
        scheduled_markers = {
            "toutiao": ("已设置定时发布", "已设置头条号定时发布", "预约发布成功", "定时发布成功", "已预约发布"),
        }
        publish_markers = {
            "zhihu": ("已发布到知乎",),
            "toutiao": ("已发布到头条号",),
            "jianshu": ("已发布到简书",),
            "yidian": ("已发布成功",),
        }
        if any(marker in output for marker in scheduled_markers.get(platform, ())):
            return "scheduled"
        if any(marker in output for marker in publish_markers.get(platform, ())):
            return "published"
        return "failed"

    draft_markers = {
        "zhihu": "已写入知乎草稿页",
        "toutiao": "已写入头条草稿页",
        "jianshu": "已生成简书草稿",
        "yidian": "已存草稿",
    }
    if page_state == "draft_saved":
        return "draft_only"
    if draft_markers.get(platform) and draft_markers[platform] in output:
        return "draft_only"
    return "success_unknown"


def infer_error_type(status, process_result):
    output = "\n".join(filter(None, [process_result.get("stdout", ""), process_result.get("stderr", "")]))
    if status == "limit_reached":
        return ErrorType.RATE_LIMITED
    if status == "skipped_existing":
        return ErrorType.DUPLICATE_OR_SKIPPED
    if process_result.get("timed_out"):
        return ErrorType.TRANSIENT_ERROR
    if any(marker in output for marker in ENVIRONMENT_MARKERS):
        return ErrorType.ENVIRONMENT_ERROR
    if "登录" in output:
        return ErrorType.LOGIN_REQUIRED
    if any(marker in output for marker in PLATFORM_CHANGED_MARKERS):
        return ErrorType.PLATFORM_CHANGED
    if process_result.get("returncode", 0) != 0:
        return ErrorType.UNKNOWN_ERROR
    return None


class BasePlatformAdapter(ABC):
    def __init__(self, base_dir: Path, platform: str):
        self.base_dir = Path(base_dir)
        self.platform = platform

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def publish(self, prepared_context):
        raise NotImplementedError

    @abstractmethod
    def verify(self, process_result, mode):
        raise NotImplementedError

    @abstractmethod
    def collect_result(self, process_result, mode):
        raise NotImplementedError


class SubprocessPlatformAdapter(BasePlatformAdapter):
    def __init__(
        self,
        base_dir: Path,
        platform: str,
        script_name: str,
        supports_theme=False,
        supports_cover=False,
        supports_template_mode=False,
        supports_article_id=False,
        supports_cover_mode=False,
        supports_ai_declaration_mode=False,
        supports_scheduled_publish_at=False,
    ):
        super().__init__(base_dir=base_dir, platform=platform)
        self.script_name = script_name
        self.supports_theme = supports_theme
        self.supports_cover = supports_cover
        self.supports_template_mode = supports_template_mode
        self.supports_article_id = supports_article_id
        self.supports_cover_mode = supports_cover_mode
        self.supports_ai_declaration_mode = supports_ai_declaration_mode
        self.supports_scheduled_publish_at = supports_scheduled_publish_at

    @property
    def script_path(self):
        return self.base_dir / self.script_name

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
        command = [sys.executable, str(self.script_path), str(markdown_file), "--mode", mode]
        if self.supports_theme and theme_name:
            command.extend(["--theme", theme_name])
        cover_value = str(cover_path) if cover_path else None
        if self.supports_cover and cover_value:
            command.extend(["--cover", cover_value])
        if self.supports_template_mode and template_mode:
            command.extend(["--template-mode", str(template_mode)])
        if self.supports_article_id and article_id:
            command.extend(["--article-id", str(article_id)])
        if self.supports_cover_mode and cover_mode:
            command.extend(["--cover-mode", str(cover_mode)])
        if self.supports_ai_declaration_mode and ai_declaration_mode:
            command.extend(["--ai-declaration-mode", str(ai_declaration_mode)])
        if self.supports_scheduled_publish_at and scheduled_publish_at:
            command.extend(["--scheduled-publish-at", str(scheduled_publish_at)])
        return {
            "platform": self.platform,
            "command": command,
            "mode": mode,
            "theme_name": theme_name,
            "cover_path": cover_value,
            "template_mode": template_mode,
            "article_id": article_id,
            "cover_mode": cover_mode,
            "ai_declaration_mode": ai_declaration_mode,
            "scheduled_publish_at": scheduled_publish_at,
        }

    def publish(self, prepared_context):
        timeout_seconds = 180
        command = prepared_context["command"]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.base_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or exc.output or "").strip()
            stderr = (exc.stderr or "").strip()
            timeout_message = f"Process timed out after {timeout_seconds} seconds"
            if stderr:
                stderr = f"{timeout_message}\n{stderr}"
            else:
                stderr = timeout_message
            return {
                "platform": self.platform,
                "command": " ".join(command),
                "returncode": 124,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
            }
        stdout, stdout_state = _extract_smoke_state(result.stdout.strip())
        stderr, stderr_state = _extract_smoke_state(result.stderr.strip())
        smoke_state = {}
        if stdout_state:
            smoke_state.update(stdout_state)
        if stderr_state:
            smoke_state.update(stderr_state)
        return {
            "platform": self.platform,
            "command": " ".join(command),
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "current_url": str(smoke_state.get("current_url", "")),
            "page_state": str(smoke_state.get("page_state", "")),
            "smoke_step": str(smoke_state.get("smoke_step", "")),
        }

    def verify(self, process_result, mode):
        return classify_process_result(self.platform, mode, process_result)

    def collect_result(self, process_result, mode):
        status = self.verify(process_result, mode)
        error_type = infer_error_type(status, process_result)
        summary = process_result.get("stderr") or process_result.get("stdout") or status
        return ExecutionResult(
            platform=self.platform,
            stage="publish",
            status=status,
            error_type=error_type,
            summary=summary,
            stdout=process_result.get("stdout", ""),
            stderr=process_result.get("stderr", ""),
            current_url=process_result.get("current_url", ""),
            page_state=process_result.get("page_state", ""),
            smoke_step=process_result.get("smoke_step", ""),
            retryable=is_retryable_error(error_type),
        )
