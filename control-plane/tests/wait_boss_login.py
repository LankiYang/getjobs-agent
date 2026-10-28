"""Poll a runner until Boss login is detected. Exits 0 on login, 2 on timeout."""

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("RUNNER_BASE", "http://127.0.0.1:19100").rstrip("/")
SECRET = os.environ.get("RUNNER_SECRET", "")
DEADLINE = time.time() + float(os.environ.get("WAIT_SECONDS", "1800"))

while time.time() < DEADLINE:
    try:
        request = urllib.request.Request(
            f"{BASE}/api/boss/status",
            headers={"X-Offerbuddy-Worker-Secret": SECRET},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            status = json.load(response)
        if status.get("isLoggedIn"):
            print("LOGGED_IN", flush=True)
            sys.exit(0)
    except Exception as exc:
        print(f"status check failed: {exc}", flush=True)
    time.sleep(5)

print("TIMEOUT", flush=True)
sys.exit(2)