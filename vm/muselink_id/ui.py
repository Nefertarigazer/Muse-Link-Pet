"""ui - adapters to the real popup and the PC.

flows.py only talks to these two interfaces; tests pass fakes with the same
methods. Keep the adapters thin: no policy logic here.

UiAnswer.kind:
  answered  the user clicked an option; `answer` is its exact label
  timeout   the wait ended without an answer (for expiring cards: the card
            closed itself at its deadline)
  closed    the user closed with X, or acknowledged with the generic pet check
  refused   another card is in flight (popup-send.sh "REFUSED")
  error     anything else (PC unreachable, staging failed, bad output)
`popup_req` is the popup's own request id (the REQ binding added in phase 1)
and goes into approval evidence.

Requirements (PopupUI)
  1. choice(text, options, timeout_s, expire=False) runs vm/popup-ask.sh
     with POPUP_WAIT_MAX=timeout_s and parses "---RESULT---" plus the REQ
     id from its output. Exit 124 -> timeout; "REFUSED" -> refused.
  2. expire=True adds the new popup-send.sh option --expires-in <timeout_s>:
     the card closes itself at the deadline and reports kind "expired",
     which this adapter maps to UiAnswer kind "timeout". Needed by the
     delay card so the user never clicks a card that no longer means
     anything. (New PC feature: pc/popup.ps1 + popup-stage.ps1, see the notes
     there.)
  3. notify(text): info card, --no-wait; failures are logged, not raised.
  4. Card text is passed through a file (popup-ask.sh already does that);
     never build a shell command line from card text.

Requirements (PcAdapter)
  5. write_identity(agent_id, record): JSON {v:1, agent_id, key_id, key,
     enrolled_at} written to a temp file under the VM user's private temp
     dir (0600), copied with scp (ml_scp_to) to
     <tool>/identities/.<agent_id>.<random>.tmp, then ONE PowerShell call
     (ml_ps via pc.sh) that: creates identities\\ if missing with
     inheritance removed and only the current user + SYSTEM granted;
     moves the temp file onto <agent_id>.json; runs icacls on it the same
     way; prints OK. The key never appears in any command line. Delete the
     local temp file in a finally block.
  6. remove_identity(agent_id): delete the PC file (revocation).
  7. write_directory(name, obj): name is the bare file name "agents.json"
     or "grants.json" (anything else -> ValueError); writes
     muse-bridge\\directory\\<name> via scp to a .tmp name + rename.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import namedtuple

from . import AGENT_ID_RX, valid_id

UiAnswer = namedtuple("UiAnswer", "kind answer popup_req")

# Lines the wait scripts print AFTER wait-popup's answer block. They used to
# be read as part of the answer: popup-send.sh appends "DELIVERED req=..."
# right after the result, so a click on "白名单" arrived as
# "白名单\nDELIVERED req=..." and never matched the option (enrollment
# defect 2026-11-13: every approval was filed as declined). PowerShell may
# also append a CLIXML progress block on stderr.
_TRAILER = re.compile(r"^(DELIVERED req=|EXPIRED req=|TAKEN BY FALLBACK|WAITER (BUDGET|ENDED)|"
                      r"STATUS:WAIT_BUDGET_EXHAUSTED|#< CLIXML|<Objs )")


def answer_block(out):
    """The answer text after ---RESULT---, without the scripts' trailer lines."""
    block = out.split("---RESULT---", 1)[1].replace("\r\n", "\n").split("\n")
    if block and block[0].strip() == "":
        block = block[1:]
    kept = []
    for line in block:
        if _TRAILER.match(line.strip()):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _psq(s):
    return "'" + str(s).replace("'", "''") + "'"


def _decode(data):
    # PC PowerShell output is UTF-8, but a chunk may come in the OEM code page
    # (GBK) on a Chinese-locale PC. errors="replace" turned such a "白名单"
    # into garbage that matches no option; read a non-UTF-8 line as GB18030.
    out = []
    for line in (data or b"").splitlines(keepends=True):
        try:
            out.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(line.decode("gb18030", errors="replace"))
    return "".join(out)


def _run(cmd, timeout=120, env=None):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    return subprocess.CompletedProcess(p.args, p.returncode, _decode(p.stdout), _decode(p.stderr))


