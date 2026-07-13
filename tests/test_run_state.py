import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ordo_engine import run_state


def test_state_file_for_uses_base_dir_and_default_uses_repo_root(tmp_path):
    assert run_state.state_file_for(tmp_path) == tmp_path / ".ordo" / "publish-state.json"
    assert run_state.STATE_FILE == Path(run_state.__file__).resolve().parents[1] / ".ordo" / "publish-state.json"


@pytest.mark.parametrize("contents", ["not json", "[]", "null"])
def test_load_state_rejects_corrupt_or_non_object_json(tmp_path, contents):
    state_file = tmp_path / "state.json"
    state_file.write_text(contents, encoding="utf-8")

    with pytest.raises(run_state.StateCorruptionError):
        run_state.load_state(state_file)


def test_load_state_returns_empty_only_for_absent_file(tmp_path):
    assert run_state.load_state(tmp_path / "missing.json") == {}


def test_mark_done_and_get_record_use_mode_nested_keys(tmp_path):
    state_file = tmp_path / ".ordo" / "publish-state.json"

    with patch.object(run_state.time, "time", return_value=123):
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
    with patch.object(run_state.time, "time", return_value=456):
        run_state.record_step("article-1", "wechat", "publish", "editor_filled", state_file=state_file)

    assert run_state.get_record("article-1", "wechat", "publish", state_file=state_file) == {
        "last_step": "editor_filled",
        "mode": "publish",
        "ts": 456,
    }


def test_save_state_atomically_replaces_file_and_fsyncs(tmp_path):
    state_file = tmp_path / ".ordo" / "publish-state.json"

    with patch.object(run_state.os, "fsync", wraps=run_state.os.fsync) as fsync, patch.object(
        run_state.os,
        "replace",
        wraps=run_state.os.replace,
    ) as replace:
        run_state.save_state(state_file, {"article-1": {}})

    assert json.loads(state_file.read_text(encoding="utf-8")) == {"article-1": {}}
    fsync.assert_called_once()
    replace.assert_called_once()
    temp_path, target_path = map(Path, replace.call_args.args)
    assert temp_path.parent == state_file.parent
    assert target_path == state_file
    assert list(state_file.parent.iterdir()) == [state_file]


def test_save_state_cleans_temp_file_on_error(tmp_path):
    state_file = tmp_path / ".ordo" / "publish-state.json"

    with patch.object(run_state.json, "dump", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            run_state.save_state(state_file, {"article-1": {}})

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
