"""Minimal OpenAI-compatible mock for local agent-loop tests (no real LLM needed)."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        has_tool_result = any(message.get("role") == "tool" for message in messages)

        if has_tool_result:
            message = {
                "role": "assistant",
                "content": "已读取当前配置：关键词是大模型应用开发/LLM应用，城市上海。需要我按这个计划开始投递吗？",
            }
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_mock_1",
                        "type": "function",
                        "function": {
                            "name": "get_channel_config",
                            "arguments": json.dumps({"channel": "boss"}),
                        },
                    }
                ],
            }

        payload = {
            "id": "mock-completion",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9999), Handler).serve_forever()