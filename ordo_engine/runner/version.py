import hashlib
import shlex
import subprocess
from pathlib import Path
from typing import Tuple, Optional


def _runtime_source_files(root: Path):
    files = list(root.glob("*.py")) + list(root.glob("*.mjs"))
    files.extend((root / "ordo_engine").rglob("*.py") if (root / "ordo_engine").is_dir() else ())
    return sorted((p for p in files if p.is_file()), key=lambda p: p.relative_to(root).as_posix())


def get_local_code_fingerprint(repo_path) -> Optional[str]:
    try:
        root = Path(repo_path).resolve()
        digest = hashlib.sha256()
        for path in _runtime_source_files(root):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()
    except Exception as e:
        print(f"[WARN] Failed to fingerprint local runtime code: {e}")
        return None


def get_remote_code_fingerprint(ssh_host, ssh_user="root", remote_path="/root/ordo-publish") -> Optional[str]:
    script = (
        "import hashlib,pathlib;root=pathlib.Path('.').resolve();"
        "fs=sorted([*root.glob('*.py'),*root.glob('*.mjs'),*root.joinpath('ordo_engine').rglob('*.py')],"
        "key=lambda p:p.relative_to(root).as_posix());h=hashlib.sha256();"
        "[(h.update(p.relative_to(root).as_posix().encode()),h.update(b'\\0'),h.update(p.read_bytes()),h.update(b'\\0')) for p in fs if p.is_file()];"
        "print(h.hexdigest())"
    )
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", f"{ssh_user}@{ssh_host}",
           f"cd {shlex.quote(remote_path)} && python3 -c {shlex.quote(script)}"]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except Exception as e:
        print(f"[WARN] Failed to fingerprint remote runtime code via SSH: {e}")
        return None


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
    remote_path: str = "/root/ordo-publish",
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
    remote_path: str = "/root/ordo-publish",
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
        local_fingerprint = get_local_code_fingerprint(local_repo_path or Path.cwd())
        remote_fingerprint = get_remote_code_fingerprint(ssh_host, ssh_user, remote_path)
        return bool(local_fingerprint and local_fingerprint == remote_fingerprint), local_fingerprint, remote_fingerprint
        
    return local_commit == remote_commit, local_commit, remote_commit
