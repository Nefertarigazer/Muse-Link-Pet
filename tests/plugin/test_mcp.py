import json
import subprocess
import sys
import time

from plugin_support import ROOT, FakeHost, Sandbox
from plugin._shared import atomic


class McpTests(Sandbox):
    def start(self, agent="codex"):
        proc = subprocess.Popen([sys.executable, "-m", "plugin.mcp_server", "--agent", agent, "--tool-dir", str(self.base)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", env=self.env(), cwd=ROOT)
        def stop():
            proc.stdin.close()
            proc.wait(timeout=10)
            self.assertEqual(proc.stderr.read(), "")
            proc.stdout.close()
            proc.stderr.close()
        self.addCleanup(stop)
        return proc

    def rpc(self, proc, method, params=None, id=1):
        proc.stdin.write(json.dumps(dict(jsonrpc="2.0", id=id, method=method, params=params or {})) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    def initialize(self, proc, version="2025-06-18"):
        response = self.rpc(proc, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        return response["result"]

    def test_handshake_list_and_fake_host_roundtrip(self):
        host = FakeHost(self.base, {"ok": True, "out": '{"status":"resolved","count":1}'})
        self.addCleanup(host.close)
        proc = self.start()
        self.assertEqual(self.initialize(proc)["protocolVersion"], "2025-06-18")
        listed = self.rpc(proc, "tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in listed}, {
            "observe", "resolve", "verify", "act", "uia_locate", "uia_read_text", "uia_focused",
            "uia_focus", "uia_invoke", "seq", "shot", "ping", "ask_user", "notify_user",
            "exp_lookup", "exp_record", "exp_feedback"})
        response = self.rpc(proc, "tools/call", {"name": "resolve", "arguments": {"app": "记事本", "query": {"name": "Edit"}}})["result"]
        self.assertFalse(response["isError"])
        self.assertEqual(json.loads(response["content"][0]["text"])["status"], "ok")
        req = host.requests[0]
        self.assertEqual(req["token"], "test-token")
        self.assertEqual(req["job"], "plugin:codex:resolve")
        self.assertTrue(time.time() < req["deadline"] <= time.time() + 60)

    def test_screenshot_is_an_image_item(self):
        # Kiro review: the model should see the picture, not base64 inside JSON text
        out = '{"fmt":"jpeg","w":4,"h":3}\nAAAA\n'
        host = FakeHost(self.base, {"ok": True, "out": out})
        self.addCleanup(host.close)
        proc = self.start()
        self.initialize(proc)
        result = self.rpc(proc, "tools/call", {"name": "shot", "arguments": {"mode": "screen"}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][1], {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"})
        text = json.loads(result["content"][0]["text"])
        self.assertEqual(text["image"], {"metadata": {"fmt": "jpeg", "w": 4, "h": 3}})

    def test_errors_unknown_and_protocol_recovery(self):
        host = FakeHost(self.base, {"ok": True, "out": '{"status":"unknown","error":"TIMEOUT"}'})
        self.addCleanup(host.close)
        proc = self.start()
        self.assertEqual(self.rpc(proc, "tools/list")["error"]["code"], -32002)
        self.assertEqual(self.initialize(proc, "2099-01-01")["protocolVersion"], "2025-11-25")
        for name, arguments, status in (("resolve", {"app": "A", "query": {"name": "N"}}, "unknown"),
                                         ("script", {}, "fail"), ("observe", {"app": "A", "token": "bad"}, "fail")):
            result = self.rpc(proc, "tools/call", {"name": name, "arguments": arguments})["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["status"], status)
        proc.stdin.write("broken-json\n")
        proc.stdin.flush()
        self.assertEqual(json.loads(proc.stdout.readline())["error"]["code"], -32700)
        self.assertEqual(self.rpc(proc, "missing")["error"]["code"], -32601)
        self.assertEqual(self.rpc(proc, "ping")["result"], {})

    def test_unregistered_tools_are_read_only_even_if_called_by_name(self):
        proc = self.start("visitor")
        self.initialize(proc)
        names = {t["name"] for t in self.rpc(proc, "tools/list")["result"]["tools"]}
        self.assertEqual(names, {"observe", "resolve", "verify", "uia_locate", "uia_read_text", "uia_focused", "shot", "ping", "exp_lookup"})
        for name in ("act", "seq", "ask_user"):
            result = self.rpc(proc, "tools/call", {"name": name})["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["status"], "denied")

    def test_cli_stdin_args_file_and_exit_codes(self):
        host = FakeHost(self.base, lambda req: {"ok": True, "out": json.dumps({"status": "unknown" if req.get("mode") == "full" else "ok"})})
        self.addCleanup(host.close)
        prefix = [sys.executable, "-m", "plugin"]
        args = ["--agent", "codex", "--tool-dir", str(self.base)]
        for name, data, code in (("observe", {"app": "A"}, 0), ("observe", {"app": "A", "mode": "full"}, 4), ("shell", {}, 1)):
            p = subprocess.run([*prefix, name, *args], input=json.dumps(data), capture_output=True, encoding="utf-8", env=self.env())
            self.assertEqual(p.returncode, code, p.stderr)
            self.assertEqual(len(p.stdout.splitlines()), 1)
        path = self.base / "args.json"
        path.write_text('{"app":"A"}', encoding="utf-8")
        p = subprocess.run([*prefix, "act", "--agent", "visitor", "--tool-dir", str(self.base), "--args-file", str(path)], capture_output=True, env=self.env())
        self.assertEqual(p.returncode, 1)  # query is required, so no consent prompt
        path.write_text('{"app":"A","query":{"name":"N"}}')
        p = subprocess.run([*prefix, "act", "--agent", "visitor", "--tool-dir", str(self.base), "--args-file", str(path)], capture_output=True, env=self.env())
        self.assertEqual(p.returncode, 10)
        atomic(self.base / "executor-events.jsonl", json.dumps({"ts": time.time(), "job": "muse"}) + "\n")
        p = subprocess.run([*prefix, "act", *args, "--args-file", str(path)], capture_output=True, env=self.env())
        self.assertEqual(p.returncode, 5)
