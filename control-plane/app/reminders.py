"""Persistent, one-shot reminders for user-owned work items.

start() initializes its tables and start()/stop() manage an idempotent in-process
polling thread. Call start after the work_items schema and hub are configured.
init_db(conn) is also available for explicit initialization.

execute(user_id, action, data):
  set: {kind, item_id, due_at}; cancel: {kind, item_id}; list: {};
  notifications: {limit?, offset?, unread_only?}; mark_read: {id}.
Times must be ISO 8601 with an explicit UTC offset (or Z); they are stored and
returned in UTC. Reminders are one-shot: set replaces the pending reminder for
an item, including its due time. Past-due reminders are processed on the next
poll, including after restart. There is no recurrence or external operation.
Deleting an item before processing discards its pending reminder. Notifications
retain a snapshot of the item's fields at processing time, even if it is later
deleted. Cancellation after processing does not remove the notification.
A committed notification is authoritative; hub delivery is best-effort and may
be lost during restart, so clients should fetch notifications on reconnect.
Validation/not-found errors return {ok: false, error: ...}; database errors
propagate from execute. The background worker logs failures and retries on the
next poll without deleting unprocessed reminders.
"""

import json
import logging
import threading
from datetime import datetime, timezone

from .db import get_conn
from .events import hub

logger = logging.getLogger(__name__)
POLL_SECONDS = 4
_MAX_INT = 2**63 - 1
_lock = threading.Lock()
_thread = None
_stop_event = None


def init_db(conn):
    """Create reminder tables using the caller's database connection."""
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('task', 'event')),
        item_id INTEGER NOT NULL,
        due_at TEXT NOT NULL,
        UNIQUE(user_id, kind, item_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS reminder_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        fields TEXT NOT NULL,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_notifications_owner "
                 "ON reminder_notifications(user_id, id)")


