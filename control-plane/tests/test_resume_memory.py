import io
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from app import resume


class ResumeMemoryTest(unittest.TestCase):
    def test_extreme_aspect_pages_are_bounded_before_rendering(self):
        document = pymupdf.open()
        document.new_page(width=100, height=14000)
        document.new_page(width=100, height=14000)
        data = document.tobytes()
        document.close()
        with patch.object(resume, 'MAX_PIXEL_HEIGHT', 1000):
            result = resume.pdf_to_png(data)
        with Image.open(io.BytesIO(result)) as image:
            self.assertLessEqual(image.height, 1000)
            self.assertGreater(image.height, 1)

    def test_busy_renderer_rejects_without_waiting_and_recovers(self):
        self.assertTrue(resume._render_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(ValueError, '繁忙'):
                resume.pdf_to_png(b'not a pdf')
        finally:
            resume._render_lock.release()
        with self.assertRaisesRegex(ValueError, '无法读取'):
            resume.pdf_to_png(b'not a pdf')
