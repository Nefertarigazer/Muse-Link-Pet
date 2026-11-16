"""On-demand diagnostics; five-second cache, no listener or data endpoint."""
from i18n import active, t
import copy
from datetime import datetime
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

from .actions import FLAGS
from .watch import iso, read, read_json


class Channels:
    def __init__(self, tool, watch, config=None, clock=time.time):
        self.tool, self.watch = Path(tool), watch
        self.config, self.clock = config or {}, clock
        self._cached = None
        self._locale = None
        self._at = float('-inf')
        self._lock = threading.Lock()

    def _tunnel(self):
        directory = self.config.get('tailscale', {}).get('exe_dir')
        exe = str(Path(directory) / 'tailscale.exe') if directory else shutil.which('tailscale')
        if not exe:
            return 'unknown', t('pet.no_tailscale')
        result = subprocess.run([exe, 'status', '--json'], timeout=1, capture_output=True,
                                encoding='utf-8', creationflags=FLAGS)
        status = json.loads(result.stdout)
        online = status.get('BackendState') == 'Running' and status.get('Self', {}).get('Online', False)
        return ('ok', t('pet.online')) if online else ('down', t('pet.offline'))

    def _host(self):
        port = self.config.get('executor', {}).get('port', 18744)
        request = dict(kind='ping', token=read(self.tool / 'executor-token.txt'), deadline=time.time() + 1)
        deadline = time.monotonic() + 1
        with socket.create_connection(('127.0.0.1', int(port)), timeout=1) as conn:
            conn.settimeout(1)
            conn.sendall((json.dumps(request) + '\n').encode())
            data = b''
            while b'\n' not in data and len(data) < 65536:
                conn.settimeout(max(.001, deadline - time.monotonic()))
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        response = json.loads(data.split(b'\n')[0])
        if not response.get('ok'):
            return 'down', t('pet.host_failed')
        at = self.watch._work
        # the card has ~34 characters per row: short local times, not ISO
        when = datetime.fromtimestamp(at).strftime('%H:%M') if at else t('pet.none')
        return 'ok', t('pet.last_work', version=response.get('version', '?'), time=when)

    def _bus(self):
        pending = read(self.tool / 'popup-pending.txt')
        ask = self.watch.snapshot()['ask']
        return ('warn', t('pet.waiting')) if ask else ('ok', t('pet.pending') if pending else t('pet.idle'))

    def _bridge(self):
        root = Path(self.config.get('bridge', {}).get('root') or self.tool / 'muse-bridge')
        if not root.exists():
            return 'unknown', t('pet.no_mailbox')
        stale = sum(1 for p in (root / 'inbox').glob('*.json') if self.clock() - p.stat().st_mtime > 180)
        recent = None
        recent_at = float('-inf')
        for directory in ('outbox', 'archive'):
            for p in (root / directory).glob('*.json'):
                # v1 replies can be named <req>.json, without a .reply suffix.
                at = read_json(p).get('replied_at')
                if isinstance(at, str):
                    try:
                        stamp = datetime.fromisoformat(at.replace('Z', '+00:00')).timestamp()
                    except ValueError:
                        continue
                    if stamp > recent_at:
                        recent, recent_at = at, stamp
        last = datetime.fromtimestamp(recent_at).strftime('%m-%d %H:%M') if recent else t('pet.none')
        if stale:
            return 'warn', t('pet.stale', n=stale, time=last)
        return 'ok', t('pet.last_reply', time=last)

    def check(self):
        with self._lock:
            locale = active().ui
            if self._cached is not None and self._locale == locale and self.clock() - self._at < 5:
                return copy.deepcopy(self._cached)
            items = []
            for id_, label, check in [('tunnel', t('pet.tunnel'), self._tunnel), ('host', t('pet.executor'), self._host),
                                      ('bus', t('pet.bus'), self._bus), ('bridge', t('pet.mailbox'), self._bridge)]:
                try:
                    level, detail = check()
                except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
                    level, detail = ('down' if id_ in ('host', 'tunnel') else 'unknown'), t('pet.check_failed')
                items.append(dict(id=id_, label=label, level=level, detail=detail))
            self._cached = dict(checked_at=iso(self.clock()), items=items)
            self._at = self.clock()
            self._locale = locale
            return copy.deepcopy(self._cached)
