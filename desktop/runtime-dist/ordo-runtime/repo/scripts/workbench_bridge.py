#!/usr/bin/env python3

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from datetime import datetime, timezone

from tiandi_engine.results.publish_records import append_publish_record_at_path  # noqa: E402
from tiandi_engine.workbench.bridge import handle_bridge_command  # noqa: E402


def _append_structured_log(entry: dict) -> None:
    """Append one NDJSON line under repo logs/ for local diagnostics."""
    path = ROOT_DIR / "logs" / "ordo-bridge.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_publish_record(result):
    append_publish_record_at_path(ROOT_DIR / "publish_records.csv", result)


def _emit_error(message):
    json.dump({"ok": False, "error": message}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError) as exc:
        _emit_error(f"无法解析输入: {exc}")
        sys.exit(1)

    try:
        _append_structured_log({"event": "bridge_request", "command": payload.get("command")})
    except OSError:
        pass

    try:
        if payload.get("command") == "run_publish_job_stream":
            from tiandi_engine.workbench.bridge import run_publish_job

            result = run_publish_job(
                ROOT_DIR,
                payload["plan"],
                append_record=append_publish_record,
                event_sink=lambda event: (json.dump(event, sys.stdout, ensure_ascii=False), sys.stdout.write("\n"), sys.stdout.flush()),
            )
            json.dump({"type": "command_result", "payload": result}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return
        response = handle_bridge_command(ROOT_DIR, payload, append_record=append_publish_record)
        json.dump(response, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    except Exception as exc:
        try:
            _append_structured_log(
                {
                    "event": "bridge_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        except OSError:
            pass
        _emit_error(f"执行异常: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
