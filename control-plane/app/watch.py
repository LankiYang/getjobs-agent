"""Background watcher: enforce per-run delivery limits and push proactive messages."""

import threading
import time

from . import runners
from .channels import CHANNELS
from .db import get_conn
from .events import hub

POLL_SECONDS = 4
MAX_WATCH_SECONDS = 2 * 3600


def save_assistant_message(user_id: int, session_id: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_messages(user_id, session_id, role, content, created_at)
               VALUES(?, ?, 'assistant', ?, datetime('now'))""",
            (user_id, session_id, content),
        )


def latest_session(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT session_id FROM agent_messages WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["session_id"] if row else None


def _get_json(user_id: int, path: str) -> dict:
    response = runners.request(user_id, "GET", path, timeout=10)
    if response.status_code >= 400:
        raise RuntimeError(f"{path} -> {response.status_code}")
    return response.json() or {}


def _recent_delivered(user_id: int, prefix: str, limit: int = 3) -> list[str]:
    try:
        data = _get_json(user_id, f"{prefix}/list?statuses=已投递&page=1&size={limit}")
    except Exception:
        return []
    items = data.get("items") or []
    names = []
    for item in items[:limit]:
        company = item.get("companyName") or item.get("compName") or ""
        job = item.get("jobName") or item.get("jobTitle") or ""
        label = " · ".join(part for part in (company, job) if part)
        if label:
            names.append(label)
    return names


def _summary(user_id: int, channel: str, baseline: int | None, target: int | None, reason: str) -> str:
    entry = CHANNELS.get(channel)
    label = entry.label if entry else channel
    prefix = entry.prefix if entry else f"/api/{channel}"

    try:
        stats = _get_json(user_id, f"{prefix}/stats")
        kpi = stats.get("kpi") or {}
        delivered = int(kpi.get("delivered") or 0)
        filtered = int(kpi.get("filtered") or 0)
        pending = int(kpi.get("pending") or 0)
        failed = int(kpi.get("failed") or 0)
    except Exception:
        delivered = filtered = pending = failed = 0

    new_count = max(0, delivered - baseline) if baseline is not None else None

    if reason == "target":
        title = f"【{label}】本轮投递已达到目标（{target} 个），已自动停止。"
    elif reason == "finished":
        title = f"【{label}】本轮投递已结束。"
    else:
        title = f"【{label}】投递监控已超时（2 小时），已暂停。"
        try:
            runners.request(user_id, "POST", f"{prefix}/stop", timeout=15)
        except Exception:
            pass

    lines = [title, ""]
    if new_count is not None:
        lines.append(f"本轮新增成功投递：{new_count} 个")
    lines.append(f"当前总计：已投递 {delivered} · 已过滤 {filtered} · 待处理 {pending} · 失败 {failed}")

    recent = _recent_delivered(user_id, prefix)
    if recent:
        lines.append("")
        lines.append("最近投递：")
        lines.extend(f"- {item}" for item in recent)

    lines.append("")
    lines.append("需要复盘、调整条件或继续下一轮，直接说。")
    return "\n".join(lines)


def _push(user_id: int, session_id: str, message: str) -> None:
    save_assistant_message(user_id, session_id, message)
    hub.publish(
        user_id,
        {
            "type": "agent_message",
            "sessionId": session_id,
            "message": message,
        },
    )


def _watch(user_id: int, channel: str, session_id: str, baseline: int | None, target: int | None) -> None:
    entry = CHANNELS.get(channel)
    if not entry:
        return
    prefix = entry.prefix
    started = time.time()

    while time.time() - started < MAX_WATCH_SECONDS:
        time.sleep(POLL_SECONDS)
        try:
            status = _get_json(user_id, f"{prefix}/status")
            stats = _get_json(user_id, f"{prefix}/stats")
        except Exception:
            continue

        running = bool(status.get("isRunning"))
        delivered = int(((stats.get("kpi") or {}).get("delivered") or 0))

        if target and baseline is not None and delivered - baseline >= target:
            try:
                runners.request(user_id, "POST", f"{prefix}/stop", timeout=15)
            except Exception:
                pass
            time.sleep(2)
            _push(user_id, session_id, _summary(user_id, channel, baseline, target, "target"))
            return

        if not running:
            _push(user_id, session_id, _summary(user_id, channel, baseline, target, "finished"))
            return

    _push(user_id, session_id, _summary(user_id, channel, baseline, target, "timeout"))


def start_watch(
    user_id: int,
    channel: str,
    session_id: str | None,
    baseline: int | None,
    target: int | None = None,
) -> None:
    session_id = session_id or latest_session(user_id)
    if not session_id:
        return
    thread = threading.Thread(
        target=_watch,
        args=(user_id, channel, session_id, baseline, target),
        daemon=True,
        name=f"watch-{user_id}-{channel}",
    )
    thread.start()