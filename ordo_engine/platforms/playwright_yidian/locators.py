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

    COVER_PICKER = ".article-cover-container .cover-setter:not([style*='display: none']) .cover-item"
    COVER_SINGLE_TEXT = "单图"
    COVER_DEFAULT_SELECTOR = '.cover-type .item:has-text("默认")'

    PUBLISH_BUTTON_TEXTS = ["发文章", "发布"]
    PUBLISH_BUTTON_CLASS = "mp-btn-primary"  # 区分真正的发布按钮（vs 存草稿/定时发布）
    CONFIRM_PUBLISH_TEXTS = ["确认", "确认发布"]
    CONFIRM_DIALOG_SELECTOR = (
        '[role="dialog"]:visible, .el-dialog__wrapper:visible, '
        '.el-message-box__wrapper:visible, .mp-dialog:visible'
    )
    SUBMIT_FAILURE_MARKERS = ["发布失败", "提交失败", "保存失败"]
    SAVE_DRAFT_TEXTS = ["存草稿", "保存草稿", "保存"]

    AI_DECLARATION_TEXT = "内容由AI生成"
    PERSONAL_OPINION_TEXT = "个人观点，仅供参考"

    # 内容声明（一点号发布必填项，缺失时发布点击被静默拦截）
    CONTENT_STATEMENT_CONTAINER = ".content-statement-content"
    CONTENT_STATEMENT_ITEM = ".content-statement-content .item"
    CONTENT_STATEMENT_AI_TEXT = "内容由AI生成"
    CONTENT_STATEMENT_NONE_TEXT = "无需声明"
    CONTENT_STATEMENT_OPINION_TEXT = "个人观点，仅供参考"

    PUBLISH_SUCCESS_MARKERS = ["发布成功", "已发布", "审核中", "提交成功"]
    DRAFT_SUCCESS_MARKERS = ["已保存", "保存成功", "草稿"]
    LIMIT_MARKERS = ["达到发布上限", "发布上限", "请明天再来"]
    # 发布前必填校验提示
    REQUIRED_FIELD_MARKERS = ["请选择内容声明", "请填写", "必填", "不能为空"]
