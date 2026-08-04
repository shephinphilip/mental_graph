"""
run.py — Development Server Launcher
=====================================

Convenience script for starting the FastAPI backend in development mode
without memorising the full uvicorn CLI flags.

Usage
-----
    python run.py

This is equivalent to running::

    uvicorn app:app --host 0.0.0.0 --port 8000 --reload

``--reload`` enables hot-reloading: uvicorn watches the working directory
for file changes and automatically restarts the server.  Do NOT use
``--reload`` in production — it adds file-system watching overhead and
may cause unexpected restarts.

Production Deployment
---------------------
For production, remove ``--reload`` and set appropriate worker counts::

    uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4

Or use Gunicorn with the uvicorn worker class::

    gunicorn app:app -k uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:8000
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app:app",           # Module path: "filename:FastAPI_instance_name"
        host="0.0.0.0",      # Bind to all network interfaces (required for Docker / LAN)
        port=8000,           # Default FastAPI/uvicorn port
        reload=True,         # Hot-reload on file changes (development only)
    )
