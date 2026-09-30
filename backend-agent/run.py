"""Start the mental-health and Exam Buddy API."""

import sys
from pathlib import Path

import uvicorn

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent / "backend-core"
_paths = [str(_HERE)]
if (_CORE / "pyproject.toml").is_file():
    _paths.append(str(_CORE))
sys.path[:0] = _paths

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
