"""Durable one-shot execution of explicitly requested application operations.

Integration contract: call start() once after database initialization and stop() on
shutdown. execute(user_id, 'schedule', {operation, channel, due_at, max_count?})
returns {ok, job}; 'list' with {} returns {ok, jobs} including history; 'cancel'
with {id} cancels an owned pending job and returns {ok, job}. Errors are
{ok: false, error}. Times are timezone-aware ISO 8601 and normalized to UTC.
Only Boss supports max_count (1-100), and only on start_application.
The caller must establish explicit user intent and provide all required fields;
this module does not infer intent, channel, operation or time. It does not
schedule interviews or other operations. Jobs use the owner's runner and
saved configuration at execution time.

Statuses: pending, cancelled, unknown, started, succeeded, stopping, failed. A
committed claim is immediately unknown: a crash or uncertain outcome never
causes a retry. Unknown means execution may or may not have happened; inspect
the channel before taking further action. 'started' means only the start
request was accepted, not that applications were successfully submitted.
'stopping' is not a confirmed stop.

Notifications share reminder_notifications and the reminder hub event. Their
kind is 'scheduled_job', item_id is the job id, and fields contain title, status,
operation and channel. A claim creates an unknown notification atomically; a
confirmed result updates that same notification and resets read_at. Notifications
are durable; hub delivery is best effort and clients should fetch on reconnect.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from . import reminders
from .db import get_conn
from .events import hub

logger = logging.getLogger(__name__)
POLL_SECONDS = 4
_lock = threading.Lock()
_thread = None
_stop_event = None


def init_db(conn):
    reminders.init_db(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        operation TEXT NOT NULL,
        channel TEXT NOT NULL,
        due_at TEXT NOT NULL,
        max_count INTEGER,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        result TEXT,
        notification_id INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due "
                 "ON scheduled_jobs(status, due_at, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_owner "
                 "ON scheduled_jobs(user_id, id)")


def _job(row):
    job = dict(row)
    job.pop("user_id")
    job.pop("notification_id")
    if job["result"] is not None:
        job["result"] = json.loads(job["result"])
    return job


def execute(user_id, action, data):
    if not reminders._positive_int(user_id):
        return {"ok": False, "error": "user_id must be a positive integer"}
    allowed = {"schedule": {"operation", "channel", "due_at", "max_count"},
               "list": set(), "cancel": {"id"}}
    if not isinstance(action, str) or action not in allowed:
        return {"ok": False, "error": "unsupported action"}
    if type(data) is not dict or any(key not in allowed[action] for key in data):
        return {"ok": False, "error": "invalid data or unknown data keys"}
    if action == "schedule":
        operation, channel = data.get("operation"), data.get("channel")
        if operation not in ("start_application", "stop_application"):
            return {"ok": False, "error": "invalid operation"}
        if channel not in ("boss", "liepin"):
            return {"ok": False, "error": "invalid channel"}
        if "max_count" in data and (operation != "start_application" or channel != "boss"
                                    or type(data["max_count"]) is not int
                                    or not 1 <= data["max_count"] <= 100):
            return {"ok": False, "error": "max_count requires Boss start and an integer from 1 to 100"}
        try:
            due_at = reminders._due_at(data.get("due_at"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if due_at <= reminders._now():
            return {"ok": False, "error": "due_at must be in the future"}
    elif action == "cancel" and not reminders._positive_int(data.get("id")):
        return {"ok": False, "error": "id must be a positive integer"}

    with get_conn() as conn:
        if action == "schedule":
            # Recheck after acquiring the write lock so a delayed insert cannot be past due.
            conn.execute("BEGIN IMMEDIATE")
            now = reminders._now()
            if due_at <= now:
                return {"ok": False, "error": "due_at must be in the future"}
            cursor = conn.execute("""INSERT INTO scheduled_jobs
                (user_id, operation, channel, due_at, max_count, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (user_id, operation, channel, due_at, data.get("max_count"), now, now))
            row = conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (cursor.lastrowid,)).fetchone()
            return {"ok": True, "job": _job(row), "note": "执行时使用该渠道当时保存的配置。"}
        if action == "list":
            rows = conn.execute("SELECT * FROM scheduled_jobs WHERE user_id=? ORDER BY id DESC",
                                (user_id,)).fetchall()
            return {"ok": True, "jobs": [_job(row) for row in rows]}
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE scheduled_jobs SET status='cancelled', updated_at=? "
                              "WHERE id=? AND user_id=? AND status='pending'",
                              (reminders._now(), data["id"], user_id))
        if not cursor.rowcount:
            return {"ok": False, "error": "pending job not found"}
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE id=? AND user_id=?",
                           (data["id"], user_id)).fetchone()
        return {"ok": True, "job": _job(row)}
def _fields(job, status):
    channel = "Boss" if job["channel"] == "boss" else "猎聘"
    operation = "启动投递" if job["operation"] == "start_application" else "停止投递"
    descriptions = {"unknown": "执行状态未知，请查询渠道状态", "failed": "执行失败",
                    "started": "启动请求已确认（不代表投递成功）", "succeeded": "已停止",
                    "stopping": "停止请求已接受，正在停止"}
    return {"title": f"定时{channel}{operation}：{descriptions[status]}",
            "status": status, "operation": job["operation"], "channel": job["channel"]}


