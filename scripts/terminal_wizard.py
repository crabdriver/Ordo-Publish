#!/usr/bin/env python3

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tiandi_engine.cli.app import main


if __name__ == "__main__":
    os.environ.setdefault("ORDO_REPO_TEMPLATE_ROOT", str(ROOT))
    raise SystemExit(main())
