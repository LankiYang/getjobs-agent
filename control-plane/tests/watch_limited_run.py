"""Watch a real delivery run and stop it after N successful applications."""

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("CONTROL_BASE", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ["CONTROL_TOKEN"]
STOP_AFTER = int(os.environ.get("STOP_AFTER", "3"))
DEADLINE = time.time() + float(os.environ.get("WAIT_SECONDS", "1200"))


def call(method: str, path: str):
    request = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=b"{}" if method == "POST" else None,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


while time.time() < DEADLINE:
    try:
        stats = call("GET", "/api/channels/boss/stats")
        status = call("GET", "/api/channels/boss/status")
    except Exception as exc:
        print(f"poll failed: {exc}", flush=True)
        time.sleep(3)
        continue

    delivered = (stats.get("kpi") or {}).get("delivered") or 0
    running = bool(status.get("isRunning"))
    print(f"delivered={delivered} running={running}", flush=True)

    if delivered >= STOP_AFTER:
        call("POST", "/api/channels/boss/stop")
        print("STOPPED_AFTER_TARGET", flush=True)
        sys.exit(0)
    if not running:
        print("FINISHED", flush=True)
        sys.exit(0)

    time.sleep(3)

try:
    call("POST", "/api/channels/boss/stop")
except Exception:
    pass
print("TIMEOUT", flush=True)
sys.exit(2)