from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path
from typing import Optional

from ordo_engine.platforms.base import BasePlatformAdapter, classify_process_result, infer_error_type
from ordo_engine.platforms.base import is_terminal_outcome
from ordo_engine.platforms.playwright.engine import BrowserCleanupError
from ordo_engine.results.errors import is_retryable_error
from ordo_engine.results.record import ExecutionResult


class PlaywrightPlatformAdapter(BasePlatformAdapter):
    """将 Playwright 发布器适配到现有 pipeline 接口

    替代 SubprocessPlatformAdapter：在进程内直接执行 Playwright 发布，
    而非 spawn 子进程。输出格式兼容现有 pipeline 的 process_result dict。
    """

    def __init__(
        self,
        base_dir: Path,
        platform: str,
        publisher_class: type,
        *,
        debug_port: int = 9333,
        engine_mode: str = "standalone",
        headless: Optional[bool] = None,
        supports_cover: bool = True,
        supports_article_id: bool = True,
        supports_cover_mode: bool = True,
        supports_ai_declaration_mode: bool = True,
        supports_scheduled_publish_at: bool = False,
    ):
        super().__init__(base_dir=base_dir, platform=platform)
        self.publisher_class = publisher_class
        self.debug_port = debug_port
        self.engine_mode = engine_mode
        self.headless = headless
        self.supports_cover = supports_cover
        self.supports_article_id = supports_article_id
        self.supports_cover_mode = supports_cover_mode
        self.supports_ai_declaration_mode = supports_ai_declaration_mode
        self.supports_scheduled_publish_at = supports_scheduled_publish_at

        # 共享引擎（由 pipeline 在「单 context 多 tab」模式下注入）
        self._shared_engine = None

    def set_shared_engine(self, engine):
        """注入共享引擎；设为 None 恢复每平台独立启动。"""
        self._shared_engine = engine

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
            "theme_name": theme_name,
            "cover_path": str(cover_path) if cover_path else None,
            "template_mode": template_mode,
            "article_id": article_id,
            "cover_mode": cover_mode,
            "ai_declaration_mode": ai_declaration_mode,
            "scheduled_publish_at": scheduled_publish_at,
        }

    def publish(self, prepared_context):
        """在进程内执行 Playwright 发布"""
        from ordo_engine.platforms.playwright.base_publisher import ArticlePayload
        from ordo_engine.platforms.playwright.engine import PlaywrightEngine

        captured_stdout = io.StringIO()
        old_stdout = sys.stdout

        # 共享引擎模式：复用 pipeline 创建的单个 context，不自行启停
        shared = self._shared_engine is not None
        engine = self._shared_engine if shared else None
        publisher = None

        try:
            article = self._load_article(prepared_context)

            if engine is None:
                engine = PlaywrightEngine(
                    mode=self.engine_mode,
                    debug_port=self.debug_port,
                    base_dir=self.base_dir,
                    headless=self.headless,
                )
                engine.connect()

            # Tee stdout: both capture and display
            sys.stdout = _TeeWriter(old_stdout, captured_stdout)

            try:
                publisher = self.publisher_class(engine)
                result = publisher.publish(article, prepared_context["mode"])
            finally:
                cleanup_errors = []
                leased_page = None
                try:
                    leased_page = engine.release_page_for_platform(self.platform)
                except Exception as exc:
                    cleanup_errors.append(exc)
                page = getattr(publisher, "page", None)
                if page is not None and page is not leased_page:
                    try:
                        page.close()
                    except Exception as exc:
                        cleanup_errors.append(exc)
                # 仅当引擎为自身创建时才关闭；共享引擎由 pipeline 统一关闭
                if not shared:
                    try:
                        engine.close()
                    except Exception as exc:
                        cleanup_errors.append(exc)
                if cleanup_errors:
                    raise BrowserCleanupError(
                        f"浏览器清理失败: {cleanup_errors[0]}"
                    ) from cleanup_errors[0]

            sys.stdout = old_stdout
            stdout_text = captured_stdout.getvalue()

            return {
                "platform": self.platform,
                "command": f"playwright:{self.platform}",
                "returncode": 0 if is_terminal_outcome(
                    result.status, prepared_context["mode"]
                ) else 1,
                "outcome_status": result.status,
                "stdout": stdout_text,
                "stderr": result.error or "",
                "current_url": result.current_url,
                "page_state": result.page_state,
                "smoke_step": result.smoke_step,
            }

        except BrowserCleanupError:
            raise
        except Exception as exc:
            sys.stdout = old_stdout
            tb = traceback.format_exc()
            stdout_text = captured_stdout.getvalue()
            return {
                "platform": self.platform,
                "command": f"playwright:{self.platform}",
                "returncode": 1,
                "stdout": stdout_text,
                "stderr": f"{exc}\n{tb}",
                "current_url": "",
                "page_state": "error",
                "smoke_step": "error",
            }

    def verify(self, process_result, mode):
        outcome_status = process_result.get("outcome_status")
        if outcome_status in {
            "published", "scheduled", "draft_only", "draft_saved", "skipped_existing",
        } and not is_terminal_outcome(outcome_status, mode):
            return "failed"
        if outcome_status in {
            "published", "scheduled", "draft_only", "draft_saved", "skipped_existing",
            "limit_reached", "submitted_unverified", "unknown", "failed",
        }:
            return outcome_status
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

    def _content_variants_enabled(self) -> bool:
        """是否对正文做按平台差异化（默认开启，可在 config.json 关闭）"""
        try:
            from ordo_engine.config import load_json_config
            cfg, _ = load_json_config(self.base_dir)
            return bool(cfg.get("content_variants", True))
        except Exception:
            return True

    def _load_article(self, ctx: dict):
        """从 prepared_context 构建 ArticlePayload"""
        from ordo_engine.platforms.playwright.base_publisher import ArticlePayload
        from ordo_engine.platforms.playwright.content_variants import generate_variant
        from ordo_engine.importers.normalize import strip_title_marker

        markdown_path = Path(ctx["markdown_file"])
        raw_text = markdown_path.read_text(encoding="utf-8")

        title = strip_title_marker(markdown_path.stem)
        body = raw_text

        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") or (
                stripped.startswith("## ") and not stripped.startswith("###")
            ):
                title = strip_title_marker(stripped.lstrip("#").strip())
                body = raw_text.replace(line, "", 1).lstrip()
                break

        title = title[:100]

        # 按平台生成内容变体（降低全网判重/降权风险）
        if self._content_variants_enabled():
            title, body = generate_variant(self.platform, title, body)

        cover_path = Path(ctx["cover_path"]) if ctx.get("cover_path") else None

        return ArticlePayload(
            title=title,
            body=body,
            markdown_path=markdown_path,
            cover_path=cover_path,
            article_id=ctx.get("article_id"),
            cover_mode=ctx.get("cover_mode"),
            ai_declaration_mode=ctx.get("ai_declaration_mode"),
            scheduled_publish_at=ctx.get("scheduled_publish_at"),
        )


class _TeeWriter:
    """同时写入两个流"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()

    def __getattr__(self, name):
        return getattr(self.streams[0], name)
