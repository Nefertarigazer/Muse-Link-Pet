"""The postman keeps its old behaviour when muselink-id.sh is not deployed."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bash():
    for p in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if p and os.path.exists(p):
            return p
    return None


@unittest.skipUnless(_bash(), "bash is not available")
class PostmanFallback(unittest.TestCase):
    def test_old_path_without_identity_module(self):
        tmp = tempfile.mkdtemp(prefix="ml-postman-")
        try:
            vm = os.path.join(tmp, "vm")
            os.makedirs(os.path.join(vm, "lib"))
            os.makedirs(os.path.join(vm, "hooks"))
            os.makedirs(os.path.join(tmp, "pc", "inbox"))
            os.makedirs(os.path.join(tmp, "bridge"))
            os.makedirs(os.path.join(tmp, "state"))
            os.makedirs(os.path.join(tmp, "bin"))
            shim = os.path.join(tmp, "bin", "python3")
            with open(shim, "w", encoding="utf-8", newline="\n") as f:
                f.write("#!/usr/bin/env bash\nexec python \"$@\"\n")
            os.chmod(shim, 0o755)
            shutil.copy(os.path.join(ROOT, "vm", "hooks", "bridge-postman.sh"),
                        os.path.join(vm, "hooks", "bridge-postman.sh"))
            shutil.copy(os.path.join(ROOT, "vm", "hooks", "hook-lib.sh"),
                        os.path.join(vm, "hooks", "hook-lib.sh"))
            common = r'''#!/usr/bin/env bash
ML_VM_DIR="__VM__"
ML_BRIDGE_HOME="__TMP__/bridge"
PC_BRIDGE_DIR="__TMP__/pc"
ML_PC_BRIDGE_FWD="__TMP__/pc"
ML_STATE_DIR="__TMP__/state"
ml_fix_owner() { :; }
ml_valid_id() { [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$ ]]; }
ml_psq() { printf "'%s'" "$1"; }
ml_ps() {
  case "$1" in
    *Get-ChildItem*) ls "__TMP__/pc/inbox" 2>/dev/null; echo LIST_OK ;;
    *) : ;;
  esac
}
ml_scp_from() { cp "__TMP__/pc/inbox/$(basename "$1")" "$2"; }
'''.replace("__VM__", vm.replace("\\", "/")).replace("__TMP__", tmp.replace("\\", "/"))
            with open(os.path.join(vm, "lib", "muselink-common.sh"), "w", encoding="utf-8", newline="\n") as f:
                f.write(common)
            rid = "req_20261112_101500_abcdef"
            req = {"req_id": rid, "created_at": "2026-11-12T10:15:00+01:00",
                   "from": "codex-legion", "type": "message", "text": "hello",
                   "payload": {}, "reply_protocol": 2}
            with open(os.path.join(tmp, "pc", "inbox", rid + ".json"), "w", encoding="utf-8") as f:
                json.dump(req, f)
            env = dict(os.environ)
            env["MUSELINK_VM_DIR"] = vm
            env["MUSELINK_HOOK_DRY_RUN"] = "0"
            env["MUSELINK_HOOK_ADAPTER"] = os.path.join(tmp, "no-adapter.sh")
            env["PATH"] = os.path.join(tmp, "bin") + os.pathsep + env.get("PATH", "")
            p = subprocess.run([_bash(), os.path.join(vm, "hooks", "bridge-postman.sh")],
                               env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
            self.assertIn("WAKE:", p.stdout, p.stderr)
            self.assertIn(rid, p.stdout)
            self.assertTrue(os.path.exists(os.path.join(tmp, "bridge", "inbox", rid + ".json")))
            self.assertFalse(os.path.exists(os.path.join(vm, "muselink-id.sh")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
