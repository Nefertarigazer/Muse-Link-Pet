#!/usr/bin/env python3
"""bridge-service.py - resident worker for the local-agent <-> Muse bridge.

Started by bridge-service.sh (which loads the MuseLink config). Watches the
local bridge inbox (filled by hooks/bridge-postman.sh). Release requests
(type publish-release) run through the deterministic
pipeline (pipelines/publish-release.py) with no LLM in the loop: claim
locally, ACK immediately, run, reply with the verdict. Every other request
type is left in the inbox for the agent worker, untouched.

Watchdog: a request claimed on the PC for over 10 minutes without a FINAL
reply in the local ledger gets an alert file in alerts/ (once per request).
The agent surfaces alerts to the user; nothing stalls silently.

Single instance via flock. Runs as the agent user (muse-bridge.service).

Identity protocol v3 (implemented 2026-11-12) (docs/protocol.en.md section 4):
  * Each loop, after the inbox scan: take the OLDEST file in pending/ and run
    `muselink-id enroll-pending` (one enrollment at a time; the CLI holds the
    enroll lock and shows the card). Its JSON line says approved / declined /
    timeout / provision_failed; send the final reply accordingly: approved ->
    the enroll flow has rewritten verdicts/<id>.json with auth "verified",
    so muse-bridge-reply.sh signs the reply with the NEW key (that proves to
    the client that the VM holds it); otherwise sign-reply exits 20 and the
    reply goes out unsigned (v2). The card
    wait can be long (enroll_limits.card_timeout_s): run it in a worker
    thread or subprocess so publish requests and the watchdog keep running.
  * The inbox scan must skip files that are not requests: with v3 the VM
    inbox only receives worker_request files, but verdicts/, envelopes/ and
    pending/ must never be scanned as requests (their names are not req_ids).
  * Alerts written by muselink_id (impersonation, tampered registry/grant)
    land in alerts/ like the stall alerts and are surfaced the same way.
"""
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

VM_DIR = os.environ.get("ML_VM_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("ML_BRIDGE_HOME") or os.path.expanduser("~/workspace/muse-bridge")
PC_BRIDGE_DIR = os.environ.get("PC_BRIDGE_DIR", "")
INBOX = f"{BASE}/inbox"
WORKING = f"{BASE}/working"
PROCESSED = f"{BASE}/processed"
REJECTED = f"{BASE}/rejected"
ALERTS = f"{BASE}/alerts"
REPLIES = f"{BASE}/replies"
PENDING = f"{BASE}/pending"
MUSELINK_CLI = f"{VM_DIR}/muselink_id/cli.py"
PIPELINE = f"{VM_DIR}/bridge/pipelines/publish-release.py"
REPLY = f"{VM_DIR}/bridge/muse-bridge-reply.sh"
PC_SH = f"{VM_DIR}/pc.sh"
PUBLISH_TYPES = {"publish-release"}
VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,95}$")
POLL_SECS = 15
if VM_DIR not in sys.path:
    sys.path.insert(0, VM_DIR)
from muselink_id import current_type  # noqa: E402  retired type names -> current
WATCHDOG_EVERY = 20          # loops (20 * 15 s = 5 min)
STALL_SECS = 600             # 10 minutes

for d in (INBOX, WORKING, PROCESSED, REJECTED, ALERTS, REPLIES, PENDING):
    os.makedirs(d, exist_ok=True)

_enroll_proc = None
_enroll_req = None


def decode_output(data):
    """Child output is read as bytes and decoded per line, never strictly.
    PowerShell on a Chinese-locale PC answers through pc.sh with chunks in
    the OEM code page (GBK), e.g. localized progress/CLIXML on stderr. With
    text=True one such byte raised UnicodeDecodeError inside subprocess.run
    and took the call down with it (the 2.6 rehearsal rollback), although the
    service only parses ASCII ids and JSON lines from that output.
    UTF-8 first; a line that is not UTF-8 is read as GB18030."""
    if not data:
        return ""
    out = []
    for line in data.splitlines(keepends=True):
        try:
            out.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(line.decode("gb18030", errors="replace"))
    return "".join(out)


def run(cmd, timeout):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return subprocess.CompletedProcess(p.args, p.returncode, decode_output(p.stdout), decode_output(p.stderr))


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    try:
        with open(f"{BASE}/service.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_reply(req_id, status, text):
    tf = os.path.join(WORKING, f".reply-{req_id}.txt")
    with open(tf, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        p = run([REPLY, req_id, status, tf], timeout=120)
        if p.returncode != 0:
            log(f"reply {req_id} status={status} failed rc={p.returncode}: {(p.stderr or '').strip()[-300:]}")
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"reply {req_id} status={status} timed out")
        return False
    finally:
        try:
            os.remove(tf)
        except OSError:
            pass


def alert_once(req_id, text):
    path = f"{ALERTS}/{req_id}.txt"
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"ALERT written for {req_id}")


