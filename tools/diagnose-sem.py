#!/usr/bin/env python3
"""Read-only semantic timing probe; never connects to a resident executor.

Run from a development checkout. State and numeric-only logs stay under its
ignored private/ directory. No UI names, paths, returned text or stderr are
saved. The selected agent-desktop executable is read, never installed/updated.
"""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z_0-9]*', value) else None


def numeric_stats(value):
    """Allow only counters/flags; upstream details may contain desktop text."""
    if isinstance(value, dict):
        return {key: clean for key, item in value.items()
                if (clean := numeric_stats(item)) is not None}
    return value if type(value) in (int, float, bool) else None


class TimedProcess:
    def __init__(self, original, write):
        self.original, self.write = original, write
        self.elapsed_ms = 0

    def __getattr__(self, name):
        return getattr(self.original, name)

    def run(self, argv, **kwargs):
        start = time.perf_counter()
        wall = time.time()
        command = argv[1] if argv[1] in ('snapshot', 'find') else 'uia-helper'
        self.write('process.begin', command=command, timeout_s=kwargs.get('timeout'))
        try:
            result = self.original.run(argv, **kwargs)
        except subprocess.TimeoutExpired:
            self.write('process.timeout', ms=round((time.perf_counter() - start) * 1000, 3))
            raise
        self.elapsed_ms = (time.perf_counter() - start) * 1000
        # A timing marker only: never retain the debug line or its contents.
        hydration = []
        for line in (result.stderr or '').splitlines():
            if 'hydrating selected locator anchor' in line:
                match = re.match(r'(\S+Z)\s', line)
                if match:
                    try:
                        hydration.append(round((datetime.fromisoformat(match[1].replace('Z', '+00:00')).timestamp() - wall) * 1000, 3))
                    except ValueError:
                        pass
        self.write('process.end', ms=round(self.elapsed_ms, 3), rc=result.returncode,
                   stdout_bytes=len((result.stdout or '').encode('utf-8')),
                   stderr_bytes=len((result.stderr or '').encode('utf-8')),
                   hydration_markers=len(hydration),
                   first_hydration_ms=hydration[0] if hydration else None,
                   last_hydration_ms=hydration[-1] if hydration else None)
        return result


def profile(host, request, write):
    """Instrument this imported host only, preserving its results and errors."""
    original_ad, original_process = host.ad, host.subprocess
    process = TimedProcess(original_process, write)

    def traced_ad(argv, timeout=30):
        start = time.perf_counter()
        write('ad.begin', command=argv[0])
        ok, data = original_ad([*argv, '--verbose'], timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        error = (data.get('error') or {}) if isinstance(data, dict) else {}
        details = error.get('details') or {}
        write('ad.end', ms=round(elapsed, 3),
              decode_ms=round(max(0, elapsed - process.elapsed_ms), 3), ok=ok,
              code=error_code(error.get('code')), counters=numeric_stats(details.get('query_stats', {})))
        return ok, data

    host.ad, host.subprocess = traced_ad, process
    start = time.perf_counter()
    try:
        result = host._run_uia(request) if request.get('op') else host._run_sem(request)
        write('host.end', ms=round((time.perf_counter() - start) * 1000, 3),
              status=result.get('status'), error=error_code(result.get('error')), count=result.get('count'))
        return result
    finally:
        host.ad, host.subprocess = original_ad, original_process


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ad-exe', required=True, type=Path)
    parser.add_argument('--app', required=True, help='Process name, not a window title')
    parser.add_argument('--verb', choices=['observe', 'resolve', 'verify', 'uia-locate'], default='resolve')
    parser.add_argument('--name', default='')
    parser.add_argument('--role')
    parser.add_argument('--exact', action='store_true')
    parser.add_argument('--surface')
    parser.add_argument('--session', default='diagnose-sem')
    args = parser.parse_args(argv)
    if not args.ad_exe.is_absolute() or not args.ad_exe.is_file():
        parser.error('--ad-exe must be an existing absolute executable path')
    if not re.fullmatch(r'[\w .-]+\.exe', args.app, re.I):
        parser.error('--app must be an .exe process name')
    if args.verb != 'observe' and not args.name:
        parser.error('--name is required for a query (avoid an unbounded UIA dump)')
    private = ROOT / 'private/sem-diagnostics'
    private.mkdir(parents=True, exist_ok=True)
    # Import functions, not main(): no token, listener, scheduled task or host log.
    spec = importlib.util.spec_from_file_location('diagnostic_host', ROOT / 'pc/executor-host.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    os.environ['AGENT_DESKTOP_HOME'] = str(private / 'ad-state')
    host.AD_EXE, host.SEM_SESSION = str(args.ad_exe), args.session
    host.emit = lambda *unused, **ignored: None
    path = private / f'{time.time_ns()}.jsonl'
    with path.open('x', encoding='utf-8') as stream:
        def write(phase, **fields):
            event = dict(phase=phase, **fields)
            line = json.dumps(event, ensure_ascii=True)
            stream.write(line + '\n')
            stream.flush()
            print(line, flush=True)
        query = dict(name=args.name, exact=args.exact)
        if args.role:
            query['role'] = args.role
        request = dict(app=args.app, verb=args.verb, query=query, mode='skeleton')
        if args.surface:
            request['surface'] = args.surface
        if args.verb == 'uia-locate':
            request['op'] = 'locate'
        write('probe.begin', app=args.app, verb=args.verb)
        profile(host, request, write)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
