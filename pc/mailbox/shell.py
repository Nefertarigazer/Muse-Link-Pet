"""Shared static-only HTTP and authenticated per-user single-instance pipe."""
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
from multiprocessing.connection import Client, Listener

class Instance:
    def __init__(self, name="mailbox", scope=None):
        self.name = name
        scoped_name = name if scope is None else name + '-' + hashlib.sha256(str(scope).encode()).hexdigest()[:24]
        # Windows kernel ACLs default to this user's token. The pipe further
        # authenticates both endpoints with a per-user DPAPI-protected key.
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._mutex = self._kernel.CreateMutexW(None, False, "muselink-" + scoped_name + "-" + getpass.getuser())
        if not self._mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.primary = ctypes.get_last_error() != 183
        identity = os.environ.get("USERDOMAIN", "") + "\\" + getpass.getuser()
        self.address = r"\\.\pipe\muselink-" + scoped_name + "-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self._key_name = scoped_name
        # CryptProtectData normally uses randomness, so a persisted shared key
        # is required; keep it in the user's private LocalAppData, never ui/.
        self.authkey = self._key()
        self.listener = None

    def _key(self):
        import secrets
        import clr
        clr.AddReference("System.Security")
        from System import Array, Byte
        from System.Security.Cryptography import ProtectedData, DataProtectionScope
        directory = Path(os.environ["LOCALAPPDATA"]) / "MuseLink" / self._key_name
        path = directory / "pipe-key.dpapi"
        if self.primary:
            directory.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                protected = ProtectedData.Protect(Array[Byte](secrets.token_bytes(32)), None, DataProtectionScope.CurrentUser)
                temp = path.with_suffix(".tmp")
                temp.write_bytes(bytes(protected))
                os.replace(temp, path)
        for _ in range(100):
            try:
                return bytes(ProtectedData.Unprotect(Array[Byte](path.read_bytes()), None, DataProtectionScope.CurrentUser))
            except FileNotFoundError:
                time.sleep(.05)
        raise RuntimeError("pipe key unavailable")

    def forward(self, thread):
        for _ in range(100):
            try:
                with Client(self.address, family="AF_PIPE", authkey=self.authkey) as pipe:
                    pipe.send_bytes(json.dumps(thread if self.name == "pet" else {"thread": thread}, ensure_ascii=False).encode("utf-8"))
                return
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(.05)
        raise RuntimeError(self.name + " startup timed out")

    def listen(self, callback):
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        def run():
            while self.listener is not None:
                try:
                    with self.listener.accept() as pipe:
                        value = json.loads(pipe.recv_bytes(65536).decode("utf-8"))
                    if self.name == "pet":
                        if value == {"show": True} or (isinstance(value, dict) and set(value) == {"mailbox"}
                                and (value["mailbox"] is None or isinstance(value["mailbox"], str))):
                            callback(value)
                    elif isinstance(value, dict) and (value.get("thread") is None or isinstance(value.get("thread"), str)):
                        callback(value.get("thread"))
                except (OSError, EOFError, ValueError):
                    continue
                except Exception:
                    continue
        threading.Thread(target=run, name=self.name + "-pipe", daemon=True).start()

    def close(self):
        listener, self.listener = self.listener, None
        if listener:
            listener.close()
        self._kernel.CloseHandle(self._mutex)


def static_server_class(ui_root):
    """pywebview server protocol, restricted to static GET/HEAD in ui/.

    Its default BottleServer also registers a POST /js_api route, even for
    Edge's native bridge. Omit that route so there is no data HTTP endpoint.
    """
    import bottle
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
    from webview.http import BottleServer

    # Override registry associations even when this server is tested alone.
    mimetypes.init()
    for suffix, mime in {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json", ".html": "text/html", ".svg": "image/svg+xml"}.items():
        mimetypes.add_type(mime, suffix)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *unused):
            pass

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class UiServer(BottleServer):
        @classmethod
        def start_server(cls, urls, http_port, **unused):
            server = cls()
            root = Path(ui_root).resolve()
            app = bottle.Bottle()

            @app.route("/i18n/i18n.js", method=["GET", "HEAD"])
            def language_script():
                bottle.response.set_header("Cache-Control", "no-store")
                return bottle.static_file("i18n.js", root=str(Path(__file__).resolve().parents[1] / "i18n"))

            @app.route("/")
            @app.route("/<file:path>", method=["GET", "HEAD"])
            def asset(file="index.html"):
                target = (root / file).resolve()
                if not target.is_relative_to(root):
                    bottle.abort(403)
                bottle.response.set_header("Cache-Control", "no-store")
                return bottle.static_file(file, root=str(root))

            server._httpd = make_server("127.0.0.1", http_port or 0, app,
                                       server_class=ThreadedServer, handler_class=QuietHandler)
            server.port = server._httpd.server_port
            server.address = "http://127.0.0.1:%d/" % server.port
            server.root_path = server.common_path = str(root)
            server.running = True
            server.thread = threading.Thread(target=server._httpd.serve_forever, daemon=True)
            server.thread.start()
            return server.address, str(root), server

    return UiServer
