"""MCP server exposing VirtualBox guest control: screen, mouse, keyboard, power, guest exec.

Coordinates are guest screen pixels, (0, 0) = top-left of the chosen monitor, i.e. the
pixel grid of a full-resolution screenshot. Input goes through the virtual hardware, so it
works for any guest OS; precise absolute mouse positioning needs Guest Additions (or a
guest with a USB tablet device).
"""

import asyncio
import concurrent.futures
import functools
import io
import os
import time

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

from . import keys, vbox

# XPCOM objects are bound to the thread that created them, and input events must not
# interleave between tool calls, so every VirtualBox call runs on this one thread.
_VBOX_THREAD = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vbox")


def tool(fn):
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_VBOX_THREAD, functools.partial(fn, *args, **kwargs))

    mcp.tool()(run)
    return fn


mcp = FastMCP(
    "vbox",
    instructions=(
        "Control VirtualBox guests. Typical loop: screenshot -> decide -> mouse_click / "
        "type_text / press_keys -> screenshot to verify. All x/y values are guest screen "
        "pixels; screenshots report the factor to convert image pixels to guest pixels."
    ),
)

BUTTONS = {"left": 0x01, "right": 0x02, "middle": 0x04}
STEP_DELAY = 0.03


def _button(name: str) -> int:
    try:
        return BUTTONS[name.lower()]
    except KeyError:
        raise ValueError(f"button must be one of {list(BUTTONS)}") from None


def _abs(c, screen: int, x: int, y: int, dz: int = 0, dw: int = 0, buttons: int = 0):
    w, h, xo, yo = vbox.screen_geometry(c, screen)
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError(f"({x}, {y}) is outside screen {screen} ({w}x{h})")
    # API is 1-based and spans all monitors.
    c.mouse.putMouseEventAbsolute(x + xo + 1, y + yo + 1, dz, dw, buttons)


# ---------------------------------------------------------------- VM management

@tool
def list_vms() -> list[dict]:
    """List all VirtualBox VMs with their state and OS type."""
    return [
        {"name": m.name, "id": m.id, "state": vbox.state(m), "os_type": m.OSTypeId}
        for m in vbox.machines()
    ]


@tool
def vm_info(vm: str) -> dict:
    """Detailed VM status: power state, hardware, guest OS info reported by Guest Additions,
    logged-in users, IP addresses, and (when running) screen sizes and mouse capabilities."""
    m = vbox.machine(vm)
    info = {
        "name": m.name,
        "state": vbox.state(m),
        "os_type": m.OSTypeId,
        "memory_mb": m.memorySize,
        "cpus": m.CPUCount,
        "guest_additions": vbox.guest_prop(m, "/VirtualBox/GuestAdd/Version"),
        "guest_os": " ".join(filter(None, [
            vbox.guest_prop(m, "/VirtualBox/GuestInfo/OS/Product"),
            vbox.guest_prop(m, "/VirtualBox/GuestInfo/OS/Release"),
        ])),
        "logged_in_users": vbox.guest_prop(m, "/VirtualBox/GuestInfo/OS/LoggedInUsersList"),
    }
    try:
        n = int(vbox.guest_prop(m, "/VirtualBox/GuestInfo/Net/Count") or 0)
    except ValueError:
        n = 0
    info["ipv4"] = [ip for i in range(n) if (ip := vbox.guest_prop(m, f"/VirtualBox/GuestInfo/Net/{i}/V4/IP"))]
    if info["state"] in ("Running", "Paused"):
        with vbox.console(vm) as c:
            screens = []
            for i in range(m.graphicsAdapter.monitorCount):
                w, h, xo, yo = vbox.screen_geometry(c, i)
                screens.append({"screen": i, "width": w, "height": h, "x_origin": xo, "y_origin": yo})
            info["screens"] = screens
            info["mouse"] = {
                "absolute_supported": bool(c.mouse.absoluteSupported),
                "relative_supported": bool(c.mouse.relativeSupported),
            }
            info["additions_run_level"] = vbox.enum_name("AdditionsRunLevelType", c.guest.additionsRunLevel)
    return info


@tool
def start_vm(vm: str, mode: str = "gui") -> str:
    """Power on a VM. mode: 'gui' (visible window), 'headless' (no window), or
    'separate' (GUI in a separate process). Returns immediately after power-on; the guest
    OS still needs time to boot — poll with screenshot."""
    if mode not in ("gui", "headless", "separate"):
        raise ValueError("mode must be 'gui', 'headless' or 'separate'")
    m = vbox.machine(vm)
    if vbox.state(m) in ("Running", "Paused"):
        return f"{m.name} is already {vbox.state(m)}"
    vbox.launch(vm, mode)
    return f"{m.name} started ({mode}); state: {vbox.state(vbox.machine(vm))}"


