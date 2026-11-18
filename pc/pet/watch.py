"""Read-only waiting bus and incremental executor event observer."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata


def iso(at=None):
    return datetime.fromtimestamp(time.time() if at is None else at, timezone.utc).isoformat()


def clean(text, limit):
    return ''.join(c for c in str(text) if not unicodedata.category(c).startswith('C'))[:limit]


def read(path):
    try:
        return Path(path).read_text(encoding='utf-8-sig').strip()
    except (OSError, UnicodeError):
        return ''


def read_json(path):
    try:
        value = json.loads(read(path))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def done_info(tool):
    raw = read(Path(tool) / 'popup-done.txt')
    kind = read(Path(tool) / 'popup-kind.txt')
    if raw != read(Path(tool) / 'popup-done.txt'):
        return '', '', ''  # a new card was staged while reading; retry next poll
    lines = raw.splitlines()
    return (next((v[4:].strip() for v in lines if v.startswith('REQ:')), ''),
            lines[0] if lines else '', kind)


def job_activity(job, detail):
    """Only expose a fixed activity code, never commands or job details."""
    kind = str(job).lower().split(':', 1)[0]
    words = set(re.findall(r'[a-z_]+', str(detail).lower()))
    if kind == 'shell' or 'shell' in words:
        return 'shell'
    if kind == 'script' or 'script' in words:
        return 'script'
    words.update(re.findall(r'[a-z_]+', str(job).lower()))
    if words & {'screenshot', 'capture', 'capture_screen', 'screen', 'snapshot'}:
        return 'screen'
    if kind == 'seq' or 'seq' in words:
        return 'steps'
    if words & {'act', 'act_if', 'invoke', 'click', 'type', 'focus', 'fill', 'press', 'keys', 'scroll', 'paste'}:
        return 'action'
    if words & {'observe', 'resolve', 'read', 'read_text', 'locate', 'focused', 'inspect'}:
        return 'read'
    return 'working'


class Watch:
    def __init__(self, tool, work_linger_s=45, clock=time.time, *, events_dir=None, on_error=None):
        self.tool = Path(tool)
        self.events_path = Path(events_dir if events_dir is not None else tool) / 'executor-events.jsonl'
        self.linger = work_linger_s
        self.clock = clock
        self._on_error = on_error
        self._err_at = float('-inf')
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._work = None
        self._job = self._activity = None
        self._brief = self._brief_id = self._brief_at = None
        self._seq = 0
        self._known = None
        self._done = done_info(self.tool)  # do not replay old confirmation on restart
        self._file = None
        self._identity = None
        # Start at EOF: a previous process's work must not survive a restart.
        self._open_events(tail=True)
        self._snapshot = dict(state='idle', brief=None, brief_id=None, brief_at=None, activity=None, ask=None,
                              last_answer=None, since=iso(self.clock()))
        self.poll()

    def _open_events(self, tail=False):
        try:
            path = self.events_path
            if os.name == 'nt':
                # The host must be able to rename the log while we hold .1 open.
                import ctypes
                from ctypes import wintypes
                import msvcrt
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                kernel.CreateFileW.restype = wintypes.HANDLE
                handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                self._file = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), 'rb')
            else:
                self._file = path.open('rb')
            st = self._file.fileno()
            self._identity = os.fstat(st).st_ino
            if tail:
                self._file.seek(0, 2)
        except OSError:
            self._file = None

    def _consume(self):
        if self._file is None:
            return
        # Keep an incomplete last line for the next pass. Bound each pass.
        for _ in range(10000):
            pos = self._file.tell()
            line = self._file.readline(65537)
            if not line:
                break
            if not line.endswith(b'\n'):
                if len(line) > 65536:
                    continue  # discard an oversized/corrupt line instead of stalling the log
                self._file.seek(pos)
                break
            try:
                event = json.loads(line)
                kind = event.get('type')
                stamp = event.get('ts', '')
                at = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
                if kind == 'host_start':
                    self._work = None
                    self._job = self._activity = None
                    self._brief = self._brief_id = self._brief_at = None
                elif kind in ('job_begin', 'job_end'):
                    self._work = at
                    job = event.get('job', '')
                    # End details contain only duration/result. Preserve the
                    # latest begin activity, even when a different job ends.
                    if kind == 'job_begin' or self._activity is None:
                        self._job = job
                        self._activity = job_activity(job, event.get('detail', ''))
                elif kind == 'phase':
                    self._seq += 1
                    text = event.get('text')
                    self._brief = (clean(' '.join(text.split()), 500).strip() or None
                                   if isinstance(text, str) else None)
                    self._brief_id = '%s:%s' % (stamp, self._seq)
                    self._brief_at = stamp
            except (ValueError, TypeError, AttributeError, UnicodeError):
                continue

    def _events(self):
        if self._file is None:
            self._open_events()
        else:
            try:
                stat = self.events_path.stat()
                if stat.st_ino != self._identity:
                    # The open handle still refers to .1. Drain it before the new log.
                    self._consume()
                    self._file.close()
                    self._open_events()
                elif stat.st_size < self._file.tell():
                    self._file.seek(0)
            except OSError:
                pass
        self._consume()

    def poll(self):
        self._events()
        now = self.clock()
        pending = read(self.tool / 'popup-pending.txt')
        armed = read_json(self.tool / 'popup-armed.json')
        answer = done_info(self.tool)
        ask = None
        if pending and pending == armed.get('req_id'):
            mode = armed.get('mode')
            if mode in ('info', 'choice', 'input'):
                ask = dict(req_id=pending, mode=mode,
                           summary=clean(str(armed.get('summary', '')).split('\n')[0], 120))
        old = self.snapshot()
        last_answer = old['last_answer']
        if answer != self._done:
            req, at, kind = answer
            # Observe the answer even if wait-popup removed pending between polls.
            if req and req in (self._known, pending if ask else None) and kind != 'expired':
                by = ('pet' if kind in ('pet_done', 'pet_typed') else
                      'popup' if kind in ('info', 'choice', 'typed', 'input') else 'other')
                last_answer = dict(req_id=req, by=by, at=at)
            self._done = answer
        if ask and answer[0] == ask['req_id']:
            ask = None
        self._known = ask['req_id'] if ask else None
        working = self._work is not None and 0 <= now - self._work < self.linger
        state = 'waiting_user' if ask else 'working' if working else 'idle'
        since = old['since'] if (old['state'], old['ask']) == (state, ask) else iso(now)
        snap = dict(state=state, brief=self._brief if working else None,
                    brief_id=self._brief_id if working else None,
                    brief_at=self._brief_at if working else None,
                    activity=self._activity if working else None, ask=ask,
                    last_answer=last_answer, since=since)
        with self._lock:
            self._snapshot = snap

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def start(self):
        def run():
            while not self._stop.wait(.5):
                try:
                    self.poll()
                except OSError:
                    pass  # transient replacement/sharing failure; retry next poll
                except Exception as exc:
                    cb = getattr(self, '_on_error', None)
                    if cb is not None:
                        now = time.monotonic()
                        if now - getattr(self, '_err_at', float('-inf')) >= 60:
                            self._err_at = now
                            try:
                                cb('watch: %s' % type(exc).__name__)
                            except Exception:
                                pass
        self._thread = threading.Thread(target=run, name='pet-watch', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        if self._file:
            self._file.close()
