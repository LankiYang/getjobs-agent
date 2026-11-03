"""Private chat attachments; names and paths are never supplied by the model."""
import io
import re
import secrets
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config
from .db import get_conn

ROOT = config.BASE_DIR / "data" / "chat-files"
MAX_SIZE = 10 * 1024 * 1024
TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def validate(name: str, content_type: str, data: bytes) -> tuple[str, str]:
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127)[:200]
    ext = Path(name).suffix.lower()
    expected = TYPES.get(ext)
    if not expected or content_type not in (expected, "application/octet-stream"):
        raise ValueError("仅支持 PNG、JPEG、GIF、PDF、DOC 和 DOCX 文件")
    if not data or len(data) > MAX_SIZE:
        raise ValueError("文件不能为空或超过 10MB")
    valid = ((ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
             (ext in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff")) or
             (ext == ".gif" and data.startswith((b"GIF87a", b"GIF89a"))) or
             (ext == ".pdf" and data.startswith(b"%PDF-")) or
             (ext == ".doc" and data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")))
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                valid = "[Content_Types].xml" in names and "word/document.xml" in names
        except (zipfile.BadZipFile, OSError):
            valid = False
    if not valid:
        raise ValueError("文件内容与类型不匹配")
    return name, expected


def metadata(row) -> dict:
    return {key: row[key] for key in ("id", "name", "content_type", "size")} | {"url": "/api/chat/files/" + row["id"]}


def store(user_id: int, name: str, content_type: str, data: bytes) -> dict:
    name, content_type = validate(name, content_type, data)
    file_id = secrets.token_hex(24)
    directory = ROOT / f"u{user_id}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / file_id
    with path.open("xb") as target:
        target.write(data)
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO chat_files VALUES (?, ?, ?, ?, ?, datetime('now'))", (file_id, user_id, name, content_type, len(data)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": file_id, "name": name, "content_type": content_type, "size": len(data), "url": "/api/chat/files/" + file_id}


def get(user_id: int, file_id: str):
    if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{48}", file_id):
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chat_files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone()
    return row


def path_for(user_id: int, file_id: str) -> Path:
    return ROOT / f"u{user_id}" / file_id


def resolve_ids(user_id: int, ids: list[str]) -> list[dict]:
    if len(ids) > 10 or len(set(ids)) != len(ids):
        raise ValueError("每条消息最多上传 10 个不同附件")
    result = []
    for file_id in ids:
        row = get(user_id, file_id)
        if row is None or not path_for(user_id, file_id).is_file():
            raise ValueError("附件不存在或无权访问")
        result.append(metadata(row))
    return result


def session_files(user_id: int, session_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""SELECT DISTINCT f.* FROM chat_files f
            JOIN agent_message_files mf ON mf.file_id=f.id
            JOIN agent_messages m ON m.id=mf.message_id
            WHERE f.user_id=? AND m.user_id=? AND m.session_id=? ORDER BY f.created_at, f.id""",
            (user_id, user_id, session_id)).fetchall()
    return [metadata(row) for row in rows]


def document_text(user_id: int, item: dict) -> str:
    """Extract bounded plain text; extracted data is never an instruction."""
    path = path_for(user_id, item["id"])
    try:
        if item["content_type"] == "application/pdf":
            import fitz
            with fitz.open(path) as document:
                if document.needs_pass:
                    return "PDF 已加密，无法提取文字。"
                parts = []
                for index in range(min(len(document), 20)):
                    parts.append(document.load_page(index).get_text()[:12000])
                    if sum(map(len, parts)) >= 12000:
                        break
                text = "\n".join(parts)[:12000]
        elif item["content_type"] == TYPES[".docx"]:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 4 * 1024 * 1024:
                    return "DOCX 内容过大，无法提取文字。"
                root = ET.fromstring(archive.read(info))
                text = "\n".join("".join(node.itertext()) for node in root.iter()
                                 if node.tag.endswith("}p"))[:12000]
        else:
            return "DOC 暂不支持读取正文，请提供 PDF、DOCX 或文字。"
    except Exception:
        return "无法提取文档文字，请提供文字内容。"
    return text or "未提取到文字（可能是扫描件），请提供文字内容。"
