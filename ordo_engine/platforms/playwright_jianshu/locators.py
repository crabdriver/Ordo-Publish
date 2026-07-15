from __future__ import annotations

"""简书文章编辑器页面定位器集合"""


class JianshuLocators:
    EDITOR_URL = "https://www.jianshu.com/writer#/"
    # 写作页同时展示草稿和已发布文章，不能作为正式发布证据。
    MANAGEMENT_URL = None
    DRAFT_MANAGEMENT_URL = "https://www.jianshu.com/writer#/notebooks"
    PUBLISHED_URL_PATTERN = r"^https?://(?:www\.)?jianshu\.com/p/[A-Za-z0-9_-]+(?:[/?#]|$)"

    TITLE_INPUT = (
        'input._24i7u, '
        'input:not([placeholder]):not([name]), '
        'input[placeholder*="标题"], '
        'input.title-input'
    )
    EDITOR_AREA = (
        'textarea#arthur-editor, '
        '.public-DraftEditor-content, '
        '.ProseMirror, '
        '#textarea, '
        '[contenteditable="true"]'
    )
    EDITOR_AREA_MIN_WIDTH = 300
    EDITOR_AREA_MIN_HEIGHT = 100

    COVER_FILE_INPUT = 'input[type="file"][accept*="image"]'
    # writer 页的哈希 class submit 属于“新建文集”，不是文章发布按钮。
    PUBLISH_BUTTON_SELECTOR = "button.dwU8Q._3zXcJ._3QfkW:visible"

    PUBLISH_BUTTON_TEXTS = ["发布文章"]
    CONFIRM_PUBLISH_TEXTS = ["直接发布", "确定发布", "确认发布"]
    SAVE_DRAFT_TEXTS = ["保存", "存草稿"]

    PUBLISH_SUCCESS_MARKERS = ["发布成功，点击查看文章", "发布成功", "已发布"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "保存成功", "草稿"]
    LIMIT_MARKERS = ["每天只能发布 2 篇公开文章", "达到发布上限", "请明天再来"]
