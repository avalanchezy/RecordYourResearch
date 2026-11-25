# -*- coding: utf-8 -*-
"""测试用的假 llama-server：只实现 /health 和 /v1/chat/completions（返回 FAKE_LLM_REPLY 里的 JSON）。"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send(200, {"status": "ok"}) if self.path == "/health" else self._send(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        assert body["response_format"]["type"] == "json_schema"
        reply = os.environ.get("FAKE_LLM_REPLY", '{"stage": "review"}')
        self._send(200, {"choices": [{"message": {"content": reply}}]})


def main():
    a = sys.argv[1:]
    port = int(a[a.index("--port") + 1])
    assert a[a.index("--host") + 1] == "127.0.0.1"
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