def process_publish(path, name, req_id):
    working_path = os.path.join(WORKING, name)
    try:
        os.rename(path, working_path)
    except OSError:
        return  # someone else claimed it first
    log(f"claimed {req_id} for pipeline")
    send_reply(req_id, "info",
               "Release request received; the resident pipeline has taken it (version guard, "
               "hash/size checks on the PC, upload, hash check on the server, manifest flip, "
               "public check). Expect the final ok/error within ~10 minutes; keep waiting - "
               "muse-call prints this note and goes on waiting for the final reply.")
    try:
        p = run([sys.executable, PIPELINE, working_path], timeout=3600)
        ok = False
        for line in reversed((p.stdout or "").strip().splitlines()):
            try:
                ok = bool(json.loads(line).get("ok"))
                break
            except Exception:
                continue
        log(f"pipeline {req_id} finished ok={ok} rc={p.returncode}")
    except Exception as e:
        log(f"pipeline {req_id} crashed: {e}")
        send_reply(req_id, "error", f"release pipeline crashed before a verdict: {e}")
    try:
        os.rename(working_path, os.path.join(PROCESSED, name))
    except OSError:
        pass


def has_final_reply(req_id):
    for path in glob.glob(f"{REPLIES}/{req_id}.[0-9][0-9][0-9].reply.json") + [f"{REPLIES}/{req_id}.reply.json"]:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("final", d.get("status") != "info"):
                return True
        except Exception:
            continue
    return False


def watchdog():
    now = time.time()
    # local working dir: non-empty only if this service died mid-run
    try:
        for name in os.listdir(WORKING):
            if name.startswith("."):
                continue
            p = os.path.join(WORKING, name)
            if now - os.path.getmtime(p) > STALL_SECS:
                req_id = name[:-5]
                alert_once(req_id, f"bridge request {req_id} has sat in the pipeline working dir "
                                   f"for over {STALL_SECS // 60} minutes.")
    except Exception as e:
        log(f"watchdog local scan failed: {e}")
    # PC claimed dir: requests picked up but never answered with a final reply
    if not PC_BRIDGE_DIR:
        return
    # no progress records (localized CLIXML on stderr); run() decodes the rest
    ps = ("$ProgressPreference = 'SilentlyContinue'; "
          "$d = Join-Path '" + PC_BRIDGE_DIR.replace("'", "''") + "' 'claimed'; "
          "Get-ChildItem -LiteralPath $d -Filter *.json -File -ErrorAction SilentlyContinue | "
          "ForEach-Object { $_.BaseName + ' ' + [int]((Get-Date) - $_.LastWriteTime).TotalSeconds }")
    try:
        p = run([PC_SH, "ps", ps], timeout=90)
        for line in (p.stdout or "").splitlines():
            parts = line.strip().rsplit(" ", 1)
            if len(parts) != 2 or not VALID_ID.match(parts[0]):
                continue
            try:
                age = float(parts[1])
            except ValueError:
                continue
            if age > STALL_SECS and not has_final_reply(parts[0]):
                alert_once(parts[0], f"bridge request {parts[0]} was claimed on the PC over "
                                     f"{int(age // 60)} minutes ago and still has no final reply.")
    except Exception as e:
        log(f"watchdog PC scan failed: {e}")


def _enroll_due(name):
    """pending/<id>.card (muselink_id.flows) holds the back-off after a
    transient failure; a request whose card is already up stays due, so a
    restarted service resumes waiting on that card."""
    try:
        with open(os.path.join(PENDING, name[:-5] + ".card"), encoding="utf-8") as f:
            return int(json.load(f).get("next_try_at") or 0) <= time.time()
    except (OSError, ValueError, AttributeError, TypeError):
        return True


