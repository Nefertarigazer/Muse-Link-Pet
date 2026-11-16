"""The three plugin tools end to end through plugin.tools / MCP / CLI."""
import unittest

import _support  # noqa: F401
from _support import Sandbox, entry, atomic, ROOT
from explib import import_recipes, schema
from explib.store import Store
from plugin import tools, mcp_server, gate
from unittest import mock
from datetime import date, datetime
import io
import json
from pathlib import Path
import subprocess
import sys
import os
import shutil


class Tools(Sandbox):
    def listed(self, agent):
        messages = [dict(jsonrpc="2.0", id=1, method="initialize", params={"protocolVersion": "2025-06-18"}),
                    dict(jsonrpc="2.0", method="notifications/initialized"),
                    dict(jsonrpc="2.0", id=2, method="tools/list")]
        out = io.StringIO()
        mcp_server.serve(agent, self.base, io.StringIO("\n".join(json.dumps(m) for m in messages)), out)
        return {t["name"] for t in json.loads(out.getvalue().splitlines()[-1])["result"]["tools"]}
    def test_listed_and_tiered(self):
        """Pass: MCP tools/list shows exp_lookup to everyone, exp_record/exp_feedback only to a
        registered agent; calling them unregistered -> denied; no consent card is ever asked."""
        self.assertTrue({"exp_lookup", "exp_record", "exp_feedback"} <= self.listed("codex"))
        self.assertIn("exp_lookup", self.listed("visitor"))
        self.assertFalse({"exp_record", "exp_feedback"} & self.listed("visitor"))
        self.assertEqual([tools.TOOLS[n].tier for n in ("exp_lookup", "exp_record", "exp_feedback")], ["read", "local", "local"])
        args = dict(app="sample.exe", goal="goal", from_trace=False, method=["step"], verify="verify")
        with mock.patch("plugin.ask.ask_user") as ask, mock.patch("plugin.executor_client.call") as host:
            self.assertEqual(tools.invoke("exp_record", args, "visitor", self.base)["status"], "denied")
            self.assertEqual(tools.invoke("exp_feedback", {"id": "x-aaaaaaaaaaaa", "worked": True}, "visitor", self.base)["status"], "denied")
            self.assertEqual(tools.invoke("exp_record", args, "codex", self.base)["status"], "ok")
            self.assertEqual(gate.decide("local", True, muse_at=0, now=0)["decision"], "allow")
            ask.assert_not_called()
            host.assert_not_called()

    def test_record_from_trace_confirm_lookup_feedback(self):
        """Pass: real tools.invoke() calls observe/act (fake host) -> exp_record(from_trace) draft
        -> confirm -> exp_lookup hit -> exp_feedback(worked) works+1; audit rows carry no goal text."""
        def invoke(name, args):
            value = tools.invoke(name, args, "codex", self.base)
            self.assertEqual(value["status"], "ok", value)
            return value
        with mock.patch("plugin.executor_client.call", return_value={"status": "ok", "readback": "SECRET"}) as host, \
                mock.patch("plugin.ask.ask_user", return_value={"status": "ok", "kind": "choice", "result": gate.t(gate.ALLOW_30)}):
            invoke("observe", {"app": "sample.exe"})
            invoke("act", {"app": "sample.exe", "query": {"name": "editor"}})
            invoke("verify", {"app": "sample.exe", "query": {"name": "result"}})
            self.assertEqual(host.call_count, 3)
        draft = invoke("exp_record", {"app": "sample.exe", "goal": "PRIVATE_GOAL", "from_trace": True})
        self.assertEqual(draft["entry"]["status"], "draft")
        confirmed = invoke("exp_record", {"confirm": draft["id"], "edits": {"ui": "contenteditable"}})
        self.assertEqual(confirmed["entry"]["status"], "valid")
        hits = invoke("exp_lookup", {"app": "sample.exe", "goal": "PRIVATE_GOAL"})["hits"]
        self.assertEqual(hits[0]["entry"]["id"], draft["id"])
        feedback = invoke("exp_feedback", {"id": draft["id"], "worked": True})
        self.assertEqual(feedback["entry"]["works"], confirmed["entry"]["works"] + 1)
        raw = (self.base / "plugin/audit.jsonl").read_text(encoding="utf-8")
        raw += (self.base / "plugin/trace/codex.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_GOAL", raw)
        self.assertNotIn("SECRET", raw)
        self.assertTrue(all("at" in json.loads(line) for line in raw.splitlines()))

    def test_cli_matches_tools(self):
        """Pass: python -m explib lookup/record/feedback with --args-file give the same results and
        exit codes as the tools."""
        args = dict(app="sample.exe", goal="发送一条消息", from_trace=False, method=["发送"], verify="消息出现")
        proc = self.cli("record", args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        draft = json.loads(proc.stdout)
        self.assertEqual(draft["entry"]["status"], "draft")
        self.assertEqual(self.cli("confirm", {"id": draft["id"]}).returncode, 0)
        lookup_args = dict(app="sample.exe", goal="发送")
        proc = self.cli("lookup", lookup_args)
        expected = tools.invoke("exp_lookup", lookup_args, "codex", self.base)
        self.assertEqual(json.loads(proc.stdout), expected)
        proc = self.cli("feedback", {"id": draft["id"], "worked": True})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["entry"]["works"], 1)
        self.assertEqual(self.cli("record", args, "visitor").returncode, 10)
        self.assertEqual(self.cli("feedback", {"id": "bad", "worked": "yes"}).returncode, 1)
        muse = self.cli("record", args, "muse")
        self.assertEqual(json.loads(muse.stdout)["entry"]["agent"], "muse")

    def test_import_recipes_dry_run(self):
        """Pass: a two-section ledger in the recipes format parses to two entries; an unparsable
        section is listed; a second run adds nothing."""
        ledger = """# synthetic ledger
### 2026-11-01 · Sample.EXE
- 界面形态：编辑器
- 目标：发送文字
- 成功方法：定位输入框后发送
- 验证：结果出现
- 状态：有效。
- 备注：合成样本

### 2026-11-02 · Other.EXE
- 界面形态：菜单
- 目标：打开设置
- 成功方法：点击设置
- 验证：菜单出现
- 状态：待复核

### broken heading
- 目标：缺少字段
"""
        path = self.base / "synthetic-ledger.md"
        path.write_text(ledger, encoding="utf-8")
        dry = import_recipes.run(path, self.base, True)
        self.assertEqual((dry["added"], dry["skipped"]), (2, ["broken heading"]))
        self.assertEqual([e["status"] for e in dry["entries"]], ["valid", "review"])
        self.assertFalse((self.base / "experience/local").exists())
        result = import_recipes.run(path, self.base)
        self.assertEqual(result["added"], 2)
        self.assertTrue(all(e["source"] == "local" and e["agent"] == "import" and e["works"] == 1 for e in result["entries"]))
        self.assertEqual(import_recipes.run(path, self.base)["added"], 0)
        self.assertEqual(len(Store(self.base).entries), 2)

    def test_vm_request_generator_carries_text_in_json_only(self):
        text = (Path(ROOT) / "vm/exp.sh").read_text(encoding="utf-8")
        body = text.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        path = self.base / "method.json"
        atomic(path, ["发送中文 ' $() ` ;"])
        proc = subprocess.run([sys.executable, "-", "record", "sample.exe", "目标 $()", str(path), "消息出现"],
                              input=body, capture_output=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        value = json.loads(proc.stdout)
        self.assertFalse(value["from_trace"])
        self.assertEqual(value["goal"], "目标 $()")
        self.assertEqual(value["method"], ["发送中文 ' $() ` ;"])
        # confirm: the draft id only, the shape the PC CLI's confirm takes
        proc = subprocess.run([sys.executable, "-", "confirm", "x-0123456789ab"],
                              input=body, capture_output=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"id": "x-0123456789ab"})
        self.assertIn("lookup|record|confirm|feedback)", text)

    def test_daily_task_plan_and_real_wrapper(self):
        # No -Apply: inspect the actual objects built by the registration
        # script, without registering even a temporary Windows task.
        registration = str(Path(ROOT) / "pc/install/register-tasks.ps1").replace("'", "''")
        location = str(self.base).replace("'", "''")
        script = ". '" + registration + "' -ToolDir '" + location + "' -IncludeExplib; " + r'''
$p = $plan | Where-Object { $_.Name -eq 'explib-share' }
@{ name=$p.Name; run=[string]$p.Principal.RunLevel; logon=[string]$p.Principal.LogonType;
   execute=$p.Action.Execute; arguments=$p.Action.Arguments; at=$p.Trigger.StartBoundary } | ConvertTo-Json -Compress
'''
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual((plan["name"], plan["run"], plan["logon"]), ("explib-share", "Limited", "Interactive"))
        scheduled = datetime.fromisoformat(plan["at"].replace("Z", "+00:00")).astimezone()
        self.assertEqual(scheduled.strftime("%H:%M"), "12:30")
        self.assertEqual(plan["execute"], str(self.base / "launcher.exe"))
        self.assertEqual(plan["arguments"], '"' + str(self.base / "explib/share-check.ps1") + '"')
        for name in ("plugin", "explib", "i18n"):
            shutil.copytree(Path(ROOT) / "pc" / name, self.base / name, ignore=shutil.ignore_patterns("__pycache__"))
        atomic(self.base / "experience/share.json", {"never": True})
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(self.base / "explib/share-check.ps1")],
                              capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"status": "ok", "reason": "never"})

    def test_confirm_rejects_management_edits_and_other_agents(self):
        draft = self.save(entry(status="draft"))
        for edits in ({"works": 100}, {"source": "public"}, {"agent": "community"}, {"id": schema.new_id()}):
            result = tools.invoke("exp_record", {"confirm": draft["id"], "edits": edits}, "codex", self.base)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(Store(self.base).get(draft["id"])["status"], "draft")
        result = tools.invoke("exp_record", {"confirm": draft["id"]}, "muse", self.base)
        self.assertEqual(result["status"], "fail")

    def test_public_feedback_through_tools_and_muse_cli(self):
        public_entry = self.save(entry(source="public", works=1))
        path = self.store.path(public_entry, "public")
        before = path.read_bytes()
        args = {"id": public_entry["id"], "worked": True}
        result = tools.invoke("exp_feedback", args, "codex", self.base)
        self.assertEqual((result["status"], result["entry"]["works"]), ("ok", 2))
        (self.base / "identities/muse.json").unlink()
        proc = self.cli("feedback", args, agent="muse")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["entry"]["works"], 3)
        proc = self.cli("lookup", {"app": public_entry["app"]}, agent="muse")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        hit = json.loads(proc.stdout)["hits"][0]
        self.assertEqual((hit["works"], hit["entry"]["works"], hit["source"]), (3, 3, "public"))
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()