def _publish(user_id, notification):
    try:
        hub.publish(user_id, {"type": "reminder", "notification": notification})
    except Exception:
        logger.exception("Failed to publish scheduled job notification %s", notification["id"])


def _claim():
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scheduled_jobs WHERE status='pending' AND due_at<=? "
                           "ORDER BY due_at, id LIMIT 1", (reminders._now(),)).fetchone()
        if row is None:
            return None
        job = dict(row)
        now = reminders._now()
        cursor = conn.execute("""INSERT INTO reminder_notifications
            (user_id, kind, item_id, fields, due_at, created_at)
            VALUES (?, 'scheduled_job', ?, ?, ?, ?)""",
            (job["user_id"], job["id"], json.dumps(_fields(job, "unknown"), ensure_ascii=False),
             job["due_at"], now))
        conn.execute("UPDATE scheduled_jobs SET status='unknown', updated_at=?, notification_id=? WHERE id=?",
                     (now, cursor.lastrowid, job["id"]))
        notification = reminders._notification(conn.execute(
            "SELECT * FROM reminder_notifications WHERE id=?", (cursor.lastrowid,)).fetchone())
    return job, notification


def _finish(job, status, result):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE scheduled_jobs SET status=?, result=?, updated_at=? WHERE id=? AND status='unknown'",
                     (status, json.dumps(result, ensure_ascii=False), reminders._now(), job["id"]))
        fields = _fields(job, status)
        detail = result.get("error") or result.get("message") if isinstance(result, dict) else None
        if detail:
            fields["title"] += "：" + str(detail)[:200]
        conn.execute("UPDATE reminder_notifications SET fields=?, read_at=NULL WHERE id=?",
                     (json.dumps(fields, ensure_ascii=False), job["notification_id"]))
        notification = reminders._notification(conn.execute(
            "SELECT * FROM reminder_notifications WHERE id=?", (job["notification_id"],)).fetchone())
    _publish(job["user_id"], notification)


def _process_due():
    # Imports are deliberately deferred: agent also imports application modules.
    from . import agent, runners
    while True:
        claimed = _claim()
        if claimed is None:
            return
        job, notification = claimed
        job["notification_id"] = notification["id"]
        due_at = datetime.fromisoformat(job["due_at"])
        if datetime.now(timezone.utc) - due_at > timedelta(minutes=10):
            _finish(job, "failed", {"error": "超过计划时间 10 分钟，未执行；请重新安排"})
            continue
        try:
            runners.ensure_running(job["user_id"])
        except Exception as exc:
            logger.exception("Failed to start runner for scheduled job %s", job["id"])
            _finish(job, "failed", {"error": str(exc)})
            continue
        args = {"channel": job["channel"], "confirm": True}
        if job["operation"] == "start_application":
            try:
                current = agent.execute_tool(job["user_id"], "get_status", {"channel": job["channel"]})
                if job["channel"] == "boss" and isinstance(current, dict) and current.get("browserReady") is False:
                    agent.execute_tool(job["user_id"], "open_login", {"channel": "boss"})
                    current = agent.execute_tool(job["user_id"], "get_status", {"channel": "boss"})
            except Exception as exc:
                _finish(job, "failed", {"error": "无法确认渠道状态，未启动", "detail": str(exc)})
                continue
            if not isinstance(current, dict) or type(current.get("isRunning")) is not bool:
                _finish(job, "failed", {"error": "无法确认渠道状态，未启动"})
                continue
            if current["isRunning"]:
                _finish(job, "failed", {"error": "渠道已在运行，未再次启动"})
                continue
            if current.get("isLoggedIn") is False:
                _finish(job, "failed", {"error": "渠道未登录，未启动；请登录后重新安排"})
                continue
        if job["max_count"] is not None:
            args["max_count"] = job["max_count"]
        try:
            result = agent.execute_tool(job["user_id"], job["operation"], args)
        except Exception as exc:
            # A request may have reached the runner even if its response was lost.
            logger.exception("Uncertain outcome for scheduled job %s", job["id"])
            _finish(job, "unknown", {"error": str(exc)})
            continue
        if not isinstance(result, dict):
            _finish(job, "unknown", {"error": "Unrecognized tool response"})
        elif result.get("status") == "limit_unconfirmed":
            _finish(job, "unknown", result)
        elif result.get("ok") is not True:
            _finish(job, "failed", result)
        elif job["operation"] == "stop_application" and result.get("status") == "stopping":
            _finish(job, "stopping", result)
        elif job["operation"] == "stop_application" and result.get("status") != "stopped":
            _finish(job, "unknown", result)
        elif job["operation"] == "start_application":
            _finish(job, "started", result)
        else:
            _finish(job, "succeeded", result)


def _run(stop_event):
    while not stop_event.is_set():
        try:
            _process_due()
        except Exception:
            logger.exception("Failed to process scheduled jobs")
        stop_event.wait(POLL_SECONDS)


def start():
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        with get_conn() as conn:
            init_db(conn)
        _stop_event = threading.Event()
        _thread = threading.Thread(target=_run, args=(_stop_event,), daemon=True, name="scheduled-jobs")
        _thread.start()


def stop():
    global _thread, _stop_event
    with _lock:
        if _thread is not None:
            _stop_event.set()
            _thread.join()
            _thread = None
            _stop_event = None
