"""Isolated chat file, history, and runner upload behavior."""
import tempfile
import io
import zipfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import agent, chat_files, db, main

PNG = b"\x89PNG\r\n\x1a\nexample"
PDF = b"%PDF-1.4\nexample"


class ChatFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for obj, key, value in [(db, "CONTROL_DB", root / "control.db"),
                                (chat_files, "ROOT", root / "files")]:
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        db.init_db()
        main.app.dependency_overrides[main.current_user] = lambda: 1
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app)

    def upload(self, name="resume.png", data=PNG, kind="image/png"):
        return self.client.post("/api/chat/files", files={"file": (name, data, kind)})

    def test_upload_access_validation_and_isolation(self):
        response = self.upload("../../resume.png")
        self.assertEqual(response.status_code, 200)
        file = response.json()
        self.assertEqual(file["name"], "resume.png")
        self.assertEqual(self.client.get(file["url"]).content, PNG)
        main.app.dependency_overrides[main.current_user] = lambda: 2
        self.assertEqual(self.client.get(file["url"]).status_code, 404)
        self.assertEqual(self.client.post("/api/agent/chat", json={"attachment_ids": [file["id"]]}).status_code, 400)
        self.assertEqual(self.upload("bad.pdf", PNG, "application/pdf").status_code, 400)
        self.assertEqual(self.upload("bad.svg", b"<svg/>", "image/svg+xml").status_code, 400)
        self.assertEqual(self.upload("big.png", b"x" * (chat_files.MAX_SIZE + 1)).status_code, 413)
        main.app.dependency_overrides.clear()
        self.assertEqual(self.client.get(file["url"]).status_code, 401)

    def test_screenshot_tool_stream_and_history(self):
        class Cancel:
            def check(self): pass
            def finish(self): pass
            def cancelled(self): return False
        calls = [
            {"choices": [{"message": {"content": None, "tool_calls": [{"id": "tool1", "function": {"name": "send_page_image", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"content": "截图已发送"}}]},
        ]
        with patch.object(agent, "configured", return_value=True), patch.object(agent, "PROVIDER", "openai"), \
             patch.object(agent, "_chat_completion", side_effect=calls), \
             patch.object(agent.runners, "request", return_value=SimpleNamespace(status_code=200, content=PNG)):
            events = list(agent.run_chat_stream(1, "screen", "看看网页", Cancel()))
        result = next(event for event in events if event["type"] == "tool_result")
        image = result["attachments"][0]
        self.assertEqual(image["content_type"], "image/png")
        self.assertEqual(self.client.get(image["url"]).content, PNG)
        self.assertEqual(agent.history(1, "screen")[1]["attachments"], [image])

    def test_empty_text_and_history_with_untrusted_attachments(self):
        item = self.upload().json()
        with patch.object(agent, "configured", return_value=True), patch.object(agent, "_run_openai", return_value={"reply": "ok"}):
            agent.run_chat(1, "session", "", [item["id"]])
        self.assertEqual(agent.history(1, "session")[0]["attachments"], [item])
        self.assertEqual(agent.execute_tool(1, "list_chat_attachments", {}, "session")["attachments"], [item])
        self.assertIn("图片未提供给模型", agent._load_history(1, "session")[0]["content"])
        self.assertEqual(agent.execute_tool(1, "list_chat_attachments", {}, "other")["attachments"], [])
        with self.assertRaises(agent.AgentToolError):
            agent.execute_tool(2, "show_chat_image", {"attachment_id": item["id"]}, "session")
        result = agent.execute_tool(1, "show_chat_image", {"attachment_id": item["id"]}, "session")
        self.assertEqual(result["attachments"], [item])
        self.assertEqual(agent.history(1, "session")[-1]["attachments"], [item])

    def test_document_text_and_opt_in_image_blocks(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as doc:
            doc.writestr("[Content_Types].xml", "<Types/>")
            doc.writestr("word/document.xml", '<w:document xmlns:w="urn:w"><w:p><w:r><w:t>真实经历</w:t></w:r></w:p></w:document>')
        doc = self.upload("cv.docx", archive.getvalue(), chat_files.TYPES[".docx"]).json()
        image = self.upload().json()
        agent._save_message(1, "docs", "user", "", [doc, image])
        with patch.object(agent, "VISION_ENABLED", True):
            blocks = agent._anthropic_history(1, "docs")[0]["content"]
        self.assertIn("真实经历", blocks[0]["text"])
        self.assertIn("不可信文档", blocks[0]["text"])
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")

    def test_configure_resume_only_from_current_session(self):
        image = self.upload().json()
        agent._save_message(1, "resume", "user", "", [image])
        response = SimpleNamespace(status_code=200, json=lambda: {"success": True})
        with patch.object(agent.runners, "request", return_value=response) as request:
            with self.assertRaises(agent.AgentToolError):
                agent.execute_tool(1, "configure_uploaded_resume", {"attachment_id": image["id"]}, "different")
            result = agent.execute_tool(1, "configure_uploaded_resume", {"attachment_id": image["id"]}, "resume")
        self.assertTrue(result["success"])
        self.assertEqual(request.call_args.kwargs["files"]["file"][1], PNG)

    def test_upload_only_from_session_and_temporary_runner_path(self):
        item = self.upload("cv.pdf", PDF, "application/pdf").json()
        agent._save_message(1, "session", "user", "", [item])
        runner_dir = Path(self.temp.name) / "runner"
        runner_dir.mkdir()
        seen = []
        def request(user, method, path, **kwargs):
            sent = Path(kwargs["json"]["path"])
            self.assertEqual(sent.parent, runner_dir / "chat-uploads")
            self.assertEqual(sent.read_bytes(), PDF)
            seen.append(sent)
            return {"ok": True}
        with patch.object(agent.runners, "_row", return_value={"data_dir": str(runner_dir)}), patch.object(agent, "_request_json", side_effect=request):
            with self.assertRaises(agent.AgentToolError):
                agent.execute_tool(1, "upload_resume_file", {"attachment_id": item["id"]}, "other")
            result = agent.execute_tool(1, "upload_resume_file", {"attachment_id": item["id"], "ref": "a"}, "session")
        self.assertTrue(result["ok"])
        self.assertFalse(seen[0].exists())


if __name__ == "__main__":
    unittest.main()
