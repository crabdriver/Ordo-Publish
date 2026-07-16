import os


class WechatVpsOnlyError(RuntimeError):
    """微信 API 在非 VPS worker 环境中被调用。"""


def require_vps_worker() -> None:
    if (
        os.environ.get("ORDO_WORKER") != "1"
        or os.environ.get("ORDO_WECHAT_VPS_WORKER") != "1"
    ):
        raise WechatVpsOnlyError(
            "微信公众号 API 仅允许在 VPS worker 中执行；已拒绝本机 IP 调用"
        )
