#!/usr/bin/env python3
"""Scan tracked file contents for installation-specific or secret values.

Only the standard library is used. Extra patterns are regular expressions,
one per line; prefix with literal: for exact strings. Empty/comment lines
are ignored. Exclusions are exact paths, or directories ending in /, each
with a required '# deletion condition'. Findings never echo matched text.
Binary files are scanned for the text they carry (see readable()).
"""
import argparse
import ipaddress
from pathlib import Path
import re
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
GIT = ['git', '-c', 'safe.directory=*', '-C', str(ROOT)]
IP = re.compile(r'(?<![\d.])100\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])')
WIN = re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s"\x27`]+)', re.I)
LINUX = re.compile(r'/home/(<[^>]+>|[\w.-]+)')
EMAIL = re.compile(r'[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----')
TAILNET = ipaddress.ip_network(((100 << 24) | (64 << 16), 10))
PLACEHOLDERS = {'x', 'winuser', 'user', 'username', 'account', 'example', 'testuser'}


def placeholder(value):
    return (value.lower().rstrip('.,;:)') in PLACEHOLDERS or
            re.fullmatch(r'<[^>]+>[.,;:)]*', value) is not None or
            value.startswith(('$', '%', '{')))


def exclusions(path):
    if not path.exists():
        return []
    result = []
    for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        name, sep, condition = raw.partition('#')
        name = name.strip().replace('\\', '/')
        if (not sep or not condition.strip() or not name or name.startswith('/') or
                '..' in name.split('/') or any(c in name for c in '*?')):
            raise ValueError(f'{path.name}:{number}: invalid exclusion or missing deletion condition')
        result.append(name)
    return result


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
STRINGS = re.compile(rb'[\x20-\x7e]{8,}')
def readable(raw):
    """The text a file can carry. Text files whole. A PNG's text lives only in
    its tEXt/iTXt/zTXt chunks; its compressed pixels are noise that can look
    like an address. Other binaries: printable ASCII runs of 8+ bytes."""
    if b'\0' not in raw[:8192]:
        return raw.decode('utf-8-sig', errors='replace')
    if raw.startswith(PNG_SIGNATURE):
        texts, i = [], len(PNG_SIGNATURE)
        while i + 8 <= len(raw):
            size, kind = int.from_bytes(raw[i:i + 4], 'big'), raw[i + 4:i + 8]
            body = raw[i + 8:i + 8 + size]
            if kind == b'zTXt':
                key, _, rest = body.partition(b'\0')
                try:
                    body = key + b'\n' + zlib.decompress(rest[1:])
                except zlib.error:
                    pass
            if kind in (b'tEXt', b'iTXt', b'zTXt'):
                texts.append(body.replace(b'\0', b'\n').decode('utf-8', errors='replace'))
            i += 12 + size
        return '\n'.join(texts)
    return '\n'.join(m.decode('ascii') for m in STRINGS.findall(raw))
def rules(line, name):
    found = set()
    is_example = ('example' in name.lower() or name.startswith(('tests/', 'spec/test-vectors/')) or
                  '/fixtures/' in name)
    for match in IP.finditer(line):
        try:
            address = ipaddress.ip_address(match[0])
        except ValueError:
            continue
        if address in TAILNET:
            allowed = is_example and int(address) - int(TAILNET.network_address) in range(1, 10)
            if not allowed:
                found.add('tailnet-ip')
    if any(not placeholder(m[1]) for m in WIN.finditer(line)):
        found.add('windows-user-path')
    if any(not placeholder(m[1]) for m in LINUX.finditer(line)):
        found.add('linux-user-path')
    for m in EMAIL.finditer(line):
        domain = m[1].lower()
        # example domains, and GitHub's no-reply domain (a public commit identity, not a mailbox)
        if not (domain == 'example' or domain.endswith('.example') or
                any(domain == d or domain.endswith('.' + d)
                    for d in ('example.com', 'example.net', 'example.org', 'users.noreply.github.com'))):
            found.add('email')
    if PRIVATE_KEY.search(line):
        found.add('private-key')
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patterns', type=Path)
    args = parser.parse_args()
    try:
        excluded = exclusions(ROOT / 'tools/public-exclude.txt')
        extra = []
        if args.patterns:
            for number, raw in enumerate(args.patterns.read_text(encoding='utf-8-sig').splitlines(), 1):
                if not raw.strip() or raw.lstrip().startswith('#'):
                    continue
                pattern = re.escape(raw[8:]) if raw.startswith('literal:') else raw
                extra.append((number, re.compile(pattern, re.I)))
        names = subprocess.check_output(GIT + ['ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode('utf-8').split('\0')
        count = 0
        for name in sorted(set(names) - {''}):
            if any(name == p or p.endswith('/') and name.startswith(p) for p in excluded):
                continue
            path = ROOT / name
            if not path.exists():
                continue
            if path.is_symlink():
                data = str(path.readlink())
            else:
                data = readable(path.read_bytes())
            for number, line in enumerate(data.splitlines(), 1):
                hits = rules(line, name)
                hits.update('pattern-' + str(i) for i, regex in extra if regex.search(line))
                for rule in sorted(hits):
                    print(f'{name}:{number}:{rule}')
                    count += 1
        print(f'check-public: {count} findings')
        return int(count != 0)
    except (OSError, ValueError, re.error, subprocess.CalledProcessError) as exc:
        print('check-public: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
