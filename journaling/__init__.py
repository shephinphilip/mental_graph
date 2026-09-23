"""Journal entries. This package is the only writer of journal_entries."""

from journaling.service import create_journal_entry

__all__ = ["create_journal_entry"]
