import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PINNED_RUNTIME = "patchright==1.61.2"


def _requirement_lines() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _pyproject_dependencies() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert block is not None
    return re.findall(r'["\']([^"\']+)["\']', block.group(1))


def _browser_runtimes(dependencies: list[str]) -> list[str]:
    return [
        dependency
        for dependency in dependencies
        if re.split(r"[<>=!~\[]", dependency, maxsplit=1)[0].strip().lower()
        in {"patchright", "playwright"}
    ]


def test_requirements_declares_one_pinned_browser_runtime():
    assert _browser_runtimes(_requirement_lines()) == [PINNED_RUNTIME]


def test_pyproject_declares_one_pinned_browser_runtime():
    assert _browser_runtimes(_pyproject_dependencies()) == [PINNED_RUNTIME]
