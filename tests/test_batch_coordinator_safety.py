import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ordo_engine.runner import pipeline as pipeline_module
from ordo_engine.run_state import (
    ArticleRecord,
    ArticleStage,
    PlatformRecord,
    PlatformStage,
    StateCorruptionError,
    save_v2_state,
    stable_article_id,
)
from ordo_engine.runner.pipeline import BatchCoordinator


def _article(path: Path, article_id: str = "article-1") -> Path:
    path.write_text(
        f"---\narticle_id: {article_id}\ntitle: Test\n---\nBody\n",
        encoding="utf-8",
    )
    return path


def _coordinator(tmp_path: Path, *, state_file: Path | None = None, engine_factory=None):
    return BatchCoordinator(
        base_dir=tmp_path,
        watch_dir=tmp_path,
        state_file=state_file or tmp_path / ".ordo" / "auto_publish_state.json",
        registry={},
        engine_factory=engine_factory,
    )


def test_corrupt_state_fails_closed_instead_of_resetting(tmp_path):
    article = _article(tmp_path / "a.md")
    state_file = tmp_path / "state.json"
    state_file.write_text("{broken", encoding="utf-8")
    coordinator = _coordinator(tmp_path, state_file=state_file)

    with pytest.raises(StateCorruptionError):
        coordinator._load_or_init_state([article])


def test_browser_preflight_does_not_require_cover(tmp_path):
    article = _article(tmp_path / "a.md")
    coordinator = _coordinator(tmp_path)
    identity = stable_article_id(article, watch_dir=tmp_path)
    coordinator._articles[identity] = ArticleRecord(article_id=identity)

    coordinator._preflight_one(article)

    record = coordinator._articles[identity]
    assert record.article_stage == ArticleStage.pending
    assert record.article_block_reason is None
    assert record.package_hash


@pytest.mark.parametrize(
    "stage,expected",
    [
        (PlatformStage.published, False),
        (PlatformStage.manual_verify, False),
        (PlatformStage.publish_attempted, False),
        (PlatformStage.draft_saved, False),
        (PlatformStage.not_executed, True),
        (PlatformStage.failed_before_draft, True),
    ],
)
def test_retry_policy_never_resubmits_ambiguous_attempts(tmp_path, stage, expected):
    coordinator = _coordinator(tmp_path)
    article = ArticleRecord(article_id="a")
    article.platforms = {
        "zhihu": {"publish": PlatformRecord(stage=stage)}
    }

    assert coordinator._needs_processing(article, "zhihu", "publish") is expected


def test_publish_click_no_effect_requires_manual_review():
    mapper = getattr(pipeline_module, "_map_payload_stage", None)
    assert mapper is not None, "_map_payload_stage must exist"

    assert mapper({
        "status": "failed",
        "error_type": "publish_click_no_effect",
    }) == PlatformStage.manual_verify


def test_browser_start_failure_does_not_overwrite_published_state(tmp_path):
    def fail_engine(**_kwargs):
        raise RuntimeError("browser unavailable")

    first = _article(tmp_path / "published.md", "published")
    second = _article(tmp_path / "pending.md", "pending")
    coordinator = _coordinator(tmp_path, engine_factory=fail_engine)
    coordinator._articles = {
        "published": ArticleRecord(
            article_id="published",
            platforms={
                "zhihu": {
                    "publish": PlatformRecord(
                        stage=PlatformStage.published,
                        published_ref="https://example.test/published",
                    )
                }
            },
        ),
        "pending": ArticleRecord(article_id="pending"),
    }

    coordinator._run_browser_platform("zhihu", [first, second])

    published = coordinator._articles["published"].platforms["zhihu"]["publish"]
    pending = coordinator._articles["pending"].platforms["zhihu"]["publish"]
    assert published.stage == PlatformStage.published
    assert published.published_ref == "https://example.test/published"
    assert pending.stage == PlatformStage.not_executed
    assert pending.error_type == "browser_start_failed"


def test_identity_change_at_same_source_path_fails_closed(tmp_path):
    article = _article(tmp_path / "a.md", "new-id")
    state_file = tmp_path / "state.json"
    old = ArticleRecord(
        article_id="old-id",
        source_path=str(article),
        platforms={
            "zhihu": {
                "publish": PlatformRecord(stage=PlatformStage.manual_verify)
            }
        },
    )
    save_v2_state({"old-id": old}, state_file)
    coordinator = _coordinator(tmp_path, state_file=state_file)

    with pytest.raises(StateCorruptionError, match="identity"):
        coordinator._load_or_init_state([article])


def test_empty_legacy_identity_for_same_source_is_safely_discarded(tmp_path):
    article = _article(tmp_path / "a.md", "new-id")
    state_file = tmp_path / "state.json"
    old = ArticleRecord(article_id="old-id", source_path=str(article))
    save_v2_state({"old-id": old}, state_file)
    coordinator = _coordinator(tmp_path, state_file=state_file)

    coordinator._load_or_init_state([article])

    assert "old-id" not in coordinator._articles
    assert coordinator._articles["new-id"].source_path == str(article)


def test_wechat_worker_bypasses_publish_cli_lock(tmp_path):
    article = _article(tmp_path / "a.md")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    coordinator = _coordinator(tmp_path)
    coordinator._articles["article-1"] = ArticleRecord(article_id="article-1")
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="[OK] 已写入微信公众号草稿: draft-media-id\n",
        stderr="",
    )

    with patch("subprocess.run", return_value=result) as run:
        coordinator._run_wechat_subprocess(article, cover)

    cmd = run.call_args.args[0]
    assert run.call_args.kwargs["timeout"] == 300
    assert Path(cmd[1]).name == "wechat_publisher.py"
    assert "publish.py" not in [Path(part).name for part in cmd]
    assert cmd[cmd.index("--cover") + 1] == str(cover)
    record = coordinator._articles["article-1"].platforms["wechat"]["draft"]
    assert record.stage == PlatformStage.draft_saved
    assert record.draft_ref == "draft-media-id"


def test_wechat_batch_delegates_invalid_cover_recovery_to_worker(tmp_path):
    article = _article(tmp_path / "a.md")
    coordinator = _coordinator(tmp_path)
    coordinator._articles["article-1"] = ArticleRecord(article_id="article-1")

    with patch.object(coordinator, "_run_wechat_subprocess") as worker:
        coordinator._run_wechat_batch([article])

    worker.assert_called_once_with(article, None)


def test_wechat_timeout_is_manual_verify_not_automatic_retry(tmp_path):
    article = _article(tmp_path / "a.md")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    coordinator = _coordinator(tmp_path)
    coordinator._articles["article-1"] = ArticleRecord(article_id="article-1")

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired("wechat", 120),
    ):
        coordinator._run_wechat_subprocess(article, cover)

    record = coordinator._articles["article-1"].platforms["wechat"]["draft"]
    assert record.stage == PlatformStage.manual_verify
    assert record.error_type == "wechat_worker_timeout"
    assert coordinator._needs_processing(
        coordinator._articles["article-1"], "wechat", "draft"
    ) is False
