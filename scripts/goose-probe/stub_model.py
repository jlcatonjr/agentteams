"""Stub OpenAI chat-completions server: logs offered tools, requests one tool call, then finishes."""
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

TARGET = sys.argv[2]          # tool-name suffix to request, e.g. write_probe
ARGS = json.loads(sys.argv[3])  # arguments for that call
LOG = sys.argv[4]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, stream):
        if stream:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            msg = body["choices"][0]["message"]
            delta = {"role": "assistant"}
            if msg.get("tool_calls"):
                delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(msg["tool_calls"])]
            else:
                delta["content"] = msg.get("content", "")
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            fin = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": "stub",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": body["choices"][0]["finish_reason"]}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            for c in (chunk, fin):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
        else:
            raw = json.dumps(body).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "stub", "object": "model"}]}, False)
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = [t["function"]["name"] for t in req.get("tools", [])]
        msgs = req.get("messages", [])
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        open(LOG + ".tools.json", "w").write(json.dumps(req.get("tools", []), indent=1)) if req.get("tools") else None
        with open(LOG, "a") as f:
            f.write(json.dumps({"tools": sorted(tools), "tool_results": [str(m.get("content"))[:300] for m in tool_results]}) + "\n")
        if not tool_results:
            name = next((t for t in tools if t == TARGET or t.endswith("__" + TARGET)), TARGET)
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(ARGS)}}]}
            fr = "tool_calls"
        else:
            msg = {"role": "assistant", "content": "done"}; fr = "stop"
        body = {"id": "x", "object": "chat.completion", "created": int(time.time()), "model": "stub",
                "choices": [{"index": 0, "message": msg, "finish_reason": fr}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        self._send(body, bool(req.get("stream")))

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
