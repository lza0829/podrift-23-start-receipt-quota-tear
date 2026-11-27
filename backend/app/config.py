from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "舱隙"
DB_PATH = Path(os.environ.get("PODRIFT_DB", str(Path(__file__).resolve().parents[2] / "data" / "podrift.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
