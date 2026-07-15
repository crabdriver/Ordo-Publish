from __future__ import annotations

"""头条号文章编辑器页面定位器集合"""


class ToutiaoLocators:
    EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
    MANAGEMENT_URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
    DRAFT_MANAGEMENT_URL = "https://mp.toutiao.com/profile_v4/manage/draft"
    PUBLISHED_URL_PATTERN = r"^https?://(?:www\.)?toutiao\.com/article/\d+(?:[/?#]|$)"

    TITLE_INPUT = (
        'textarea[placeholder="请输入文章标题（2～30个字）"], '
        'textarea[placeholder*="标题"], '
        'textarea[aria-label*="标题"], '
        'textarea.semi-input-textarea'
    )
    EDITOR_AREA = (
        '.ProseMirror, '
        '[contenteditable="true"][role="textbox"], '
        '.editor-shell [contenteditable="true"], '
        '[contenteditable="true"]'
    )
    EDITOR_AREA_MIN_WIDTH = 300
    EDITOR_AREA_MIN_HEIGHT = 100

    COVER_FILE_INPUT = ".btn-upload-handle input[type=file], #upload-drag-input"

    PUBLISH_BUTTON_TEXTS = ["预览并发布", "立即发布", "发表"]
    CONFIRM_PUBLISH_TEXTS = ["确定并发布", "确认发布"]
    CONFIRM_DIALOG_SELECTOR = (
        '[role="dialog"]:visible, .byte-modal-wrapper:visible'
    )
    SUBMIT_FAILURE_MARKERS = ["保存失败", "提交失败", "发布失败"]
    SAVE_DRAFT_TEXTS = ["存草稿", "保存草稿"]

    AI_CHECKBOX_LABEL = "引用AI"
    AI_CHECKBOX_CONTAINER = ".pgc-edit-cell"

    PUBLISH_SUCCESS_MARKERS = ["发布成功", "已发布"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "草稿", "存草稿成功"]
    LIMIT_MARKERS = ["达到发布上限", "发布上限", "请明天再来", "次数上限"]