def _positive_int(value):
    return type(value) is int and 0 < value <= _MAX_INT


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _due_at(value):
    if not isinstance(value, str):
        raise ValueError("due_at must be an ISO 8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as exc:
        raise ValueError("due_at must be an ISO 8601 timestamp with timezone") from exc


def _notification(row):
    return {"id": row["id"], "kind": row["kind"], "item_id": row["item_id"],
            "fields": json.loads(row["fields"]), "due_at": row["due_at"],
            "created_at": row["created_at"], "read_at": row["read_at"]}


def execute(user_id: int, action: str, data: dict) -> dict:
    if not _positive_int(user_id):
        return {"ok": False, "error": "user_id must be a positive integer"}
    allowed = {"set": {"kind", "item_id", "due_at"},
               "cancel": {"kind", "item_id"}, "list": set(),
               "notifications": {"limit", "offset", "unread_only"},
               "mark_read": {"id"}}
    if not isinstance(action, str) or action not in allowed:
        return {"ok": False, "error": "unsupported action"}
    if type(data) is not dict or any(key not in allowed[action] for key in data):
        return {"ok": False, "error": "invalid data or unknown data keys"}
    try:
        if action in ("set", "cancel"):
            if data.get("kind") not in ("task", "event"):
                raise ValueError("kind must be task or event")
            if not _positive_int(data.get("item_id")):
                raise ValueError("item_id must be a positive integer")
        if action == "set":
            due_at = _due_at(data.get("due_at"))
        if action == "mark_read" and not _positive_int(data.get("id")):
            raise ValueError("id must be a positive integer")
        if action == "notifications":
            limit, offset = data.get("limit", 50), data.get("offset", 0)
            unread_only = data.get("unread_only", False)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("limit must be an integer between 1 and 100")
            if type(offset) is not int or not 0 <= offset <= _MAX_INT:
                raise ValueError("offset must be a non-negative integer")
            if type(unread_only) is not bool:
                raise ValueError("unread_only must be a boolean")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    with get_conn() as conn:
        if action in ("set", "cancel"):
            # Serialize ownership check and mutation with item deletion and firing.
            conn.execute("BEGIN IMMEDIATE")
            kind, item_id = data["kind"], data["item_id"]
            if action == "set":
                item = conn.execute("SELECT 1 FROM work_items WHERE user_id=? AND kind=? AND id=?",
                                    (user_id, kind, item_id)).fetchone()
                if item is None:
                    return {"ok": False, "error": "item not found"}
                conn.execute("""INSERT INTO reminders(user_id, kind, item_id, due_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(user_id, kind, item_id)
                    DO UPDATE SET due_at=excluded.due_at""", (user_id, kind, item_id, due_at))
                return {"ok": True, "reminder": {"kind": kind, "item_id": item_id, "due_at": due_at}}
            cursor = conn.execute("DELETE FROM reminders WHERE user_id=? AND kind=? AND item_id=?",
                                  (user_id, kind, item_id))
            if not cursor.rowcount:
                return {"ok": False, "error": "reminder not found"}
            return {"ok": True}
        if action == "list":
            rows = conn.execute("""SELECT r.kind, r.item_id, r.due_at FROM reminders r
                JOIN work_items w ON w.id=r.item_id AND w.user_id=r.user_id AND w.kind=r.kind
                WHERE r.user_id=? ORDER BY r.due_at, r.id""", (user_id,)).fetchall()
            return {"ok": True, "reminders": [dict(row) for row in rows]}
        if action == "notifications":
            where = "user_id=?" + (" AND read_at IS NULL" if unread_only else "")
            total = conn.execute(f"SELECT count(*) FROM reminder_notifications WHERE {where}",
                                 (user_id,)).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM reminder_notifications WHERE {where} "
                                "ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset)).fetchall()
            return {"ok": True, "notifications": [_notification(row) for row in rows],
                    "total": total, "limit": limit, "offset": offset}
        conn.execute("UPDATE reminder_notifications SET read_at=COALESCE(read_at, ?) "
                     "WHERE id=? AND user_id=?", (_now(), data["id"], user_id))
        row = conn.execute("SELECT * FROM reminder_notifications WHERE id=? AND user_id=?",
                           (data["id"], user_id)).fetchone()
        if row is None:
            return {"ok": False, "error": "notification not found"}
        return {"ok": True, "notification": _notification(row)}


def _process_due():
    """Atomically consume due reminders, then publish committed notifications."""
    while True:
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT r.id, r.user_id, r.kind, r.item_id, r.due_at,
                w.fields FROM reminders r LEFT JOIN work_items w ON w.id=r.item_id
                AND w.user_id=r.user_id AND w.kind=r.kind
                WHERE r.due_at<=? ORDER BY r.due_at, r.id LIMIT 1""", (_now(),)).fetchone()
            if row is None:
                return
            notification = None
            if row["fields"] is not None:
                cursor = conn.execute("""INSERT INTO reminder_notifications
                    (user_id, kind, item_id, fields, due_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (row["user_id"], row["kind"], row["item_id"], row["fields"], row["due_at"], _now()))
                saved = conn.execute("SELECT * FROM reminder_notifications WHERE id=?",
                                     (cursor.lastrowid,)).fetchone()
                notification = _notification(saved)
            conn.execute("DELETE FROM reminders WHERE id=?", (row["id"],))
            user_id = row["user_id"]
        if notification is not None:
            try:
                hub.publish(user_id, {"type": "reminder", "notification": notification})
            except Exception:
                logger.exception("Failed to publish reminder notification %s", notification["id"])


def _run(stop_event):
    while not stop_event.is_set():
        try:
            _process_due()
        except Exception:
            logger.exception("Failed to process reminders; retrying")
        stop_event.wait(POLL_SECONDS)


def start():
    """Start the background poller; repeated calls are harmless."""
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        with get_conn() as conn:
            init_db(conn)
        _stop_event = threading.Event()
        _thread = threading.Thread(target=_run, args=(_stop_event,), daemon=True, name="reminders")
        _thread.start()


def stop():
    """Stop and join the poller; safe if it was not started."""
    global _thread, _stop_event
    with _lock:
        if _thread is not None:
            _stop_event.set()
            _thread.join()
            _thread = None
            _stop_event = None
