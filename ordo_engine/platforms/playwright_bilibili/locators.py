from __future__ import annotations

"""B站专栏文章编辑器页面定位器集合


注意：B站 专栏编辑器主体（标题/正文/按钮）全部位于 iframe 内，
iframe src 含 'read-editor'。所有操作需通过 frame_locator 或 frame 对象进行。
"""


class BilibiliLocators:
    EDITOR_URL = "https://member.bilibili.com/platform/upload/text/new-edit"
    MANAGEMENT_URL = "https://member.bilibili.com/platform/upload/text/manage"
    DRAFT_MANAGEMENT_URL = "https://member.bilibili.com/platform/upload/text/manage?tab=draft"
    PUBLISHED_URL_PATTERN = r"member\.bilibili\.com/(?:platform/upload/text/(?:manage|new-edit)|opus)"

    # 标题框在 iframe 内：<textarea class="title-input__inner" placeholder="请输入标题（建议30字以内）">
    TITLE_INPUT = (
        'textarea.title-input__inner, '
        'textarea[placeholder*="请输入标题"]'
    )
    # 正文框是 Tiptap/ProseMirror 富文本编辑器（在 iframe 内）
    EDITOR_AREA = (
        'div.tiptap.ProseMirror, '
        '[role="textbox"][contenteditable="true"], '
        '.eva3-editor'
    )
    EDITOR_AREA_MIN_WIDTH = 300
    EDITOR_AREA_MIN_HEIGHT = 100

    # 封面上传（在 iframe 内）
    COVER_FILE_INPUT = 'input[type="file"][accept*="image"]'

    PUBLISH_BUTTON_TEXTS = ["发布"]
    PUBLISH_BUTTON_CLASS = "vui_button--blue"
    CONFIRM_PUBLISH_TEXTS = ["确认发布", "确定"]
    SAVE_DRAFT_TEXTS = ["保存为草稿"]
    # 注意："保存为草稿" 不是 "保存草稿"

    PUBLISH_SETTINGS_TEXT = "发布设置"

    PUBLISH_SUCCESS_MARKERS = ["发布成功", "已发布"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "保存成功", "草稿"]
    LIMIT_MARKERS = ["达到发布上限", "发布上限", "频率限制"]

    # iframe 匹配规则
    IFRAME_SRC_PATTERN = "read-editor"
