import json
import os
import sys
import time
import urllib.request

BASE_URL = os.environ.get("GETJOBS_BASE_URL", "http://localhost:9527").rstrip("/")
DEADLINE = time.time() + float(os.environ.get("WAIT_SECONDS", "900"))

while time.time() < DEADLINE:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/boss/status", timeout=10) as response:
            status = json.load(response)
        if status.get("isLoggedIn"):
            print("LOGGED_IN", flush=True)
            sys.exit(0)
    except Exception as exc:
        print(f"status check failed: {exc}", flush=True)
    time.sleep(5)

print("TIMEOUT", flush=True)
sys.exit(2)