class PopupUI:
    def __init__(self, vm_dir):
        self.vm_dir = vm_dir

    def choice(self, text, options, timeout_s, expire=False, resume=None, on_staged=None):
        """resume / on_staged make a card survive its waiter (enrollment):
        on_staged(popup_req) is called once the card is up, before waiting;
        resume={"popup_req", "staged_at"} waits on that card again instead
        of staging a new one, so a click made while no waiter was alive
        still counts."""
        ask = os.path.join(self.vm_dir, "popup-ask.sh")
        env = dict(os.environ)
        # An expiring card closes itself timeout_s after it is SHOWN, which is
        # some seconds after staging. With a wait budget of exactly timeout_s
        # the wait ended first, exit 124 was read as "timeout" and the delay
        # flow went ahead while the card was still on screen (review
        # 2026-11-12). Give the card room to report its own expiry.
        budget = int(timeout_s) + 60 if expire else int(timeout_s)
        if on_staged is not None or resume:
            return self._resumable(ask, text, options, timeout_s, budget, expire, resume, on_staged, env)
        args = [ask]
        if expire:
            args += ["--expires-in", str(int(timeout_s))]
        args.append(text)
        args.extend(options)
        env["POPUP_WAIT_MAX"] = str(budget)
        try:
            p = _run(args, timeout=budget + 180, env=env)
        except Exception:
            return UiAnswer("error", "", "")
        return self._parse(p, expire)

    def _resumable(self, ask, text, options, timeout_s, budget, expire, resume, on_staged, env):
        import time
        if resume and resume.get("popup_req"):
            popup_req = str(resume["popup_req"])
            left = budget - max(0, int(time.time()) - int(resume.get("staged_at") or 0))
            budget = max(60, left)
        else:
            # stage without waiting; the owner tag keeps the hook fallback from
            # handing this card's answer to Muse while no waiter is alive
            args = [ask, "--no-wait", "--owner", "bridge-service"]
            if expire:
                args += ["--expires-in", str(int(timeout_s))]
            args.append(text)
            args.extend(options)
            try:
                p = _run(args, timeout=180, env=env)
            except Exception:
                return UiAnswer("error", "", "")
            out = (p.stdout or "") + "\n" + (p.stderr or "")
            if re.search(r"^REFUSED:", out, re.M):
                return UiAnswer("refused", "", "")
            m = re.search(r"^ARMED req=(\S+)", out, re.M)
            if p.returncode != 0 or not m:
                return UiAnswer("error", "", "")
            popup_req = m.group(1)
            on_staged(popup_req)
        try:
            p = _run([os.path.join(self.vm_dir, "popup-wait.sh"), popup_req, str(budget)],
                     timeout=budget + 300, env=env)
        except Exception:
            return UiAnswer("error", "", popup_req)
        if p.returncode == 124:
            # popup-wait.sh retired the card (it should have expired long ago)
            return UiAnswer("error", "card_retired", popup_req)
        ans = self._parse(p, expire)
        return ans._replace(popup_req=ans.popup_req or popup_req)

    def _parse(self, p, expire):
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        m = re.search(r"REQ_ID:(\S+)", out)
        if not m:
            m = re.search(r"req=(\S+)", out)
        popup_req = m.group(1) if m else ""
        # match the scripts' own status lines, never words inside card text
        if re.search(r"^REFUSED:", out, re.M):
            return UiAnswer("refused", "", popup_req)
        if re.search(r"^EXPIRED req=", out, re.M) or re.search(r"^KIND:expired\s*$", out, re.M):
            return UiAnswer("timeout", "", popup_req)
        if re.search(r"^KIND:pet_done\s*$", out, re.M):
            # A generic acknowledgement is never an authorization option,
            # even if a malformed/older producer attached nonempty text.
            return UiAnswer("closed", "", popup_req)
        if p.returncode == 124:
            if expire:
                # the card never confirmed its own expiry: its state is
                # unknown, so this must not count as "the user let it pass"
                return UiAnswer("error", "", popup_req)
            return UiAnswer("timeout", "", popup_req)
        if "---RESULT---" in out:
            result = answer_block(out)
            if result == "":
                return UiAnswer("closed", "", popup_req)
            return UiAnswer("answered", result, popup_req)
        return UiAnswer("error", "", popup_req)

    def notify(self, text):
        ask = os.path.join(self.vm_dir, "popup-ask.sh")
        try:
            _run([ask, "--no-wait", text], timeout=180)
        except Exception:
            pass


