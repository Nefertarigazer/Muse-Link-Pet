"""Pet-owned mailbox window: no second application, singleton, or tray.

Window and sync errors stay inside this controller. A failed open is retryable;
closing hides the child; shutting down the pet destroys it and stops its writer.
"""
import ctypes
from ctypes import wintypes as W
import json
import threading
from pathlib import Path

from i18n import t
from mailbox.api import Api
from mailbox.app import _geometry, _round_corners
from mailbox.shell import static_server_class
from mailbox.syncer import Syncer
from mlindex.__main__ import _resolve_paths
from pet.actions import ActionError
from pet.tray import ICON_PATH


class MailboxWindow:
    def __init__(self, config, directory, state_directory=None, actions=None, on_set_avatar=None):
        self.config, self.directory = config, Path(directory)
        self.state_directory = Path(state_directory) if state_directory else self.directory
        self._actions = actions
        self._on_set_avatar = on_set_avatar
        self.window = self.syncer = self._server = None
        self._lock = threading.RLock()
        self._geometry_lock = threading.Lock()
        self._exiting = False
        self._ready = False
        self._visible = False
        self._pending = None

    def start(self):
        try:
            with self._lock:
                if self.syncer is None:
                    root, index = _resolve_paths(None, None, self.config)
                    self.index = index
                    self.syncer = Syncer(root, index, seen_path=self.state_directory / 'seen.json')
                    self.syncer.start()
        except Exception:
            self.syncer = None

    def unread(self):
        try:
            if not self.syncer:
                return 0
            val = self.syncer.attention
            return val if type(val) is int else 0
        except Exception:
            return 0

    def open(self, thread=None):
        try:
            with self._lock:
                if self._exiting:
                    raise RuntimeError('pet exiting')
                self.start()
                if self.syncer is None:
                    raise RuntimeError('sync unavailable')
                self._pending = thread
                if self.window is not None:
                    if self._ready:
                        self._focus()
                    return
                import webview
                api = Api(self.syncer, self.config, actions=self._actions,
                          history_path=self.state_directory / 'pet-history.jsonl',
                          on_minimize=self.minimize,
                          avatar_path=self.state_directory / 'user-avatar.png',
                          on_set_avatar=self._pick_avatar)
                if self._server is None:
                    # pywebview 6 treats an inherited is_running property on a
                    # server class as truthy and reuses the pet server for a
                    # dynamic local URL. Start a restricted server explicitly;
                    # its HTTP URL keeps the two static roots independent.
                    _, _, self._server = static_server_class(self.directory / 'ui').start_server([], 0)
                window = webview.create_window(t('mailbox.title'), self._server.address + 'index.html',
                    js_api=api, frameless=True, easy_drag=False, resizable=False, hidden=True,
                    background_color='#F5F5F7', **_geometry(self.state_directory))
                if window is None:
                    raise RuntimeError('window unavailable')
                self.window = window
                api._attach(window)
                window.events.loaded += self._loaded
                window.events.closing += self._closing
                window.events.closed += self._closed
                window.events.moved += self._save_geometry
        except Exception as exc:
            failed = self.window
            self._closed()
            if failed:
                try:
                    failed.destroy()
                except Exception:
                    pass
            raise ActionError('open_failed', t('pet.error.mailbox_open')) from exc

    def _loaded(self):
        try:
            from System import Action
            from System.Drawing import Icon
            window = self.window
            def decorate():
                icon_path = str(ICON_PATH)
                window.native.Icon = Icon(icon_path)
                # Show on the taskbar with the MusePet icon so minimizing goes
                # cleanly to the taskbar and clicking it reopens the mailbox.
                window.native.ShowIcon = True
                window.native.ShowInTaskbar = True
                window.native.MinimizeBox = True
                hwnd = window.native.Handle.ToInt64()
                user = ctypes.WinDLL('user32', use_last_error=True)
                user.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
                user.LoadImageW.restype = W.HANDLE
                user.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, ctypes.c_void_p]
                user.SendMessageW.restype = ctypes.c_ssize_t
                sm_cx = user.GetSystemMetrics(11) or 32
                sm_sm = user.GetSystemMetrics(49) or 16
                h_big = user.LoadImageW(None, icon_path, 1, sm_cx, sm_cx, 0x0010)
                h_small = user.LoadImageW(None, icon_path, 1, sm_sm, sm_sm, 0x0010)
                if h_big:
                    user.SendMessageW(hwnd, 0x0080, 1, h_big)
                if h_small:
                    user.SendMessageW(hwnd, 0x0080, 0, h_small)
                try:
                    set_cls = getattr(user, 'SetClassLongPtrW', user.SetClassLongW)
                    set_cls.argtypes = [W.HWND, ctypes.c_int, ctypes.c_void_p]
                    set_cls.restype = ctypes.c_void_p
                    if h_big:
                        set_cls(hwnd, -14, h_big)
                    if h_small:
                        set_cls(hwnd, -34, h_small)
                except Exception:
                    pass
                style = user.GetWindowLongW(hwnd, -16)
                user.SetWindowLongW(hwnd, -16, style | 0x00020000 | 0x00080000)
                user.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
                _round_corners(window)
            window.native.Invoke(Action(decorate))
            self._ready = True
            self._focus()
        except Exception:
            # A bad mailbox bridge or native decoration must not tear down pet.
            window = self.window
            self._closed()
            if window:
                try:
                    window.destroy()
                except Exception:
                    pass

    def _focus(self):
        self._visible = True
        self.window.show()
        self.window.restore()
        try:
            if self._pending is not None:
                self.window.evaluate_js(
                    'location.hash = "thread=" + encodeURIComponent(%s); '
                    'window.MuseMailbox && MuseMailbox.onShow && MuseMailbox.onShow()' %
                    json.dumps(self._pending, ensure_ascii=True))
            else:
                self.window.evaluate_js('window.MuseMailbox && MuseMailbox.onShow && MuseMailbox.onShow()')
        except Exception:
            pass

    def minimize(self):
        self._save_geometry()
        if self.window:
            try:
                self.window.minimize()
            except Exception:
                pass

    def _pick_avatar(self):
        if self._on_set_avatar is None:
            return False
        owner = self.window.native if self.window and self._visible else None
        return bool(self._on_set_avatar(owner))

    def refresh_avatar(self):
        if self.window and self._ready:
            try:
                self.window.evaluate_js('window.MuseMailbox && MuseMailbox.refreshAvatar && MuseMailbox.refreshAvatar()')
            except Exception:
                pass

    def _closing(self):
        self._visible = False
        self._save_geometry()
        if self._exiting or not self._ready:
            return True
        try:
            self.window.hide()
        except Exception:
            return True
        return False

    def _save_geometry(self, *unused):
        try:
            with self._geometry_lock:
                if self.window is None:
                    return
                data = {key:getattr(self.window,key) for key in ('x','y')}
                self.state_directory.mkdir(parents=True,exist_ok=True)
                path = self.state_directory / 'window.json'
                temp = path.with_suffix('.json.tmp')
                temp.write_text(json.dumps(data),encoding='utf-8')
                temp.replace(path)
        except Exception:
            pass

    def _closed(self):
        self.window = None
        self._ready = False

    def stop(self):
        self._exiting = True
        window, self.window = self.window, None
        if window:
            try:
                window.destroy()
            except Exception:
                pass
        if self.syncer:
            try:
                self.syncer.stop()
            except Exception:
                pass
        if self._server:
            try:
                self._server._httpd.shutdown()
                self._server._httpd.server_close()
            except Exception:
                pass
            self._server = None
