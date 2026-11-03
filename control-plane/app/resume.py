"""Convert an uploaded PDF resume into a single tall PNG for Boss image delivery."""

import io
import math
import threading

import pymupdf
from PIL import Image

MAX_PAGES = 6
WIDTH_CANDIDATES = (1240, 1000, 820, 680)
MAX_PIXEL_HEIGHT = 20000
MAX_OUTPUT_BYTES = 9 * 1024 * 1024
_render_lock = threading.Lock()


def pdf_to_png(data: bytes) -> bytes:
    if not _render_lock.acquire(blocking=False):
        raise ValueError("简历转换繁忙，请稍后重试")
    try:
        return _render_pdf(data)
    finally:
        _render_lock.release()


def _render_pdf(data: bytes) -> bytes:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法读取 PDF 文件") from exc

    with document:
        if document.page_count == 0:
            raise ValueError("PDF 没有页面")
        pages = [document.load_page(i) for i in range(min(document.page_count, MAX_PAGES))]
        ratios = []
        for page in pages:
            rect = page.rect
            if not (math.isfinite(rect.width) and math.isfinite(rect.height)
                    and rect.width > 0 and rect.height > 0):
                raise ValueError("PDF 页面尺寸无效")
            ratios.append(rect.height / rect.width)

        for width in WIDTH_CANDIDATES:
            # Bound the render itself, not just the image after allocating it.
            # Reserve two pixels per page for pixmap dimension rounding.
            render_width = min(width, (MAX_PIXEL_HEIGHT - 2 * len(pages)) / sum(ratios))
            if render_width < 1:
                raise ValueError("PDF 页面尺寸过大，无法转换")
            canvas_width = math.ceil(render_width)
            canvas_height = sum(math.ceil(render_width * ratio) + 1 for ratio in ratios)
            canvas = Image.new("RGB", (canvas_width + 1, canvas_height), "white")
            offset = 0
            for page in pages:
                zoom = render_width / page.rect.width
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                del pixmap
                canvas.paste(image, (0, offset))
                offset += image.height
                image.close()
            if offset < canvas.height:
                cropped = canvas.crop((0, 0, canvas.width, offset))
                canvas.close()
                canvas = cropped
            output = io.BytesIO()
            canvas.save(output, format="PNG", optimize=True)
            canvas.close()
            payload = output.getvalue()
            if len(payload) <= MAX_OUTPUT_BYTES:
                return payload

    raise ValueError("PDF 转图片后超过 9MB，请压缩 PDF 后重试")