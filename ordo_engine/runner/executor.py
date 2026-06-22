from abc import ABC, abstractmethod
import re
import shlex
import subprocess
from typing import List, Dict, Optional

_SAFE_ENV_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class BaseExecutor(ABC):
    @abstractmethod
    def execute(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 180,
    ) -> dict:
        """
        Execute a command and return a standardized execution dictionary:
        {
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "timed_out": bool,
            "timeout_seconds": int
        }
        """
        pass


class LocalSubprocessExecutor(BaseExecutor):
    def execute(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 180,
    ) -> dict:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "timed_out": False,
                "timeout_seconds": timeout,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "").strip()
            stderr = str(exc.stderr or "").strip()
            return {
                "returncode": 124,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": True,
                "timeout_seconds": timeout,
            }


class RemoteSubprocessExecutor(BaseExecutor):
    def __init__(
        self,
        ssh_host: str,
        ssh_user: str = "root",
        remote_cwd: Optional[str] = None,
        proxy_tunnel: str = "7890:127.0.0.1:7890",
    ):
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.remote_cwd = remote_cwd
        self.proxy_tunnel = proxy_tunnel

    def execute(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 180,
    ) -> dict:
        # 1. Format the command for remote execution
        remote_cmd = shlex.join(command)
        
        # Determine the working directory on remote
        effective_cwd = self.remote_cwd or cwd
        if effective_cwd:
            remote_cmd = f"cd {shlex.quote(effective_cwd)} && {remote_cmd}"

        # 2. Prepend env declarations to the remote command
        if env:
            env_exports = []
            for k, v in env.items():
                if not _SAFE_ENV_KEY.match(k):
                    raise ValueError(f"Unsafe env variable name rejected: {k!r}")
                env_exports.append(f"export {k}={shlex.quote(str(v))}")
            if env_exports:
                remote_cmd = " && ".join(env_exports) + " && " + remote_cmd

        # 3. Assemble local SSH command
        ssh_command = ["ssh"]
        if self.proxy_tunnel:
            ssh_command.extend(["-R", self.proxy_tunnel])

        # BatchMode ensures it fails fast without password prompt if keys aren't configured
        ssh_command.extend([
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            f"{self.ssh_user}@{self.ssh_host}",
            remote_cmd
        ])

        try:
            result = subprocess.run(
                ssh_command,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "timed_out": False,
                "timeout_seconds": timeout,
            }
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "").strip()
            stderr = str(exc.stderr or "").strip()
            return {
                "returncode": 124,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": True,
                "timeout_seconds": timeout,
            }
