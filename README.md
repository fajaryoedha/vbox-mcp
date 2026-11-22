# vbox-mcp

MCP server that gives an AI agent eyes and hands inside any VirtualBox guest: screenshots,
absolute mouse, keyboard (scancodes, so it works on any OS, including boot screens),
power control, guest info, and command execution through Guest Additions.

Built on the VirtualBox Main API via the `vboxapi` Python binding that ships with
VirtualBox (XPCOM on Linux). Tested with VirtualBox 7.2.14 and a Windows 11 guest.

## Install

```bash
uv venv --python /usr/bin/python3 --system-site-packages .venv   # system site-packages = vboxapi
uv pip install --python .venv/bin/python -e .
```

## Register with Claude Code

```bash
claude mcp add --scope user vbox -- /path/to/vbox-mcp/.venv/bin/python -m vbox_mcp.server
```

Any other MCP host: run `.venv/bin/python -m vbox_mcp.server` over stdio.
Optional env for `guest_exec`: `VBOX_GUEST_USER`, `VBOX_GUEST_PASSWORD`.

## Tools

| Area | Tools |
|---|---|
| VM | `list_vms`, `vm_info`, `start_vm` (gui/headless/separate), `stop_vm` (acpi/save/poweroff), `pause_vm`, `resume_vm`, `reset_vm` |
| Screen | `screenshot` (downscaled PNG + coordinate factor, optional `region` crop to zoom) |
| Mouse | `mouse_move`, `mouse_click` (left/right/middle, double), `mouse_drag`, `mouse_scroll` |
| Keyboard | `type_text` (US layout ASCII), `press_keys` (`"win+r"`, `"ctrl+a delete"`, `"alt+f4"`), `send_ctrl_alt_del`, `release_all_keys` |
| Guest | `guest_exec` (run a program, get exit code/stdout/stderr; needs guest credentials) |
| Misc | `wait` |

All coordinates are guest screen pixels with (0, 0) at the top-left of the monitor.

## Notes

- All VirtualBox calls run on one dedicated thread: XPCOM objects are thread-bound and
  input events from different tool calls must not interleave.
- `mcp` is pinned to 1.x: the 2.x server speaks the 2026-07-28 protocol and Claude Code's
  handshake failed against it (`claude mcp get` reported -32022).
- Precise absolute mouse needs Guest Additions (or a USB tablet pointing device).
- `type_text` sends US-layout scancodes; the guest keyboard layout should be US.
- On Windows, `guest_exec` processes run non-interactively (not on the user's desktop);
  use mouse/keyboard for GUI work.

## Manual testing

```bash
.venv/bin/python scripts/call.py                                    # list tools
.venv/bin/python scripts/call.py vm_info '{"vm":"My VM"}'
.venv/bin/python scripts/call.py --out shots screenshot '{"vm":"My VM"}'
```

## License

MIT, see [LICENSE](LICENSE).
