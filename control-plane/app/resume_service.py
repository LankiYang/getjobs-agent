"""Generate a PDF/image resume from the structured profile and sync it to the runner."""

import datetime as dt
from pathlib import Path

from . import runners
from .resume import pdf_to_png
from .resume_builder import build_resume_pdf

RESUME_DIR = Path(__file__).resolve().parent.parent / "data" / "resumes"


def pdf_path(user_id: int) -> Path:
    return RESUME_DIR / f"u{user_id}.pdf"


def generate(user_id: int, profile: dict) -> dict:
    if not str(profile.get("name") or "").strip():
        raise ValueError("请先填写姓名等基本资料")

    pdf = build_resume_pdf(profile)
    png = pdf_to_png(pdf)

    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    path = pdf_path(user_id)
    path.write_bytes(pdf)

    runners.ensure_running(user_id)
    response = runners.request(
        user_id,
        "POST",
        "/api/boss/config/resume-image",
        files={"file": ("resume.png", png, "image/png")},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError("简历图片同步到投递环境失败")

    return {
        "ok": True,
        "pdfSize": len(pdf),
        "imageSize": len(png),
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }