"""Start the platform process."""

import sys
from pathlib import Path

import uvicorn

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

if __name__ == "__main__":
    uvicorn.run("core_app:app", host="0.0.0.0", port=8001, reload=True)
