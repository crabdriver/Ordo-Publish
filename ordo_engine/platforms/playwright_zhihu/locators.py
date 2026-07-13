from __future__ import annotations

"""知乎文章编辑器页面定位器集合

集中管理所有 CSS selector / text matcher，避免硬编码在业务逻辑中。
当知乎改版时只需修改此文件。
"""


class ZhihuLocators:
    # ── URLs ────────────────────────────────────────────────
    EDITOR_URL = "https://zhuanlan.zhihu.com/write"
    MANAGEMENT_URL = "https://www.zhihu.com/creator/manage/creation/all?type=article"
    DRAFT_MANAGEMENT_URL = (
        "https://www.zhihu.com/creator/manage/creation/draft?type=article"
    )
    PUBLISHED_URL_PATTERN = r"^https?://zhuanlan\.zhihu\.com/p/\d+(?:[/?#]|$)"

    # ── 编辑器元素 ──────────────────────────────────────────
    TITLE_INPUT = 'textarea[placeholder*="标题"], input[placeholder*="标题"]'
    EDITOR_AREA = (
        ".public-DraftEditor-content, "
        ".ProseMirror, "
        '[data-lexical-editor="true"], '
        '[contenteditable="true"]'
    )
    EDITOR_AREA_MIN_WIDTH = 300
    EDITOR_AREA_MIN_HEIGHT = 10

    # ── 封面 ────────────────────────────────────────────────
    COVER_FILE_INPUT = "input.UploadPicture-input"

    # ── 发布按钮 ────────────────────────────────────────────
    PUBLISH_BUTTON_TEXTS = ["发布", "Publish"]
    CONFIRM_PUBLISH_TEXTS = ["确认发布", "立即发布", "确定"]
    SAVE_DRAFT_TEXTS = ["存草稿", "保存草稿", "Save Draft"]

    # ── AI 创作声明 ─────────────────────────────────────────
    AI_DECLARATION_LABEL = "创作声明"
    AI_DECLARATION_OPTION_TEXT = "包含 AI 辅助创作"
    AI_COMBOBOX_ROLE = "combobox"
    AI_OPTION_ROLE = "option"

    # ── 验证 ────────────────────────────────────────────────
    PUBLISH_SUCCESS_MARKERS = ["已发布", "发布成功"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "草稿", "存草稿成功"]
    LIMIT_MARKERS = ["达到发布上限", "发布上限", "次数上限"]
