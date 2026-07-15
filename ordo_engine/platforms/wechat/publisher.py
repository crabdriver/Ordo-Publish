import os
import re
import shlex
import subprocess
from pathlib import Path

from ordo_engine.platforms.base import SubprocessPlatformAdapter, _extract_smoke_state


def load_vps_config(base_dir: Path) -> dict:
    env_path = Path(base_dir) / "secrets.env"
    config = {}
    if not env_path.exists():
        return config
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


class WeChatPlatformAdapter(SubprocessPlatformAdapter):
    """本机只负责任务传输；微信 API 必须由 VPS worker 调用。"""

    def __init__(self, base_dir: Path, executor=None):
        super().__init__(
            base_dir=base_dir,
            platform="wechat",
            script_name="wechat_publisher.py",
            supports_theme=True,
            supports_cover=True,
            supports_cover_mode=True,
            executor=executor,
        )

    def publish(self, prepared_context):
        if (
            os.environ.get("ORDO_WORKER") == "1"
            and os.environ.get("ORDO_WECHAT_VPS_WORKER") == "1"
        ):
            return super().publish(prepared_context)

        config = load_vps_config(self.base_dir)
        vps_ip = config.get("VPS_IP") or config.get("VPS_HOST")
        if not vps_ip:
            return self._blocked(
                prepared_context,
                "微信公众号发布必须走 VPS：secrets.env 缺少 VPS_IP/VPS_HOST，已拒绝本机发送。",
            )

        vps_port = config.get("VPS_PORT", "22")
        vps_user = config.get("VPS_USER", "root")
        vps_path = config.get("VPS_PATH", "/root/ordo-publish").rstrip("/")
        ssh_key = config.get("VPS_SSH_KEY")
        ssh_options = ["-p", vps_port]
        scp_options = ["-P", vps_port]
        if ssh_key:
            expanded_key = str(Path(ssh_key).expanduser())
            ssh_options.extend(["-i", expanded_key])
            scp_options.extend(["-i", expanded_key])

        target = f"{vps_user}@{vps_ip}"
        local_command = list(prepared_context["command"])
        local_article = Path(local_command[2]).expanduser().resolve()
        remote_temp = f"{vps_path}/temp"
        remote_article = f"{remote_temp}/{local_article.name}"

        local_cover = None
        remote_cover = None
        if "--cover" in local_command:
            cover_index = local_command.index("--cover") + 1
            local_cover = Path(local_command[cover_index]).expanduser().resolve()
            remote_cover = f"{remote_temp}/{local_cover.name}"

        try:
            subprocess.run(
                ["ssh", *ssh_options, target, f"mkdir -p {shlex.quote(remote_temp)}"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["scp", *scp_options, str(local_article), f"{target}:{remote_article}"],
                check=True,
                capture_output=True,
                text=True,
            )
            if local_cover is not None:
                subprocess.run(
                    ["scp", *scp_options, str(local_cover), f"{target}:{remote_cover}"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            self._upload_referenced_images(
                local_article,
                target,
                remote_temp,
                ssh_options,
                scp_options,
            )
        except subprocess.CalledProcessError as exc:
            stderr = self._subprocess_error(exc)
            return self._failed(prepared_context, exc.returncode, f"微信任务上传 VPS 失败: {stderr}")

        remote_args = []
        for arg in local_command[1:]:
            if arg == str(local_article):
                remote_args.append(remote_article)
            elif local_cover is not None and arg == str(local_cover):
                remote_args.append(remote_cover)
            elif arg == str(self.script_path):
                remote_args.append(self.script_name)
            else:
                remote_args.append(arg)
        quoted_args = " ".join(shlex.quote(arg) for arg in remote_args)
        remote_shell = (
            "unset WECHAT_PROXY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; "
            "export ORDO_WORKER=1 ORDO_WECHAT_VPS_WORKER=1; "
            f"cd {shlex.quote(vps_path)} && "
            f"if [ -x .venv312/bin/python ]; then .venv312/bin/python {quoted_args}; "
            f"elif [ -x .venv/bin/python ]; then .venv/bin/python {quoted_args}; "
            f"else python3 {quoted_args}; fi"
        )
        ssh_command = ["ssh", *ssh_options, target, remote_shell]

        try:
            result = subprocess.run(
                ssh_command,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            self._cleanup_remote(target, remote_temp, ssh_options)
            stdout = (exc.stdout or exc.output or "").strip()
            stderr = (exc.stderr or "").strip()
            return {
                "platform": self.platform,
                "command": " ".join(local_command),
                "returncode": 124,
                "stdout": stdout,
                "stderr": f"VPS 微信 worker 超时（300s）{': ' + stderr if stderr else ''}",
                "timed_out": True,
                "remote_started": True,
            }

        self._cleanup_remote(target, remote_temp, ssh_options)
        stdout, stdout_state = _extract_smoke_state((result.stdout or "").strip())
        stderr, stderr_state = _extract_smoke_state((result.stderr or "").strip())
        smoke_state = {}
        if stdout_state:
            smoke_state.update(stdout_state)
        if stderr_state:
            smoke_state.update(stderr_state)
        return {
            "platform": self.platform,
            "command": " ".join(local_command),
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "current_url": str(smoke_state.get("current_url", "")),
            "page_state": str(smoke_state.get("page_state", "")),
            "smoke_step": str(smoke_state.get("smoke_step", "")),
            "remote_started": True,
        }

    def _upload_referenced_images(
        self,
        article: Path,
        target: str,
        remote_temp: str,
        ssh_options: list[str],
        scp_options: list[str],
    ) -> None:
        content = article.read_text(encoding="utf-8")
        for raw_path in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content):
            image_path = raw_path.strip()
            if image_path.startswith(("http://", "https://")):
                continue
            local_image = (article.parent / image_path).resolve()
            if not local_image.is_file():
                continue
            remote_image = f"{remote_temp}/{image_path}"
            remote_dir = str(Path(remote_image).parent)
            subprocess.run(
                ["ssh", *ssh_options, target, f"mkdir -p {shlex.quote(remote_dir)}"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["scp", *scp_options, str(local_image), f"{target}:{remote_image}"],
                check=True,
                capture_output=True,
                text=True,
            )

    @staticmethod
    def _cleanup_remote(target: str, remote_temp: str, ssh_options: list[str]) -> None:
        subprocess.run(
            ["ssh", *ssh_options, target, f"rm -rf {shlex.quote(remote_temp)}"],
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _subprocess_error(exc: subprocess.CalledProcessError) -> str:
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return stderr.strip() or str(exc)

    def _blocked(self, prepared_context, message):
        return self._failed(prepared_context, 2, message)

    def _failed(self, prepared_context, returncode, message):
        return {
            "platform": self.platform,
            "command": " ".join(prepared_context["command"]),
            "returncode": returncode,
            "stdout": "",
            "stderr": message,
            "remote_started": False,
        }
