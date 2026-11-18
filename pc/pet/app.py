"""Ordinary-user transparent WebView2 pet; only static HTTP, native JS bridge."""
import argparse
import atexit
import ctypes
from ctypes import wintypes as W
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from i18n import t

from mailbox.app import _runtime_available
from mailbox.shell import Instance, static_server_class
from pet.api import Api
from pet.geometry import CHAR_RECT, SPILL, UI_SCALE, WINDOW_WIDTH, WINDOW_HEIGHT, clamp_character, available
from pet.actions import Actions
from pet.channels import Channels
from pet.watch import Watch, iso, read_json
from pet.tray import ICON_PATH, Tray

HERE = Path(__file__).resolve().parent
POSITION_FILE = 'pet-position.json'   # v2 visible sprite origin (physical px); legacy window origins accepted


def source_layout(directory=HERE):
    """Recognize the source tree, including exports without Git metadata.

    Installed PC packages are flat. An unrelated ancestor's .git says
    nothing about the layout of this package.
    """
    directory = Path(directory).resolve()
    root = directory.parent.parent
    return (directory == root / 'pc' / 'pet' and (root / 'vm').is_dir()
            and (root / 'tools' / 'check-public.py').is_file())


def event_directory(directory=HERE):
    """Choose the stream by package layout, never by log existence."""
    directory = Path(directory).resolve()
    return directory if source_layout(directory) else directory.parent


def instance_scope(directory=HERE):
    """All launches sharing a popup bus share a singleton, even with an
    explicit event-stream override. Separate source trees stay isolated."""
    directory = Path(directory).resolve()
    return os.path.normcase(str(directory.parent)) if source_layout(directory) else None


def _dialog(text, name='Muse Pet'):
    ctypes.windll.user32.MessageBoxW(None, text, name, 0x10)


def _diag_path(scope=None, directory=None):
    if directory is not None:
        return Path(directory) / 'pet-runtime.log'
    scoped = 'pet' if scope is None else 'pet-' + hashlib.sha256(str(scope).encode()).hexdigest()[:24]
    base = Path(os.environ.get('LOCALAPPDATA') or str(HERE))
    return base / 'MuseLink' / scoped / 'pet-runtime.log'