class MuseThroughCli(Sandbox):
    """Kiro review: Muse is never a locally enrolled agent; vm/exp.sh must still work."""

    def test_muse_records_and_reports_without_a_local_identity(self):
        (self.base / "identities/muse.json").unlink()
        method = [{"step": "打开设置", "tool": "act", "args": {"app": "sample.exe"}}, "核对开关"]
        proc = self.cli("record", {"app": "Sample.exe", "goal": "打开设置页", "method": method,
                                   "from_trace": False, "verify": "设置页标题出现"}, agent="muse")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        draft = json.loads(proc.stdout)
        self.assertEqual((draft["entry"]["status"], draft["entry"]["agent"]), ("draft", "muse"))
        proc = self.cli("confirm", {"id": draft["id"]}, agent="muse")
        self.assertEqual(json.loads(proc.stdout)["entry"]["status"], "valid")
        proc = self.cli("feedback", {"id": draft["id"], "worked": True}, agent="muse")
        self.assertEqual(json.loads(proc.stdout)["entry"]["works"], 1)
        rows = (self.base / "plugin/audit.jsonl").read_text(encoding="utf-8")
        self.assertIn('"agent": "muse"', rows)
        self.assertNotIn("打开设置页", rows)
        # another local agent without an identity is still denied
        self.assertEqual(self.cli("feedback", {"id": draft["id"], "worked": True}, agent="stranger").returncode, 10)
