#!/usr/bin/env python3
"""HTTP CONNECT helper for reaching tailnet addresses from the Muse VM.

Used as an ssh ProxyCommand:  python3 ts-connect.py <host> <port>
Reads the runtime egress proxy from $HTTPS_PROXY, connects to its tailnet
tunnel port ($TUNNEL_PORT, default 3130), issues CONNECT, then relays stdio.

2026-11-12 fix: the 20 s connect timeout used to stay on the socket, so 20 s
without data from the far side raised TimeoutError, the relay loop swallowed
it and exited, and ssh lost its connection. Only the ServerAliveInterval=15
keepalive (just under 20) masked it. The timeout now covers the handshake
only; the relay itself blocks without a timeout (ssh keepalives detect dead
paths).
"""
import base64
import os
import socket
import sys
import threading
import urllib.parse

HANDSHAKE_TIMEOUT = 20


def main() -> int:
    host, port = sys.argv[1], int(sys.argv[2])
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        print("no proxy env", file=sys.stderr)
        return 1
    u = urllib.parse.urlparse(proxy)
    tunnel_port = int(os.environ.get("TUNNEL_PORT", "3130"))
    s = socket.create_connection((u.hostname, tunnel_port), timeout=HANDSHAKE_TIMEOUT)
    auth = ""
    if u.username:
        creds = f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}"
        auth = "Proxy-Authorization: Basic " + base64.b64encode(creds.encode()).decode() + "\r\n"
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{auth}\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            print("proxy closed during CONNECT", file=sys.stderr)
            return 1
        buf += chunk
        if len(buf) > 65536:
            print("oversized CONNECT response", file=sys.stderr)
            return 1
    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n")[0]
    if b" 200 " not in status_line:
        print("CONNECT failed: " + head.decode(errors="replace"), file=sys.stderr)
        return 1
    s.settimeout(None)  # the handshake deadline must not apply to the relay

    def pump_in() -> None:
        try:
            while True:
                data = sys.stdin.buffer.read1(65536) if hasattr(sys.stdin.buffer, "read1") else sys.stdin.buffer.read(65536)
                if not data:
                    break
                s.sendall(data)
        except OSError:
            pass
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    threading.Thread(target=pump_in, daemon=True).start()
    try:
        if rest:
            sys.stdout.buffer.write(rest)
            sys.stdout.buffer.flush()
        while True:
            data = s.recv(65536)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except OSError as e:
        print(f"relay ended: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
