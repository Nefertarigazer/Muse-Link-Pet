"""preview.py - serve this UI folder on 127.0.0.1 for a browser preview of the
demo data (frontend development only; the real window is the backend's).

    python preview.py [port]     then open http://127.0.0.1:<port>/index.html?demo

Why not `python -m http.server`: on Windows it takes MIME types from the
registry, which often maps .js to text/plain, and browsers refuse to run
ES modules served that way (blank page). The backend must apply the same
fixed table (docs/architecture.en.md section 7).
"""
import functools
import http.server
import os
from pathlib import Path
import sys

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = TYPES

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean in ("/i18n/i18n.js", "/i18n/en.json", "/i18n/zh-Hans.json", "/i18n/zh-Hant.json"):
            return str(Path(__file__).resolve().parents[2] / "i18n" / clean.rsplit("/", 1)[1])
        return super().translate_path(path)

    def guess_type(self, path):
        return TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=os.path.dirname(os.path.abspath(__file__)))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as srv:
        print("http://127.0.0.1:%d/index.html?demo" % port, flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