def poll_enroll():
    """Run at most one enrollment card at a time, in a child process."""
    global _enroll_proc, _enroll_req
    vm_home = os.environ.get("ML_VM_HOME") or os.path.expanduser("~")
    id_home = os.environ.get("MUSELINK_ID_HOME") or os.path.join(vm_home, ".config/muselink/identity")
    policy = os.environ.get("MUSELINK_POLICY") or os.path.join(vm_home, ".config/muselink/policy.json")
    if not (os.path.exists(MUSELINK_CLI) and os.path.exists(policy)):
        return
    if _enroll_proc is not None:
        if _enroll_proc.poll() is None:
            return
        try:
            out, err = _enroll_proc.communicate(timeout=5)
            out, err = decode_output(out), decode_output(err)
        except Exception:
            out, err = "", ""
        data = None
        for line in reversed((out or "").strip().splitlines()):
            try:
                data = json.loads(line)
                break
            except Exception:
                continue
        rc = _enroll_proc.returncode
        if err and err.strip():
            log(f"enroll-pending stderr (rc={rc}): {err.strip()[-600:]}")
        if data and data.get("pending") and data.get("req_id"):
            log(f"enrollment {data.get('req_id')} for {data.get('agent_id')}: how={data.get('how')} "
                f"proceed={data.get('proceed')} final={data.get('final', True)} rc={rc}")
        elif not data:
            log(f"enroll-pending ended without a result line (rc={rc}); the request stays pending")
        if data and data.get("pending") and data.get("req_id") and data.get("final") is False:
            pass  # nothing decided yet: the request stays in pending/ and is retried
        elif data and data.get("pending") and data.get("req_id"):
            req_id = str(data["req_id"])
            if data.get("proceed") and data.get("how") != "provision_failed":
                sent = send_reply(req_id, "ok", "Enrollment approved; the identity file has been provisioned on the PC.")
            elif data.get("how") == "provision_failed":
                sent = send_reply(req_id, "error", "Enrollment approved, but writing the identity file to the PC failed. "
                                                    "Ask the user to run: muselink-id agents provision " + str(data.get("agent_id", "")))
            else:
                sent = send_reply(req_id, "error", "Enrollment not approved: " + str(data.get("how") or data.get("reason") or "declined"))
            if not sent:
                # the requester waits for this reply; the reason is in the
                # stderr line logged by send_reply
                log(f"enrollment {req_id}: the final reply could not be sent")
                alert_once(req_id, f"Enrollment {req_id} ({data.get('agent_id', '')}, how={data.get('how')}) "
                                   "finished, but its final reply could not be signed or delivered. See service.log.")
        _enroll_proc = None
        _enroll_req = None
    try:
        names = sorted((n for n in os.listdir(PENDING) if n.endswith(".json") and _enroll_due(n)),
                       key=lambda n: os.path.getmtime(os.path.join(PENDING, n)))
    except OSError:
        return
    if not names:
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = VM_DIR + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    env["MUSELINK_ID_HOME"] = id_home
    env["MUSELINK_POLICY"] = policy
    try:
        _enroll_proc = subprocess.Popen([sys.executable, "-m", "muselink_id.cli", "enroll-pending"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        _enroll_req = names[0][:-5]
        resumed = os.path.exists(os.path.join(PENDING, _enroll_req + ".card"))
        log(f"enrollment {'resumed' if resumed else 'card started'} for {_enroll_req} (pid {_enroll_proc.pid})")
    except Exception as e:
        _enroll_proc = None
        _enroll_req = None
        log(f"could not start enroll-pending: {e}")


def main():
    lockf = open(f"{BASE}/service.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another instance holds the lock; exiting")
        return
    log("bridge-service started")
    loop = 0
    while True:
        loop += 1
        try:
            names = sorted((n for n in os.listdir(INBOX) if n.endswith(".json")),
                           key=lambda n: os.path.getmtime(os.path.join(INBOX, n)))
            for name in names:
                path = os.path.join(INBOX, name)
                req_id = name[:-5]
                if not VALID_ID.match(req_id):
                    shutil.move(path, os.path.join(REJECTED, name))
                    log(f"rejected file with invalid id: {name!r}")
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        req = json.load(f)
                except Exception:
                    continue  # unparsable: the agent worker judges it
                if not isinstance(req, dict) or req.get("req_id") != req_id:
                    shutil.move(path, os.path.join(REJECTED, name))
                    log(f"rejected {name}: req_id field does not match the file name")
                    continue
                if "v" in req:
                    shutil.move(path, os.path.join(REJECTED, name))
                    log(f"rejected {name}: an envelope is not a worker request")
                    continue
                # current_type: a request queued before the rename may still
                # carry a retired name (inbound routing maps new ones)
                if current_type(req.get("type")) in PUBLISH_TYPES:
                    process_publish(path, name, req_id)
        except Exception as e:
            log(f"main loop error: {e}")
        try:
            poll_enroll()
        except Exception as e:
            log(f"enrollment poll error: {e}")
        if loop % WATCHDOG_EVERY == 0:
            watchdog()
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
