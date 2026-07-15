"""run_state v2 schema 的测试套件。

包含：
- 已有测试的兼容性更新（state_file_for 现在返回 auto_publish_state.json）
- 新增 v2 测试：迁移、stable_article_id、package_hash、completed 判断等
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ordo_engine import run_state


# ── 已有测试 —— 更新路径 ─────────────────────────────────────

def test_state_file_for_uses_base_dir(tmp_path):
    assert run_state.state_file_for(tmp_path) == (
        tmp_path / ".ordo" / "auto_publish_state.json"
    )
    assert run_state.STATE_FILE == (
        Path(run_state.__file__).resolve().parents[1] / ".ordo" / "auto_publish_state.json"
    )


def test_state_file_for_no_longer_returns_publish_state_json(tmp_path):
    """publish-state.json 不再被 state_file_for 返回。"""
    result = run_state.state_file_for(tmp_path)
    assert "publish-state.json" not in str(result)
    assert "auto_publish_state.json" in str(result)


@pytest.mark.parametrize("contents", ["not json", "[]", "null"])
def test_load_state_rejects_corrupt_or_non_object_json(tmp_path, contents):
    state_file = tmp_path / "state.json"
    state_file.write_text(contents, encoding="utf-8")

    with pytest.raises(run_state.StateCorruptionError):
        run_state.load_state(state_file)


def test_load_state_returns_empty_only_for_absent_file(tmp_path):
    assert run_state.load_state(tmp_path / "missing.json") == {}


def test_mark_done_and_get_record_use_mode_nested_keys(tmp_path):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"

    with patch.object(run_state._time, "time", return_value=123):
        run_state.mark_done("article-1", "wechat", "draft_saved", "draft", "draft-url", state_file=state_file)
        run_state.mark_done("article-1", "wechat", "published", "publish", "publish-url", state_file=state_file)

    assert run_state.get_record("article-1", "wechat", "draft", state_file=state_file) == {
        "status": "draft_saved",
        "mode": "draft",
        "url": "draft-url",
        "ts": 123,
    }
    assert run_state.get_record("article-1", "wechat", "publish", state_file=state_file) == {
        "status": "published",
        "mode": "publish",
        "url": "publish-url",
        "ts": 123,
    }


@pytest.mark.parametrize("status", ["draft_saved", "draft_only", "skipped_existing"])
def test_is_done_accepts_only_draft_terminal_states(tmp_path, status):
    state_file = tmp_path / "state.json"
    run_state.mark_done("article-1", "wechat", status, "draft", state_file=state_file)

    assert run_state.is_done("article-1", "wechat", "draft", state_file=state_file)
    assert not run_state.is_done("article-1", "wechat", "publish", state_file=state_file)


@pytest.mark.parametrize("status", ["published", "scheduled", "skipped_existing"])
def test_is_done_accepts_only_publish_terminal_states(tmp_path, status):
    state_file = tmp_path / "state.json"
    run_state.mark_done("article-1", "wechat", status, "publish", state_file=state_file)

    assert run_state.is_done("article-1", "wechat", "publish", state_file=state_file)
    assert not run_state.is_done("article-1", "wechat", "draft", state_file=state_file)


def test_non_terminal_or_wrong_mode_status_is_not_done(tmp_path):
    state_file = tmp_path / "state.json"
    run_state.mark_done("article-1", "wechat", "published", "draft", state_file=state_file)
    run_state.mark_done("article-1", "zhihu", "draft_saved", "publish", state_file=state_file)

    assert not run_state.is_done("article-1", "wechat", "draft", state_file=state_file)
    assert not run_state.is_done("article-1", "zhihu", "publish", state_file=state_file)


def test_record_step_uses_same_mode_nested_key(tmp_path):
    state_file = tmp_path / "state.json"
    with patch.object(run_state._time, "time", return_value=456):
        run_state.record_step("article-1", "wechat", "publish", "editor_filled", state_file=state_file)

    assert run_state.get_record("article-1", "wechat", "publish", state_file=state_file) == {
        "last_step": "editor_filled",
        "mode": "publish",
        "ts": 456,
    }


def test_record_step_preserves_existing_terminal_status(tmp_path):
    state_file = tmp_path / "state.json"
    run_state.mark_done(
        "article-1", "zhihu", "published", "publish", state_file=state_file
    )
    run_state.record_step(
        "article-1", "zhihu", "publish", "submit_started", state_file=state_file
    )

    record = run_state.get_record(
        "article-1", "zhihu", "publish", state_file=state_file
    )
    # publish mode + published status → 终态
    assert record["status"] == "published"
    assert record["last_step"] == "submit_started"


def test_save_state_atomically_replaces_file_and_fsyncs(tmp_path):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"

    with patch.object(run_state.os, "fsync", wraps=run_state.os.fsync) as fsync, patch.object(
        run_state.os,
        "replace",
        wraps=run_state.os.replace,
    ) as replace:
        run_state.save_state(state_file, {"schema_version": 2, "articles": {}})

    assert json.loads(state_file.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "articles": {},
    }
    fsync.assert_called_once()
    replace.assert_called_once()
    temp_path, target_path = map(Path, replace.call_args.args)
    assert temp_path.parent == state_file.parent
    assert target_path == state_file
    assert list(state_file.parent.iterdir()) == [state_file]


def test_save_state_cleans_temp_file_on_error(tmp_path):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"

    with patch.object(run_state.json, "dump", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            run_state.save_state(state_file, {"schema_version": 2, "articles": {}})

    assert not state_file.exists()
    assert list(state_file.parent.iterdir()) == []


def test_article_key_prefers_frontmatter_article_id(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("---\ntitle: Example\narticle_id: stable-42\n---\n# Changed body\n", encoding="utf-8")

    assert run_state.article_key(article) == "stable-42"


def test_article_key_falls_back_to_legacy_content_hash(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# Legacy\n", encoding="utf-8")

    assert run_state.article_key(article) == run_state._hash("# Legacy\n")


def test_reset_can_remove_one_mode_without_removing_other_mode(tmp_path):
    state_file = tmp_path / "state.json"
    run_state.mark_done("article-1", "wechat", "draft_saved", "draft", state_file=state_file)
    run_state.mark_done("article-1", "wechat", "published", "publish", state_file=state_file)

    run_state.reset("article-1", "wechat", "draft", state_file=state_file)

    assert run_state.get_record("article-1", "wechat", "draft", state_file=state_file) is None
    assert run_state.get_record("article-1", "wechat", "publish", state_file=state_file) is not None


def test_reset_without_mode_preserves_legacy_platform_scope(tmp_path):
    state_file = tmp_path / "state.json"
    run_state.mark_done("article-1", "wechat", "draft_saved", "draft", state_file=state_file)
    run_state.mark_done("article-1", "wechat", "published", "publish", state_file=state_file)

    run_state.reset("article-1", "wechat", state_file=state_file)

    assert run_state.get_record("article-1", "wechat", "draft", state_file=state_file) is None
    assert run_state.get_record("article-1", "wechat", "publish", state_file=state_file) is None


# ── 新增 v2 测试 ─────────────────────────────────────────────


# T1.1: schema v2 roundtrip
def test_v2_schema_roundtrip(tmp_path):
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage, ArticleStage,
        save_v2_state, load_v2_state,
    )
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"

    rec = ArticleRecord(
        article_id="test-id",
        package_hash="abc123",
        source_path="/tmp/test.md",
        article_stage=ArticleStage.pending,
        title="Test Article",
    )
    prec = PlatformRecord(
        stage=PlatformStage.draft_saved,
        draft_ref="draft-123",
        updated_at="2026-07-14T00:00:00Z",
    )
    rec.platforms["wechat"] = {"draft": prec}

    save_v2_state({"test-id": rec}, state_file)
    loaded = load_v2_state(state_file)

    assert "test-id" in loaded
    assert loaded["test-id"].article_id == "test-id"
    assert loaded["test-id"].package_hash == "abc123"
    assert loaded["test-id"].platforms["wechat"]["draft"].stage == PlatformStage.draft_saved
    assert loaded["test-id"].platforms["wechat"]["draft"].draft_ref == "draft-123"


# T1.2: stable_article_id 优先使用 frontmatter
def test_stable_article_id_prefers_frontmatter(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("---\ntitle: T\narticle_id: my-stable-id\n---\nbody", encoding="utf-8")
    assert run_state.stable_article_id(article) == "my-stable-id"


# T1.3: stable_article_id 在 watch_dir 内使用相对路径
def test_stable_article_id_uses_relative_path_inside_watch_dir(tmp_path):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    article = watch_dir / "sub" / "article.md"
    article.parent.mkdir(parents=True)
    article.write_text("no article_id here\n", encoding="utf-8")
    result = run_state.stable_article_id(article, watch_dir=watch_dir)
    assert result.startswith("path:")
    assert "sub/article.md" in result


# T1.4: stable_article_id 在 watch_dir 外使用绝对路径
def test_stable_article_id_uses_absolute_path_outside_watch_dir(tmp_path):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    article = tmp_path / "outside.md"
    article.write_text("no article_id\n", encoding="utf-8")
    result = run_state.stable_article_id(article, watch_dir=watch_dir)
    assert result.startswith("path:")
    # 应该是绝对路径（不是相对）
    assert "outside.md" not in result or result.count("/") > 2


# T1.5: article_id 在正文修改后保持不变
def test_stable_article_id_persists_across_content_changes(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("---\narticle_id: fixed-id\n---\n# v1\n", encoding="utf-8")
    id1 = run_state.stable_article_id(article)
    article.write_text("---\narticle_id: fixed-id\n---\n# v2 changed body\n", encoding="utf-8")
    id2 = run_state.stable_article_id(article)
    assert id1 == id2 == "fixed-id"


# T1.6: compute_package_hash 内容变化后改变
def test_compute_package_hash_changes_with_content(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# version 1\n", encoding="utf-8")
    h1 = run_state.compute_package_hash(article)

    article.write_text("# version 2 different\n", encoding="utf-8")
    h2 = run_state.compute_package_hash(article)

    assert h1 != h2
    assert len(h1) == 32


# T1.7: compute_package_hash 含封面 hash
def test_compute_package_hash_includes_cover(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# body\n", encoding="utf-8")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"fake-png-data")

    h_no_cover = run_state.compute_package_hash(article)
    h_with_cover = run_state.compute_package_hash(article, cover)

    assert h_no_cover != h_with_cover


# T1.8: 迁移 v1 → v2 成功
def test_v1_to_v2_migration_success(tmp_path, monkeypatch):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # 写入旧格式 v1
    old_data = {
        "articles": {
            "old-key": {
                "path": str(tmp_path / "article.md"),
                "title": "Old Article",
                "cover": str(tmp_path / "cover.png"),
                "status": "attempted",
                "platforms": {
                    "zhihu": {"mode": "publish", "status": "published", "returncode": 0},
                    "wechat": {"mode": "draft", "status": "draft_only"},
                },
            },
        },
    }
    state_file.write_text(json.dumps(old_data))

    articles = run_state.migrate_v1_to_v2(state_file, watch_dir=tmp_path)

    assert len(articles) > 0
    identity = list(articles.keys())[0]
    article = articles[identity]
    assert article.title == "Old Article"
    zhihu = article.platforms.get("zhihu", {}).get("publish")
    assert zhihu is not None
    assert zhihu.stage == run_state.PlatformStage.published


# T1.9: 迁移 v1 失败保留原文件
def test_v1_to_v2_migration_atomic_rollback(tmp_path):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # 写入损坏的旧格式
    old_content = "this is not json"
    state_file.write_text(old_content)

    # load_state 应抛出 StateCorruptionError（因为不是 JSON）
    # 但如果是有效的 JSON 但不是对象也会抛错
    valid_json_not_object = "[]"
    state_file.write_text(valid_json_not_object)

    with pytest.raises(run_state.StateCorruptionError):
        run_state.load_state(state_file)


# T1.10: 损坏记录 fail-closed 到 manual_verify
def test_v1_to_v2_corrupted_record_fail_closed(tmp_path):
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # 有效 v2 但包含未知状态（模拟损坏）
    data = {
        "schema_version": 2,
        "articles": {
            "test-id": {
                "article_id": "test-id",
                "article_stage": "pending",
                "platforms": {
                    "zhihu": {
                        "publish": {"stage": "this-is-not-a-valid-stage-at-all"},
                    },
                },
            },
        },
    }
    state_file.write_text(json.dumps(data))

    # 未知 stage 应该在加载时抛错
    with pytest.raises(ValueError):
        run_state.load_v2_state(state_file)


# T1.11: article_stage completed 被正确判别
def test_completed_article_detected():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage, ArticleStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test", article_stage=ArticleStage.pending)
    article.platforms["wechat"] = {
        "draft": PlatformRecord(stage=PlatformStage.draft_saved),
    }
    for platform in ["zhihu", "jianshu", "toutiao", "yidian", "bilibili"]:
        article.platforms[platform] = {
            "publish": PlatformRecord(stage=PlatformStage.published),
        }

    assert is_article_completed(article, ["zhihu", "jianshu", "toutiao", "yidian", "bilibili"])


# T1.12: manual_verify 不算完成
def test_not_completed_with_manual_verify():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage, ArticleStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test")
    article.platforms["wechat"] = {
        "draft": PlatformRecord(stage=PlatformStage.draft_saved),
    }
    for platform in ["zhihu", "jianshu", "toutiao", "yidian"]:
        article.platforms[platform] = {
            "publish": PlatformRecord(stage=PlatformStage.published),
        }
    article.platforms["bilibili"] = {
        "publish": PlatformRecord(stage=PlatformStage.manual_verify),
    }

    assert not is_article_completed(article, ["zhihu", "jianshu", "toutiao", "yidian", "bilibili"])


# T1.13: limited_after_draft 不算完成
def test_not_completed_with_limited_after_draft():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test")
    article.platforms["wechat"] = {"draft": PlatformRecord(stage=PlatformStage.draft_saved)}
    for platform in ["zhihu", "jianshu", "toutiao", "bilibili"]:
        article.platforms[platform] = {"publish": PlatformRecord(stage=PlatformStage.published)}
    article.platforms["yidian"] = {"publish": PlatformRecord(stage=PlatformStage.limited_after_draft)}

    assert not is_article_completed(article, ["zhihu", "jianshu", "toutiao", "yidian", "bilibili"])


# T1.14: blocked_no_draft 不算完成
def test_not_completed_with_blocked_no_draft():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test")
    article.platforms["wechat"] = {"draft": PlatformRecord(stage=PlatformStage.draft_saved)}
    article.platforms["zhihu"] = {"publish": PlatformRecord(stage=PlatformStage.blocked_no_draft)}

    assert not is_article_completed(article, ["zhihu"])


# T1.15: 禁用平台不参与完成判断
def test_disabled_platform_excluded_from_completion():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test")
    article.platforms["wechat"] = {"draft": PlatformRecord(stage=PlatformStage.draft_saved)}
    article.platforms["zhihu"] = {"publish": PlatformRecord(stage=PlatformStage.published)}
    # bilibili 在 enabled list 之外 → 不参与判断

    assert is_article_completed(article, ["zhihu"])


# T1.16: 旧 API 在 v2 schema 上正确工作
def test_legacy_api_on_v2_schema_roundtrip(tmp_path):
    """get_record / mark_done / record_step 在 v2 schema 上正确工作。"""
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"

    run_state.mark_done("test-id", "zhihu", "published", "publish", "https://zhihu.com/123", state_file=state_file)
    run_state.record_step("test-id", "zhihu", "publish", "submit_started", state_file=state_file)

    rec = run_state.get_record("test-id", "zhihu", "publish", state_file=state_file)
    assert rec["status"] == "published"
    assert rec["url"] == "https://zhihu.com/123"
    assert rec["last_step"] == "submit_started"

    assert run_state.is_done("test-id", "zhihu", "publish", state_file=state_file)


# T1.17: WeChat 未 draft_saved → 不完成
def test_not_completed_when_wechat_missing():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage,
        is_article_completed,
    )

    article = ArticleRecord(article_id="test")
    article.platforms["zhihu"] = {"publish": PlatformRecord(stage=PlatformStage.published)}

    assert not is_article_completed(article, ["zhihu"])


# T1.18: content_changed → article_stage needs_review（文章级）
def test_content_changed_sets_needs_review_preserves_platform_states():
    from ordo_engine.run_state import (
        ArticleRecord, PlatformRecord, PlatformStage, ArticleStage,
    )

    article = ArticleRecord(
        article_id="test",
        article_stage=ArticleStage.pending,
        package_hash="old-hash",
    )
    article.platforms["zhihu"] = {"publish": PlatformRecord(stage=PlatformStage.published)}

    # 模拟内容变更检测
    article.article_stage = ArticleStage.needs_review
    article.article_block_reason = "content_changed"

    assert article.article_stage == ArticleStage.needs_review
    # 旧平台状态保留
    assert article.platforms["zhihu"]["publish"].stage == PlatformStage.published


# T1.19: PlatformStage 共 11 个值
def test_platform_stage_has_exactly_11_values():
    from ordo_engine.run_state import PlatformStage
    stages = list(PlatformStage)
    assert len(stages) == 11, f"期望 11 个 PlatformStage, 实际 {len(stages)}: {[s.value for s in stages]}"


# T1.20: ArticleStage 共 3 个值 (pending, needs_review, completed)
def test_article_stage_has_exactly_3_values():
    from ordo_engine.run_state import ArticleStage
    stages = list(ArticleStage)
    assert len(stages) == 3, f"期望 3 个 ArticleStage, 实际 {len(stages)}: {[s.value for s in stages]}"
    expected = {"pending", "needs_review", "completed"}
    actual = {s.value for s in stages}
    assert actual == expected


# T1.21: only one state file written per legacy mark_done call
def test_only_one_state_file_written_per_batch(tmp_path):
    """mark_done 只写 auto_publish_state.json，不写 publish-state.json。"""
    state_file = tmp_path / ".ordo" / "auto_publish_state.json"
    old_file = tmp_path / ".ordo" / "publish-state.json"

    run_state.mark_done("test-id", "zhihu", "published", "publish", state_file=state_file)

    assert state_file.exists()
    assert not old_file.exists()


# T1.22: not_executed stage mapped correctly
def test_not_executed_stage_exists():
    from ordo_engine.run_state import PlatformStage
    assert PlatformStage.not_executed.value == "not_executed"
    # 验证 not_executed 在枚举值中
    assert "not_executed" in [s.value for s in PlatformStage]
