"""Proof helper: logs every POST (headers that matter and the body) to /data/hook.log."""
import http.server
import json
import time


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode()
        rec = {
            "t": time.time(),
            "path": self.path,
            "secret": self.headers.get("X-Hook-Secret"),
            "body": body,
            "ua": self.headers.get("User-Agent"),
            "ct": self.headers.get("Content-Type"),
        }
        with open("/data/hook.log", "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


http.server.HTTPServer(("0.0.0.0", 8000), H).serve_forever()
