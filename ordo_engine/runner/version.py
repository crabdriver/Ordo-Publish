import subprocess
from pathlib import Path
from typing import Tuple, Optional


def get_local_git_commit(repo_path: Optional[str] = None) -> Optional[str]:
    """Retrieve current local git commit hash.
    
    Args:
        repo_path: Path to the local git repository. If None, uses cwd.
    """
    try:
        cwd = str(repo_path) if repo_path else None
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
        return res.stdout.strip()
    except Exception as e:
        print(f"[WARN] Failed to read local git commit: {e}")
        return None


def get_remote_git_commit(
    ssh_host: str,
    ssh_user: str = "root",
    remote_path: str = "/root/ordo-publish-runtime/repo",
) -> Optional[str]:
    """Retrieve remote git commit hash from VPS via SSH."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        f"{ssh_user}@{ssh_host}",
        f"cd {remote_path} && git rev-parse HEAD"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception as e:
        print(f"[WARN] Failed to read remote git commit via SSH: {e}")
        return None


def verify_codebase_version(
    ssh_host: str,
    ssh_user: str = "root",
    remote_path: str = "/root/ordo-publish-runtime/repo",
    local_repo_path: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Checks if local and remote commit hashes match.
    
    Args:
        ssh_host: VPS hostname or IP.
        ssh_user: SSH username.
        remote_path: Path to the repo on VPS.
        local_repo_path: Path to the local git repository. If None, uses cwd.
    
    Returns:
        (is_matching, local_commit, remote_commit)
    """
    local_commit = get_local_git_commit(repo_path=local_repo_path)
    remote_commit = get_remote_git_commit(ssh_host, ssh_user, remote_path)
    
    if not local_commit or not remote_commit:
        return False, local_commit, remote_commit
        
    return local_commit == remote_commit, local_commit, remote_commit

