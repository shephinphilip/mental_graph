"""Locust scenarios for a live API. Not a capacity certificate.

Run against a running server (never start this inside the API process):

    locust -f load_tests/locustfile.py --host http://127.0.0.1:8000

These paths hit PUBLIC or AUTHENTICATED endpoints. Chat/report tasks will
call Bedrock if you enable them — keep those users at 0 unless you intend
to spend model quota.

Measured results belong in docs/PRODUCTION_READINESS_REPORT.md after a run.
Until then the status is NOT TESTED.
"""

from locust import HttpUser, between, task


class HealthUser(HttpUser):
    wait_time = between(0.1, 0.5)
    weight = 10

    @task
    def live(self):
        self.client.get("/health/live")

    @task
    def ready(self):
        self.client.get("/health/ready")


class ReadUser(HttpUser):
    """Authenticated read mix. Requires LOCUST_TOKEN in the environment."""

    wait_time = between(1, 3)
    weight = 3

    def on_start(self):
        import os

        token = os.environ.get("LOCUST_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    @task
    def sleep_recent(self):
        if self.headers:
            self.client.get("/api/sleep/recent", headers=self.headers)

    @task
    def mood_recent(self):
        if self.headers:
            self.client.get("/api/mood/recent", headers=self.headers)

    @task
    def journal_recent(self):
        if self.headers:
            self.client.get("/journal/recent-entries", headers=self.headers)

    @task
    def list_tasks(self):
        if self.headers:
            user = __import__("os").environ.get("LOCUST_USER_ID", "")
            if user:
                self.client.get(f"/api/report_card/tasks/{user}", headers=self.headers)
