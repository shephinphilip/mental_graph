"""Dump the live FastAPI OpenAPI document."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app

OUT_JSON = ROOT / "docs" / "api" / "openapi.json"
OUT_YAML = ROOT / "docs" / "api" / "openapi.yaml"


def main() -> None:
    spec = app.openapi()
    OUT_JSON.write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
    # Keep YAML as a pointer to the generated JSON so it cannot drift.
    OUT_YAML.write_text(
        (
            "# Generated companion. Do not edit paths by hand.\n"
            "# Full spec: docs/api/openapi.json (FastAPI app.openapi()).\n"
            "# Regenerate: python scripts/export_openapi.py\n"
            "openapi: 3.1.0\n"
            "info:\n"
            f"  title: {spec.get('info', {}).get('title', 'Zenark API')}\n"
            f"  version: {spec.get('info', {}).get('version', '0.4.0')}\n"
            f"  description: Live dump with {len(spec.get('paths', {}))} paths including /api/v1 aliases.\n"
            "servers:\n"
            "  - url: http://localhost:8000\n"
            "    description: Local development\n"
            "paths:\n"
            "  $ref: \"./openapi.json#/paths\"\n"
            "components:\n"
            "  $ref: \"./openapi.json#/components\"\n"
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT_JSON} paths={len(spec.get('paths', {}))}")


if __name__ == "__main__":
    main()
