"""executor-call.py - bridge from an SSH session to the resident executor host.

    python executor-call.py <base64(request JSON)>     # small requests
    python executor-call.py -                          # request JSON on stdin

Adds the shared token (executor-token.txt, same directory) and a deadline
computed on THIS machine's clock (the host shares it, the VM does not), then
prints the host's JSON response line.

Exit codes: 0 ok, 1 host reported failure, 2 host unreachable (caller may
heal and retry), 3 bad request, 4 outcome unknown (client-side timeout: the
host may still be running the request, so do not blindly retry).
"""
import base64
import json
import os
import socket
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))

# The SSH session's console code page is GBK on Chinese Windows; without this
# a non-GBK character in a response crashes the print (bit us on 2026-11-08).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def emit(obj, code):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.exit(code)


def load_port():
    try:
        with open(os.path.join(BASE, "muselink.json"), encoding="utf-8-sig") as f:
            return int(json.load(f).get("executor", {}).get("port", 18744))
    except Exception:
        return 18744


def load_token():
    try:
        with open(os.path.join(BASE, "executor-token.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


try:
    arg = sys.argv[1] if len(sys.argv) > 1 else "-"
    raw = sys.stdin.buffer.read() if arg == "-" else base64.b64decode(arg)
    req = json.loads(raw.decode("utf-8"))
    if not isinstance(req, dict):
        raise ValueError("request must be a JSON object")
except Exception as e:
    emit({"ok": False, "ms": 0, "out": f"BAD REQUEST: {e}"}, 3)

timeout = float(req.get("timeout", 60))
req["deadline"] = time.time() + timeout
req["token"] = load_token()

try:
    s = socket.create_connection(("127.0.0.1", load_port()), timeout=10)
except Exception as e:
    emit({"ok": False, "ms": 0, "out": f"HOST UNREACHABLE: {e}"}, 2)

try:
    s.settimeout(timeout + 15)
    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
except socket.timeout:
    emit({"ok": False, "ms": 0, "status": "unknown",
          "out": json.dumps({"status": "unknown", "error": "CLIENT_TIMEOUT"})}, 4)
except OSError as e:
    emit({"ok": False, "ms": 0, "status": "unknown",
          "out": json.dumps({"status": "unknown", "error": f"CLIENT_IO {e}"})}, 4)
finally:
    try:
        s.close()
    except Exception:
        pass

text = buf.decode("utf-8", errors="replace").strip()
if not text:
    emit({"ok": False, "ms": 0, "status": "unknown",
          "out": json.dumps({"status": "unknown", "error": "EMPTY_RESPONSE"})}, 4)
sys.stdout.write(text + "\n")
try:
    sys.exit(0 if json.loads(text).get("ok") else 1)
except Exception:
    sys.exit(1)
