from __future__ import annotations

"""一点号文章编辑器页面定位器集合"""


class YidianLocators:
    EDITOR_URL = "https://mp.yidianzixun.com/#/Writing/articleEditor"
    MANAGEMENT_URL = "https://mp.yidianzixun.com/#/ArticleManual/original/article"
    DRAFT_MANAGEMENT_URL = "https://mp.yidianzixun.com/#/ArticleManual/original/draft"
    PUBLISHED_URL_PATTERN = r"^https?://(?:www\.)?yidianzixun\.com/article/[A-Za-z0-9_-]+(?:[/?#]|$)"

    TITLE_INPUT = (
        'input.post-title, '
        'input[placeholder*="标题"], '
        'input[placeholder*="标题"]'
    )
    EDITOR_AREA = (
        ".editor-content[contenteditable='true'], "
        "[contenteditable='true']"
    )
    EDITOR_AREA_MIN_WIDTH = 300
    EDITOR_AREA_MIN_HEIGHT = 100

    COVER_FILE_INPUT = "input.upload-input"
    COVER_SINGLE_TEXT = "单图"

    PUBLISH_BUTTON_TEXTS = ["发文章", "发布"]
    CONFIRM_PUBLISH_TEXTS = ["确认发布", "确定", "发布"]
    SAVE_DRAFT_TEXTS = ["存草稿", "保存草稿", "保存"]

    AI_DECLARATION_TEXT = "内容由AI生成"
    PERSONAL_OPINION_TEXT = "个人观点，仅供参考"

    PUBLISH_SUCCESS_MARKERS = ["发布成功", "已发布", "审核中"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "保存成功", "草稿"]
    LIMIT_MARKERS = ["达到发布上限", "发布上限", "请明天再来"]
