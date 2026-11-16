"""Ordinary-user WebView2 shell; authenticated local pipe, no data port."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from i18n import t

from mailbox.shell import static_server_class

HERE = Path(__file__).resolve().parent


def _dialog(text):
    ctypes.windll.user32.MessageBoxW(None, text, t('mailbox.title'), 0x10)


def _runtime_available():
    import winreg
    client = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, client, 0, winreg.KEY_READ | view) as key:
                    if winreg.QueryValueEx(key, "pv")[0] not in ("", "0.0.0.0"):
                        return True
            except OSError:
                pass
    return False


# The mailbox is a fixed-size card, not a browser-sized page: frameless,
# rounded by DWM, dragged by its top strip (ui/ .pywebview-drag-region).
CARD_SIZE = (820, 580)


def _geometry(directory=HERE):
    result = {"width": CARD_SIZE[0], "height": CARD_SIZE[1]}
    try:
        data = json.loads((Path(directory) / "window.json").read_text(encoding="utf-8"))
        x, y = int(data["x"]), int(data["y"])
        # A removed monitor must not leave the window unreachable.
        u = ctypes.windll.user32
        left, top = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        right, bottom = left + u.GetSystemMetrics(78), top + u.GetSystemMetrics(79)
        result["x"] = max(left, min(right - 100, x))
        result["y"] = max(top, min(bottom - 100, y))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


def _round_corners(window):
    """Windows 11 rounds frameless windows only on request (no-op on 10)."""
    try:
        hwnd = wintypes.HWND(window.native.Handle.ToInt64())
        preference = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
    except Exception:
        pass


def _static_server_class():
    return static_server_class(HERE / "ui")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--args-file"]:
        args = json.loads(Path(args[1]).read_text(encoding="utf-8-sig"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread")
    parser.add_argument("--config")
    parser.add_argument("--root")
    parser.add_argument("--index")
    options = parser.parse_args(args)
    # Compatibility entrance only. The mailbox no longer owns a process,
    # singleton or tray; an installed/source pet owns its second window.
    from pet.app import main as pet_main
    forwarded = ['--mailbox']
    if options.thread is not None:
        forwarded += ['--thread', options.thread]
    if options.config:
        forwarded += ['--config', options.config]
    if options.root or options.index:
        raise ValueError('Use an isolated config for root/index overrides')
    return pet_main(forwarded)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _dialog(t('mailbox.start_failed', error=type(exc).__name__))
        sys.exit(1)
