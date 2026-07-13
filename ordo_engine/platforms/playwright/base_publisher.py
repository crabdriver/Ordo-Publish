from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    from patchright.sync_api import Page
except ImportError:
    from playwright.sync_api import Page

from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.run_state import article_key, get_record, mark_done, record_step, state_file_for


SMOKE_STATE_PREFIX = "[SMOKE_STATE] "


class PublishState(str, Enum):
    INIT = "init"
    EDITOR_READY = "editor_ready"
    TITLE_FILLED = "title_filled"
    BODY_FILLED = "body_filled"
    COVER_UPLOADED = "cover_uploaded"
    SETTINGS_CONFIGURED = "settings_configured"
    SUBMIT_STARTED = "submit_started"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    ERROR = "error"


@dataclass
class ArticlePayload:
    """发布任务的文章数据"""

    title: str
    body: str  # 纯文本或 HTML，取决于平台
    markdown_path: Path
    cover_path: Optional[Path] = None
    article_id: Optional[str] = None
    cover_mode: Optional[str] = None
    ai_declaration_mode: Optional[str] = None
    scheduled_publish_at: Optional[str] = None


@dataclass
class PublishResult:
    """发布结果"""

    platform: str
    status: str  # published / draft_only / failed / limit_reached / scheduled
    page_state: str = ""
    current_url: str = ""
    smoke_step: str = ""
    message: str = ""
    error: Optional[str] = None
    screenshots: list = field(default_factory=list)


def is_terminal_outcome(status: str, mode: str) -> bool:
    if status == "skipped_existing":
        return True
    if mode == "publish":
        return status in {"published", "scheduled"}
    return status in {"draft_only", "draft_saved"}


