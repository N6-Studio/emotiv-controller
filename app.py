import json
import os
import ssl
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from queue import Queue, Empty
from typing import Callable, Optional

import websocket
from dotenv import load_dotenv
from pynput import keyboard as pynput_keyboard
import tkinter as tk
from tkinter import ttk, messagebox

from core import (
    COM_MAPPED_MENTAL_ACTIONS,
    compute_motion_movements,
    mental_command_to_sets,
)
from update_service import (
    apply_staged_update,
    check_update_available,
    download_and_verify,
    get_app_version,
    get_update_manifest_url,
)


load_dotenv()

CONFIG_PATH = Path("config.json")

CORTEX_URL = os.getenv("CORTEX_URL", "wss://localhost:6868")
STREAMS = [s.strip() for s in os.getenv("STREAMS", "mot").split(",") if s.strip()]

CLIENT_ID = os.getenv("EMOTIV_CLIENT_ID")
CLIENT_SECRET = os.getenv("EMOTIV_CLIENT_SECRET")
LICENSE = os.getenv("EMOTIV_LICENSE", "")
DEBIT = int(os.getenv("EMOTIV_DEBIT", "1"))

DEFAULT_THRESHOLD = 5.0
DEFAULT_COM_POWER_THRESHOLD = float(os.getenv("COM_POWER_THRESHOLD", "0.25"))

UI = {
    "bg_outer": "#0a0a0c",
    "bg_panel": "#141418",
    "bg_canvas": "#0a0a0c",
    "text": "#f4f4f5",
    "text_muted": "#a1a1aa",
    "text_dim": "#71717a",
    "accent": "#2dd4bf",
    "accent_strong": "#14b8a6",
    "pad_idle_bg": "#27272a",
    "pad_active_bg": "#0d9488",
    "pad_active_fg": "#ffffff",
    "border": "#3f3f46",
    "btn_neutral_bg": "#3f3f46",
    "btn_neutral_active": "#52525b",
    "btn_primary_bg": "#0d9488",
    "btn_primary_active": "#0f766e",
    "crosshair": "#52525b",
    "dot": "#2dd4bf",
    "error": "#f87171",
}


def ui_font(size: int, bold: bool = False):
    if bold:
        return ("Segoe UI", size, "bold")
    return ("Segoe UI", size)


MOVEMENTS = {
    "forward": {
        "label": "W",
        "ui_name": "Forward",
        "default_key": "w",
    },
    "left": {
        "label": "A",
        "ui_name": "Left",
        "default_key": "a",
    },
    "backward": {
        "label": "S",
        "ui_name": "Backward",
        "default_key": "s",
    },
    "right": {
        "label": "D",
        "ui_name": "Right",
        "default_key": "d",
    },
}


@dataclass
class AppConfig:
    neutral_x: Optional[float] = None
    neutral_y: Optional[float] = None
    threshold: float = DEFAULT_THRESHOLD
    threshold_global: bool = True
    movement_thresholds: dict = field(default_factory=dict)
    keyboard_enabled: bool = False
    com_power_threshold: float = DEFAULT_COM_POWER_THRESHOLD
    key_bindings: dict = None
    com_key_bindings: dict = None

    def __post_init__(self):
        if self.key_bindings is None:
            self.key_bindings = {
                movement: data["default_key"]
                for movement, data in MOVEMENTS.items()
            }
        if self.movement_thresholds is None:
            self.movement_thresholds = {}
        base = float(self.threshold)
        self.movement_thresholds = {
            movement: float(self.movement_thresholds.get(movement, base))
            for movement in MOVEMENTS
        }
        com_defaults = {
            "push": self.key_bindings.get("forward", "w"),
            "pull": self.key_bindings.get("backward", "s"),
            "left": self.key_bindings.get("left", "a"),
            "right": self.key_bindings.get("right", "d"),
        }
        if self.com_key_bindings is None:
            self.com_key_bindings = dict(com_defaults)
        else:
            merged = dict(self.com_key_bindings)
            for cmd in COM_MAPPED_MENTAL_ACTIONS:
                v = merged.get(cmd)
                if not v or not str(v).strip():
                    merged[cmd] = com_defaults[cmd]
                else:
                    merged[cmd] = str(v).strip()
            self.com_key_bindings = merged


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return AppConfig(**raw)
    except Exception:
        return AppConfig()


def save_config(config: AppConfig):
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), indent=2),
        encoding="utf-8",
    )


class SimulatedKeyboard:
    def __init__(self):
        self.controller = pynput_keyboard.Controller()
        self.pressed_movements = set()
        self.pressed_com_actions = set()
        self._key_refcount: dict[str, int] = {}

    def _add_physical_key(self, key: str):
        n = self._key_refcount.get(key, 0) + 1
        self._key_refcount[key] = n
        if n == 1:
            self.controller.press(key)

    def _remove_physical_key(self, key: str):
        n = self._key_refcount.get(key, 0)
        if n <= 0:
            return
        n -= 1
        if n == 0:
            self._key_refcount.pop(key, None)
            self.controller.release(key)
        else:
            self._key_refcount[key] = n

    def press(self, movement: str, key: str):
        if movement in self.pressed_movements:
            return

        self._add_physical_key(key)
        self.pressed_movements.add(movement)

    def release(self, movement: str, key: str):
        if movement not in self.pressed_movements:
            return

        self._remove_physical_key(key)
        self.pressed_movements.remove(movement)

    def press_com(self, action: str, key: str):
        if action in self.pressed_com_actions:
            return
        self._add_physical_key(key)
        self.pressed_com_actions.add(action)

    def release_com(self, action: str, key: str):
        if action not in self.pressed_com_actions:
            return
        self._remove_physical_key(key)
        self.pressed_com_actions.remove(action)

    def sync(
        self,
        motion_movements: set,
        com_actions: set,
        config: AppConfig,
    ):
        if not config.keyboard_enabled:
            self.release_all(config)
            return

        for movement, key in config.key_bindings.items():
            if movement in motion_movements:
                self.press(movement, key)
            else:
                self.release(movement, key)

        for action in COM_MAPPED_MENTAL_ACTIONS:
            key = config.com_key_bindings.get(action)
            if not key:
                continue
            if action in com_actions:
                self.press_com(action, key)
            else:
                self.release_com(action, key)

    def release_all(self, config: AppConfig):
        for movement in list(self.pressed_movements):
            key = config.key_bindings.get(movement)
            if key:
                self.release(movement, key)
        for action in list(self.pressed_com_actions):
            key = config.com_key_bindings.get(action)
            if key:
                self.release_com(action, key)