@tool
def stop_vm(vm: str, method: str = "acpi") -> str:
    """Stop a running VM. method: 'acpi' (press the power button; guest shuts down cleanly),
    'save' (save machine state to disk), or 'poweroff' (pull the plug — may lose data)."""
    with vbox.session(vm) as s:
        c = s.console
        if c is None:
            return f"{vm} is not running"
        if method == "acpi":
            c.powerButton()
            return "ACPI power button pressed; guest is shutting down"
        if method == "save":
            vbox.wait_progress(s.machine.saveState())
            return "machine state saved"
        if method == "poweroff":
            vbox.wait_progress(c.powerDown())
            return "VM powered off"
    raise ValueError("method must be 'acpi', 'save' or 'poweroff'")


@tool
def pause_vm(vm: str) -> str:
    """Pause (freeze) a running VM."""
    with vbox.console(vm) as c:
        c.pause()
    return "paused"


@tool
def resume_vm(vm: str) -> str:
    """Resume a paused VM."""
    with vbox.console(vm) as c:
        c.resume()
    return "resumed"


@tool
def reset_vm(vm: str) -> str:
    """Hard-reset a running VM (like pressing the reset button)."""
    with vbox.console(vm) as c:
        c.reset()
    return "reset"


# ---------------------------------------------------------------- screen

