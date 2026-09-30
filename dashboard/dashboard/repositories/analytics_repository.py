"""Analytics reads are the shared school bundle. Formulas live in metrics."""

from dashboard.repositories.dashboard_repository import SchoolBundle, load_bundle

__all__ = ["SchoolBundle", "load_bundle"]
