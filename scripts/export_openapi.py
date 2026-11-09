"""Write the API's OpenAPI schema to frontend/openapi.json (no server needed).

Usage: .venv/bin/python scripts/export_openapi.py
Then:  cd frontend && npm run gen:api
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api  # noqa: E402

OUT = ROOT / "frontend" / "openapi.json"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        schema = api.create_app(Path(tmp) / "openapi.db", token=None).openapi()
    OUT.write_text(json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