@tool
def screenshot(
    vm: str,
    screen: int = 0,
    max_width: int = 1280,
    region: list[int] | None = None,
) -> list:
    """Capture the guest screen as PNG.

    region: optional [x, y, width, height] in guest pixels to crop (use to zoom in on
    small text). The image is downscaled to max_width; the returned note gives the
    factor to convert image pixel coordinates to guest pixels for mouse tools."""
    with vbox.console(vm) as c:
        png, w, h = vbox.screenshot_png(c, screen)
    img = PILImage.open(io.BytesIO(png))
    ox, oy = 0, 0
    if region:
        if len(region) != 4:
            raise ValueError("region must be [x, y, width, height]")
        ox, oy, rw, rh = region
        img = img.crop((ox, oy, min(ox + rw, w), min(oy + rh, h)))
    scale = 1.0
    if max_width and img.width > max_width:
        scale = img.width / max_width
        img = img.resize((max_width, round(img.height / scale)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    note = (
        f"Guest screen {screen}: {w}x{h}. Image {img.width}x{img.height}. "
        f"guest_x = {ox} + image_x * {scale:.4f}, guest_y = {oy} + image_y * {scale:.4f}"
    )
    return [Image(data=buf.getvalue(), format="png"), note]


# ---------------------------------------------------------------- mouse

@tool
def mouse_move(vm: str, x: int, y: int, screen: int = 0) -> str:
    """Move the mouse pointer to (x, y) without clicking."""
    with vbox.console(vm) as c:
        _abs(c, screen, x, y)
    return f"moved to ({x}, {y})"


@tool
def mouse_click(
    vm: str, x: int, y: int, button: str = "left", clicks: int = 1, screen: int = 0
) -> str:
    """Click at (x, y). button: left/right/middle. clicks: 2 for double-click."""
    b = _button(button)
    with vbox.console(vm) as c:
        _abs(c, screen, x, y)
        time.sleep(STEP_DELAY)
        for _ in range(clicks):
            _abs(c, screen, x, y, buttons=b)
            time.sleep(STEP_DELAY)
            _abs(c, screen, x, y)
            time.sleep(STEP_DELAY)
    return f"{button} x{clicks} at ({x}, {y})"


@tool
def mouse_drag(
    vm: str, x1: int, y1: int, x2: int, y2: int, button: str = "left",
    steps: int = 25, screen: int = 0,
) -> str:
    """Press at (x1, y1), move smoothly to (x2, y2), release. Use for moving/resizing
    windows, selecting text, drag-and-drop."""
    b = _button(button)
    steps = max(1, steps)
    with vbox.console(vm) as c:
        _abs(c, screen, x1, y1)
        time.sleep(STEP_DELAY)
        _abs(c, screen, x1, y1, buttons=b)
        time.sleep(0.1)
        for i in range(1, steps + 1):
            _abs(c, screen, round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps), buttons=b)
            time.sleep(STEP_DELAY)
        time.sleep(0.1)
        _abs(c, screen, x2, y2)
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"


@tool
def mouse_scroll(vm: str, x: int, y: int, down: int = 3, right: int = 0, screen: int = 0) -> str:
    """Scroll the wheel with the pointer at (x, y). down > 0 scrolls down, < 0 up;
    right > 0 scrolls right, < 0 left. Units are wheel notches."""
    with vbox.console(vm) as c:
        _abs(c, screen, x, y)
        time.sleep(STEP_DELAY)
        _abs(c, screen, x, y, dz=down, dw=-right)
    return f"scrolled down={down} right={right} at ({x}, {y})"


# ---------------------------------------------------------------- keyboard

@tool
def type_text(vm: str, text: str, delay_ms: int = 15) -> str:
    """Type text as keystrokes (US layout; ASCII printable chars plus \\n and \\t).
    For shortcuts or special keys use press_keys."""
    groups = keys.text_codes(text)
    with vbox.console(vm) as c:
        for codes in groups:
            c.keyboard.putScancodes(codes)
            time.sleep(delay_ms / 1000)
    return f"typed {len(text)} characters"


@tool
def press_keys(vm: str, keys_spec: str, repeat: int = 1, delay_ms: int = 80) -> str:
    """Press keys or key combos. Combos join keys with '+', several steps are separated by
    spaces, e.g. 'win+r', 'ctrl+shift+esc', 'alt+f4', 'ctrl+a delete', 'down down enter'.
    Key names: letters, digits, f1-f12, enter, esc, tab, backspace, space, delete, insert,
    home, end, pageup, pagedown, up/down/left/right, ctrl/alt/shift/win (and r-variants),
    menu, printscreen, punctuation characters, kp0-kp9."""
    steps = [keys.combo(s) for s in keys_spec.split()]
    with vbox.console(vm) as c:
        for _ in range(max(1, repeat)):
            for codes in steps:
                c.keyboard.putScancodes(codes)
                time.sleep(delay_ms / 1000)
    return f"pressed {keys_spec!r} x{repeat}"


@tool
def send_ctrl_alt_del(vm: str) -> str:
    """Send Ctrl+Alt+Del to the guest (cannot be done via press_keys on most hosts)."""
    with vbox.console(vm) as c:
        c.keyboard.putCAD()
    return "Ctrl+Alt+Del sent"


@tool
def release_all_keys(vm: str) -> str:
    """Release any keys stuck down in the guest."""
    with vbox.console(vm) as c:
        c.keyboard.releaseKeys()
    return "all keys released"


# ---------------------------------------------------------------- guest control

@tool
def guest_exec(
    vm: str,
    executable: str,
    args: list[str] | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout_s: int = 60,
) -> dict:
    """Run a program inside the guest via Guest Additions guest control and return its exit
    code, stdout and stderr. Needs a guest account: user/password, or env vars
    VBOX_GUEST_USER / VBOX_GUEST_PASSWORD on the MCP server. Use a full path for the
    executable (e.g. C:\\Windows\\System32\\cmd.exe, /bin/sh). Note: on Windows the
    process runs non-interactively and its windows are not shown on the user's desktop."""
    user = user or os.environ.get("VBOX_GUEST_USER")
    password = password if password is not None else os.environ.get("VBOX_GUEST_PASSWORD", "")
    if not user:
        raise ValueError("guest_exec needs a guest user (argument or VBOX_GUEST_USER)")
    k = vbox.const()
    out, err = bytearray(), bytearray()
    with vbox.console(vm) as c:
        gs = c.guest.createSession(user, password, "", "vbox-mcp")
        try:
            gs.waitForArray([k.GuestSessionWaitForFlag_Start], 30_000)
            proc = gs.processCreate(
                executable, [executable] + list(args or []), "", [],
                [k.ProcessCreateFlag_WaitForStdOut, k.ProcessCreateFlag_WaitForStdErr],
                timeout_s * 1000,
            )
            proc.waitForArray([k.ProcessWaitForFlag_Start], 30_000)
            deadline = time.monotonic() + timeout_s + 5
            terminal = {k.ProcessStatus_TerminatedNormally, k.ProcessStatus_TerminatedSignal,
                        k.ProcessStatus_TerminatedAbnormally, k.ProcessStatus_TimedOutKilled,
                        k.ProcessStatus_TimedOutAbnormally, k.ProcessStatus_Down, k.ProcessStatus_Error}
            while True:
                proc.waitForArray([k.ProcessWaitForFlag_Terminate, k.ProcessWaitForFlag_StdOut,
                                   k.ProcessWaitForFlag_StdErr], 500)
                for handle, buf in ((1, out), (2, err)):
                    data = proc.read(handle, 64 * 1024, 0)
                    if data:
                        buf += bytes(data)
                vbox.pump()
                if proc.status in terminal or time.monotonic() > deadline:
                    break
            status = vbox.enum_name("ProcessStatus", proc.status)
            exit_code = proc.exitCode if proc.status in terminal else None
        finally:
            gs.close()
    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": out.decode("utf-8", "replace"),
        "stderr": err.decode("utf-8", "replace"),
    }


@tool
def wait(seconds: float) -> str:
    """Sleep for a number of seconds (e.g. while the guest boots or an app opens)."""
    time.sleep(max(0.0, min(seconds, 300)))
    return f"waited {seconds}s"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
