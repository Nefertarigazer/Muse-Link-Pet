import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pc"))
from plugin._shared import atomic


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="muselink-plugin-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        atomic(self.base / "executor-token.txt", "test-token")
        atomic(self.base / "identities/codex.json", {"v": 1, "agent_id": "codex", "display_name": "Codex"})

    def scripts(self):
        shutil.copytree(ROOT / "pc/i18n", self.base / "i18n", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("muselink-common.ps1", "popup.ps1", "Complete-MuseWait.ps1", "wait-popup.ps1"):
            shutil.copy2(ROOT / "pc" / name, self.base / name)
        # No popup-sound.txt; no production task or installation is used.

    def env(self):
        return dict(os.environ, PYTHONPATH=str(ROOT / "pc"), PYTHONDONTWRITEBYTECODE="1")


class FakeHost:
    def __init__(self, base, reply):
        self.reply, self.requests = reply, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.1)
        atomic(base / "muselink.json", {"executor": {"port": self.sock.getsockname()[1]}})
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                data = bytearray()
                while b"\n" not in data:
                    data.extend(conn.recv(65536))
                req = json.loads(data.split(b"\n", 1)[0])
                self.requests.append(req)
                response = self.reply(req) if callable(self.reply) else self.reply
                conn.sendall((json.dumps(response) + "\n").encode())

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()
