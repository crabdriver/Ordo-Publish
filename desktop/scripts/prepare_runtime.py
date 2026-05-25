#!/usr/bin/env python3

from __future__ import annotations

import fnmatch
import json
import hashlib
import shutil
import site
import sys
import tarfile
from pathlib import Path
from typing import Mapping, Sequence


DESKTOP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DESKTOP_DIR.parent
MANIFEST_PATH = DESKTOP_DIR / "runtime-manifest.json"
OUTPUT_ROOT = DESKTOP_DIR / "runtime-dist"
DEFAULT_REQUIRED_SITE_PACKAGE_NAMES = {
    "PIL",
    "bs4",
    "certifi",
    "charset_normalizer",
    "dotenv",
    "idna",
    "markdown",
    "requests",
    "soupsieve",
    "urllib3",
    "pypdf",
    "docx",
    "lxml",
    "yaml",
    "_yaml",
    "typing_extensions.py",
}


def load_manifest(path: Path = MANIFEST_PATH) -> Mapping[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_repo_copy_jobs(repo_root: Path, manifest: Mapping[str, object]) -> list[tuple[Path, Path]]:
    include = manifest.get("include") or []
    jobs = []
    for raw in include:
        relative = Path(str(raw))
        jobs.append((repo_root / relative, relative))
    return jobs


def _make_ignore_fn(exclude_globs: list[str]):
    def _ignore(directory: str, entries: list[str]) -> set[str]:
        ignored = set()
        for entry in entries:
            rel = str(Path(directory) / entry)
            for pattern in exclude_globs:
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(entry, pattern):
                    ignored.add(entry)
                    break
        return ignored
    return _ignore


def copy_path(src: Path, dest: Path, exclude_globs: list[str] | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        ignore_fn = _make_ignore_fn(exclude_globs) if exclude_globs else None
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=ignore_fn)
    else:
        shutil.copy2(src, dest)


def prune_site_packages(target_site_packages: Path, required_site_package_names: set[str]) -> None:
    if not target_site_packages.exists():
        return
    for child in target_site_packages.iterdir():
        if child.name in required_site_package_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def normalize_permissions(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod(0o755)
        elif path.suffix in {".dylib", ".so"}:
            path.chmod(0o755)
        elif mode & 0o111:
            path.chmod(0o755)
        else:
            path.chmod(0o644)


def current_node_metadata() -> dict[str, object]:
    node_path = shutil.which("node")
    if not node_path:
        raise RuntimeError("未找到 node，可通过 PATH 提供 node 运行时以打包浏览器自动化链路。")
    executable = Path(node_path).resolve()
    return {
        "prefix": str(executable.parent.parent),
        "executable": str(executable),
    }


def current_python_metadata() -> dict[str, object]:
    version_dir_name = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = []
    seen = set()
    for raw in [*site.getsitepackages(), site.getusersitepackages()]:
        if not raw:
            continue
        path = Path(raw).resolve()
        if path.exists() and path not in seen:
            site_packages.append(str(path))
            seen.add(path)
    return {
        "base_prefix": str(Path(sys.base_prefix).resolve()),
        "executable": str(Path(sys.executable).resolve()),
        "site_packages": site_packages,
        "version_dir_name": version_dir_name,
    }


def prepare_python_runtime(runtime_dir: Path, manifest: Mapping[str, object], python_metadata: Mapping[str, object]) -> dict[str, object]:
    python_root = runtime_dir / str((manifest.get("bundled_python") or {}).get("root") or "python")
    base_prefix = Path(str(python_metadata["base_prefix"])).resolve()
    executable = Path(str(python_metadata["executable"])).resolve()
    version_dir_name = str(python_metadata["version_dir_name"])
    site_packages = [Path(str(item)).resolve() for item in python_metadata.get("site_packages", [])]
    required_site_package_names = {
        str(item)
        for item in python_metadata.get("required_site_package_names", DEFAULT_REQUIRED_SITE_PACKAGE_NAMES)
    }

    shutil.copytree(base_prefix, python_root, dirs_exist_ok=True)

    target_executable = runtime_dir / str((manifest.get("bundled_python") or {}).get("executable") or "python/bin/python3")
    target_executable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, target_executable)

    target_site_packages = python_root / "lib" / version_dir_name / "site-packages"
    target_site_packages.mkdir(parents=True, exist_ok=True)
    prune_site_packages(target_site_packages, required_site_package_names)
    for source_dir in site_packages:
        if not source_dir.exists():
            continue
        if source_dir.is_relative_to(base_prefix):
            continue
        for child in source_dir.iterdir():
            if child.name not in required_site_package_names:
                continue
            copy_path(child, target_site_packages / child.name)

    return {
        "base_prefix": str(base_prefix),
        "executable": str(executable),
        "version_dir_name": version_dir_name,
        "site_packages": [str(path) for path in site_packages],
        "required_site_package_names": sorted(required_site_package_names),
    }


def prepare_node_runtime(runtime_dir: Path, manifest: Mapping[str, object], node_metadata: Mapping[str, object]) -> dict[str, object]:
    node_root = runtime_dir / str((manifest.get("bundled_node") or {}).get("root") or "node")
    prefix = Path(str(node_metadata["prefix"])).resolve()
    executable = Path(str(node_metadata["executable"])).resolve()

    shutil.copytree(prefix, node_root, dirs_exist_ok=True)

    target_executable = runtime_dir / str((manifest.get("bundled_node") or {}).get("executable") or "node/bin/node")
    target_executable.parent.mkdir(parents=True, exist_ok=True)
    if not target_executable.exists():
        shutil.copy2(executable, target_executable)
    normalize_permissions(node_root)

    return {
        "prefix": str(prefix),
        "executable": str(executable),
    }


def write_node_archive(runtime_dir: Path, manifest: Mapping[str, object]) -> str:
    node_root = runtime_dir / str((manifest.get("bundled_node") or {}).get("root") or "node")
    archive_path = runtime_dir / "node-runtime.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(node_root, arcname=node_root.name)
    return str(archive_path)


def compute_runtime_fingerprint(runtime_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(runtime_dir.rglob("*")):
        if not path.is_file() or path.name == "runtime-metadata.json":
            continue
        digest.update(str(path.relative_to(runtime_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def prepare_runtime(
    *,
    repo_root: Path,
    output_root: Path,
    manifest: Mapping[str, object],
    python_metadata: Mapping[str, object] | None = None,
    node_metadata: Mapping[str, object] | None = None,
) -> Path:
    runtime_dir = output_root / str(manifest["runtime_root"])
    if output_root.exists():
        shutil.rmtree(output_root)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    bundled_repo_root = runtime_dir / str(manifest["bundled_repo_root"])
    exclude_globs = list(manifest.get("exclude_globs") or [])
    for src, relative in build_repo_copy_jobs(repo_root, manifest):
        copy_path(src, bundled_repo_root / relative, exclude_globs=exclude_globs)

    python_info = prepare_python_runtime(runtime_dir, manifest, python_metadata or current_python_metadata())
    node_info = prepare_node_runtime(runtime_dir, manifest, node_metadata or current_node_metadata())
    node_archive = write_node_archive(runtime_dir, manifest)
    normalize_permissions(runtime_dir)
    metadata = {
        "runtime_root": str(runtime_dir),
        "repo_root": str(bundled_repo_root),
        "python": python_info,
        "node": node_info,
        "node_archive": node_archive,
        "source_fingerprint": compute_runtime_fingerprint(runtime_dir),
        "include": list(manifest.get("include") or []),
        "exclude_globs": list(manifest.get("exclude_globs") or []),
    }
    (runtime_dir / "runtime-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_dir


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv or sys.argv[1:]
    runtime_dir = prepare_runtime(
        repo_root=REPO_ROOT,
        output_root=OUTPUT_ROOT,
        manifest=load_manifest(),
    )
    print(runtime_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
