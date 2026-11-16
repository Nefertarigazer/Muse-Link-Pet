import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest import mock

from plugin_support import ROOT, Sandbox
from plugin import executor_client, tools, trace
from plugin._install import merge


def script_request(script, argv):
    """Run the actual checked-in shell script's Python request generator."""
    text = (ROOT / "vm" / script).read_text(encoding="utf-8")
    body = text.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
    p = subprocess.run([sys.executable, "-", *argv], input=body, capture_output=True, encoding="utf-8", check=True,
                       env=dict(os.environ, PYTHONUTF8="1"))
    return json.loads(p.stdout)


class ToolTests(Sandbox):
    def equal_request(self, name, args, script, argv):
        expected = script_request(script, argv)
        expected["job"] = "plugin:codex:" + name
        self.assertEqual(tools.build_request(name, args, "codex"), expected)

    def test_all_semantic_verbs_match_sem_sh(self):
        self.equal_request("observe", {"app": "记事本"}, "sem.sh", ["observe", "记事本"])
        self.equal_request("resolve", {"app": "Kiro.exe", "query": {"name": "Minimize"}, "find_timeout_ms": 30000},
                           "sem.sh", ["resolve", "Kiro.exe", "Minimize", "--find-timeout-ms", "30000"])
        self.equal_request("observe", {"app": "A", "mode": "full", "surface": "menu"}, "sem.sh", ["observe", "A", "full", "--surface", "menu"])
        query = {"name": "发送", "exact": True, "role": "button", "path_contains": "chat", "path_excludes": "menu"}
        flags = ["发送", "--exact", "--role", "button", "--path-contains", "chat", "--path-excludes", "menu", "--surface", "sheet"]
        for verb in ("resolve", "verify", "act"):
            args = dict(app="微信", query=query, surface="sheet")
            tail = []
            if verb == "verify":
                args["should"] = "absent"
                tail = ["absent"]
            if verb == "act":
                args.update(action="press", key="ENTER", nth=2,
                            expect={"query": {"name": "已发", "exact": True}, "should": "absent", "text": "body"})
                tail = ["press", "--key", "ENTER", "--nth", "2", "--expect-name", "已发", "--expect-should", "absent", "--expect-text", "body"]
            self.equal_request(verb, args, "sem.sh", [verb, "微信", flags[0], *tail, *flags[1:]])

    def test_every_uia_verb_matches_uia_sh(self):
        for op in ("locate", "read_text", "focused", "focus", "invoke"):
            self.equal_request("uia_" + op, {"app": "A"}, "uia.sh", [op, "A"])
            self.equal_request("uia_" + op, dict(app="A", query=dict(name="N", exact=True, controlType="Button", classContains="X")),
                               "uia.sh", [op, "A", "N", "--exact", "--type", "Button", "--class", "X"])

    def test_seq_matches_seq_sh(self):
        steps = [{"verb": "act_if", "app": "A", "query": {"name": "N"}, "unless": {"name": "Done"},
                  "alternatives": [{"kind": "uia", "op": "invoke", "app": "B", "query": {"name": "N"}}]}, {"verb": "sleep", "ms": 10}]
        path = self.base / "recipe.json"
        path.write_text(json.dumps({"job": "recipe", "steps": steps}), encoding="utf-8")
        self.equal_request("seq", {"steps": steps, "timeout": 75}, "seq.sh", [str(path), "75"])
        self.assertEqual(tools._steps(steps), {"A", "B"})

    def test_shot_and_ping_are_fixed_requests(self):
        self.assertEqual(tools.build_request("ping", {}, "codex"), {"kind": "ping", "job": "plugin:codex:ping"})
        self.assertEqual(tools.build_request("shot", {"app": "A"}, "codex"),
                         {"kind": "script", "name": "muse-shot.ps1", "args": ["-Mode", "window", "-App", "A"], "job": "plugin:codex:shot"})
        self.assertEqual(tools.build_request("shot", {"mode": "screen"}, "codex")["args"], ["-Mode", "screen"])

    def test_every_enum_schema_declares_a_type(self):
        # Gemini/Vertex function-calling rejects enum schemas without a type.
        def walk(node, path):
            if isinstance(node, dict):
                if "enum" in node:
                    self.assertIn("type", node, path)
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")
        for name, tool in tools.TOOLS.items():
            walk(tool.schema, name)

    def test_three_state_mapping_ignores_outer_ok(self):
        for inner, expected in (("resolved", "ok"), ("ok", "ok"), ("fail", "fail"), ("unknown", "unknown"), ("expired", "fail")):
            r = tools.normalize({"ok": inner == "fail", "out": json.dumps({"status": inner, "error": "E"})}, "act")
            self.assertEqual(r["status"], expected)
        result = tools.normalize({"ok": True, "out": json.dumps({"status": "ok", "steps": [{"status": "ok", "prior_attempts": [{"status": "unknown"}]}]})}, "seq")
        self.assertEqual(result["status"], "unknown")
        self.assertIn("observe/verify", result["guidance"])
        self.assertEqual(tools.normalize({"ok": True, "out": "malformed"}, "act")["status"], "unknown")

    def test_unknown_timeout_and_bad_response_are_never_retried(self):
        with mock.patch("plugin.executor_client.socket.create_connection") as connect:
            connect.return_value.recv.side_effect = TimeoutError()
            self.assertEqual(executor_client.call({"kind": "ping"}, self.base)["status"], "unknown")
            self.assertEqual(connect.call_count, 1)

    def test_closed_surface_and_recursive_seq_validation(self):
        self.assertFalse({"script", "shell", "ad", "raw"} & tools.TOOLS.keys())
        for args in ({"app": "A", "job": "muse"}, {"app": "A", "query": {"name": "N", "ref": "@1"}}):
            self.assertEqual(tools.invoke("resolve", args, "codex", self.base)["status"], "fail")
        for step in ({"kind": "shell", "verb": "act", "app": "A"}, {"verb": "act", "app": "A", "deadline": 0},
                     {"verb": "sleep", "ms": -1}, {"verb": "act", "app": "A", "alternatives": [{"kind": "script", "app": "A"}]}):
            self.assertEqual(tools.invoke("seq", {"steps": [step]}, "codex", self.base)["status"], "fail")

    def test_trace_bound_and_body_exclusion(self):
        for i in range(53):
            trace.record(self.base, "codex", "resolve", ["A"], {"name": str(i), "text": "SECRET"}, "ok")
        raw = (self.base / "plugin/trace/codex.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(raw.splitlines()), 50)
        self.assertNotIn("SECRET", raw)
        self.assertEqual(json.loads(raw.splitlines()[0])["query"]["name"], "3")

    def test_installer_merges_and_backs_up_only_existing_files(self):
        config = {"command": "python", "args": ["-m", "plugin.mcp_server", "--agent", "codex"]}
        for name, original, toml in (("config.toml", '[mcp_servers.other]\ncommand = "keep"\n', True),
                                     ("mcp.json", '{"extra":1,"mcpServers":{"other":{"command":"keep"}}}', False)):
            path = self.base / name
            path.write_text(original, encoding="utf-8")
            merge(path, config, toml)
            after = path.read_text(encoding="utf-8")
            self.assertIn("keep", after)
            self.assertEqual(next(self.base.glob(name + ".*.bak")).read_text(encoding="utf-8"), original)
            merge(path, config, toml)
            self.assertEqual(path.read_text(encoding="utf-8"), after)
        absent = self.base / "absent.json"
        merge(absent, config)
        self.assertFalse(absent.exists())

    def test_register_extension_uses_same_gate(self):
        tool = tools.Tool("test_extension", "input", tools.obj(), lambda *a: {"status": "ok"}, "test")
        tools.register(tool)
        try:
            self.assertEqual(tools.invoke(tool.name, {}, "visitor", self.base)["status"], "denied")
        finally:
            del tools.TOOLS[tool.name]

    def test_installer_never_duplicates_inline_or_nested_entries(self):
        path = self.base / "config.toml"
        for original in ('[mcp_servers]\nmuselink = {command="keep"}\n',
                         '[mcp_servers."muselink".env]\nVALUE="keep"\n'):
            path.write_text(original, encoding="utf-8")
            merge(path, {"command": "new"}, True)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
        original = 'mcp_servers = {other={command="keep"}}\n'
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            merge(path, {"command": "new"}, True)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        original = '[mcp_servers."mu\\u0073elink"]\ncommand = "keep"\n'
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(ValueError):
            merge(path, {"command": "new"}, True)

    def test_installer_keeps_codex_project_tables_with_backslashes(self):
        path = self.base / "config.toml"
        original = "model = \"x\"\n\n[projects.'D:\\work\\repo']\ntrust_level = \"trusted\"\n\n[projects.\"C:\\\\Users\\\\user\"]\ntrust_level = \"trusted\"\n"
        path.write_text(original, encoding="utf-8")
        self.assertTrue(merge(path, {"command": "new"}, True).startswith("merged"))
        after = path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(original.rstrip()))
        self.assertIn("[mcp_servers.muselink]", after)

    def test_installer_creates_a_client_file_only_inside_an_installed_client(self):
        config = {"command": "python", "args": ["-c", "x", "--agent", "kiro"]}
        target = self.base / ".kiro/settings/mcp.json"
        self.assertIn("skipped", merge(target, config, create_in=self.base / ".kiro"))
        self.assertFalse(target.exists())
        (self.base / ".kiro").mkdir()
        self.assertTrue(merge(target, config, apply=False, create_in=self.base / ".kiro").startswith("new"))
        self.assertFalse(target.exists())
        self.assertTrue(merge(target, config, create_in=self.base / ".kiro").startswith("created"))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"mcpServers": {"muselink": config}})
        other = {"command": "python", "args": ["-c", "x", "--agent", "codex"]}
        self.assertIn("for agent kiro kept", merge(target, other, create_in=self.base / ".kiro"))
