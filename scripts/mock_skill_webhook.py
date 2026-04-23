#!/usr/bin/env python
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}

        response = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "mode": "http",
                            "received_tool": payload.get("tool"),
                            "received_arguments": payload.get("arguments", {}),
                            "status": "ok",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            "isError": False,
        }

        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8765), Handler)
    server.serve_forever()
