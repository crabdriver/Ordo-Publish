from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page
except ImportError:
    from playwright.sync_api import Page

from markdown_utils import should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload, DraftCheckpoint, PlaywrightBasePublisher, PublishResult,
)
from ordo_engine.platforms.playwright._common import (
    fill_title_common, fill_body_common,
    click_publish_with_evidence, save_draft_common, verify_result_common,
)
from ordo_engine.platforms.playwright_yidian.locators import YidianLocators


class YidianPlaywrightPublisher(PlaywrightBasePublisher):
    """一点号文章 Playwright 人像化发布器"""

    platform = "yidian"

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("yidian")
        if "articleEditor" not in (page.url or ""):
            page.goto(YidianLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
        self._wait_for_login_if_needed(page, "articleEditor", YidianLocators.TITLE_INPUT, "一点号", YidianLocators.EDITOR_URL)
        print(f"[INFO] 一点号编辑器已就绪: {page.url}")
        return page

    def fill_title(self, title: str):
        fill_title_common(self.human, self.page, title, YidianLocators.TITLE_INPUT, "一点号")

    def fill_body(self, body: str):
        fill_body_common(
            self.human, self.page, body,
            YidianLocators.EDITOR_AREA, "一点号",
            YidianLocators.EDITOR_AREA_MIN_WIDTH, YidianLocators.EDITOR_AREA_MIN_HEIGHT,
        )

    def upload_cover(self, cover_path: Path):
        # 一点号需要先点击"单图"选择封面模式
        try:
            single_cover_btn = self.page.locator(f'text="{YidianLocators.COVER_SINGLE_TEXT}"')
            if single_cover_btn.count() > 0:
                self.human.human_click(single_cover_btn.first)
                time.sleep(1)
        except Exception:
            pass

        picker = self.page.locator(YidianLocators.COVER_PICKER)
        if picker.count() == 0:
            raise RuntimeError("未找到一点号单图封面上传入口")
        try:
            with self.page.expect_file_chooser(timeout=10000) as chooser_info:
                self.human.human_click(picker.first)
            chooser_info.value.set_files(str(Path(cover_path).expanduser().resolve()))
        except Exception as exc:
            raise RuntimeError(
                "一点号账号未提供本地封面上传入口：该账号只能从正文已有图片选封面，"
                "请开通站外图片上传权限或在正文加入可用图片"
            ) from exc
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        if not getattr(article, "cover_path", None) or getattr(article, "cover_mode", None) == "force_off":
            self._select_default_cover()
        # 内容声明是发布必填项，无论 AI 与否都必须选一个（互斥单选，选完即止）
        self._set_content_declaration(article)
        # 选择第一个可用分类（一点号必填，缺失会导致发布/存草稿按钮无效）
        self._select_first_category()

    def _select_first_category(self):
        """一点号发布前必须选分类。自动选第一个可用分类。"""
        # 先找分类/频道下拉框的触发元素
        selectors = [
            '.el-select.category-select input',
            '.el-select input[placeholder*="分类"]',
            '.el-select input[placeholder*="频道"]',
            'input[placeholder*="分类"]',
            'input[placeholder*="请选择"]',
        ]
        trigger = None
        for sel in selectors:
            loc = self.page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                trigger = loc
                break

        if not trigger:
            # 尝试通过 label 文本定位
            label_sel = self.page.locator('label:has-text("分类"), label:has-text("频道"), .el-form-item__label:has-text("分类")')
            if label_sel.count() > 0:
                # 找到 label 旁边的 input/select
                parent = label_sel.first.locator('xpath=..')
                trigger = parent.locator('input, .el-select').first

        if not trigger:
            print("[WARN] 一点号未找到分类选择器，将尝试跳过")
            return

        try:
            self.human.human_click(trigger)
            time.sleep(1)

            # 在下拉列表中选第一个选项
            option = self.page.locator(
                '.el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled):first-child, '
                '.el-popper:visible .el-select-dropdown__item:first-child'
            ).first
            if option.count() > 0 and option.is_visible():
                self.human.human_click(option)
                print(f"[INFO] 一点号已选择分类: {(option.inner_text() or '')[ :40]}")
                time.sleep(0.5)
            else:
                print("[WARN] 一点号分类下拉未展开或无可用选项")
        except Exception as exc:
            print(f"[WARN] 一点号分类选择失败: {exc}")

    def _select_default_cover(self):
        default = self.page.locator(YidianLocators.COVER_DEFAULT_SELECTOR).first
        if default.count() == 0:
            raise RuntimeError("未找到一点号默认封面选项")
        if "checked" not in (default.get_attribute("class") or "").split():
            # 该 Vue 单选框的可点击区域与视觉框不完全重合，坐标点击会静默失败。
            default.click(force=True)
            time.sleep(0.5)
        if "checked" not in (default.get_attribute("class") or "").split():
            raise RuntimeError("一点号默认封面未选中")
        print("[INFO] 一点号已选择平台默认封面")

    def _set_content_declaration(self, article: ArticlePayload):
        """一点号发布前必填：内容声明。
        AI 内容选「内容由AI生成」，否则选「无需声明」。
        注意：可点击元素是 .item div，不是内层 span；旧代码用 text= 匹配到 span 导致声明从未选中。
        """
        need_ai = should_declare_ai(article.title, article.body, article.ai_declaration_mode or "auto")
        target_text = YidianLocators.CONTENT_STATEMENT_AI_TEXT if need_ai else YidianLocators.CONTENT_STATEMENT_NONE_TEXT
        print(f"[INFO] 设置一点号内容声明: {target_text} (need_ai={need_ai})")
        try:
            item = self.page.locator(
                f'{YidianLocators.CONTENT_STATEMENT_ITEM}:has-text("{target_text}")'
            ).first
            if item.count() == 0 or not item.is_visible():
                print(f"[WARN] 未找到内容声明选项: {target_text}")
                return
            # 已选中则跳过
            if "checked" in (item.get_attribute("class") or "").split():
                print(f"[INFO] 内容声明已为: {target_text}")
                return
            self.human.human_click(item)
            time.sleep(1)
            # 校验是否真的选中
            if "checked" in (item.get_attribute("class") or "").split():
                print(f"[INFO] 内容声明已设置: {target_text}")
            else:
                print(f"[WARN] 内容声明点击后未生效: {target_text}")
        except Exception as exc:
            print(f"[WARN] 设置一点号内容声明失败: {exc}")

    def _set_ai_declaration(self):
        """向后兼容：保留旧方法名，实际委托给内容声明选择（AI 模式）"""
        print("[INFO] 开始设置一点号 AI 声明...")
        try:
            item = self.page.locator(
                f'{YidianLocators.CONTENT_STATEMENT_ITEM}:has-text("{YidianLocators.CONTENT_STATEMENT_AI_TEXT}")'
            ).first
            if item.count() > 0 and item.is_visible():
                if "checked" not in (item.get_attribute("class") or "").split():
                    self.human.human_click(item)
                print("[INFO] 一点号 AI 声明已设置")
            else:
                print("[WARN] 未找到一点号 AI 声明选项")
        except Exception as exc:
            print(f"[WARN] 设置一点号 AI 声明失败: {exc}")

    def _set_personal_opinion(self):
        try:
            opinion_label = self.page.locator(f'text="{YidianLocators.PERSONAL_OPINION_TEXT}"')
            if opinion_label.count() > 0:
                self.human.human_click(opinion_label.first)
                time.sleep(0.5)
        except Exception:
            pass

    def click_publish(self):
        """一点号发布：mp-btn-primary 按钮直接提交无确认弹窗，等 URL 变化"""
        from ordo_engine.platforms.playwright._common import (
            find_visible_button, _feedback_text, _is_interactive,
            _raise_if_submit_failed, _locator_diagnostics,
        )
        from ordo_engine.platforms.playwright.base_publisher import PublishClickNoEffect

        publish_btn = find_visible_button(
            self.page,
            YidianLocators.PUBLISH_BUTTON_TEXTS,
            button_class=YidianLocators.PUBLISH_BUTTON_CLASS,
        )
        if not publish_btn or not _is_interactive(publish_btn):
            raise PublishClickNoEffect("一点号发布按钮不可交互")

        pre_url = self.page.url or ""
        pre_feedback = _feedback_text(self.page)
        print("[INFO] 点击一点号发布按钮...")
        try:
            publish_btn.click()
        except Exception as exc:
            raise PublishClickNoEffect(f"一点号发布按钮不可点击: {exc}") from exc

        # 等 URL 变化 或 页面文字变化（发布成功后跳转到文章管理页）
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current_url = self.page.url or ""
            current_feedback = _feedback_text(self.page)
            _raise_if_submit_failed(current_feedback, YidianLocators.SUBMIT_FAILURE_MARKERS, "一点号")

            # 检测必填项未填提示（如"请选择内容声明"）——说明声明没选上
            for marker in YidianLocators.REQUIRED_FIELD_MARKERS:
                if marker in (current_feedback or ""):
                    raise PublishClickNoEffect(
                        f"一点号发布被拦截：页面提示「{marker}」，内容声明可能未正确选择"
                    )

            # 检测发布成功信号
            if current_url != pre_url:
                print(f"[INFO] 一点号页面已跳转: {current_url}")
                return
            for marker in YidianLocators.PUBLISH_SUCCESS_MARKERS:
                if marker in (current_feedback or ""):
                    print(f"[INFO] 一点号发布成功: 页面显示「{marker}」")
                    return

            # 检测确认弹窗「你确定要发布吗？」→ 点击「确定」
            try:
                confirm_btn = self.page.locator(
                    'button:has-text("确定"), button:has-text("确认"), '
                    '.mp-dialog:visible button:has-text("确定")'
                ).first
                if confirm_btn.count() > 0 and confirm_btn.is_visible() and confirm_btn.is_enabled():
                    # 确认弹窗出现（非必填提示），点击确定完成发布
                    print("[INFO] 一点号出现确认弹窗「你确定要发布吗？」，点击确定...")
                    confirm_btn.click()
                    time.sleep(3)
                    # 点击确定后等待发布结果
                    post_url = self.page.url or ""
                    if post_url != pre_url:
                        print(f"[INFO] 一点号发布后页面跳转: {post_url}")
                        return
                    for marker in YidianLocators.PUBLISH_SUCCESS_MARKERS:
                        if marker in (_feedback_text(self.page) or ""):
                            print(f"[INFO] 一点号发布成功: {marker}")
                            return
                    # 确定已点，给后端一点时间
                    time.sleep(2)
                    return
            except Exception:
                pass

            time.sleep(0.5)

        raise PublishClickNoEffect(
            f"一点号发布按钮点击后无变化; diagnostics: {_locator_diagnostics(self.page, publish_btn)}"
        )

    def save_draft(self):
        save_draft_common(self.human, self.page, YidianLocators.SAVE_DRAFT_TEXTS, "一点号")

    def verify_result(self, mode: str) -> PublishResult:
        """一点号验证：已发布文章在「已发布」tab（需点击切换，URL 直跳默认不在该 tab）。
        先点 tab 搜索标题并提取文章 URL，找不到再回退通用验证。"""
        from datetime import datetime, timezone
        title = getattr(self._article, "title", "")
        published_url = ""
        try:
            self.page.goto(YidianLocators.MANAGEMENT_URL, wait_until="domcontentloaded", timeout=15000)
            self.human.human_wait(1, 2)
            for tab_text in ["已发布", "审核中"]:
                try:
                    tab = self.page.locator(f'.mp-tab-nav-item:has-text("{tab_text}")').first
                    if tab.count() > 0 and tab.is_visible():
                        tab.click()
                        time.sleep(2)
                except Exception:
                    pass
                if title:
                    res = self.page.evaluate(
                        """(t) => {
                            if (!(document.body.innerText || '').includes(t)) return null;
                            const links = Array.from(document.querySelectorAll('a[href*="article/"]'));
                            for (const l of links) {
                                if ((l.innerText || '').includes(t.substring(0, 8))) return l.href;
                            }
                            return 'found_no_link';
                        }""",
                        title,
                    )
                    if res:
                        published_url = res if res.startswith("http") else (self.page.url or "")
                        break
        except Exception as exc:
            print(f"[WARN] 一点号已发布列表核验异常: {exc}")

        if published_url:
            return PublishResult(
                platform=self.platform,
                status="published",
                current_url=published_url,
                page_state="published",
                smoke_step="verify",
                message=f"一点号已发布: {published_url}",
            )

        # 回退通用验证（处理 draft / 限额等）
        return verify_result_common(
            self.page, "一点号", mode,
            YidianLocators.PUBLISHED_URL_PATTERN,
            YidianLocators.PUBLISH_SUCCESS_MARKERS,
            YidianLocators.DRAFT_SUCCESS_MARKERS,
            YidianLocators.LIMIT_MARKERS,
            YidianLocators.MANAGEMENT_URL,
            YidianLocators.DRAFT_MANAGEMENT_URL,
            expected_title=title,
        )

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        """核验一点号草稿。一点号封面限制较多，draft_ref 可能为空 → 调用方应处理为 blocked_no_draft 或 manual_verify。"""
        from datetime import datetime, timezone
        try:
            self.page.goto(YidianLocators.DRAFT_MANAGEMENT_URL or YidianLocators.MANAGEMENT_URL,
                           wait_until="domcontentloaded", timeout=15000)
            self.human.human_wait(1, 2)
            title = getattr(self._article, "title", "")
            draft_ref = ""
            if title:
                try:
                    el = self.page.locator(f'text="{title}"').first
                    if el.count() > 0:
                        draft_ref = self.page.url or ""
                except Exception:
                    pass
            return DraftCheckpoint(
                platform=self.platform, draft_ref=draft_ref,
                saved_at=datetime.now(timezone.utc).isoformat(),
                verification_evidence={"method": "draft_list_title_match",
                                       "title_matched": bool(draft_ref)})
        except Exception as exc:
            return DraftCheckpoint(
                platform=self.platform, draft_ref="",
                verification_evidence={"method": "draft_list_error", "error": str(exc)})

    def publish_from_draft(self, draft_ref: str) -> PublishResult:
        if draft_ref:
            self.page.goto(draft_ref, wait_until="domcontentloaded", timeout=15000)
        self._submission_started = True
        self.click_publish()
        return self.verify_result("publish")

    def verify_published(self, published_ref: str) -> bool:
        try:
            self.page.goto(published_ref, wait_until="domcontentloaded", timeout=10000)
            return self.page.title() != "" and "404" not in self.page.title()
        except Exception:
            return False