class PlaywrightBasePublisher(ABC):
    """所有 Playwright 平台发布器的抽象基类

    定义标准发布流水线（状态机模式）：
    INIT → EDITOR_READY → TITLE_FILLED → BODY_FILLED → COVER_UPLOADED
    → SETTINGS_CONFIGURED → SUBMITTED → VERIFIED

    子类覆写各阶段实现。每个阶段完成后自动截图和输出 smoke state。
    """

    platform: str = ""  # 子类设置

    def __init__(self, engine: PlaywrightEngine):
        self.engine = engine
        self.page: Optional[Page] = None
        self.human: Optional[HumanBehavior] = None
        self.state = PublishState.INIT
        self._screenshots: list = []

    def _init_human(self, page: Page) -> HumanBehavior:
        """创建 HumanBehavior 实例，子类可覆写以定制参数"""
        return HumanBehavior(page)

    def _wait_for_login_if_needed(self, page: Page, editor_url_contains: str, title_selector: str, platform: str, editor_url: str = ""):
        """检测是否需要登录，如果需要则等待用户扫码登录

        在 navigate_to_editor 中调用：先导航到编辑器 URL，
        如果页面跳转到登录页（没有标题输入框），则等待用户登录。

        判定规则（覆盖各平台差异）：
        - 标题框出现            → 已登录，编辑器就绪
        - 明确的登录页          → 需要登录
        - 被重定向出编辑器域    → 需要登录（如简书未登录跳首页）
        - 停在编辑器域但无标题框 → 需要登录（如简书 writer#/ 仅加载外壳）
        """
        import time

        # 登录页常见标记（中英文）
        login_markers = ["登录", "扫码", "sign in", "login", "二维码", "手机号", "立即登录", "注册", "请先登录"]

        def _title_ready() -> bool:
            try:
                el = page.locator(title_selector)
                return el.count() > 0 and el.first.is_visible()
            except Exception:
                return False

        def _on_login_page() -> bool:
            url = page.url or ""
            if "sign_in" in url or "login" in url:
                return True
            try:
                txt = page.evaluate("() => document.body?.innerText || ''")
            except Exception:
                txt = ""
            return any(m in txt for m in login_markers)

        if self.engine.headless and not _title_ready() and _on_login_page():
            raise RuntimeError(
                f"{platform} 登录已失效；请运行 publish.py --bootstrap-browser"
            )

        # 等待页面加载/客户端重定向稳定
        time.sleep(3)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # 轮询探测：在 ~20s 内判断「标题框出现=已登录」还是「登录页=需登录」
        needs_login = False
        deadline = time.time() + 20
        while time.time() < deadline:
            if _title_ready():
                return  # 已登录，编辑器已就绪
            if _on_login_page():
                needs_login = True
                break
            time.sleep(2)

        # 轮询未命中时，用最终状态兜底判定
        if not needs_login:
            current_url = page.url or ""
            if editor_url_contains not in current_url:
                # 被重定向出编辑器域（如简书跳首页）→ 需要登录
                needs_login = True
                print(f"[INFO] {platform} 被重定向到非编辑器页 ({current_url})，判定为需要登录")
            elif not _title_ready():
                # 停在编辑器域但标题框缺失 → 未登录（编辑器外壳已加载）
                needs_login = True
                print(f"[INFO] {platform} 停留在编辑器页但标题框未出现，判定为需要登录")

        if needs_login:
            if self.engine.headless:
                raise RuntimeError(
                    f"{platform} 登录已失效；请运行 publish.py --bootstrap-browser"
                )
            print(f"[INFO] 检测到 {platform} 需要登录，请在浏览器窗口中扫码/登录...")
            print(f"[INFO] 等待登录完成（最多 300 秒）")

            # 截图让用户看到二维码
            self.engine.screenshot(page, platform, "login_required")

            # 轮询等待登录完成：标题框出现即视为登录成功（不依赖 URL 是否含编辑器串）
            max_wait = 300  # 5分钟
            for i in range(max_wait // 3):
                time.sleep(3)
                try:
                    if _title_ready():
                        print(f"[INFO] {platform} 登录成功，编辑器已就绪")
                        time.sleep(2)  # 额外等待页面完全加载
                        return
                except Exception:
                    pass
                # 登录遮罩已消失但页面未自动渲染编辑器时，
                # 刷新编辑器让登录态生效（如一点号/B站 登录后停留在遮罩态）。
                # 每 15 秒刷新一次（用户扫码通常 < 15 秒）。
                if editor_url and i > 0 and i % 5 == 0:
                    try:
                        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(3)  # 等 SPA / iframe 加载
                    except Exception:
                        pass
                if i % 5 == 4:
                    print(f"[INFO] 仍在等待 {platform} 登录... ({(i+1)*3}s)")

            raise RuntimeError(f"{platform} 登录超时（300秒），请重试")
        else:
            # 不在登录页但编辑器未就绪，再等一会
            print(f"[INFO] {platform} 页面加载中，等待编辑器就绪...")
            try:
                page.wait_for_selector(title_selector, state="visible", timeout=30000)
            except Exception:
                raise RuntimeError(f"{platform} 编辑器超时未就绪")

    def publish(self, article: ArticlePayload, mode: str) -> PublishResult:
        """标准发布流水线"""
        # 记录文章幂等键，使状态机每一步都持久化到运行状态文件（支撑失败可恢复）
        self._article_key = article_key(article.markdown_path)
        self._mode = mode
        self._article = article
        self._submission_started = False
        try:
            prior = get_record(
                self._article_key,
                self.platform,
                mode,
                state_file=self._state_file,
            )
            prior_step = (prior or {}).get("last_step") or (prior or {}).get("status")
            if prior_step in {"submit_started", "submitted", "submitted_unverified", "unknown"}:
                return self._reconcile(mode)

            # Step 1: Navigate to editor
            self.page = self.navigate_to_editor()
            self.human = self._init_human(self.page)
            self.state = PublishState.EDITOR_READY
            self._emit_state("editor_ready")
            self._take_screenshot("editor_ready")

            # Step 2: Fill title
            self.fill_title(article.title)
            self.state = PublishState.TITLE_FILLED
            self._emit_state("title_filled")
            self._take_screenshot("title_filled")
            self.human.human_wait(0.5, 1.0)

            # Step 3: Fill body
            self.fill_body(article.body)
            self.state = PublishState.BODY_FILLED
            self._emit_state("body_filled")
            self._take_screenshot("body_filled")
            self.human.human_wait(0.5, 1.5)

            # Step 4: Upload cover
            if article.cover_path and article.cover_mode != "force_off":
                self.upload_cover(article.cover_path)
                self.state = PublishState.COVER_UPLOADED
                self._emit_state("cover_uploaded")
                self._take_screenshot("cover_uploaded")
                self.human.human_wait(0.5, 1.0)

            # Step 5: Configure settings
            self.configure_settings(article)
            self.state = PublishState.SETTINGS_CONFIGURED
            self._emit_state("settings_configured")
            self._take_screenshot("settings_configured")

            # Step 6: Submit
            self.state = PublishState.SUBMIT_STARTED
            self._emit_state("submit_started")
            self._submission_started = True
            if mode == "publish":
                self.click_publish()
            else:
                self.save_draft()
            self.state = PublishState.SUBMITTED
            self._emit_state("submitted")
            self._take_screenshot("submitted")
            self.human.human_wait(1.0, 2.0)

            # Step 7: Verify
            result = self.verify_result(mode)
            if not self._is_terminal(result.status, mode) and result.status != "limit_reached":
                result = self._unverified_result(result)
            self.state = PublishState.VERIFIED
            # 结果尚未落盘时保持 submitted 写前状态，避免崩溃后重复提交。
            self._emit_state("verified", page_state=result.page_state, persist=False)
            self._take_screenshot("verified")

            result.screenshots = list(self._screenshots)
            self._persist_result(result, mode)
            return result

        except Exception as exc:
            if self._submission_started:
                result = PublishResult(
                    platform=self.platform,
                    status="submitted_unverified",
                    page_state="submitted_unverified",
                    current_url=self.page.url if self.page else "",
                    smoke_step="verify",
                    message="提交可能已发生，需要人工复核",
                    error=str(exc),
                    screenshots=list(self._screenshots),
                )
                self._emit_state("submitted_unverified", page_state="submitted_unverified", error=str(exc))
                return result
            self.state = PublishState.ERROR
            self._emit_state("error", page_state="error", error=str(exc))
            self._take_screenshot(f"error_{self.state.value}")
            return PublishResult(
                platform=self.platform,
                status="failed",
                page_state="error",
                current_url=self.page.url if self.page else "",
                smoke_step=self.state.value,
                error=str(exc),
                screenshots=list(self._screenshots),
            )

    @property
    def _state_file(self):
        return state_file_for(self.engine.base_dir)

    def _reconcile(self, mode: str) -> PublishResult:
        """危险提交状态只做核验；无法确认时绝不再次提交。"""
        self._submission_started = True
        self.page = self.engine.context.new_page()
        result = self.verify_result(mode)
        if not self._is_terminal(result.status, mode):
            result = self._unverified_result(result, reconciliation=True)
        self._persist_result(result, mode)
        return result

    def _unverified_result(self, result: PublishResult, *, reconciliation: bool = False) -> PublishResult:
        message = "历史提交无法确认，需要人工复核；未再次提交" if reconciliation else "提交结果无法确认，需要人工复核"
        return PublishResult(
            platform=self.platform,
            status="submitted_unverified",
            page_state="submitted_unverified",
            current_url=result.current_url or (self.page.url if self.page else ""),
            smoke_step="verify",
            message=message,
            error=result.error,
            screenshots=list(self._screenshots),
        )

    @staticmethod
    def _is_terminal(status: str, mode: str) -> bool:
        return is_terminal_outcome(status, mode)

    def _persist_result(self, result: PublishResult, mode: str) -> None:
        if self._is_terminal(result.status, mode):
            mark_done(
                self._article_key,
                self.platform,
                result.status,
                mode,
                result.current_url,
                state_file=self._state_file,
            )
            return
        record_step(
            self._article_key,
            self.platform,
            mode,
            result.status,
            state_file=self._state_file,
        )

    def _take_screenshot(self, step: str):
        if self.page and self.engine:
            path = self.engine.screenshot(self.page, self.platform, step)
            if path:
                self._screenshots.append(path)

    def _emit_state(self, smoke_step: str, *, page_state: str = "", error: str = "", persist: bool = True):
        """输出 smoke state JSON 到 stdout（兼容旧 pipeline），并持久化步骤到运行状态文件"""
        payload = {
            "current_url": self.page.url if self.page else "",
            "smoke_step": smoke_step,
            "page_state": page_state or self.state.value,
        }
        if error:
            payload["error"] = error
        # 状态机步骤持久化（支撑断点续跑）
        if persist:
            record_step(
                getattr(self, "_article_key", ""),
                self.platform,
                getattr(self, "_mode", "draft"),
                smoke_step,
                state_file=self._state_file,
            )
        print(f"{SMOKE_STATE_PREFIX}{json.dumps(payload, ensure_ascii=False)}")

    # ── 子类必须实现的抽象方法 ─────────────────────────────────

    @abstractmethod
    def navigate_to_editor(self) -> Page:
        """导航到编辑器页面并等待就绪"""
        ...

    @abstractmethod
    def fill_title(self, title: str):
        """填写文章标题"""
        ...

    @abstractmethod
    def fill_body(self, body: str):
        """填写文章正文"""
        ...

    @abstractmethod
    def upload_cover(self, cover_path: Path):
        """上传封面图片"""
        ...

    @abstractmethod
    def configure_settings(self, article: ArticlePayload):
        """配置发布设置（AI声明、分类等）"""
        ...

    @abstractmethod
    def click_publish(self):
        """点击发布按钮"""
        ...

    @abstractmethod
    def save_draft(self):
        """保存草稿"""
        ...

    @abstractmethod
    def verify_result(self, mode: str) -> PublishResult:
        """验证发布/草稿结果"""
        ...
