"""Actions cross the existing script boundaries, never write the waiting bus."""
from datetime import datetime, timezone
from i18n import t
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
import webbrowser

from .watch import done_info, read, read_json

FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
POWERSHELL = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) /
                 'System32/WindowsPowerShell/v1.0/powershell.exe')


MUSE_URL = 'https://muse.ai/'
MUSE_APP_NAME = 'Muse'     # the name in muse.ai's web app manifest
PROFILE_RX = re.compile(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}')
# Chrome and Edge register each installed web app as an uninstall entry:
#   DisplayName = the app's name, UninstallString =
#   "<browser>.exe" --profile-directory=<p> --uninstall-app-id=<32 x a-p>
_UNINSTALL_RX = re.compile(r'^\s*"([^"]+\\(?:chrome|msedge)\.exe)"\s+--profile-directory=(?:"([^"]+)"|(\S+))'
                           r'\s+--uninstall-app-id=([a-p]{32})\s*$', re.I)
def installed_web_apps():
    """[{name, browser, profile, app_id}] of the current user's installed
    Chrome / Edge web apps; [] when none or not on Windows."""
    try:
        import winreg
    except ImportError:
        return []
    out = []
    path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return []
    with root:
        for i in range(4096):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, sub) as key:
                    name = str(winreg.QueryValueEx(key, 'DisplayName')[0])
                    cmd = str(winreg.QueryValueEx(key, 'UninstallString')[0])
            except OSError:
                continue
            m = _UNINSTALL_RX.match(cmd)
            profile = m and (m[2] or m[3])
            if m and PROFILE_RX.fullmatch(profile) and os.path.isfile(m[1]):
                out.append(dict(name=name, browser=m[1], profile=profile, app_id=m[4].lower()))
    return out
class ActionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Actions:
    def __init__(self, tool, config=None, runner=subprocess.run, opener=webbrowser.open,
                 web_apps=installed_web_apps, popen=subprocess.Popen):
        self.tool = Path(tool)
        self.config = config or {}
        self._run = runner
        self._opener = opener
        self._web_apps = web_apps
        self._popen = popen

    def _script(self, script, *args):
        return self._run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                          'Bypass', '-File', str(script), *args], cwd=str(self.tool),
                         creationflags=FLAGS, timeout=10, capture_output=True,
                         encoding='utf-8', errors='replace')

    def _complete(self, req_id, kind, text):
        # JSON splatting avoids PowerShell's native empty-string/quote losses,
        # and keeps user text out of process command lines and shell source.
        with tempfile.TemporaryDirectory(prefix='muselink-pet-') as directory:
            directory = Path(directory)
            params = directory / 'args.json'
            params.write_text(json.dumps(dict(script=str(self.tool / 'Complete-MuseWait.ps1'),
                                            req_id=req_id, kind=kind, text=text)), encoding='utf-8')
            wrapper = directory / 'complete.ps1'
            wrapper.write_text("param([string]$ArgsFile)\n$ErrorActionPreference = 'Stop'\n"
                               "$p = [IO.File]::ReadAllText($ArgsFile, [Text.Encoding]::UTF8) | ConvertFrom-Json\n"
                               "& $p.script -ReqId $p.req_id -Kind $p.kind -Result $p.text\n"
                               "exit $LASTEXITCODE\n", encoding='ascii')
            result = self._script(wrapper, '-ArgsFile', str(params))
        if result.returncode == 3:
            raise ActionError('already_answered', t('pet.error.already_answered'))
        if result.returncode:
            raise ActionError('delivery_failed', t('pet.error.answer_delivery'))
        req, _, actual_kind = done_info(self.tool)
        return req == req_id and actual_kind == kind

    def _record_wait_history(self, req_id, kind, ask_text, answer):
        mailbox_dir = self.tool / 'mailbox'
        if not mailbox_dir.is_dir():
            return
        try:
            entry = dict(
                ts=datetime.now(timezone.utc).timestamp(),
                req_id=req_id,
                ask=str(ask_text or '').strip(),
                kind=kind,
                answer=str(answer or ''),
            )
            with (mailbox_dir / 'pet-history.jsonl').open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def done(self, req_id):
        armed = read_json(self.tool / 'popup-armed.json')
        ask_text = armed.get('summary', '') if armed.get('req_id') == req_id else ''
        delivered = self._complete(req_id, 'pet_done', '')
        if delivered:
            self._record_wait_history(req_id, 'pet_done', ask_text, '')
        return {'delivered': delivered}

    def say(self, text, reply_to=None, thread='pet/chat'):
        if not isinstance(thread, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', thread):
            raise ValueError('thread')
        if reply_to and reply_to == read(self.tool / 'popup-pending.txt'):
            armed = read_json(self.tool / 'popup-armed.json')
            ask_text = armed.get('summary', '') if armed.get('req_id') == reply_to else ''
            if not self._complete(reply_to, 'pet_typed', text):
                raise ActionError('stale_request', t('pet.error.stale_request'))
            self._record_wait_history(reply_to, 'pet_typed', ask_text, text)
            return dict(via='wait', req_id=reply_to)
        with tempfile.TemporaryDirectory(prefix='muselink-pet-') as directory:
            path = Path(directory) / 'text.txt'
            path.write_bytes(text.encode('utf-8'))
            result = self._script(self.tool / 'muse-call.ps1', '-Type', 'message', '-NoWait',
                                  '-As', 'pet', '-Thread', thread, '-TextFile', str(path))
        match = re.search(r'MUSE_CALL_POSTED req=([A-Za-z0-9_-]+)', result.stdout)
        if result.returncode or not match:
            raise ActionError('delivery_failed', t('pet.error.message_delivery'))
        return dict(via='bridge', req_id=match[1])

    def open_mailbox(self, thread=None):
        # Bound to the in-process controller by pet.app before any UI starts.
        raise ActionError('open_failed', t('pet.error.mailbox_open'))

    def open_session(self):
        """The Muse session, in this order:
        1. the installed Muse web app (Chrome / Edge "install app"), launched
           as its own window; with pet.session_profile set, the one installed
           in that profile is preferred;
        2. pet.session_browser (+ pet.session_profile): that browser and
           profile open the session URL (a personal value, empty by default);
        3. the system default browser opens the session URL.
        The session URL is pet.session_url, default https://muse.ai/."""
        url = self.config.get('session_url') or MUSE_URL
        browser = self.config.get('session_browser') or ''
        profile = self.config.get('session_profile') or ''
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
                or not isinstance(browser, str) or not isinstance(profile, str)
                or (profile and not PROFILE_RX.fullmatch(profile))
                or (browser and not (os.path.isabs(browser) and browser.lower().endswith('.exe')))):
            raise ActionError('invalid_config', t('pet.error.invalid_config'))
        apps = [a for a in self._web_apps() if a['name'] == MUSE_APP_NAME]
        apps.sort(key=lambda a: a['profile'] != profile)
        for app in apps:
            if self._launch([app['browser'], '--profile-directory=' + app['profile'], '--app-id=' + app['app_id']]):
                return
        if browser:
            if not os.path.isfile(browser):
                raise ActionError('invalid_config', t('pet.error.invalid_config'))
            argv = [browser] + (['--profile-directory=' + profile] if profile else []) + [url]
            if self._launch(argv):
                return
            raise ActionError('open_failed', t('pet.error.open_failed'))
        if not self._opener(url):
            raise ActionError('open_failed', t('pet.error.open_failed'))
    def _launch(self, argv):
        try:
            self._popen(argv, cwd=str(Path(argv[0]).parent), creationflags=FLAGS)
            return True
        except OSError:
            return False
