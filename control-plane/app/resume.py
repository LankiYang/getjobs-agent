"""Convert an uploaded PDF resume into a single tall PNG for Boss image delivery."""

import io

import pymupdf
from PIL import Image

MAX_PAGES = 6
WIDTH_CANDIDATES = (1240, 1000, 820, 680)
MAX_PIXEL_HEIGHT = 20000
MAX_OUTPUT_BYTES = 9 * 1024 * 1024


def pdf_to_png(data: bytes) -> bytes:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法读取 PDF 文件") from exc

    if document.page_count == 0:
        raise ValueError("PDF 没有页面")

    pages = list(document)[:MAX_PAGES]
    last_error: Exception | None = None

    for width in WIDTH_CANDIDATES:
        rendered: list[Image.Image] = []
        for page in pages:
            zoom = width / page.rect.width
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            image = Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )
            rendered.append(image)

        total_height = sum(image.height for image in rendered)
        scale = min(1.0, MAX_PIXEL_HEIGHT / total_height) if total_height else 1.0
        if scale < 1.0:
            rendered = [
                image.resize(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                )
                for image in rendered
            ]

        canvas_width = rendered[0].width
        canvas_height = sum(image.height for image in rendered)
        canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
        offset = 0
        for image in rendered:
            canvas.paste(image, (0, offset))
            offset += image.height

        output = io.BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        payload = output.getvalue()
        if len(payload) <= MAX_OUTPUT_BYTES:
            return payload
        last_error = ValueError("PDF 转图片后超过 9MB，请压缩 PDF 后重试")

    raise last_error or ValueError("PDF 转换失败")