import os


def resolve_node_executable(default: str = "node") -> str:
    return os.environ.get("ORDO_NODE") or default
