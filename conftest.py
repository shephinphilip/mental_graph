"""Put each application on the import path without hiding one ``tests`` package."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT / "dashboard", _ROOT / "backend-core", _ROOT / "backend-agent"):
    _text = str(_path)
    if _text in sys.path:
        sys.path.remove(_text)
    sys.path.insert(0, _text)
