"""One authenticated localhost request; never retry an uncertain operation."""
import json
import socket
import time

from ._shared import read_json, tool_dir

MAX_RESPONSE = 8 * 1024 * 1024


def call(request, base=None):
    base = tool_dir(base)
    req = dict(request)
    timeout = float(req.get("timeout", 60))
    req["deadline"] = time.time() + timeout
    try:
        req["token"] = (base / "executor-token.txt").read_text(encoding="utf-8").strip()
        port = int((read_json(base / "muselink.json", {}) or {}).get("executor", {}).get("port", 18744))
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    except (OSError, ValueError, TypeError):
        return {"ok": False, "status": "fail", "error": "HOST_UNREACHABLE"}
    try:
        with sock:
            sock.settimeout(timeout + 15)
            sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = bytearray()
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_RESPONSE:
                    raise ValueError("oversized response")
            reply = json.loads(bytes(buf).split(b"\n", 1)[0].decode("utf-8"))
            if not isinstance(reply, dict):
                raise ValueError("response must be object")
            return reply
    except socket.timeout:
        return {"status": "unknown", "error": "CLIENT_TIMEOUT"}
    except (OSError, ValueError):
        return {"status": "unknown", "error": "CLIENT_IO_OR_BAD_RESPONSE"}
