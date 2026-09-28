"""
Settings package.

Canonical module: ``from config.config import logger``.
``from config import settings`` / ``from config import get_settings`` remain
valid so older imports keep working after the root ``config.py`` shim was
removed (it collided with this package on Windows/Python).
"""

from config.config import Settings, get_settings, logger, settings

__all__ = ["Settings", "get_settings", "logger", "settings"]
