"""Tiny stand-in for the GitHub contents API, for local tests only.
GET/PUT /repos/<owner>/<repo>/contents/<path> read and write files under REPO_DIR.
Usage: python3 mock_github.py <repo_dir> [port]"""
import base64, hashlib, json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
LOG = ROOT / "mock_github_commits.log"

def target(path):
    parts = urlsplit(path).path.split("/contents/", 1)
    if len(parts) != 2 or ".." in parts[1]:
        return None
    return ROOT / parts[1]

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p = target(self.path)
        if not self.headers.get("authorization", "").startswith("Bearer "): return self._send(401, {"message": "Bad credentials"})
        if not p or not p.exists(): return self._send(404, {"message": "Not Found"})
        raw = p.read_bytes()
        self._send(200, {"content": base64.b64encode(raw).decode(), "sha": hashlib.sha1(raw).hexdigest(), "path": str(p.relative_to(ROOT))})
    def do_PUT(self):
        p = target(self.path)
        if not p: return self._send(404, {"message": "Not Found"})
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if p.exists() and body.get("sha") != hashlib.sha1(p.read_bytes()).hexdigest():
            return self._send(409, {"message": "sha mismatch"})
        if not p.exists() and body.get("sha"):
            return self._send(409, {"message": "file does not exist"})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(body["content"]))
        with LOG.open("a") as f: f.write(json.dumps({"path": str(p.relative_to(ROOT)), "message": body["message"], "branch": body.get("branch"), "committer": body.get("committer")}) + "\n")
        self._send(201, {"content": {"path": str(p.relative_to(ROOT))}, "commit": {"message": body["message"]}})
    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
