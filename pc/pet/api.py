"""Native bridge. Response fields are defined by ui/fixtures/contract.json."""
from i18n import t
import functools
import re
import subprocess

from .actions import ActionError
from .geometry import WINDOW_WIDTH, WINDOW_HEIGHT


def _wrap(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        try:
            return {'ok': True, 'data': method(self, *args, **kwargs)}
        except (ValueError, TypeError):
            return dict(ok=False, error=t('pet.error.invalid_input'), code='invalid_input')
        except ActionError as exc:
            return dict(ok=False, error=str(exc), code=exc.code)
        except subprocess.TimeoutExpired:
            return dict(ok=False, error=t('pet.error.timeout'), code='timeout')
        except Exception:
            return dict(ok=False, error=t('pet.error.backend'), code='backend')
    return call


def _req(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,95}', value):
        raise ValueError('req_id')


class Api:
    @_wrap
    def i18n(self):
        from i18n import bundle
        return bundle()

    @_wrap
    def languages(self):
        from i18n import languages
        return languages()

    @_wrap
    def set_language(self, tag):
        from i18n import set_language
        return set_language(tag)

    def __init__(self, watch, actions, channels, config=None, mailbox=None):
        self._watch, self._actions, self._channels = watch, actions, channels
        self._config = config or {}
        self._mailbox = mailbox
        self._window = None

    def _attach(self, window):
        self._window = window

    @_wrap
    def config(self):
        defaults = dict(display_name='Muse Pet', pack='jolly', bubble_ms=8000, poll_ms=1000, sound=True)
        for key in defaults:
            if key in self._config:
                value = self._config[key]
                if type(value) is type(defaults[key]):
                    defaults[key] = value
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', defaults['pack']):
            defaults['pack'] = 'jolly'
        if not defaults['display_name'].strip():
            defaults['display_name'] = 'Muse Pet'
        defaults['bubble_ms'] = max(1, min(60000, defaults['bubble_ms']))
        defaults['poll_ms'] = max(100, min(60000, defaults['poll_ms']))
        return defaults

    @_wrap
    def state(self):
        result = self._watch.snapshot()
        result["unread"] = self._mailbox.unread() if self._mailbox else 0
        return result

    @_wrap
    def channels(self):
        return self._channels.check()

    @_wrap
    def done(self, req_id):
        _req(req_id)
        out = self._actions.done(req_id)
        if self._mailbox and getattr(self._mailbox, 'syncer', None):
            try:
                self._mailbox.syncer.request()
            except Exception:
                pass
        return out

    @_wrap
    def say(self, text, reply_to=None):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or '\x00' in text:
            raise ValueError('text')
        if reply_to is not None:
            _req(reply_to)
        out = self._actions.say(text.strip(), reply_to)
        if self._mailbox and getattr(self._mailbox, 'syncer', None):
            try:
                self._mailbox.syncer.request()
            except Exception:
                pass
        return out

    @_wrap
    def open_mailbox(self, thread=None):
        if thread is not None and (not isinstance(thread, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', thread)):
            raise ValueError('thread')
        self._actions.open_mailbox(thread)

    @_wrap
    def open_session(self):
        self._actions.open_session()

    @_wrap
    def hide(self):
        self._window.hide()

    @_wrap
    def screen(self):
        return self._window.screen()

    @_wrap
    def exit(self):
        self._window.exit()

    @_wrap
    def set_avatar(self):
        updated = bool(self._window.set_avatar())
        if updated and self._mailbox and hasattr(self._mailbox, 'refresh_avatar'):
            self._mailbox.refresh_avatar()
        return {'updated': updated}

    @_wrap
    def region(self, rects):
        if not isinstance(rects, list) or len(rects) > 32:
            raise ValueError('rects')
        for r in rects:
            if not isinstance(r, dict) or set(r) != {'x', 'y', 'w', 'h'} or any(type(v) is not int for v in r.values()):
                raise ValueError('rect')
            if r['x'] < 0 or r['y'] < 0 or r['w'] < 0 or r['h'] < 0 or r['x'] + r['w'] > WINDOW_WIDTH or r['y'] + r['h'] > WINDOW_HEIGHT:
                raise ValueError('bounds')
        self._window.region(rects)

    @_wrap
    def drag(self):
        self._window.drag()

    @_wrap
    def activate(self, on):
        if type(on) is not bool:
            raise ValueError('on')
        self._window.activate(on)
