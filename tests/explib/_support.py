"""Shared helpers for the extension 5 tests (skeleton: path setup only).

Implementer: add a temporary tool dir factory here (experience/, plugin/
trace/, identities/<agent>.json, muselink.json with explib settings) and a
fake ask_fn / fake fetch / fake gh runner. Tests never touch
C:\\Users\\<user>\\<tool-dir> or the network.
"""
import os
import sys
import copy
from datetime import date
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "pc"))

from plugin._shared import atomic, read_json
from explib import schema
from explib.store import Store


def entry(**changes):
    day = date.today().isoformat()
    result = dict(id=schema.new_id(), schema=1, app="sample.exe", app_version="1.4.2",
                  ui="Electron", goal="向输入框写入文字并发送", method=["定位输入框", "发送"],
                  verify="消息出现", status="valid", created=day, last_verified=day,
                  works=2, fails=0, source="local", agent="codex", tags=["input"])
    result.update(changes)
    return result


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="muselink-explib-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        atomic(self.base / "identities/codex.json", {"v": 1, "agent_id": "codex", "display_name": "Codex"})
        atomic(self.base / "identities/muse.json", {"v": 1, "agent_id": "muse", "display_name": "Muse"})
        self.configure({"explib": {"redact": {"literals": []}, "public": {"index_url": "", "repo": ""}}})
        self.store = Store(self.base)

    def configure(self, cfg):
        atomic(self.base / "muselink.json", cfg)

    def save(self, value):
        if value["source"] == "public":
            atomic(self.store.path(value, "public"), schema.render(value))
            self.store.load()
        else:
            self.store.put(value)
        return value

    def state(self):
        return read_json(self.base / "experience/share.json", {})

    def cli(self, verb, args, agent="codex"):
        path = self.base / "args.json"
        atomic(path, args)
        return subprocess.run([sys.executable, "-m", "explib", verb, "--agent", agent,
                               "--tool-dir", str(self.base), "--args-file", str(path)],
                              capture_output=True, encoding="utf-8", timeout=20,
                              env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, "pc"), PYTHONUTF8="1"))


class FakeAsk:
    def __init__(self, answer=None, status="ok", kind="choice"):
        self.answer, self.status, self.kind, self.calls = answer, status, kind, []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return dict(status=self.status, kind=self.kind, result=self.answer)


class FakeFetch:
    def __init__(self, values):
        self.values, self.calls = values, []

    def __call__(self, url, headers, limit):
        self.calls.append((url, headers, limit))
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value


class FakeGh:
    def __init__(self, authorized=True):
        self.calls, self.authorized = [], authorized

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        endpoint = args[2] if args[1] == "api" else "auth"
        response = {"auth": {}, "user": {"login": "test-user"},
                    "repos/team/library": {"default_branch": "main"},
                    "repos/team/library/forks": {"full_name": "test-user/library"},
                    "repos/test-user/library/git/ref/heads/main": {"object": {"sha": "a" * 40}},
                    "repos/team/library/pulls": {"html_url": "https://example.invalid/pr/1"}}.get(endpoint, {})
        # auth status writes human text in real gh; caller must not JSON-parse it.
        stdout = "Logged in to github.com\n" if endpoint == "auth" else json.dumps(response)
        return subprocess.CompletedProcess(args, 0 if self.authorized else 1, stdout, "")