class _DiagLog:
    """Bounded local diagnostic and faulthandler log (no user/message text)."""

    def __init__(self, path, max_bytes=262144):
        self.path = Path(path)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._fh = None
        self._prev_excepthook = None
        self._prev_threading_hook = None
        self._atexit_cb = None
        self._ui_exc_handler = None
        self._domain_exc_handler = None

    def open(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self._max_bytes:
                try:
                    os.replace(self.path, self.path.with_suffix('.log.1'))
                except OSError:
                    pass
            self._fh = self.path.open('a', encoding='utf-8', buffering=1)
            try:
                faulthandler.enable(file=self._fh, all_threads=True)
            except Exception:
                pass
        except OSError:
            self._fh = None

    def write(self, message):
        with self._lock:
            fh = self._fh
            if fh is None:
                return
            try:
                fh.write('%s pid=%d %s\n' % (iso(), os.getpid(), message))
                fh.flush()
            except OSError:
                pass

    def _write_tb(self, tb):
        if tb is None:
            return
        with self._lock:
            fh = self._fh
            if fh is None:
                return
            try:
                for frame in traceback.extract_tb(tb)[-12:]:
                    fh.write('  at %s:%d in %s\n' % (os.path.basename(frame.filename), frame.lineno, frame.name))
                fh.flush()
            except OSError:
                pass

    def install_hooks(self):
        if self._prev_excepthook is None:
            self._prev_excepthook = sys.excepthook
            def on_exc(exc_type, exc, tb):
                self.write('main unhandled %s' % exc_type.__name__)
                self._write_tb(tb)
                if self._prev_excepthook:
                    self._prev_excepthook(exc_type, exc, tb)
            sys.excepthook = on_exc
        if self._prev_threading_hook is None and hasattr(threading, 'excepthook'):
            self._prev_threading_hook = threading.excepthook
            def on_thread_exc(args):
                name = getattr(args.thread, 'name', '?')
                self.write('thread unhandled %s: %s' % (name, args.exc_type.__name__))
                self._write_tb(args.exc_traceback)
                if self._prev_threading_hook:
                    self._prev_threading_hook(args)
            threading.excepthook = on_thread_exc
        if self._atexit_cb is None:
            self._atexit_cb = lambda: self.write('atexit')
            atexit.register(self._atexit_cb)

    def install_clr_hooks(self):
        # Must run on the WinForms STA UI thread so Application.ThreadException
        # attaches to the UI thread context rather than a background thread.
        try:
            import System.Windows.Forms as WinForms
            from System import AppDomain, UnhandledExceptionEventHandler
            from System.Threading import ThreadExceptionEventHandler
            if self._ui_exc_handler is None:
                def on_ui_exc(sender, args):
                    exc = getattr(args, 'Exception', None)
                    self.write('winforms thread exception: %s' % (type(exc).__name__ if exc is not None else '?'))
                self._ui_exc_handler = ThreadExceptionEventHandler(on_ui_exc)
                WinForms.Application.ThreadException += self._ui_exc_handler
            if self._domain_exc_handler is None:
                def on_domain_exc(sender, args):
                    exc = getattr(args, 'ExceptionObject', None)
                    term = getattr(args, 'IsTerminating', None)
                    self.write('clr unhandled terminating=%s: %s' % (term, type(exc).__name__ if exc is not None else '?'))
                self._domain_exc_handler = UnhandledExceptionEventHandler(on_domain_exc)
                AppDomain.CurrentDomain.UnhandledException += self._domain_exc_handler
        except Exception:
            pass

    def close(self):
        try:
            faulthandler.disable()
        except Exception:
            pass
        if self._atexit_cb is not None:
            try:
                atexit.unregister(self._atexit_cb)
            except Exception:
                pass
            self._atexit_cb = None
        if self._prev_excepthook is not None:
            sys.excepthook = self._prev_excepthook
            self._prev_excepthook = None
        if self._prev_threading_hook is not None and hasattr(threading, 'excepthook'):
            threading.excepthook = self._prev_threading_hook
            self._prev_threading_hook = None
        try:
            import System.Windows.Forms as WinForms
            from System import AppDomain
            if self._ui_exc_handler is not None:
                WinForms.Application.ThreadException -= self._ui_exc_handler
                self._ui_exc_handler = None
            if self._domain_exc_handler is not None:
                AppDomain.CurrentDomain.UnhandledException -= self._domain_exc_handler
                self._domain_exc_handler = None
        except Exception:
            pass
        with self._lock:
            fh, self._fh = self._fh, None
            if fh is not None:
                try:
                    fh.flush()
                    fh.close()
                except OSError:
                    pass


class Shell:
    def __init__(self, window, directory=HERE, on_error=None, on_ui_prepare=None):
        self.window, self.directory = window, Path(directory)
        self._on_error = on_error
        self._on_ui_prepare = on_ui_prepare
        self._err_at = {}
        self._js_worker = None
        self.visible = True
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.rects = []
        self.dragging = False
        self._region_seq = 0
        self._lock = threading.RLock()
        self._user = ctypes.WinDLL('user32', use_last_error=True)
        self._gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self._user.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
        self._user.SetWindowLongW.argtypes = [W.HWND, ctypes.c_int, W.LONG]
        self._user.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
        self._user.SetWindowRgn.argtypes = [W.HWND, W.HRGN, W.BOOL]
        self._user.SetForegroundWindow.argtypes = [W.HWND]
        self._user.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
        self._user.GetDpiForWindow.argtypes = [W.HWND]
        self._user.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
        self._user.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
        self._user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user.GetAsyncKeyState.restype = ctypes.c_short
        self._user.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._gdi.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        self._gdi.CreateRectRgn.restype = W.HRGN
        self._gdi.CombineRgn.argtypes = [W.HRGN, W.HRGN, W.HRGN, ctypes.c_int]
        self._gdi.DeleteObject.argtypes = [W.HGDIOBJ]

    @property
    def hwnd(self):
        return W.HWND(self.window.native.Handle.ToInt64())

    def _ui(self, callback):
        from System import Action
        if self.window.native.InvokeRequired:
            self.window.native.Invoke(Action(callback))
        else:
            callback()

    def _report_error(self, where, exc):
        cb = getattr(self, '_on_error', None)
        if cb is None:
            return
        now = time.monotonic()
        prev = getattr(self, '_err_at', {})
        if now - prev.get(where, float('-inf')) < 60:
            return
        prev[where] = now
        self._err_at = prev
        try:
            cb('%s: %s' % (where, type(exc).__name__))
        except Exception:
            pass

    def _eval_js(self, script, timeout=1.0):
        stop = getattr(self, 'stop_event', None)
        if stop is not None and stop.is_set():
            return False
        worker = getattr(self, '_js_worker', None)
        if worker is not None and worker.is_alive():
            return False  # WebView2 JS call already wedged; avoid stalling or leaking threads
        done = threading.Event()
        err = [None]
        def call():
            try:
                self.window.evaluate_js(script)
            except Exception as exc:
                err[0] = exc
            finally:
                done.set()
        t = threading.Thread(target=call, name='pet-js', daemon=True)
        self._js_worker = t
        t.start()
        if not done.wait(timeout):
            self._report_error('eval_js_timeout', TimeoutError('evaluate_js'))
            return False
        if err[0] is not None:
            raise err[0]
        return True

    def presence(self):
        with self._lock:
            path = self.directory / 'pet-presence.json'
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(dict(visible=self.visible, at=iso())), encoding='utf-8')
            os.replace(temp, path)

    def hide(self):
        self.activate(False)
        self.window.hide()
        self.visible = False
        try:
            self.presence()
        except OSError as exc:
            self._report_error('hide-presence', exc)

    def exit(self):
        # Use the tray's shutdown gate: destroy alone is intercepted as hide.
        self._exit_callback()

    def set_avatar(self, owner=None):
        chosen = [None]
        host = owner if owner is not None else self.window.native
        def pick():
            from System.Windows.Forms import DialogResult, OpenFileDialog
            try:
                host.Activate()
            except Exception:
                pass
            dlg = OpenFileDialog()
            dlg.Title = t('pet.set_avatar')
            dlg.Filter = 'Images (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif)|*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif'
            dlg.Multiselect = False
            dlg.CheckFileExists = True
            if dlg.ShowDialog(host) == DialogResult.OK and dlg.FileName:
                chosen[0] = str(dlg.FileName)
        if owner is not None:
            from System import Action
            if owner.InvokeRequired:
                owner.Invoke(Action(pick))
            else:
                pick()
        else:
            self._ui(pick)
        src = chosen[0]
        if not src:
            return False
        from PIL import Image, ImageOps
        dest = self.directory.parent / 'mailbox' / 'user-avatar.png'
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_suffix('.png.tmp')
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img).convert('RGBA')
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            cropped = img.crop((left, top, left + side, top + side))
            resized = cropped.resize((256, 256), Image.Resampling.LANCZOS)
            resized.save(temp, format='PNG')
        os.replace(temp, dest)
        return True

    def show(self, talk=False):
        if not self.ready.wait(10):
            return
        # SW_SHOWNOACTIVATE avoids pywebview.show(), which calls Activate().
        self._ui(lambda: self._user.ShowWindow(self.hwnd, 4))
        self.anchor(raise_=True)
        self.visible = True
        try:
            self.presence()
        except OSError as exc:
            self._report_error('show-presence', exc)
        try:
            self._eval_js('MusePet.shown()')
            if talk:
                self._eval_js('MusePet.talk()')
        except Exception as exc:
            self._report_error('show-js', exc)

    def activate(self, on):
        def change():
            self.window.focus = on  # pywebview's activation callback observes this too
            style = self._user.GetWindowLongW(self.hwnd, -20) | 0x80
            style = style & ~0x08000000 if on else style | 0x08000000
            self._user.SetWindowLongW(self.hwnd, -20, style)
            if on:
                self._user.SetForegroundWindow(self.hwnd)
        self._ui(change)

    def region(self, rects):
        self.rects = [dict(r) for r in rects]
        self._region_seq = getattr(self, '_region_seq', 0) + 1
        def change():
            stop = getattr(self, 'stop_event', None)
            if (stop and stop.is_set()) or getattr(self.window.native, 'IsDisposed', False) is True:
                return
            scale = (self._user.GetDpiForWindow(self.hwnd) / 96) or 1
            combined = self._gdi.CreateRectRgn(0, 0, 0, 0)
            try:
                for r in self.rects:
                    part = self._gdi.CreateRectRgn(round(r['x'] * scale), round(r['y'] * scale),
                        round((r['x'] + r['w']) * scale), round((r['y'] + r['h']) * scale))
                    try:
                        if not part or not self._gdi.CombineRgn(combined, combined, part, 2):
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        if part:
                            self._gdi.DeleteObject(part)
                if not self._user.SetWindowRgn(self.hwnd, combined, True):
                    raise ctypes.WinError(ctypes.get_last_error())
                combined = None  # ownership transferred to Windows
            finally:
                if combined:
                    self._gdi.DeleteObject(combined)
        self._ui(change)

    def _load_position(self):
        try:
            value = json.loads((self.directory / POSITION_FILE).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if isinstance(value, dict) and type(value.get('x')) is int and type(value.get('y')) is int:
            # Legacy positions were the 740px window origin. Preserve the
            # sprite's physical location when adding the left spill.
            scale = self._user.GetDpiForWindow(self.hwnd) / 96 or 1
            if value.get('version') == 2:
                return (round(value['x'] - CHAR_RECT['x'] * scale),
                        round(value['y'] - CHAR_RECT['y'] * scale))
            return round(value['x'] - SPILL['left'] * UI_SCALE * scale), value['y']
        return None

    def _save_position(self, x, y):
        path = self.directory / POSITION_FILE
        temp = path.with_suffix('.tmp')
        scale = self._user.GetDpiForWindow(self.hwnd) / 96 or 1
        temp.write_text(json.dumps(dict(version=2, x=round(x + CHAR_RECT['x'] * scale),
                                        y=round(y + CHAR_RECT['y'] * scale))), encoding='utf-8')
        os.replace(temp, path)

    def anchor(self, raise_=False):
        """Default spot: bottom-right of the primary work area. After the user
        has dragged the pet: the saved spot, kept inside the work area of the
        screen it is on (a monitor may have gone away). Physical pixels."""
        stop = getattr(self, 'stop_event', None)
        if self.dragging or (stop and stop.is_set()):
            return
        def move():
            if (stop and stop.is_set()) or getattr(self.window.native, 'IsDisposed', False) is True:
                return
            from System.Drawing import Rectangle
            from System.Windows.Forms import Screen
            scale = (self._user.GetDpiForWindow(self.hwnd) / 96) or 1
            width, height = round(WINDOW_WIDTH * scale), round(WINDOW_HEIGHT * scale)
            c = CHAR_RECT
            saved = self._load_position()
            if saved is None:
                primary = Screen.PrimaryScreen
                if primary is None:
                    return
                area = primary.WorkingArea
                x, y = area.Right - (c['x']+c['w'])*scale, area.Bottom - (c['y']+c['h'])*scale
            else:
                scr = Screen.FromRectangle(Rectangle(round(saved[0]+c['x']*scale),
                    round(saved[1]+c['y']*scale), round(c['w']*scale), round(c['h']*scale)))
                if scr is None:
                    return
                area = scr.WorkingArea
                x, y = saved
            x, y = clamp_character(x,y,scale,(area.Left,area.Top,area.Right,area.Bottom))
            # HWND_TOPMOST only when asked (start, show): the 5 s re-anchor
            # keeps the z-order (SWP_NOZORDER), so a card shown later stays
            # above the pet instead of being covered again within 5 s.
            after, flags = (W.HWND(-1), 0x10) if raise_ else (None, 0x10 | 0x04)
            self._user.SetWindowPos(self.hwnd, after, x, y, width, height, flags)  # SWP_NOACTIVATE
        self._ui(move)
        self.push_avail()

    def push_avail(self):
        try:
            area = self.screen()
            if area != getattr(self, "_last_avail", None):
                if self._eval_js("window.MusePet && MusePet.avail(%s)" % json.dumps(area)):
                    self._last_avail = area
        except Exception:
            pass  # frontend is still loading or has already closed

    def screen(self):
        """The work area of the screen the character is on, in window logical
        pixels relative to the window (the menu stays inside it)."""
        from System.Drawing import Rectangle
        from System.Windows.Forms import Screen
        rect = W.RECT()
        if not self._user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        scale = self._user.GetDpiForWindow(self.hwnd) / 96 or 1
        c = CHAR_RECT
        char = Rectangle(round(rect.left+c['x']*scale), round(rect.top+c['y']*scale),
                         round(c['w']*scale), round(c['h']*scale))
        scr = Screen.FromRectangle(char)
        if scr is None:
            return None
        area = scr.WorkingArea
        return available(rect.left,rect.top,(rect.right-rect.left)/scale,(rect.bottom-rect.top)/scale,
                         scale,(area.Left,area.Top,area.Right,area.Bottom))

    def drag(self):
        """Move the window with the cursor until the button that started the
        drag is released, then remember the spot. Cursor and window positions
        are both physical pixels, so mixed-DPI monitors need no conversion."""
        if self.dragging:
            return
        hwnd, point, rect = self.hwnd, W.POINT(), W.RECT()
        if not self._user.GetCursorPos(ctypes.byref(point)) or not self._user.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        dx, dy = point.x - rect.left, point.y - rect.top
        button = 0x02 if self._user.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON -> VK_RBUTTON
        self.dragging = True
        def run():
            moved = False
            try:
                deadline = time.monotonic() + 600
                last = (rect.left, rect.top)
                while (not self.stop_event.is_set() and time.monotonic() < deadline
                       and self._user.GetAsyncKeyState(button) & 0x8000):
                    if self._user.GetCursorPos(ctypes.byref(point)):
                        spot = (point.x - dx, point.y - dy)
                        if spot != last:
                            # NOSIZE | NOZORDER | NOACTIVATE | ASYNCWINDOWPOS
                            self._user.SetWindowPos(hwnd, None, spot[0], spot[1], 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x4000)
                            last, moved = spot, True
                    time.sleep(1 / 60)
                if moved and self._user.GetWindowRect(hwnd, ctypes.byref(rect)):
                    self._save_position(rect.left, rect.top)
            except OSError:
                pass
            finally:
                self.dragging = False
                if moved:
                    self.anchor()   # keep it inside a work area
        threading.Thread(target=run, name='pet-drag', daemon=True).start()

    def start(self):
        def prepare():
            # Extend the DWM composition surface behind WebView2. A layered
            # color key makes the GPU child visible but also makes it click-through.
            from System.Drawing import Color, Icon
            self.window.native.Icon = Icon(str(ICON_PATH))
            self.window.native.ShowInTaskbar = False
            self.window.native.BackColor = Color.Black
            # DPI/work-area events re-anchor and push avail immediately; the
            # periodic path remains a fallback for disconnected monitors.
            from Microsoft.Win32 import SystemEvents
            def changed(sender, event):
                def refresh():
                    try:
                        if self.ready.is_set() and not self.stop_event.is_set():
                            self.anchor()
                            self.region(self.rects)
                    except Exception:
                        pass
                # evaluate_js waits for WebView callbacks, so it must never
                # run on the native UI thread that delivers DpiChanged.
                threading.Thread(target=refresh,name='pet-display',daemon=True).start()
            self._display_changed = changed
            self.window.native.DpiChanged += changed
            SystemEvents.DisplaySettingsChanged += changed
            class Blur(ctypes.Structure):
                _fields_ = [('flags', W.DWORD), ('enable', W.BOOL), ('region', W.HRGN), ('transition', W.BOOL)]
            dwm = ctypes.WinDLL('dwmapi')
            dwm.DwmEnableBlurBehindWindow.argtypes = [W.HWND, ctypes.POINTER(Blur)]
            dwm.DwmExtendFrameIntoClientArea.argtypes = [W.HWND, ctypes.c_void_p]
            region = self._gdi.CreateRectRgn(0, 0, -1, -1)
            try:
                blur = Blur(3, True, region, False)
                if dwm.DwmEnableBlurBehindWindow(self.hwnd, ctypes.byref(blur)) < 0:
                    raise RuntimeError('DWM composition unavailable')
            finally:
                self._gdi.DeleteObject(region)
            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            if dwm.DwmExtendFrameIntoClientArea(self.hwnd, margins) < 0:
                raise RuntimeError('DWM composition unavailable')
            if getattr(self, '_on_ui_prepare', None) is not None:
                self._on_ui_prepare()
        self._ui(prepare)
        self.activate(False)
        self.anchor(raise_=True)
        self.region(self.rects)
        self.ready.set()
        try:
            self.presence()
        except OSError as exc:
            self._report_error('start-presence', exc)
        def run():
            ticks = 0
            while not self.stop_event.wait(1):
                ticks += 1
                if ticks % 2 == 0:
                    try:
                        self.presence()
                    except Exception as exc:
                        self._report_error('presence', exc)
                if ticks % 5 == 0:
                    try:
                        self.anchor()
                        self.region(self.rects)
                    except Exception as exc:
                        self._report_error('anchor', exc)
        threading.Thread(target=run, name='pet-presence', daemon=True).start()
        threading.Thread(target=self._track_pointer, name='pet-pointer', daemon=True).start()

    def _inside(self, rects):
        """Is the cursor over the drawn (clickable) region? Logical px."""
        point, rect = W.POINT(), W.RECT()
        if not self._user.GetCursorPos(ctypes.byref(point)) or not self._user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return None
        scale = self._user.GetDpiForWindow(self.hwnd) / 96 or 1
        x, y = (point.x - rect.left) / scale, (point.y - rect.top) / scale
        return any(r['x'] <= x < r['x'] + r['w'] and r['y'] <= y < r['y'] + r['h'] for r in rects)

    def _track_pointer(self):
        # WebView2 gets no pointerleave when the cursor leaves the window
        # region (SetWindowRgn) quickly, so the hover buttons would stay up
        # (found in Kiro's real-window acceptance). Report the transition, and
        # re-verify on region changes so a fast graze cannot leave hover stuck.
        inside = False
        seen_seq = getattr(self, '_region_seq', 0)
        while not self.stop_event.wait(.05):
            try:
                if not self.visible:
                    inside = False
                    seen_seq = self._region_seq
                    continue
                seq = getattr(self, '_region_seq', 0)
                now = self._inside(list(self.rects))
                if now is not None and (now != inside or (not now and seq != seen_seq)):
                    if self._eval_js('window.MusePet && MusePet.pointer(%s)' % ('true' if now else 'false')):
                        inside = now
                        seen_seq = seq
                elif now is not None:
                    seen_seq = seq
            except Exception as exc:
                self._report_error('pointer', exc)

    def stop(self):
        self.stop_event.set()
        try:
            from Microsoft.Win32 import SystemEvents
            disp = getattr(self, '_display_changed', None)
            if disp is not None:
                SystemEvents.DisplaySettingsChanged -= disp
                try:
                    self.window.native.DpiChanged -= disp
                except Exception:
                    pass
                self._display_changed = None
        except Exception:
            pass
        self.visible = False
        try:
            self.presence()
        except OSError as exc:
            self._report_error('stop-presence', exc)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--args-file']:
        args = json.loads(Path(args[1]).read_text(encoding='utf-8-sig'))
    parser = argparse.ArgumentParser()
    parser.add_argument('--mailbox', action='store_true')
    parser.add_argument('--thread')
    parser.add_argument('--config', type=Path, default=HERE.parent / 'muselink.json')
    parser.add_argument('--events-dir', type=Path, default=event_directory(),
                        help='Directory containing this instance\'s executor-events.jsonl')
    options = parser.parse_args(args)
    if os.name != 'nt':
        raise RuntimeError('WebView2 requires Windows')
    config = read_json(options.config)
    pet = config.get('pet')
    if not isinstance(pet, dict):
        pet = {}
    name = pet.get('display_name')
    if not isinstance(name, str) or not name.strip():
        name = 'Muse Pet'
    if ctypes.windll.shell32.IsUserAnAdmin():
        _dialog(t('pet.normal_user', name=name), name)
        return 1
    if not _runtime_available():
        _dialog(t('pet.webview_missing'), name)
        return 1
    events_dir = options.events_dir.resolve()
    # A checkout must not forward its launch to the installed production pet.
    scope = instance_scope()
    print('pet runtime: layout=%s events=%s scope=%s' %
          ('source' if source_layout() else 'installed', events_dir, scope or 'installed'),
          file=sys.stderr, flush=True)
    instance = Instance('pet', scope=scope)
    watch = tray = shell = mailbox = diag = None
    try:
        if not instance.primary:
            instance.forward({'mailbox': options.thread} if options.mailbox else {'show': True})
            return 0
        diag = _DiagLog(_diag_path(scope))
        diag.open()
        diag.install_hooks()
        diag.write('start layout=%s' % ('source' if source_layout() else 'installed'))
        os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--autoplay-policy=no-user-gesture-required'
        import webview
        linger = pet.get('work_linger_s', 45)
        if type(linger) not in (int, float) or not 0 <= linger <= 3600:
            linger = 45
        watch = Watch(HERE.parent, linger, events_dir=events_dir, on_error=diag.write)
        actions = Actions(HERE.parent, pet)
        from pet.mailbox_window import MailboxWindow
        mailbox = MailboxWindow(options.config, HERE.parent / "mailbox", actions=actions)
        actions.open_mailbox = mailbox.open
        mailbox.start()
        api = Api(watch, actions, Channels(HERE.parent, watch, config), pet, mailbox=mailbox)
        window = webview.create_window(name, str(HERE / 'ui/index.html'), js_api=api,
            width=WINDOW_WIDTH, height=WINDOW_HEIGHT, frameless=True, transparent=True, on_top=True,
            focus=False, resizable=False, easy_drag=False, shadow=False)
        shell = Shell(window, on_error=diag.write, on_ui_prepare=diag.install_clr_hooks)
        mailbox._on_set_avatar = shell.set_avatar
        api._attach(shell)
        startup_errors = []
        def loaded():
            nonlocal tray
            try:
                shell.start()
                def shutdown():
                    diag.write('tray shutdown requested')
                    mailbox.stop()
                    shell.stop()
                tray = Tray(shell, actions, shutdown, api.config()["data"]["display_name"])
                shell._exit_callback = tray.exit
                window.events.closing += tray.closing
                tray.start()
                diag.write('loaded ok')
            except Exception as exc:
                diag.write('loaded error: %s' % type(exc).__name__)
                startup_errors.append(type(exc).__name__)
                window.destroy()
        window.events.loaded += loaded
        def forwarded(value):
            if "mailbox" in value:
                mailbox.open(value["mailbox"])
            else:
                shell.show()
        instance.listen(forwarded)
        if options.mailbox:
            window.events.loaded += lambda: mailbox.open(options.thread)
        watch.start()
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('MuseLink.DesktopPet')
        except Exception:
            pass
        webview.start(gui='edgechromium', http_server=True, server=static_server_class(HERE / 'ui'), icon=str(ICON_PATH))
        diag.write('webview.start returned startup_errors=%s' % startup_errors)
        if startup_errors:
            raise RuntimeError('Pet window initialization failed: ' + startup_errors[0])
        return 0
    finally:
        if diag:
            diag.write('finally cleanup begin')
        if mailbox:
            mailbox.stop()
        if tray:
            tray.stop()
        if watch:
            watch.stop()
        if shell:
            shell.stop()
        instance.close()
        if diag:
            diag.write('finally cleanup done')
            diag.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        _dialog(t('pet.start_failed', error=type(exc).__name__, name='Muse Pet'))
        sys.exit(1)
