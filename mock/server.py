"""Deterministic OpenAI-compatible upstream. Every completion reports 10 output tokens,
so with output_cost_per_token=0.00001 each admitted call costs exactly $0.0001.
GET /hits returns how many completions were served (ground truth); POST /reset zeroes it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

hits = 0
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"hits": hits})

    def do_POST(self):
        global hits
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with lock:
            hits = 0 if self.path == "/reset" else hits + 1
        if self.path == "/reset":
            return self._send({"hits": 0})
        self._send({
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 0, "model": "gpt-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        })

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
