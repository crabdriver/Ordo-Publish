import pytest
from unittest.mock import patch

from ordo_engine.platforms.wechat.runtime import (
    WechatVpsOnlyError,
    require_vps_worker,
)
from ordo_engine.platforms.wechat import api as wechat_api
import wechat_publisher


def test_local_wechat_worker_is_blocked(monkeypatch):
    monkeypatch.delenv("ORDO_WORKER", raising=False)

    with pytest.raises(WechatVpsOnlyError, match="VPS"):
        require_vps_worker()


def test_vps_worker_is_allowed(monkeypatch):
    monkeypatch.setenv("ORDO_WORKER", "1")
    monkeypatch.setenv("ORDO_WECHAT_VPS_WORKER", "1")

    require_vps_worker()


def test_generic_worker_flag_alone_does_not_allow_wechat(monkeypatch):
    monkeypatch.setenv("ORDO_WORKER", "1")
    monkeypatch.delenv("ORDO_WECHAT_VPS_WORKER", raising=False)

    with pytest.raises(WechatVpsOnlyError):
        require_vps_worker()


def test_root_publisher_blocks_before_token_request(monkeypatch):
    monkeypatch.delenv("ORDO_WORKER", raising=False)
    publisher = wechat_publisher.WeChatPublisher("appid", "secret")

    with patch.object(wechat_publisher.requests, "get") as request_get:
        with pytest.raises(WechatVpsOnlyError):
            publisher.get_access_token()

    request_get.assert_not_called()


def test_shared_api_blocks_before_draft_request(monkeypatch):
    monkeypatch.delenv("ORDO_WORKER", raising=False)

    with patch.object(wechat_api.requests, "post") as request_post:
        with pytest.raises(WechatVpsOnlyError):
            wechat_api.push_draft("token", "title", "body", "thumb")

    request_post.assert_not_called()
