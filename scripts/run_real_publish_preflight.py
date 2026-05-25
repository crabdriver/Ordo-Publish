#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tiandi_engine.workbench.preflight import build_real_publish_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all-platform preflight for real publish inputs.")
    parser.add_argument("--source-dir", required=True, help="Article source directory")
    parser.add_argument("--cover-dir", help="Cover image directory")
    parser.add_argument(
        "--platforms",
        default="wechat,zhihu,toutiao,jianshu,yidian",
        help="Comma-separated platform list",
    )
    parser.add_argument("--seed", type=int, default=20260329, help="Deterministic assignment seed")
    parser.add_argument("--report-id", help="Optional fixed report id")
    args = parser.parse_args()

    platforms = tuple(item.strip() for item in args.platforms.split(",") if item.strip())
    payload = build_real_publish_preflight(
        Path(__file__).resolve().parent.parent,
        source_path=args.source_dir,
        cover_dir=args.cover_dir,
        platforms=platforms,
        seed=args.seed,
        report_id=args.report_id,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
