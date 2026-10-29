"""Build a clean PDF resume from the structured user profile."""

import io
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

FONT = "STSong-Light"

STYLE_NAME = ParagraphStyle(
    "name", fontName=FONT, fontSize=22, leading=28, spaceAfter=4
)
STYLE_CONTACT = ParagraphStyle(
    "contact", fontName=FONT, fontSize=10, leading=16, textColor="#444444"
)
STYLE_SECTION = ParagraphStyle(
    "section",
    fontName=FONT,
    fontSize=13,
    leading=18,
    spaceBefore=12,
    spaceAfter=4,
    textColor="#1f4d3d",
)
STYLE_BODY = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=17)
STYLE_BULLET = ParagraphStyle("bullet", fontName=FONT, fontSize=10.5, leading=17)
STYLE_SMALL = ParagraphStyle(
    "small", fontName=FONT, fontSize=9.5, leading=15, textColor="#555555"
)


def _text(value) -> str:
    return escape(str(value or "").strip())


def _as_list(value) -> list:
    if isinstance(value, list):
        return [item for item in value if item]
    if isinstance(value, str):
        return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
    return []


def _bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_text(item), STYLE_BULLET), leftIndent=10) for item in items],
        bulletType="bullet",
        start="●",
        leftIndent=12,
        bulletFontSize=7,
    )


def _experience_block(entry) -> list:
    flow = []
    if isinstance(entry, dict):
        title = _text(entry.get("title") or entry.get("position") or "")
        company = _text(entry.get("company") or "")
        period = _text(entry.get("period") or "")
        if not period:
            start = _text(entry.get("start") or "")
            end = _text(entry.get("end") or "")
            period = " - ".join(part for part in (start, end) if part)
        head = " · ".join(part for part in (company, title) if part) or "经历"
        if period:
            head = f"{head}（{period}）" if head else period
        flow.append(Paragraph(f"<b>{head}</b>", STYLE_BODY))
        highlights = entry.get("highlights") or entry.get("details") or []
        if highlights:
            flow.append(_bullets([str(item) for item in highlights]))
        elif entry.get("description"):
            flow.append(Paragraph(_text(entry["description"]), STYLE_BODY))
    elif entry:
        flow.append(Paragraph(_text(entry), STYLE_BODY))
    return flow


def build_resume_pdf(profile: dict) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{profile.get('name') or '简历'} - 简历",
        author=str(profile.get("name") or ""),
    )

    story = []
    story.append(Paragraph(_text(profile.get("name") or "姓名未填写"), STYLE_NAME))
    if profile.get("target_title"):
        story.append(Paragraph(_text(profile["target_title"]), STYLE_CONTACT))

    contacts = [
        profile.get("phone"),
        profile.get("email"),
        profile.get("city"),
        profile.get("years_experience"),
        profile.get("expected_salary"),
    ]
    contact_line = " · ".join(_text(item) for item in contacts if item)
    if contact_line:
        story.append(Paragraph(contact_line, STYLE_CONTACT))

    skills = _as_list(profile.get("skills"))
    if skills:
        story.append(Paragraph("技能", STYLE_SECTION))
        story.append(Paragraph("；".join(_text(item) for item in skills), STYLE_BODY))

    work_history = profile.get("work_history") or []
    if work_history:
        story.append(Paragraph("工作经历", STYLE_SECTION))
        for entry in work_history if isinstance(work_history, list) else [work_history]:
            story.extend(_experience_block(entry))

    projects = profile.get("projects") or []
    if projects:
        story.append(Paragraph("项目经历", STYLE_SECTION))
        for entry in projects if isinstance(projects, list) else [projects]:
            story.extend(_experience_block(entry))

    education_bits = [
        profile.get("school"),
        profile.get("major"),
        profile.get("degree"),
        profile.get("education"),
    ]
    education_line = " · ".join(_text(item) for item in education_bits if item)
    if education_line:
        story.append(Paragraph("教育经历", STYLE_SECTION))
        story.append(Paragraph(education_line, STYLE_BODY))

    if profile.get("self_intro"):
        story.append(Paragraph("自我评价", STYLE_SECTION))
        story.append(Paragraph(_text(profile["self_intro"]), STYLE_BODY))

    if not any(
        [
            skills,
            work_history,
            projects,
            education_line,
            profile.get("self_intro"),
        ]
    ):
        story.append(Spacer(1, 10))
        story.append(
            Paragraph(
                "简历内容还不完整，请先在「资料与简历」里补充教育、技能或工作经历。",
                STYLE_SMALL,
            )
        )

    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            "本简历由 GetJobs Agent 根据你提供的资料生成，请在投递前核对内容真实性。",
            STYLE_SMALL,
        )
    )

    document.build(story)
    return buffer.getvalue()