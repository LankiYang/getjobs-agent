"""Track per-user activity and reclaim idle runner processes."""

import os
import threading
import time

DEFAULT_IDLE_MINUTES = 15


def _idle_seconds() -> int:
    raw = os.environ.get("RUNNER_IDLE_MINUTES", "").strip()
    if not raw:
        return DEFAULT_IDLE_MINUTES * 60
    try:
        minutes = float(raw)
    except ValueError:
        return DEFAULT_IDLE_MINUTES * 60
    return max(0, int(minutes * 60))


IDLE_SECONDS = _idle_seconds()

_lock = threading.Lock()
_last: dict[int, float] = {}
_busy: dict[int, int] = {}
_vnc: dict[int, int] = {}


def touch(user_id: int) -> None:
    with _lock:
        _last[user_id] = time.time()


def begin_busy(user_id: int) -> None:
    with _lock:
        _busy[user_id] = _busy.get(user_id, 0) + 1
        _last[user_id] = time.time()


def end_busy(user_id: int) -> None:
    with _lock:
        count = _busy.get(user_id, 0) - 1
        if count > 0:
            _busy[user_id] = count
        else:
            _busy.pop(user_id, None)
        _last[user_id] = time.time()


def vnc_open(user_id: int) -> None:
    with _lock:
        _vnc[user_id] = _vnc.get(user_id, 0) + 1
        _last[user_id] = time.time()


def vnc_close(user_id: int) -> None:
    with _lock:
        count = _vnc.get(user_id, 0) - 1
        if count > 0:
            _vnc[user_id] = count
        else:
            _vnc.pop(user_id, None)
        _last[user_id] = time.time()


def _claim_idle(now: float) -> list[int]:
    with _lock:
        claimed = [
            user_id
            for user_id, last in list(_last.items())
            if now - last >= IDLE_SECONDS
            and _busy.get(user_id, 0) == 0
            and _vnc.get(user_id, 0) == 0
        ]
        for user_id in claimed:
            _last.pop(user_id, None)
        return claimed


def start_reaper(stop_runner, interval_seconds: int = 60) -> None:
    if IDLE_SECONDS <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(interval_seconds)
            for user_id in _claim_idle(time.time()):
                try:
                    stop_runner(user_id)
                except Exception:
                    pass

    threading.Thread(target=loop, daemon=True, name="runner-idle-reaper").start()