class CortexClient(threading.Thread):
    def __init__(
        self,
        on_stream: Callable[[dict], None],
        on_status: Callable[[str], None],
        on_error: Callable[[str], None],
    ):
        super().__init__(daemon=True)
        self.on_stream = on_stream
        self.on_status = on_status
        self.on_error = on_error

        self.ws_app = None
        self.ws = None
        self.ws_open = False
        self.next_id = 1
        self.pending = {}
        self.connected_event = threading.Event()
        self.stop_event = threading.Event()

    def run(self):
        if not CLIENT_ID or not CLIENT_SECRET:
            self.on_error("Missing EMOTIV_CLIENT_ID or EMOTIV_CLIENT_SECRET in .env file")
            return

        self.ws_app = websocket.WebSocketApp(
            CORTEX_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_ws_error,
            on_close=self._on_close,
        )

        websocket_thread = threading.Thread(
            target=lambda: self.ws_app.run_forever(
                sslopt={
                    "cert_reqs": ssl.CERT_NONE,
                    "check_hostname": False,
                }
            ),
            daemon=True,
        )
        websocket_thread.start()

        if not self.connected_event.wait(timeout=15):
            self.on_error("Cortex connection timeout")
            return

        try:
            self.initialize_cortex()
        except Exception as exc:
            self.on_error(str(exc))

        while not self.stop_event.is_set():
            time.sleep(0.1)

    def _on_open(self, ws):
        self.ws = ws
        self.ws_open = True
        self.connected_event.set()
        self.on_status("Connected to Cortex")

    def _on_close(self, ws, close_status_code, close_msg):
        was_open = self.ws_open
        self.ws_open = False
        self.ws = None
        if self.stop_event.is_set():
            return
        if was_open:
            self.on_error("Connection lost")
        else:
            self.on_status("Connection closed")

    def is_websocket_connected(self) -> bool:
        return self.ws_open

    def _on_ws_error(self, ws, error):
        self.on_error(f"WebSocket error: {error}")

    def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        msg_id = msg.get("id")
        if msg_id in self.pending:
            item = self.pending.pop(msg_id)
            item["response"] = msg
            item["event"].set()
            return

        self.on_stream(msg)

    def request_v2(self, method: str, params: Optional[dict] = None, timeout: int = 15):
        if params is None:
            params = {}

        request_id = self.next_id
        self.next_id += 1

        event = threading.Event()
        holder = {
            "event": event,
            "response": None,
        }
        self.pending[request_id] = holder

        self.ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }))

        if not event.wait(timeout=timeout):
            self.pending.pop(request_id, None)
            raise TimeoutError(f"Timeout: {method}")

        response = holder["response"]

        if response.get("error"):
            raise RuntimeError(response["error"].get("message", f"Error: {method}"))

        return response.get("result")

    def initialize_cortex(self):
        self.on_status("Requesting access...")

        access = self.request_v2("requestAccess", {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
        })

        if not access.get("accessGranted"):
            raise RuntimeError("Access denied. Approve the app in EMOTIV Launcher.")

        self.on_status("Authorizing...")

        auth = self.request_v2("authorize", {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            "license": LICENSE,
            "debit": DEBIT,
        })

        cortex_token = auth["cortexToken"]

        self.on_status("Searching for headset...")

        headsets = self.request_v2("queryHeadsets")
        headset = None

        for item in headsets:
            if item.get("status") == "connected":
                headset = item
                break

        if headset is None and headsets:
            headset = headsets[0]

        if headset is None:
            raise RuntimeError("No headset found.")

        if headset.get("status") != "connected":
            self.on_status("Connecting headset...")
            self.request_v2("controlDevice", {
                "command": "connect",
                "headset": headset["id"],
            })

        self.on_status("Creating session...")

        session = self.request_v2("createSession", {
            "cortexToken": cortex_token,
            "headset": headset["id"],
            "status": "active",
        })

        self.request_v2("subscribe", {
            "cortexToken": cortex_token,
            "session": session["id"],
            "streams": STREAMS,
        })

        self.on_status(f"Ready · Headset {headset['id']}")

    def stop(self):
        self.stop_event.set()
        if self.ws_app:
            self.ws_app.close()


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("EMOTIV Movement")
        self.minsize(360, 400)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        init_w = max(420, min(560, int(sw * 0.28)))
        init_h = max(460, min(720, int(sh * 0.55)))
        self.geometry(f"{init_w}x{init_h}")

        self.config_data = load_config()
        self.sim_keyboard = SimulatedKeyboard()

        self.stream_queue = Queue()
        self.status_queue = Queue()
        self.error_queue = Queue()
        self.keyboard_shortcut_queue = Queue()
        self._hotkey_control_queue = Queue()
        self._hotkey_thread: Optional[threading.Thread] = None
        self._hotkey_win32_registered = threading.Event()

        self.current_x = 0.0
        self.current_y = 0.0
        self.active_movements = set()

        self.calibration_active = False
        self.calibration_started_at = None
        self.calibration_samples = []
        self.pending_neutral_x = None
        self.pending_neutral_y = None

        self.current_view = None
        self.movement_buttons = {}
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_power_labels = None
        self.com_threshold_hint = None
        self._com_font_targets = None

        self.connection_failed = False
        self.retry_button = None
        self._update_check_in_progress = False

        self.configure(bg=UI["bg_outer"])

        self.canvas = tk.Canvas(
            self,
            bg=UI["bg_canvas"],
            highlightthickness=0,
        )
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.content = tk.Frame(
            self.canvas,
            bg=UI["bg_panel"],
            highlightthickness=1,
            highlightbackground=UI["border"],
            highlightcolor=UI["border"],
        )
        cw0 = init_w
        ch0 = init_h
        self._content_window_id = self.canvas.create_window(
            init_w // 2,
            init_h // 2,
            window=self.content,
            anchor="center",
            width=cw0,
            height=ch0,
        )
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.content.bind("<Configure>", self._on_content_configure)

        self._setup_ttk_styles()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.show_main_view()
        self.start_shortcut_listener()

        self.cortex = CortexClient(
            on_stream=lambda msg: self.stream_queue.put(msg),
            on_status=lambda msg: self.status_queue.put(msg),
            on_error=lambda msg: self.error_queue.put(msg),
        )
        self.cortex.start()

        self.after(30, self.tick)

    def _setup_ttk_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TSpinbox",
            fieldbackground=UI["pad_idle_bg"],
            background=UI["btn_neutral_bg"],
            foreground=UI["text"],
            bordercolor=UI["border"],
            lightcolor=UI["border"],
            darkcolor=UI["border"],
            arrowcolor=UI["text_muted"],
            insertcolor=UI["text"],
        )
        style.map(
            "Dark.TSpinbox",
            fieldbackground=[("readonly", UI["pad_idle_bg"])],
        )

    def _make_button(
        self,
        parent,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        width: Optional[int] = None,
    ) -> tk.Button:
        if primary:
            kw = {
                "bg": UI["btn_primary_bg"],
                "fg": "#fafafa",
                "activebackground": UI["btn_primary_active"],
                "activeforeground": "#fafafa",
            }
        else:
            kw = {
                "bg": UI["btn_neutral_bg"],
                "fg": UI["text"],
                "activebackground": UI["btn_neutral_active"],
                "activeforeground": UI["text"],
            }
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            relief="flat",
            bd=0,
            padx=18,
            pady=9,
            cursor="hand2",
            font=ui_font(10, True),
            highlightthickness=0,
            **kw,
        )
        if width is not None:
            btn.config(width=width)
        return btn

    def _toggle_keyboard_via_shortcut(self):
        self.config_data.keyboard_enabled = not self.config_data.keyboard_enabled
        save_config(self.config_data)
        self.status_queue.put(
            "Simulated keyboard on"
            if self.config_data.keyboard_enabled
            else "Simulated keyboard off"
        )

    def _install_pynput_hotkey(self):
        if self.hotkey_listener is not None:
            return
        cb = lambda: self.after(0, self._toggle_keyboard_via_shortcut)
        self.hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<ctrl>+<shift>+k": cb,
            "<ctrl>+<alt>+k": cb,
        })
        self.hotkey_listener.start()

    def _win32_hotkey_thread_main(self):
        import ctypes
        from ctypes import wintypes

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
                    self._hotkey_control_queue.put(hint_cmd)
                break

        if not registered:
            self._hotkey_control_queue.put("fallback_pynput")
            return

        self._hotkey_win32_registered.set()
        msg = MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:
                break
            if r == -1:
                break
            if msg.message == WM_HOTKEY:
                self.keyboard_shortcut_queue.put(True)
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(None, hotkey_id)

    def start_shortcut_listener(self):
        self.hotkey_listener = None
        self._hotkey_win32_registered.clear()

        if sys.platform != "win32":
            self._install_pynput_hotkey()
            return

        self._hotkey_thread = threading.Thread(
            target=self._win32_hotkey_thread_main,
            name="win32-hotkey",
            daemon=True,
        )
        self._hotkey_thread.start()

    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()
        self.calibration_instruction_label = None
        self.timer_label = None
        self.calibration_xy_label = None
        self.review_xy_label = None
        self.review_neutral_label = None
        self.xy_label = None
        self.status_label = None
        self.error_label = None
        self.retry_button = None
        self.keyboard_label = None
        self.movement_buttons = {}
        self.com_power_labels = None
        self.com_threshold_hint = None
        self._com_font_targets = None

    def retry_connection(self):
        try:
            self.cortex.stop()
        except Exception:
            pass

        self.connection_failed = False
        if self.retry_button is not None:
            try:
                self.retry_button.pack_forget()
            except tk.TclError:
                pass
        if self.error_label is not None:
            try:
                self.error_label.config(text="")
            except tk.TclError:
                pass

        self.cortex = CortexClient(
            on_stream=lambda msg: self.stream_queue.put(msg),
            on_status=lambda msg: self.status_queue.put(msg),
            on_error=lambda msg: self.error_queue.put(msg),
        )
        self.cortex.start()
        self.status_queue.put("Reconnecting...")

    def _on_canvas_configure(self, event):
        if event.widget is not self.canvas:
            return
        w = max(event.width, 1)
        h = max(event.height, 1)
        cw = w
        ch = h
        self.canvas.coords(self._content_window_id, w // 2, h // 2)
        self.canvas.itemconfig(self._content_window_id, width=cw, height=ch)

    def _on_content_configure(self, event):
        if event.widget is not self.content:
            return
        w = self.content.winfo_width()
        h = self.content.winfo_height()
        if w < 24 or h < 24:
            return

        lab = getattr(self, "calibration_instruction_label", None)
        if lab is not None:
            try:
                if lab.winfo_exists():
                    lab.config(wraplength=max(160, w - 48))
            except tk.TclError:
                self.calibration_instruction_label = None

        err = getattr(self, "error_label", None)
        if err is not None and self.current_view == "main":
            try:
                if err.winfo_exists():
                    err.config(wraplength=max(120, w - 64))
            except tk.TclError:
                pass

        scale = min(w / 380.0, h / 420.0)
        scale = max(0.85, min(scale, 2.4))
        pad_w = max(5, int(6 * scale))
        pad_h = max(2, int(3 * scale))
        btn_font = ("Segoe UI", max(11, int(16 * scale)), "bold")
        for btn in self.movement_buttons.values():
            try:
                if btn.winfo_exists():
                    btn.config(width=pad_w, height=pad_h, font=btn_font)
            except tk.TclError:
                pass

        if self.current_view == "calibration" and getattr(self, "timer_label", None):
            try:
                if self.timer_label.winfo_exists():
                    self.timer_label.config(
                        font=("Segoe UI", max(22, int(48 * scale)), "bold"),
                    )
            except tk.TclError:
                pass

        if self.current_view == "main":
            ft = getattr(self, "_com_font_targets", None)
            if ft:
                try:
                    tf = ("Segoe UI", max(9, int(11 * scale)), "bold")
                    nf = ("Segoe UI", max(9, int(10 * scale)))
                    vf = ("Segoe UI", max(9, int(10 * scale)), "bold")
                    hf = ("Segoe UI", max(8, int(9 * scale)))
                    if ft["title"].winfo_exists():
                        ft["title"].config(font=tf)
                    for lbl in ft["names"]:
                        if lbl.winfo_exists():
                            lbl.config(font=nf)
                    for lbl in ft["values"]:
                        if lbl.winfo_exists():
                            lbl.config(font=vf)
                    if ft["hint"].winfo_exists():
                        ft["hint"].config(font=hf)
                except tk.TclError:
                    pass

    def show_main_view(self):
        self.current_view = "main"
        self.calibration_active = False
        self.clear_content()
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}

        error_bar = tk.Frame(self.content, bg=UI["bg_panel"])
        error_bar.pack(side="bottom", fill="x", padx=16, pady=(4, 12))

        self.error_label = tk.Label(
            error_bar,
            text="",
            fg=UI["error"],
            bg=UI["bg_panel"],
            font=ui_font(10),
            justify="center",
            wraplength=320,
        )
        self.error_label.pack(anchor="center")

        self.retry_button = self._make_button(
            error_bar,
            "Retry connection",
            self.retry_connection,
            primary=True,
            width=18,
        )
        if self.connection_failed:
            self.retry_button.pack(anchor="center", pady=(6, 0))

        top_bar = tk.Frame(self.content, bg=UI["bg_panel"])
        top_bar.pack(side="top", fill="x", padx=12, pady=(12, 10))

        info_col = tk.Frame(top_bar, bg=UI["bg_panel"])
        info_col.pack(side="left", fill="x", expand=True)

        self.status_label = tk.Label(
            info_col,
            text="Connecting...",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        )
        self.status_label.pack(anchor="w")

        self.xy_label = tk.Label(
            info_col,
            text="x=0 · y=0",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        )
        self.xy_label.pack(anchor="w", pady=(4, 0))

        btn_bar = tk.Frame(top_bar, bg=UI["bg_panel"])
        btn_bar.pack(side="right", anchor="n")

        self._make_button(
            btn_bar,
            "Calibrate",
            self.show_calibration_view,
            primary=True,
            width=14,
        ).pack(side="left", padx=(0, 8))

        self._make_button(
            btn_bar,
            "Settings",
            self.show_settings_view,
            width=14,
        ).pack(side="left")

        self._make_button(
            btn_bar,
            "Check for updates",
            self._on_check_for_updates,
            width=18,
        ).pack(side="left", padx=(8, 0))

        main_body = tk.Frame(self.content, bg=UI["bg_panel"])
        main_body.pack(side="top", fill="both", expand=True)

        keyboard_row = tk.Frame(main_body, bg=UI["bg_panel"])
        keyboard_row.pack(side="bottom", fill="x")

        self.keyboard_label = tk.Label(
            keyboard_row,
            text="",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        )
        self.keyboard_label.pack(pady=(4, 10))

        center_fill = tk.Frame(main_body, bg=UI["bg_panel"])
        center_fill.pack(side="top", fill="both", expand=True)

        center_row = tk.Frame(center_fill, bg=UI["bg_panel"])
        center_row.place(relx=0.5, rely=0.5, anchor="center")

        pad_host = tk.Frame(center_row, bg=UI["bg_panel"])
        pad_host.pack(side="left")

        self.create_movement_pad(pad_host)
        self._create_com_power_panel(center_row)

    def _create_com_power_panel(self, parent):
        com_wrap = tk.Frame(
            parent,
            bg=UI["bg_panel"],
            highlightthickness=1,
            highlightbackground=UI["border"],
            highlightcolor=UI["border"],
        )
        com_wrap.pack(side="left", padx=(20, 0))

        title = tk.Label(
            com_wrap,
            text="COM power",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(11, True),
        )
        title.pack(anchor="w", padx=10, pady=(10, 6))

        inner = tk.Frame(com_wrap, bg=UI["bg_panel"])
        inner.pack(fill="x", padx=4, pady=(0, 2))

        self.com_power_labels = {}
        name_labels = []
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            row = tk.Frame(inner, bg=UI["bg_panel"])
            row.pack(fill="x")
            nl = tk.Label(
                row,
                text=cmd,
                fg=UI["text_dim"],
                bg=UI["bg_panel"],
                font=ui_font(10),
                width=8,
                anchor="w",
            )
            nl.pack(side="left", padx=(8, 4))
            name_labels.append(nl)
            vl = tk.Label(
                row,
                text="0.00",
                fg=UI["text"],
                bg=UI["bg_panel"],
                font=ui_font(10, True),
                width=5,
                anchor="e",
            )
            vl.pack(side="right", padx=(4, 10))
            self.com_power_labels[cmd] = vl

        self.com_threshold_hint = tk.Label(
            com_wrap,
            text="",
            fg=UI["text_dim"],
            bg=UI["bg_panel"],
            font=ui_font(8),
        )
        self.com_threshold_hint.pack(anchor="w", padx=10, pady=(2, 10))

        self._com_font_targets = {
            "title": title,
            "names": name_labels,
            "values": list(self.com_power_labels.values()),
            "hint": self.com_threshold_hint,
        }

    def show_calibration_view(self):
        if not self.cortex.is_websocket_connected():
            messagebox.showwarning(
                "Cortex",
                "Not connected to the Cortex WebSocket. "
                "Start EMOTIV Launcher and ensure Cortex is reachable, then try again.",
            )
            return

        self.current_view = "calibration"
        self.clear_content()

        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []

        tk.Label(
            self.content,
            text="Calibration",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(20, True),
        ).pack(pady=(28, 10))

        self.calibration_instruction_label = tk.Label(
            self.content,
            text="Hold a neutral head position for 10 seconds.",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(11),
            wraplength=max(160, self.content.winfo_width() - 48),
        )
        self.calibration_instruction_label.pack(pady=(0, 20))

        self.timer_label = tk.Label(
            self.content,
            text="10",
            fg=UI["accent"],
            bg=UI["bg_panel"],
            font=("Segoe UI", 48, "bold"),
        )
        self.timer_label.pack(pady=(0, 14))

        self.calibration_xy_label = tk.Label(
            self.content,
            text="avg x=0 · avg y=0",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        )
        self.calibration_xy_label.pack(pady=(0, 24))

        self._make_button(
            self.content,
            "Cancel",
            self.show_main_view,
            width=16,
        ).pack()

    def show_calibration_review_view(self):
        self.current_view = "calibration_review"
        self.calibration_active = False
        self.clear_content()

        tk.Label(
            self.content,
            text="Verify configuration",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(19, True),
        ).pack(pady=(22, 8))

        self.review_xy_label = tk.Label(
            self.content,
            text="x=0 · y=0",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        )
        self.review_xy_label.pack(pady=(0, 6))

        self.review_neutral_label = tk.Label(
            self.content,
            text=f"Neutral x={self.pending_neutral_x:.2f} · y={self.pending_neutral_y:.2f}",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        )
        self.review_neutral_label.pack(pady=(0, 10))

        self.create_movement_pad(self.content)

        button_row = tk.Frame(self.content, bg=UI["bg_panel"])
        button_row.pack(pady=18)

        self._make_button(
            button_row,
            "Cancel",
            self.show_main_view,
            width=10,
        ).grid(row=0, column=0, padx=5)

        self._make_button(
            button_row,
            "Save",
            self.save_calibration,
            primary=True,
            width=10,
        ).grid(row=0, column=1, padx=5)

        self._make_button(
            button_row,
            "Retry",
            self.show_calibration_view,
            width=10,
        ).grid(row=0, column=2, padx=5)

    def show_settings_view(self):
        self.current_view = "settings"
        self.clear_content()

        tk.Label(
            self.content,
            text="Settings",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(20, True),
        ).pack(pady=(24, 16))

        form = tk.Frame(self.content, bg=UI["bg_panel"])
        form.pack(pady=4, padx=12, fill="x")
        form.columnconfigure(0, weight=1)

        keyboard_var = tk.BooleanVar(value=self.config_data.keyboard_enabled)

        keyboard_check = tk.Checkbutton(
            form,
            text="Enable simulated keyboard",
            variable=keyboard_var,
            fg=UI["text"],
            bg=UI["bg_panel"],
            selectcolor=UI["pad_idle_bg"],
            activebackground=UI["bg_panel"],
            activeforeground=UI["text"],
            font=ui_font(11),
        )
        keyboard_check.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        tk.Label(
            form,
            text="Shortcut: Ctrl + Shift + K · or Ctrl + Alt + K if the first is in use",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 16))

        threshold_global_var = tk.BooleanVar(value=self.config_data.threshold_global)

        global_threshold_cb = tk.Checkbutton(
            form,
            text="Single threshold for all movements",
            variable=threshold_global_var,
            fg=UI["text"],
            bg=UI["bg_panel"],
            selectcolor=UI["pad_idle_bg"],
            activebackground=UI["bg_panel"],
            activeforeground=UI["text"],
            font=ui_font(11),
        )
        global_threshold_cb.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))

        threshold_inner = tk.Frame(form, bg=UI["bg_panel"])
        threshold_inner.grid(row=3, column=0, columnspan=2, sticky="ew", padx=0, pady=4)

        threshold_var = tk.DoubleVar(value=self.config_data.threshold)

        global_thr_row = tk.Frame(threshold_inner, bg=UI["bg_panel"])
        tk.Label(
            global_thr_row,
            text="Movement threshold",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=8)
        threshold_spin = ttk.Spinbox(
            global_thr_row,
            from_=1,
            to=50,
            increment=0.5,
            textvariable=threshold_var,
            width=8,
            style="Dark.TSpinbox",
        )
        threshold_spin.grid(row=0, column=1, padx=6, pady=8)

        per_thr_frame = tk.Frame(threshold_inner, bg=UI["bg_panel"])
        per_movement_vars: dict[str, tk.DoubleVar] = {}
        for i, movement in enumerate(MOVEMENTS):
            per_movement_vars[movement] = tk.DoubleVar(
                value=self.config_data.movement_thresholds[movement]
            )
            tk.Label(
                per_thr_frame,
                text=f"{MOVEMENTS[movement]['ui_name']} threshold ({MOVEMENTS[movement]['label']})",
                fg=UI["text"],
                bg=UI["bg_panel"],
                font=ui_font(11),
            ).grid(row=i, column=0, sticky="w", padx=6, pady=6)
            spin = ttk.Spinbox(
                per_thr_frame,
                from_=1,
                to=50,
                increment=0.5,
                textvariable=per_movement_vars[movement],
                width=8,
                style="Dark.TSpinbox",
            )
            spin.grid(row=i, column=1, padx=6, pady=6)

        def refresh_threshold_mode(*_args):
            if threshold_global_var.get():
                per_thr_frame.pack_forget()
                global_thr_row.pack(fill="x")
            else:
                global_thr_row.pack_forget()
                per_thr_frame.pack(fill="x")

        threshold_global_var.trace_add("write", refresh_threshold_mode)
        refresh_threshold_mode()

        tk.Label(
            form,
            text="Mental command power threshold",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        ).grid(row=4, column=0, sticky="w", padx=6, pady=8)

        com_var = tk.DoubleVar(value=self.config_data.com_power_threshold)

        com_spin = ttk.Spinbox(
            form,
            from_=0,
            to=1,
            increment=0.05,
            textvariable=com_var,
            width=8,
            style="Dark.TSpinbox",
        )
        com_spin.grid(row=4, column=1, padx=6, pady=8)

        tk.Label(
            form,
            text="Mental command keys (held while COM power is above threshold)",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=(14, 4))

        com_binding_vars: dict[str, tk.StringVar] = {}
        com_binding_defaults = {
            "push": self.config_data.key_bindings.get("forward", "w"),
            "pull": self.config_data.key_bindings.get("backward", "s"),
            "left": self.config_data.key_bindings.get("left", "a"),
            "right": self.config_data.key_bindings.get("right", "d"),
        }
        for i, cmd in enumerate(COM_MAPPED_MENTAL_ACTIONS):
            row = 6 + i
            tk.Label(
                form,
                text=cmd,
                fg=UI["text"],
                bg=UI["bg_panel"],
                font=ui_font(11),
            ).grid(row=row, column=0, sticky="w", padx=6, pady=4)
            com_binding_vars[cmd] = tk.StringVar(
                value=str(self.config_data.com_key_bindings.get(cmd, ""))
            )
            tk.Entry(
                form,
                textvariable=com_binding_vars[cmd],
                width=10,
                bg=UI["pad_idle_bg"],
                fg=UI["text"],
                insertbackground=UI["text"],
                highlightthickness=1,
                highlightbackground=UI["border"],
                highlightcolor=UI["accent"],
                relief="flat",
                font=ui_font(11),
            ).grid(row=row, column=1, padx=6, pady=4, sticky="w")

        def save_settings():
            self.config_data.keyboard_enabled = bool(keyboard_var.get())
            self.config_data.threshold_global = bool(threshold_global_var.get())
            self.config_data.threshold = float(threshold_var.get())
            for movement, var in per_movement_vars.items():
                self.config_data.movement_thresholds[movement] = float(var.get())
            self.config_data.com_power_threshold = float(com_var.get())
            for cmd in COM_MAPPED_MENTAL_ACTIONS:
                raw = com_binding_vars[cmd].get().strip()
                self.config_data.com_key_bindings[cmd] = (
                    raw if raw else com_binding_defaults[cmd]
                )
            save_config(self.config_data)
            self.show_main_view()

        button_row = tk.Frame(self.content, bg=UI["bg_panel"])
        button_row.pack(pady=24)

        self._make_button(
            button_row,
            "Back",
            self.show_main_view,
            width=12,
        ).grid(row=0, column=0, padx=6)

        self._make_button(
            button_row,
            "Save",
            save_settings,
            primary=True,
            width=12,
        ).grid(row=0, column=1, padx=6)

    def create_movement_pad(self, parent):
        pad = tk.Frame(parent, bg=UI["bg_panel"])
        pad.pack(pady=8)

        self.movement_buttons = {}

        positions = {
            "forward": (0, 1),
            "left": (1, 0),
            "backward": (1, 1),
            "right": (1, 2),
        }

        for movement, pos in positions.items():
            btn = tk.Label(
                pad,
                text=MOVEMENTS[movement]["label"],
                width=6,
                height=3,
                bg=UI["pad_idle_bg"],
                fg=UI["text_dim"],
                font=("Segoe UI", 16, "bold"),
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=UI["border"],
                highlightcolor=UI["border"],
            )
            btn.grid(row=pos[0], column=pos[1], padx=6, pady=6)
            self.movement_buttons[movement] = btn

    def save_calibration(self):
        self.config_data.neutral_x = self.pending_neutral_x
        self.config_data.neutral_y = self.pending_neutral_y
        save_config(self.config_data)
        self.show_main_view()

    def process_stream_message(self, msg: dict):
        has_input = False
        motion_detected = set()
        com_movements = set()
        com_actions = set()

        if isinstance(msg.get("mot"), list):
            has_input = True
            mot = msg["mot"]
            if len(mot) >= 2:
                self.current_x = float(mot[-2] or 0)
                self.current_y = float(mot[-1] or 0)
                motion_detected.update(self.map_motion(self.current_x, self.current_y))

        if isinstance(msg.get("com"), list):
            has_input = True
            com = msg["com"]
            action = str(com[0] or "neutral").lower()
            power = float(com[1] or 0)
            for k in self.com_powers:
                self.com_powers[k] = 0.0
            if action in self.com_powers:
                self.com_powers[action] = power
            cm, ca = self.map_mental_command(com)
            com_movements.update(cm)
            com_actions.update(ca)

        if not has_input:
            return

        detected = motion_detected | com_movements
        self.active_movements = detected
        self.sim_keyboard.sync(motion_detected, com_actions, self.config_data)

        if self.calibration_active:
            self.calibration_samples.append((self.current_x, self.current_y))

    def map_motion(self, x: float, y: float) -> set:
        neutral_x = self.get_active_neutral_x()
        neutral_y = self.get_active_neutral_y()
        if neutral_x is None or neutral_y is None:
            return set()
        cfg = self.config_data
        return compute_motion_movements(
            x,
            y,
            float(neutral_x),
            float(neutral_y),
            threshold_global=cfg.threshold_global,
            threshold=float(cfg.threshold),
            movement_thresholds=cfg.movement_thresholds,
        )

    def map_mental_command(self, com: list) -> tuple[set, set]:
        """Returns (movement labels for the pad UI, mental actions for COM keys)."""
        return mental_command_to_sets(
            com,
            power_threshold=float(self.config_data.com_power_threshold),
        )

    def get_active_neutral_x(self):
        if self.current_view == "calibration_review" and self.pending_neutral_x is not None:
            return self.pending_neutral_x

        return self.config_data.neutral_x

    def get_active_neutral_y(self):
        if self.current_view == "calibration_review" and self.pending_neutral_y is not None:
            return self.pending_neutral_y

        return self.config_data.neutral_y

    def update_ui(self):
        self.draw_crosshair()

        xy_text = f"x={self.current_x:.2f} · y={self.current_y:.2f}"

        xy = getattr(self, "xy_label", None)
        if xy is not None:
            self.xy_label.config(text=xy_text)

        rxy = getattr(self, "review_xy_label", None)
        if rxy is not None:
            self.review_xy_label.config(text=xy_text)

        kbd = getattr(self, "keyboard_label", None)
        if kbd is not None:
            self.keyboard_label.config(
                text=(
                    "Simulated keyboard: on"
                    if self.config_data.keyboard_enabled
                    else "Simulated keyboard: off"
                )
            )

        if self.current_view == "main" and getattr(self, "com_power_labels", None):
            thr = float(self.config_data.com_power_threshold)
            hint = getattr(self, "com_threshold_hint", None)
            if hint is not None:
                try:
                    if hint.winfo_exists():
                        hint.config(text=f"Activate if power ≥ {thr:.2f}")
                except tk.TclError:
                    pass
            com_names = (getattr(self, "_com_font_targets", None) or {}).get("names")
            if com_names and len(com_names) == len(COM_MAPPED_MENTAL_ACTIONS):
                for cmd, nl in zip(COM_MAPPED_MENTAL_ACTIONS, com_names):
                    try:
                        if not nl.winfo_exists():
                            continue
                    except tk.TclError:
                        continue
                    key = str(self.config_data.com_key_bindings.get(cmd, ""))
                    nl.config(text=f"{cmd} → {key}")
            for cmd, lab in self.com_power_labels.items():
                try:
                    if not lab.winfo_exists():
                        continue
                except tk.TclError:
                    continue
                p = float(self.com_powers.get(cmd, 0.0))
                lab.config(
                    text=f"{p:.2f}",
                    fg=UI["accent_strong"] if p >= thr else UI["text"],
                )

        for movement, btn in self.movement_buttons.items():
            if movement in self.active_movements:
                btn.config(
                    bg=UI["pad_active_bg"],
                    fg=UI["pad_active_fg"],
                    highlightbackground=UI["accent_strong"],
                )
            else:
                btn.config(
                    bg=UI["pad_idle_bg"],
                    fg=UI["text_dim"],
                    highlightbackground=UI["border"],
                )

        if self.calibration_active:
            elapsed = time.time() - self.calibration_started_at
            remaining = max(0, 10 - elapsed)

            tl = getattr(self, "timer_label", None)
            if tl is not None:
                tl.config(text=str(int(remaining) + 1 if remaining > 0 else 0))

            if self.calibration_samples:
                avg_x = sum(x for x, _ in self.calibration_samples) / len(self.calibration_samples)
                avg_y = sum(y for _, y in self.calibration_samples) / len(self.calibration_samples)
                self.calibration_xy_label.config(
                    text=f"avg x={avg_x:.2f} · avg y={avg_y:.2f}"
                )

            if elapsed >= 10:
                if not self.calibration_samples:
                    messagebox.showerror(
                        "Error",
                        "No motion data received during calibration.",
                    )
                    self.show_main_view()
                    return

                self.pending_neutral_x = (
                    sum(x for x, _ in self.calibration_samples)
                    / len(self.calibration_samples)
                )
                self.pending_neutral_y = (
                    sum(y for _, y in self.calibration_samples)
                    / len(self.calibration_samples)
                )

                self.show_calibration_review_view()

    def draw_crosshair(self):
        self.canvas.delete("crosshair")

        width = self.winfo_width()
        height = self.winfo_height()

        cx = width / 2
        cy = height / 2

        neutral_x = self.get_active_neutral_x()
        neutral_y = self.get_active_neutral_y()

        if neutral_x is None or neutral_y is None:
            dx = 0
            dy = 0
        else:
            dx = self.current_y - neutral_y
            dy = self.current_x - neutral_x

        scale = 7
        max_radius_x = width * 0.38
        max_radius_y = height * 0.38

        px = cx + max(-max_radius_x, min(max_radius_x, dx * scale))
        py = cy + max(-max_radius_y, min(max_radius_y, dy * scale))

        arm = max(16, min(width, height) // 22)
        dot_r = max(6, min(width, height) // 55)

        self.canvas.create_line(
            cx - arm,
            cy,
            cx + arm,
            cy,
            fill=UI["crosshair"],
            width=2,
            tags="crosshair",
        )
        self.canvas.create_line(
            cx,
            cy - arm,
            cx,
            cy + arm,
            fill=UI["crosshair"],
            width=2,
            tags="crosshair",
        )

        self.canvas.create_oval(
            px - dot_r,
            py - dot_r,
            px + dot_r,
            py + dot_r,
            fill=UI["dot"],
            outline=UI["accent_strong"],
            width=1,
            tags="crosshair",
        )

        self.canvas.tag_raise("crosshair", self._content_window_id)

    def tick(self):
        try:
            while True:
                msg = self.stream_queue.get_nowait()
                self.process_stream_message(msg)
        except Empty:
            pass

        try:
            while True:
                cmd = self._hotkey_control_queue.get_nowait()
                if cmd == "fallback_pynput":
                    self._install_pynput_hotkey()
                elif cmd == "hint_ctrl_alt_k":
                    self.status_queue.put(
                        "Keyboard shortcut is Ctrl+Alt+K (Ctrl+Shift+K is reserved by another app)."
                    )
        except Empty:
            pass

        try:
            while True:
                self.keyboard_shortcut_queue.get_nowait()
                self._toggle_keyboard_via_shortcut()
        except Empty:
            pass

        try:
            while True:
                status = self.status_queue.get_nowait()
                if getattr(self, "status_label", None) is not None:
                    self.status_label.config(text=status)
                err = getattr(self, "error_label", None)
                if err is not None:
                    err.config(text="")
                self.connection_failed = False
                rb = getattr(self, "retry_button", None)
                if rb is not None:
                    try:
                        if rb.winfo_ismapped():
                            rb.pack_forget()
                    except tk.TclError:
                        pass
        except Empty:
            pass

        try:
            while True:
                error = self.error_queue.get_nowait()
                err = getattr(self, "error_label", None)
                if err is not None:
                    err.config(text=error)
                print(error)
                self.connection_failed = True
                rb = getattr(self, "retry_button", None)
                if rb is not None:
                    try:
                        if not rb.winfo_ismapped():
                            rb.pack(anchor="center", pady=(6, 0))
                    except tk.TclError:
                        pass
        except Empty:
            pass

        self.update_ui()
        self.after(30, self.tick)

    def _on_check_for_updates(self):
        if self._update_check_in_progress:
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Check for updates",
                "In-app updates apply only to the packaged Windows executable.",
            )
            return
        if sys.platform != "win32":
            messagebox.showinfo(
                "Check for updates",
                "In-app updates are only supported on Windows.",
            )
            return
        if not get_update_manifest_url():
            messagebox.showinfo(
                "Check for updates",
                "Updates are not configured for this build.",
            )
            return
        self._update_check_in_progress = True

        def work():
            try:
                url = get_update_manifest_url()
                is_newer, manifest, err = check_update_available(url)
                self.after(
                    0,
                    lambda i=is_newer, m=dict(manifest), er=err: self._update_check_finished(
                        i, m, er
                    ),
                )
            except Exception as e:
                self.after(
                    0,
                    lambda ex=str(e): self._update_check_finished(False, {}, ex),
                )

        threading.Thread(target=work, daemon=True).start()

    def _update_check_finished(self, is_newer, manifest, err):
        self._update_check_in_progress = False
        if err:
            messagebox.showerror("Check for updates", f"Update check failed:\n{err}")
            return
        if not is_newer:
            ch = manifest.get("version", "?")
            messagebox.showinfo(
                "Check for updates",
                f"You are up to date.\n\nInstalled: {get_app_version()}\n"
                f"Update channel: {ch}",
            )
            return
        latest = manifest["version"]
        if not messagebox.askyesno(
            "Check for updates",
            f"Version {latest} is available (you have {get_app_version()}).\n\n"
            "Download and install now? The app will close and restart.",
        ):
            return
        self._update_check_in_progress = True

        def download_work():
            try:
                staged = download_and_verify(manifest)
                apply_staged_update(staged)
                self.after(0, self._update_install_queued_exit)
            except Exception as e:
                self.after(0, lambda e=e: self._update_download_failed(str(e)))

        threading.Thread(target=download_work, daemon=True).start()

    def _update_download_failed(self, msg):
        self._update_check_in_progress = False
        messagebox.showerror(
            "Check for updates",
            f"Download or install failed:\n{msg}",
        )

    def _update_install_queued_exit(self):
        self._update_check_in_progress = False
        messagebox.showinfo(
            "Check for updates",
            "The update is ready. This window will close and the app will restart automatically.",
        )
        os._exit(0)

    def on_close(self):
        self.sim_keyboard.release_all(self.config_data)

        try:
            self.cortex.stop()
        except Exception:
            pass

        if (
            sys.platform == "win32"
            and self._hotkey_thread is not None
            and self._hotkey_win32_registered.is_set()
            and self._hotkey_thread.is_alive()
        ):
            import ctypes
            from ctypes import wintypes

            WM_QUIT = 0x0012
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            tid = self._hotkey_thread.ident
            if tid is not None:
                user32.PostThreadMessageW(
                    wintypes.DWORD(tid), WM_QUIT, 0, 0
                )
            self._hotkey_thread.join(timeout=3.0)

        self._hotkey_thread = None
        self._hotkey_win32_registered.clear()

        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass

        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()