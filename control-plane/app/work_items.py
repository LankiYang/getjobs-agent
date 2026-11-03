"""User-owned task and event records, independent of runner execution.

``execute`` accepts create: {fields}, list: {limit?, offset?},
get/delete: {id}, and update: {id, fields?, delete_fields?}. Fields are a
JSON object; updates replace only supplied top-level fields. Deletion names
refer to top-level fields and missing names are ignored.
"""

import json
from datetime import datetime, timezone

from .db import get_conn

_MAX_INT = 2**63 - 1


def _positive_int(value):
    return type(value) is int and 0 < value <= _MAX_INT


def _json_value(value):
    if value is None or type(value) in (str, bool, int, float):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("field names must be strings")
            _json_value(item)
        return
    raise ValueError("fields must contain only JSON values")


def _encode(fields):
    if type(fields) is not dict:
        raise ValueError("fields must be a JSON object")
    try:
        _json_value(fields)
        return json.dumps(fields, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"invalid fields: {exc}") from exc


def _item(row):
    return {"id": row["id"], "kind": row["kind"],
            "fields": json.loads(row["fields"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def execute(user_id: int, kind: str, action: str, data: dict) -> dict:
    """Perform a scoped CRUD operation; invalid input and missing items are errors."""
    if not _positive_int(user_id):
        return {"ok": False, "error": "user_id must be a positive integer"}
    if kind not in ("task", "event"):
        return {"ok": False, "error": "kind must be task or event"}
    allowed = {"create": {"fields"}, "list": {"limit", "offset"},
               "get": {"id"}, "update": {"id", "fields", "delete_fields"},
               "delete": {"id"}}
    if not isinstance(action, str) or action not in allowed:
        return {"ok": False, "error": "unsupported action"}
    if type(data) is not dict or any(key not in allowed[action] for key in data):
        return {"ok": False, "error": "invalid data or unknown data keys"}
    try:
        if action == "create":
            if "fields" not in data:
                raise ValueError("fields is required")
            encoded = _encode(data["fields"])
        if action in ("get", "update", "delete") and not _positive_int(data.get("id")):
            raise ValueError("id must be a positive integer")
        if action == "update":
            if "fields" not in data and "delete_fields" not in data:
                raise ValueError("fields or delete_fields is required")
            fields = data.get("fields", {})
            _encode(fields)
            deleted = data.get("delete_fields", [])
            if type(deleted) is not list or any(type(name) is not str for name in deleted):
                raise ValueError("delete_fields must be an array of strings")
            if any(name in fields for name in deleted):
                raise ValueError("a field cannot be updated and deleted at the same time")
        if action == "list":
            limit, offset = data.get("limit", 50), data.get("offset", 0)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("limit must be an integer between 1 and 100")
            if type(offset) is not int or not 0 <= offset <= _MAX_INT:
                raise ValueError("offset must be a non-negative integer")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    with get_conn() as conn:
        if action == "list":
            total = conn.execute("SELECT count(*) FROM work_items WHERE user_id=? AND kind=?",
                                 (user_id, kind)).fetchone()[0]
            rows = conn.execute("SELECT * FROM work_items WHERE user_id=? AND kind=? "
                                "ORDER BY id DESC LIMIT ? OFFSET ?",
                                (user_id, kind, limit, offset)).fetchall()
            return {"ok": True, "items": [_item(row) for row in rows],
                    "limit": limit, "offset": offset, "total": total}
        if action == "create":
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute("INSERT INTO work_items (user_id, kind, fields, created_at, updated_at) "
                                  "VALUES (?, ?, ?, ?, ?)", (user_id, kind, encoded, now, now))
            row = conn.execute("SELECT * FROM work_items WHERE id=? AND user_id=? AND kind=?",
                               (cursor.lastrowid, user_id, kind)).fetchone()
            return {"ok": True, "item": _item(row)}
        # Serialize read/modify/write updates against other SQLite writers.
        if action in ("update", "delete"):
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM work_items WHERE id=? AND user_id=? AND kind=?",
                           (data["id"], user_id, kind)).fetchone()
        if row is None:
            return {"ok": False, "error": "item not found"}
        item = _item(row)
        if action == "delete":
            conn.execute("DELETE FROM work_items WHERE id=? AND user_id=? AND kind=?",
                         (data["id"], user_id, kind))
        elif action == "update":
            item["fields"].update(fields)
            for name in deleted:
                item["fields"].pop(name, None)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE work_items SET fields=?, updated_at=? "
                         "WHERE id=? AND user_id=? AND kind=?",
                         (_encode(item["fields"]), item["updated_at"], data["id"], user_id, kind))
        return {"ok": True, "item": item}
