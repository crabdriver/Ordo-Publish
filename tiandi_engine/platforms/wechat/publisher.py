import os
import subprocess
from pathlib import Path

from tiandi_engine.platforms.base import SubprocessPlatformAdapter


def load_vps_config(base_dir: Path) -> dict:
    env_path = base_dir / "secrets.env"
    config = {}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return config


class WeChatPlatformAdapter(SubprocessPlatformAdapter):
    def __init__(self, base_dir: Path):
        super().__init__(
            base_dir=base_dir,
            platform="wechat",
            script_name="wechat_publisher.py",
            supports_theme=True,
            supports_cover=True,
        )

    def publish(self, prepared_context):
        vps_cfg = load_vps_config(self.base_dir)
        vps_ip = vps_cfg.get("VPS_IP")
        if not vps_ip:
            # Fallback to local subprocess execution
            return super().publish(prepared_context)

        vps_port = vps_cfg.get("VPS_PORT", "22")
        vps_user = vps_cfg.get("VPS_USER", "root")
        vps_ssh_key = vps_cfg.get("VPS_SSH_KEY")

        local_cmd = prepared_context["command"]
        
        # 1. Parse markdown file and cover paths to upload
        # command looks like: [sys.executable, 'wechat_publisher.py', '/path/to/article.md', ...]
        local_md_path = Path(local_cmd[2])
        remote_md_path = f"~/ordo-publish/temp/{local_md_path.name}"

        local_cover_path = None
        remote_cover_path = None
        if "--cover" in local_cmd:
            idx = local_cmd.index("--cover")
            local_cover_path = Path(local_cmd[idx + 1])
            remote_cover_path = f"~/ordo-publish/temp/{local_cover_path.name}"

        # 2. Configure SSH/SCP options
        ssh_opts = ["-p", vps_port]
        scp_opts = ["-P", vps_port]
        if vps_ssh_key:
            expanded_key = str(Path(vps_ssh_key).expanduser())
            ssh_opts.extend(["-i", expanded_key])
            scp_opts.extend(["-i", expanded_key])

        # Automatically skip host key check to avoid interactive prompts
        ssh_opts.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
        scp_opts.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])

        # 3. Create temp dir and upload files via SCP
        try:
            mkdir_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", "mkdir -p ~/ordo-publish/temp"]
            subprocess.run(mkdir_cmd, check=True, capture_output=True)

            scp_md_cmd = ["scp"] + scp_opts + [str(local_md_path), f"{vps_user}@{vps_ip}:{remote_md_path}"]
            subprocess.run(scp_md_cmd, check=True, capture_output=True)

            if local_cover_path:
                scp_cover_cmd = ["scp"] + scp_opts + [str(local_cover_path), f"{vps_user}@{vps_ip}:{remote_cover_path}"]
                subprocess.run(scp_cover_cmd, check=True, capture_output=True)

            # Upload referenced local images
            import re
            md_content = local_md_path.read_text(encoding="utf-8")
            img_paths = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', md_content)
            for img_path in img_paths:
                img_path = img_path.strip()
                if img_path.startswith(("http://", "https://")):
                    continue
                abs_img_path = (local_md_path.parent / img_path).resolve()
                if abs_img_path.is_file():
                    remote_img_path = f"~/ordo-publish/temp/{img_path}"
                    remote_dir = os.path.dirname(remote_img_path)
                    mkdir_img_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", f"mkdir -p {remote_dir}"]
                    subprocess.run(mkdir_img_cmd, check=True, capture_output=True)
                    scp_img_cmd = ["scp"] + scp_opts + [str(abs_img_path), f"{vps_user}@{vps_ip}:{remote_img_path}"]
                    subprocess.run(scp_img_cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return {
                "platform": self.platform,
                "command": " ".join(local_cmd),
                "returncode": exc.returncode,
                "stdout": "",
                "stderr": f"Failed to upload draft/cover/images to VPS via SCP: {stderr}",
            }

        # 4. Construct remote args
        remote_args = []
        for arg in local_cmd[1:]:  # skip local python path
            if arg == str(local_md_path):
                remote_args.append(remote_md_path)
            elif local_cover_path and arg == str(local_cover_path):
                remote_args.append(remote_cover_path)
            elif arg == str(self.script_path):
                remote_args.append(self.script_name)
            else:
                remote_args.append(arg)

        # Build remote shell command: checks for virtual env python, else falls back to python3
        remote_shell_cmd = (
            f"if [ -f ~/ordo-publish/.venv/bin/python ]; then "
            f"~/ordo-publish/.venv/bin/python {' '.join(remote_args)}; "
            f"else python3 {' '.join(remote_args)}; fi"
        )
        ssh_exec_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", f"cd ~/ordo-publish && {remote_shell_cmd}"]

        # 5. Execute command on VPS
        timeout_seconds = 180
        try:
            result = subprocess.run(
                ssh_exec_cmd,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            # Cleanup remote files
            cleanup_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", "rm -rf ~/ordo-publish/temp"]
            subprocess.run(cleanup_cmd, capture_output=True)

            stdout = (exc.stdout or exc.output or "").strip()
            stderr = (exc.stderr or "").strip()
            timeout_message = f"Remote process timed out after {timeout_seconds} seconds"
            return {
                "platform": self.platform,
                "command": " ".join(local_cmd),
                "returncode": 124,
                "stdout": stdout,
                "stderr": f"{timeout_message}\n{stderr}" if stderr else timeout_message,
                "timed_out": True,
            }

        # Cleanup remote files
        cleanup_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", "rm -rf ~/ordo-publish/temp"]
        subprocess.run(cleanup_cmd, capture_output=True)

        from tiandi_engine.platforms.base import _extract_smoke_state
        stdout, stdout_state = _extract_smoke_state(result.stdout.strip())
        stderr, stderr_state = _extract_smoke_state(result.stderr.strip())
        smoke_state = {}
        if stdout_state:
            smoke_state.update(stdout_state)
        if stderr_state:
            smoke_state.update(stderr_state)

        return {
            "platform": self.platform,
            "command": " ".join(local_cmd),
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "current_url": str(smoke_state.get("current_url", "")),
            "page_state": str(smoke_state.get("page_state", "")),
            "smoke_step": str(smoke_state.get("smoke_step", "")),
        }
