"""Post-chat extraction wrapper.

Today this still runs as a FastAPI ``BackgroundTasks`` job inside the API
process. That is not safe for a multi-instance production deployment: a
restart drops in-flight work, and two replicas can both extract the same
turn if the client retries.

Callers must keep using the existing ``run_background_extraction``
idempotency (insight + graph upserts). This module does not start a second
extractor.
"""

from services.extraction import run_background_extraction

__all__ = ["run_background_extraction"]
