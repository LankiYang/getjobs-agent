"""Per-user saved application sites and their login/availability state."""

import datetime as dt
import json
from urllib.parse import urlparse

from . import runners
from .db import get_conn

LOGIN_ELEMENT_HINTS = ("人机验证", "验证码", "captcha", "我不是机器人", "登录/注册")
LOGIN_TEXT_HINTS = ("请先登录", "请登录后", "登录已过期")
UNREACHABLE = "unreachable"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def validate_url(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return "url 不能为空"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL 格式不正确"
    if parsed.scheme not in ("http", "https"):
        return "只支持 http/https 地址"
    host = (parsed.hostname or "").lower()
    if not host:
        return "URL 缺少主机名"
    if (
        host == "localhost"
        or host.endswith(".local")
        or host in ("::1",)
        or host.startswith("127.")
        or host.startswith("10.")
        or host.startswith("192.168.")
        or host.startswith("169.254.")
    ):
        return "不允许访问本机或内网地址"
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1:
            try:
                if 16 <= int(parts[1]) <= 31:
                    return "不允许访问内网地址"
            except ValueError:
                pass
    return None


def normalize_url(url: str) -> str:
    return (url or "").split("#")[0].strip()


def analyze_snapshot(snapshot: dict) -> str:
    if not isinstance(snapshot, dict):
        return "unknown"
    elements = snapshot.get("elements") or []
    for element in elements:
        if str(element.get("type") or "").lower() == "password":
            return "login_required"
    for element in elements:
        haystack = " ".join(
            str(element.get(key) or "")
            for key in ("label", "placeholder", "text")
        ).lower()
        if any(hint in haystack for hint in LOGIN_ELEMENT_HINTS):
            return "login_required"
    text = str(snapshot.get("text") or "").lower()
    if any(hint in text for hint in LOGIN_TEXT_HINTS):
        return "login_required"
    return "ok"


def list_sites(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, url, state, last_checked, created_at FROM custom_sites "
            "WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_site(user_id: int, site_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, url, state, last_checked FROM custom_sites "
            "WHERE id = ? AND user_id = ?",
            (site_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def upsert_site(user_id: int, name: str, url: str, state: str = "unknown") -> dict:
    url = normalize_url(url)
    name = (name or url).strip()[:120]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM custom_sites WHERE user_id = ? AND url = ?",
            (user_id, url),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE custom_sites SET name = ? WHERE id = ?",
                (name, row["id"]),
            )
            site_id = row["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO custom_sites(user_id, name, url, state, last_checked, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, name, url, state, now(), now()),
            )
            site_id = cursor.lastrowid
    return get_site(user_id, site_id)


def delete_site(user_id: int, site_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM custom_sites WHERE id = ? AND user_id = ?", (site_id, user_id)
        )
    return cursor.rowcount > 0


def update_state(user_id: int, url: str, state: str) -> None:
    if not url:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE custom_sites SET state = ?, last_checked = ? "
            "WHERE user_id = ? AND url = ?",
            (state, now(), user_id, normalize_url(url)),
        )


def check_site(user_id: int, site: dict) -> dict:
    try:
        runners.request(
            user_id,
            "POST",
            "/api/browser/open",
            json={"url": site["url"]},
            timeout=60,
        )
        response = runners.request(
            user_id, "GET", "/api/browser/snapshot", timeout=30
        )
        payload = response.json() or {}
        raw = payload.get("snapshot") or "{}"
        snapshot = json.loads(raw) if isinstance(raw, str) else raw
        state = analyze_snapshot(snapshot)
        update_state(user_id, site["url"], state)
        return {
            "ok": True,
            "state": state,
            "title": snapshot.get("title") or site["name"],
            "url": snapshot.get("url") or site["url"],
            "elements": len(snapshot.get("elements") or []),
        }
    except Exception as exc:
        update_state(user_id, site["url"], UNREACHABLE)
        return {"ok": False, "state": UNREACHABLE, "error": str(exc)}