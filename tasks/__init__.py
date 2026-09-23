"""daily_tasks is the only task store. This package is its only writer."""

from tasks.store import persist_report_tasks

__all__ = ["persist_report_tasks"]
