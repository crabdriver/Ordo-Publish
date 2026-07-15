"""report 模块测试 —— 纯函数，不写文件。"""
import pytest
from ordo_engine.results.report import render_report, snapshot_to_structured


def test_not_executed_not_shown_as_failed():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试文章",
                "article_stage": "pending",
                "platforms": {
                    "zhihu:publish": {"stage": "not_executed", "error_type": "browser_start_failed"},
                },
            }
        }
    }
    text = render_report(snapshot)
    assert "未执行" in text
    assert "失败" not in text
    assert "browser_start_failed" in text


def test_manual_verify_not_shown_as_success():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "pending",
                "platforms": {
                    "zhihu:publish": {"stage": "manual_verify"},
                },
            }
        }
    }
    text = render_report(snapshot)
    assert "需要人工核验" in text
    assert "已正式发布" not in text


def test_limited_after_draft_shows_draft_preserved():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "pending",
                "platforms": {
                    "toutiao:publish": {
                        "stage": "limited_after_draft",
                        "draft_ref": "https://example.test/draft/1",
                    },
                },
            }
        }
    }
    text = render_report(snapshot)
    assert "受发布数量限制" in text
    assert "草稿已保存" in text


def test_rate_limit_without_draft_evidence_does_not_claim_draft_saved():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "pending",
                "platforms": {
                    "bilibili:publish": {
                        "stage": "limited_after_draft",
                        "error_type": "rate_limited",
                    },
                },
            }
        }
    }

    text = render_report(snapshot)

    assert "达到发布数量限制" in text
    assert "草稿未核验" in text
    assert "草稿已保存" not in text


def test_published_shown_correctly():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "completed",
                "platforms": {
                    "zhihu:publish": {
                        "stage": "published",
                        "published_ref": "https://zhuanlan.zhihu.com/p/123",
                    },
                    "wechat:draft": {"stage": "draft_saved"},
                },
            }
        }
    }
    text = render_report(snapshot)
    assert "已正式发布" in text
    assert "已完成" in text
    assert "zhuanlan.zhihu.com/p/123" in text


def test_report_does_not_write_files(tmp_path):
    import os
    files_before = set(os.listdir(tmp_path))
    snapshot = {"articles": {}}
    render_report(snapshot)
    files_after = set(os.listdir(tmp_path))
    assert files_before == files_after


def test_same_snapshot_produces_same_report():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "pending",
                "platforms": {
                    "zhihu:publish": {"stage": "published"},
                    "wechat:draft": {"stage": "draft_saved"},
                },
            }
        }
    }
    r1 = render_report(snapshot)
    r2 = render_report(snapshot)
    assert r1 == r2


def test_needs_review_article_shown():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "变更文章",
                "article_stage": "needs_review",
                "article_block_reason": "content_changed",
                "platforms": {},
            }
        }
    }
    text = render_report(snapshot)
    assert "需人工确认" in text
    assert "content_changed" in text


def test_structured_output():
    snapshot = {
        "articles": {
            "test": {
                "article_id": "test",
                "title": "测试",
                "article_stage": "completed",
                "platforms": {
                    "zhihu:publish": {"stage": "published", "published_ref": "url"},
                },
            }
        }
    }
    struct = snapshot_to_structured(snapshot)
    assert len(struct["articles"]) == 1
    assert struct["articles"][0]["article_id"] == "test"
    assert len(struct["articles"][0]["platforms"]) == 1
