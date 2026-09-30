"""Start the school dashboard API."""

import sys
from pathlib import Path

import uvicorn

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path[:0] = [str(_HERE), str(_ROOT / "backend-core")]

if __name__ == "__main__":
    uvicorn.run("dashboard_app:app", host="0.0.0.0", port=8002, reload=True)
