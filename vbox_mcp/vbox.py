"""Thin wrapper over the VirtualBox Main API (vboxapi / XPCOM).

All XPCOM objects are bound to the thread that created the manager, so every call here
must run on the same thread (FastMCP runs sync tools on its event-loop thread).
"""

import contextlib
import time

import vboxapi

_mgr = None


def mgr():
    global _mgr
    if _mgr is None:
        _mgr = vboxapi.VirtualBoxManager(None, None)
    return _mgr


def const():
    return mgr().constants


def vbox():
    return mgr().getVirtualBox()


def enum_name(enum: str, value: int) -> str:
    for name, v in const().all_values(enum).items():
        if v == value:
            return name
    return str(value)


def machines():
    # XPCOM array attributes must be read through getArray.
    return mgr().getArray(vbox(), "machines")


def machine(name_or_id: str):
    try:
        return vbox().findMachine(name_or_id)
    except Exception as e:
        names = ", ".join(m.name for m in machines())
        raise ValueError(f"VM {name_or_id!r} not found. Available: {names}") from e


def state(m) -> str:
    return enum_name("MachineState", m.state)


def wait_progress(progress, timeout_ms: int = -1):
    progress.waitForCompletion(timeout_ms)
    if progress.completed and progress.resultCode != 0:
        info = progress.errorInfo
        raise RuntimeError(info.text if info else f"operation failed (rc={progress.resultCode:#x})")


@contextlib.contextmanager
def session(name_or_id: str):
    """Lock the machine with a shared lock (works while the GUI frontend owns the VM)."""
    m = machine(name_or_id)
    s = mgr().getSessionObject()
    m.lockMachine(s, const().LockType_Shared)
    try:
        yield s
    finally:
        with contextlib.suppress(Exception):
            s.unlockMachine()


@contextlib.contextmanager
def console(name_or_id: str):
    with session(name_or_id) as s:
        c = s.console
        if c is None:
            raise RuntimeError(f"VM {name_or_id!r} is not running (state: {state(machine(name_or_id))})")
        yield c


def launch(name_or_id: str, frontend: str = "gui"):
    m = machine(name_or_id)
    s = mgr().getSessionObject()
    progress = m.launchVMProcess(s, frontend, [])
    try:
        wait_progress(progress)
    finally:
        with contextlib.suppress(Exception):
            s.unlockMachine()


def screen_geometry(c, screen: int = 0) -> tuple[int, int, int, int]:
    """(width, height, x_origin, y_origin) of a guest monitor."""
    w, h, _bpp, xo, yo, _status = c.display.getScreenResolution(screen)
    return w, h, xo, yo


def screenshot_png(c, screen: int = 0) -> tuple[bytes, int, int]:
    w, h, _bpp, _xo, _yo, status = c.display.getScreenResolution(screen)
    status = enum_name("GuestMonitorStatus", status)
    if status != "Enabled" or not w or not h:
        raise RuntimeError(
            f"Guest monitor {screen} is {status} (display off / power saving / still booting). "
            "Wake it with mouse_move or press_keys('shift'), then retry."
        )
    data = c.display.takeScreenShotToArray(screen, w, h, const().BitmapFormat_PNG)
    return bytes(data), w, h


def guest_prop(m, key: str) -> str:
    try:
        return m.getGuestPropertyValue(key) or ""
    except Exception:
        return ""


def pump(seconds: float = 0):
    """Let XPCOM process pending events (needed between long guest-control waits)."""
    end = time.monotonic() + seconds
    while True:
        mgr().waitForEvents(0)
        if time.monotonic() >= end:
            return
        time.sleep(0.05)
