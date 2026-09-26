"""Throwaway live check of the mood and habit routes."""

import requests

B = "http://127.0.0.1:8000"

r = requests.post(
    B + "/auth/login",
    json={"email": "ananya.rao@zenark.demo", "password": "Zenark@123"},
    timeout=20,
)
print("login", r.status_code)
body = r.json()
tok = body.get("access_token") or body.get("token")
h = {"Authorization": "Bearer " + tok}

print(
    "mood",
    requests.post(
        B + "/api/mood",
        headers=h,
        json={
            "mood": "anxious",
            "score": 3,
            "note": "before the mock",
            "input_format": "EMOJI",
            "client_event_id": "verify-1",
        },
        timeout=20,
    ).json(),
)
print(
    "duplicate",
    requests.post(
        B + "/api/mood",
        headers=h,
        json={"mood": "anxious", "client_event_id": "verify-1"},
        timeout=20,
    ).json()["duplicate"],
)
print("recent", requests.get(B + "/api/mood/recent", headers=h, timeout=20).json()["moods"][:2])

hb = requests.post(
    B + "/api/habits", headers=h, json={"title": "Morning walk", "frequency": "daily"}, timeout=20
).json()
print("habit", hb)
print(
    "check-in",
    requests.post(
        B + "/api/habits/" + hb["habit_id"] + "/check-in", headers=h, json={}, timeout=20
    ).json(),
)
print(
    "streaks on",
    requests.post(B + "/api/habits/streaks", headers=h, json={"show_streaks": True}, timeout=20).json(),
)
print("list", requests.get(B + "/api/habits", headers=h, timeout=20).json())
print(
    "cross-user",
    requests.post(
        B + "/api/mood", headers=h, json={"mood": "x", "user_id": "someone_else"}, timeout=20
    ).status_code,
)
