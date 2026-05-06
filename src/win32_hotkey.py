"""Windows global hotkey registration (ctypes RegisterHotKey + message pump)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from queue import Queue


def win32_hotkey_thread_main(
    *,
    keyboard_shortcut_queue: Queue[bool],
    control_queue: Queue[str],
    registered_event: threading.Event,
) -> None:
    """Register Ctrl+Shift+K (or Ctrl+Alt+K), pump WM_HOTKEY, unblock with WM_QUIT on shutdown."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]

    WM_HOTKEY = 0x0312
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    VK_K = 0x4B
    hotkey_id = 0x4D42

    primary_mod = MOD_CONTROL | MOD_SHIFT
    attempts = (
        (primary_mod, None),
        (MOD_CONTROL | MOD_ALT, "hint_ctrl_alt_k"),
    )
    registered = False
    for mod_flags, hint_cmd in attempts:
        if user32.RegisterHotKey(None, hotkey_id, mod_flags, VK_K):
            registered = True
            if hint_cmd:
                control_queue.put(hint_cmd)
            break

    if not registered:
        control_queue.put("fallback_pynput")
        return

    registered_event.set()
    msg = MSG()
    while True:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0:
            break
        if r == -1:
            break
        if msg.message == WM_HOTKEY:
            keyboard_shortcut_queue.put(True)
        else:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnregisterHotKey(None, hotkey_id)
