"""Install-scoped paths, atomic files and cross-process mutexes."""
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def tool_dir(path=None):
    return Path(path or Path(__file__).resolve().parents[1]).resolve()


def agent_id(agent):
    if not isinstance(agent, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", agent):
        raise ValueError("invalid agent id")
    return agent


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def mutex(base, lane="complete", timeout_ms=10000):
    """The complete name matches Complete-MuseWait/popup-stage byte for byte."""
    if os.name != "nt":
        raise OSError("popup/state mutexes require Windows")
    key = hashlib.sha256(str(tool_dir(base)).lower().encode("utf-8")).hexdigest().upper()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "muselink-" + lane + "-" + key)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    held = False
    try:
        held = kernel.WaitForSingleObject(handle, timeout_ms) in (0, 0x80)
        if not held:
            raise TimeoutError("mutex busy")
        yield
    finally:
        if held:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