class PcAdapter:
    def __init__(self, vm_dir):
        self.vm_dir = vm_dir

    def _pc(self, *args, timeout=120):
        return _run([os.path.join(self.vm_dir, "pc.sh")] + list(args), timeout=timeout)

    def write_identity(self, agent_id, record):
        tool = os.environ.get("PC_TOOL_DIR", "")
        if not tool:
            raise OSError("PC_TOOL_DIR is not set")
        tmpdir = tempfile.mkdtemp(prefix="mlid-")
        try:
            local = os.path.join(tmpdir, "identity.json")
            fd = os.open(local, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
            if not valid_id(AGENT_ID_RX, agent_id):
                raise ValueError("bad agent_id")
            token = os.urandom(6).hex()
            remote_fwd = tool.replace("\\", "/") + "/identities/." + agent_id + "." + token + ".tmp"
            remote_win = tool + "\\identities\\." + agent_id + "." + token + ".tmp"
            final = tool + "\\identities\\" + agent_id + ".json"
            # 1) the private folder must exist BEFORE scp writes into it (the
            #    first enrollment on a PC used to fail here), and it must be
            #    private before the key lands, so the temp file inherits a
            #    user+SYSTEM-only ACL. icacls exit codes are checked: a
            #    failed icacls used to print OK and leave the key readable by
            #    whatever the tool dir allows (e.g. CodexSandboxUsers).
            prep = (
                "$ErrorActionPreference='Stop';"
                "$dir=Join-Path " + _psq(tool) + " 'identities';"
                "if(-not (Test-Path -LiteralPath $dir)){New-Item -ItemType Directory -Path $dir | Out-Null};"
                "$me=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
                "& icacls $dir /inheritance:r /grant:r \"${me}:(OI)(CI)F\" \"*S-1-5-18:(OI)(CI)F\" | Out-Null;"
                "if($LASTEXITCODE -ne 0){throw 'icacls on identities failed'};"
                "'OK'"
            )
            p = self._pc("ps", prep)
            if p.returncode != 0 or "OK" not in (p.stdout or ""):
                raise OSError("PC identity folder setup failed: " + ((p.stdout or "") + (p.stderr or ""))[:200])
            # 2) the key travels only through scp (SFTP), never a command line
            p = self._pc("scp-to", local, remote_fwd)
            if p.returncode != 0:
                raise OSError("scp failed: " + (p.stderr or p.stdout)[:200])
            # 3) move into place, then pin the file ACL as well
            code = (
                "$ErrorActionPreference='Stop';"
                "$me=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
                "Move-Item -LiteralPath " + _psq(remote_win) + " -Destination " + _psq(final) + " -Force;"
                "& icacls " + _psq(final) + " /inheritance:r /grant:r \"${me}:F\" \"*S-1-5-18:F\" | Out-Null;"
                "if($LASTEXITCODE -ne 0){Remove-Item -LiteralPath " + _psq(final) + " -Force; throw 'icacls on the identity file failed'};"
                "'OK'"
            )
            p = self._pc("ps", code)
            if p.returncode != 0 or "OK" not in (p.stdout or ""):
                raise OSError("PC identity install failed: " + ((p.stdout or "") + (p.stderr or ""))[:200])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def remove_identity(self, agent_id):
        tool = os.environ.get("PC_TOOL_DIR", "")
        if not tool:
            raise OSError("PC_TOOL_DIR is not set")
        if not valid_id(AGENT_ID_RX, agent_id):
            raise ValueError("bad agent_id")
        final = tool + "\\identities\\" + agent_id + ".json"
        code = "Remove-Item -LiteralPath " + _psq(final) + " -Force -ErrorAction SilentlyContinue; 'OK'"
        p = self._pc("ps", code)
        if p.returncode != 0:
            raise OSError("PC identity removal failed")

    def write_directory(self, name, obj):
        if name not in ("agents.json", "grants.json"):
            raise ValueError("bad directory file name")
        bridge = os.environ.get("PC_BRIDGE_DIR", "")
        if not bridge:
            raise OSError("PC_BRIDGE_DIR is not set")
        tmpdir = tempfile.mkdtemp(prefix="mlid-")
        try:
            local = os.path.join(tmpdir, name)
            fd = os.open(local, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            remote_fwd = bridge.replace("\\", "/") + "/directory/" + name + ".tmp"
            remote_win = bridge + "\\directory\\" + name + ".tmp"
            final = bridge + "\\directory\\" + name
            code = ("$d=Join-Path " + _psq(bridge) + " 'directory';"
                    "if(-not (Test-Path -LiteralPath $d)){New-Item -ItemType Directory -Path $d | Out-Null};'OK'")
            p = self._pc("ps", code)
            if p.returncode != 0:
                raise OSError("PC directory create failed")
            p = self._pc("scp-to", local, remote_fwd)
            if p.returncode != 0:
                raise OSError("scp failed: " + (p.stderr or p.stdout)[:200])
            p = self._pc("ps", "Move-Item -LiteralPath " + _psq(remote_win) + " -Destination " + _psq(final) + " -Force;'OK'")
            if p.returncode != 0:
                raise OSError("PC directory install failed")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
