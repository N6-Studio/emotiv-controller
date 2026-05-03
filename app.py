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
        "ui_name": "Avanti",
        "default_key": "w",
    },
    "left": {
        "label": "A",
        "ui_name": "Sinistra",
        "default_key": "a",
    },
    "backward": {
        "label": "S",
        "ui_name": "Indietro",
        "default_key": "s",
    },
    "right": {
        "label": "D",
        "ui_name": "Destra",
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

    def press(self, movement: str, key: str):
        if movement in self.pressed_movements:
            return

        self.controller.press(key)
        self.pressed_movements.add(movement)

    def release(self, movement: str, key: str):
        if movement not in self.pressed_movements:
            return

        self.controller.release(key)
        self.pressed_movements.remove(movement)

    def sync(self, active_movements: set, config: AppConfig):
        if not config.keyboard_enabled:
            self.release_all(config)
            return

        for movement, key in config.key_bindings.items():
            if movement in active_movements:
                self.press(movement, key)
            else:
                self.release(movement, key)

    def release_all(self, config: AppConfig):
        for movement in list(self.pressed_movements):
            key = config.key_bindings.get(movement)
            if key:
                self.release(movement, key)


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
        self.next_id = 1
        self.pending = {}
        self.connected_event = threading.Event()
        self.stop_event = threading.Event()

    def run(self):
        if not CLIENT_ID or not CLIENT_SECRET:
            self.on_error("Mancano EMOTIV_CLIENT_ID o EMOTIV_CLIENT_SECRET nel file .env")
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
            self.on_error("Timeout connessione Cortex")
            return

        try:
            self.initialize_cortex()
        except Exception as exc:
            self.on_error(str(exc))

        while not self.stop_event.is_set():
            time.sleep(0.1)

    def _on_open(self, ws):
        self.ws = ws
        self.connected_event.set()
        self.on_status("Connesso a Cortex")

    def _on_close(self, ws, close_status_code, close_msg):
        self.on_status("Connessione chiusa")

    def _on_ws_error(self, ws, error):
        self.on_error(f"Errore WebSocket: {error}")

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
            raise RuntimeError(response["error"].get("message", f"Errore: {method}"))

        return response.get("result")

    def initialize_cortex(self):
        self.on_status("Richiesta accesso...")

        access = self.request_v2("requestAccess", {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
        })

        if not access.get("accessGranted"):
            raise RuntimeError("Accesso non concesso. Approva l'app in EMOTIV Launcher.")

        self.on_status("Autorizzazione...")

        auth = self.request_v2("authorize", {
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            "license": LICENSE,
            "debit": DEBIT,
        })

        cortex_token = auth["cortexToken"]

        self.on_status("Ricerca headset...")

        headsets = self.request_v2("queryHeadsets")
        headset = None

        for item in headsets:
            if item.get("status") == "connected":
                headset = item
                break

        if headset is None and headsets:
            headset = headsets[0]

        if headset is None:
            raise RuntimeError("Nessun headset trovato.")

        if headset.get("status") != "connected":
            self.on_status("Connessione headset...")
            self.request_v2("controlDevice", {
                "command": "connect",
                "headset": headset["id"],
            })

        self.on_status("Creazione sessione...")

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

        self.on_status(f"Pronto · Headset {headset['id']}")

    def stop(self):
        self.stop_event.set()
        if self.ws_app:
            self.ws_app.close()


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("EMOTIV Movimenti")
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
        cw0 = max(int(init_w * 0.94), 200)
        ch0 = max(int(init_h * 0.94), 200)
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
            "Tastiera simulata attiva"
            if self.config_data.keyboard_enabled
            else "Tastiera simulata disattivata"
        )

    def _install_pynput_hotkey(self):
        self.hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<ctrl>+<shift>+k": lambda: self.after(0, self._toggle_keyboard_via_shortcut),
        })
        self.hotkey_listener.start()

    def _install_win32_hotkey(self) -> bool:
        if sys.platform != "win32":
            return False

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)

        WM_HOTKEY = 0x0312
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        GWL_WNDPROC = -4
        VK_K = 0x4B
        hotkey_id = 0x4D42

        self.update_idletasks()
        hwnd = wintypes.HWND(int(self.winfo_id()))
        if not hwnd:
            return False

        if not user32.RegisterHotKey(hwnd, hotkey_id, MOD_CONTROL | MOD_SHIFT, VK_K):
            return False

        LRESULT = ctypes.c_longlong
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = wintypes.LONG_PTR
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            WNDPROC,
        ]
        user32.SetWindowLongPtrW.restype = wintypes.LONG_PTR
        user32.CallWindowProcW.argtypes = [
            wintypes.LONG_PTR,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallWindowProcW.restype = LRESULT

        old_wndproc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)
        if not old_wndproc:
            user32.UnregisterHotKey(hwnd, hotkey_id)
            return False

        app = self

        @WNDPROC
        def hotkey_wndproc(hwnd_cb, msg, wparam, lparam):
            if msg == WM_HOTKEY and wparam == hotkey_id:
                app.after(0, app._toggle_keyboard_via_shortcut)
                return 0
            return user32.CallWindowProcW(
                old_wndproc, hwnd_cb, msg, wparam, lparam
            )

        user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, hotkey_wndproc)

        self._win_hotkey_hwnd = hwnd
        self._win_hotkey_id = hotkey_id
        self._win_hotkey_old_wndproc = old_wndproc
        self._win_hotkey_wndproc = hotkey_wndproc
        return True

    def start_shortcut_listener(self):
        self.hotkey_listener = None
        self._win_hotkey_hwnd = None
        self._win_hotkey_id = None
        self._win_hotkey_old_wndproc = None
        self._win_hotkey_wndproc = None

        def try_install():
            if self._install_win32_hotkey():
                return
            self._install_pynput_hotkey()

        self.after_idle(try_install)

    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()
        self.calibration_instruction_label = None

    def _on_canvas_configure(self, event):
        if event.widget is not self.canvas:
            return
        w = max(event.width, 1)
        h = max(event.height, 1)
        cw = max(int(w * 0.94), 200)
        ch = max(int(h * 0.94), 200)
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

        if self.current_view == "calibration" and hasattr(self, "timer_label"):
            try:
                if self.timer_label.winfo_exists():
                    self.timer_label.config(
                        font=("Segoe UI", max(22, int(48 * scale)), "bold"),
                    )
            except tk.TclError:
                pass

    def show_main_view(self):
        self.current_view = "main"
        self.calibration_active = False
        self.clear_content()

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

        top_bar = tk.Frame(self.content, bg=UI["bg_panel"])
        top_bar.pack(side="top", fill="x", padx=12, pady=(12, 10))

        info_col = tk.Frame(top_bar, bg=UI["bg_panel"])
        info_col.pack(side="left", fill="x", expand=True)

        self.status_label = tk.Label(
            info_col,
            text="Connessione...",
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
            "Inizializza",
            self.show_calibration_view,
            primary=True,
            width=14,
        ).pack(side="left", padx=(0, 8))

        self._make_button(
            btn_bar,
            "Impostazioni",
            self.show_settings_view,
            width=14,
        ).pack(side="left")

        main_body = tk.Frame(self.content, bg=UI["bg_panel"])
        main_body.pack(side="top", fill="both", expand=True)

        self.create_movement_pad(main_body)

        self.keyboard_label = tk.Label(
            main_body,
            text="",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        )
        self.keyboard_label.pack(pady=(8, 14))

    def show_calibration_view(self):
        self.current_view = "calibration"
        self.clear_content()

        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []

        tk.Label(
            self.content,
            text="Inizializzazione",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(20, True),
        ).pack(pady=(28, 10))

        self.calibration_instruction_label = tk.Label(
            self.content,
            text="Resta in posizione neutra per 10 secondi.",
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
            text="x medio=0 · y medio=0",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        )
        self.calibration_xy_label.pack(pady=(0, 24))

        self._make_button(
            self.content,
            "Annulla",
            self.show_main_view,
            width=16,
        ).pack()

    def show_calibration_review_view(self):
        self.current_view = "calibration_review"
        self.calibration_active = False
        self.clear_content()

        tk.Label(
            self.content,
            text="Verifica configurazione",
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
            text=f"Neutro x={self.pending_neutral_x:.2f} · y={self.pending_neutral_y:.2f}",
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
            "Annulla",
            self.show_main_view,
            width=10,
        ).grid(row=0, column=0, padx=5)

        self._make_button(
            button_row,
            "Salva",
            self.save_calibration,
            primary=True,
            width=10,
        ).grid(row=0, column=1, padx=5)

        self._make_button(
            button_row,
            "Riprova",
            self.show_calibration_view,
            width=10,
        ).grid(row=0, column=2, padx=5)

    def show_settings_view(self):
        self.current_view = "settings"
        self.clear_content()

        tk.Label(
            self.content,
            text="Impostazioni",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(20, True),
        ).pack(pady=(24, 16))

        keyboard_var = tk.BooleanVar(value=self.config_data.keyboard_enabled)

        keyboard_check = tk.Checkbutton(
            self.content,
            text="Abilita tastiera simulata",
            variable=keyboard_var,
            fg=UI["text"],
            bg=UI["bg_panel"],
            selectcolor=UI["pad_idle_bg"],
            activebackground=UI["bg_panel"],
            activeforeground=UI["text"],
            font=ui_font(11),
        )
        keyboard_check.pack(anchor="w", padx=34, pady=(0, 8))

        tk.Label(
            self.content,
            text="Scorciatoia: Ctrl + Shift + K",
            fg=UI["text_muted"],
            bg=UI["bg_panel"],
            font=ui_font(10),
        ).pack(anchor="w", padx=38, pady=(0, 16))

        form = tk.Frame(self.content, bg=UI["bg_panel"])
        form.pack(pady=4)

        threshold_global_var = tk.BooleanVar(value=self.config_data.threshold_global)

        global_threshold_cb = tk.Checkbutton(
            form,
            text="Soglia unica per tutti i movimenti",
            variable=threshold_global_var,
            fg=UI["text"],
            bg=UI["bg_panel"],
            selectcolor=UI["pad_idle_bg"],
            activebackground=UI["bg_panel"],
            activeforeground=UI["text"],
            font=ui_font(11),
        )
        global_threshold_cb.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))

        threshold_inner = tk.Frame(form, bg=UI["bg_panel"])
        threshold_inner.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=4)

        threshold_var = tk.DoubleVar(value=self.config_data.threshold)

        global_thr_row = tk.Frame(threshold_inner, bg=UI["bg_panel"])
        tk.Label(
            global_thr_row,
            text="Soglia movimenti",
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
                text=f"Soglia {MOVEMENTS[movement]['ui_name']} ({MOVEMENTS[movement]['label']})",
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
            text="Soglia potenza com",
            fg=UI["text"],
            bg=UI["bg_panel"],
            font=ui_font(11),
        ).grid(row=2, column=0, sticky="w", padx=6, pady=8)

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
        com_spin.grid(row=2, column=1, padx=6, pady=8)

        def save_settings():
            self.config_data.keyboard_enabled = bool(keyboard_var.get())
            self.config_data.threshold_global = bool(threshold_global_var.get())
            self.config_data.threshold = float(threshold_var.get())
            for movement, var in per_movement_vars.items():
                self.config_data.movement_thresholds[movement] = float(var.get())
            self.config_data.com_power_threshold = float(com_var.get())
            save_config(self.config_data)
            self.show_main_view()

        button_row = tk.Frame(self.content, bg=UI["bg_panel"])
        button_row.pack(pady=24)

        self._make_button(
            button_row,
            "Indietro",
            self.show_main_view,
            width=12,
        ).grid(row=0, column=0, padx=6)

        self._make_button(
            button_row,
            "Salva",
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
        detected = set()

        if isinstance(msg.get("mot"), list):
            has_input = True
            mot = msg["mot"]
            if len(mot) >= 2:
                self.current_x = float(mot[-2] or 0)
                self.current_y = float(mot[-1] or 0)
                detected.update(self.map_motion(self.current_x, self.current_y))

        if isinstance(msg.get("com"), list):
            has_input = True
            detected.update(self.map_mental_command(msg["com"]))

        if not has_input:
            return

        self.active_movements = detected
        self.sim_keyboard.sync(self.active_movements, self.config_data)

        if self.calibration_active:
            self.calibration_samples.append((self.current_x, self.current_y))

    def map_motion(self, x: float, y: float) -> set:
        neutral_x = self.get_active_neutral_x()
        neutral_y = self.get_active_neutral_y()
        cfg = self.config_data
        if cfg.threshold_global:
            t_fwd = t_back = t_left = t_right = float(cfg.threshold)
        else:
            m = cfg.movement_thresholds
            t_fwd = float(m["forward"])
            t_back = float(m["backward"])
            t_left = float(m["left"])
            t_right = float(m["right"])

        if neutral_x is None or neutral_y is None:
            return set()

        movements = set()

        if x <= neutral_x - t_fwd:
            movements.add("forward")
        elif x >= neutral_x + t_back:
            movements.add("backward")

        if y <= neutral_y - t_left:
            movements.add("left")
        elif y >= neutral_y + t_right:
            movements.add("right")

        return movements

    def map_mental_command(self, com: list) -> set:
        action = str(com[0] or "neutral")
        power = float(com[1] or 0)

        if power < self.config_data.com_power_threshold:
            return set()

        mapping = {
            "push": "forward",
            "pull": "backward",
            "left": "left",
            "right": "right",
        }

        movement = mapping.get(action)
        return {movement} if movement else set()

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

        if hasattr(self, "xy_label"):
            self.xy_label.config(text=xy_text)

        if hasattr(self, "review_xy_label"):
            self.review_xy_label.config(text=xy_text)

        if hasattr(self, "keyboard_label"):
            self.keyboard_label.config(
                text=(
                    "Tastiera simulata: attiva"
                    if self.config_data.keyboard_enabled
                    else "Tastiera simulata: disattivata"
                )
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

            self.timer_label.config(text=str(int(remaining) + 1 if remaining > 0 else 0))

            if self.calibration_samples:
                avg_x = sum(x for x, _ in self.calibration_samples) / len(self.calibration_samples)
                avg_y = sum(y for _, y in self.calibration_samples) / len(self.calibration_samples)
                self.calibration_xy_label.config(
                    text=f"x medio={avg_x:.2f} · y medio={avg_y:.2f}"
                )

            if elapsed >= 10:
                if not self.calibration_samples:
                    messagebox.showerror(
                        "Errore",
                        "Nessun dato ricevuto durante la calibrazione.",
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
                status = self.status_queue.get_nowait()
                if hasattr(self, "status_label"):
                    self.status_label.config(text=status)
                if hasattr(self, "error_label"):
                    self.error_label.config(text="")
        except Empty:
            pass

        try:
            while True:
                error = self.error_queue.get_nowait()
                if hasattr(self, "error_label"):
                    self.error_label.config(text=error)
                print(error)
        except Empty:
            pass

        self.update_ui()
        self.after(30, self.tick)

    def on_close(self):
        self.sim_keyboard.release_all(self.config_data)

        try:
            self.cortex.stop()
        except Exception:
            pass

        if getattr(self, "_win_hotkey_hwnd", None):
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            GWL_WNDPROC = -4
            hwnd = self._win_hotkey_hwnd
            try:
                user32.UnregisterHotKey(hwnd, self._win_hotkey_id)
            except Exception:
                pass
            try:
                user32.SetWindowLongPtrW.argtypes = [
                    wintypes.HWND,
                    ctypes.c_int,
                    wintypes.LONG_PTR,
                ]
                user32.SetWindowLongPtrW.restype = wintypes.LONG_PTR
                user32.SetWindowLongPtrW(
                    hwnd, GWL_WNDPROC, self._win_hotkey_old_wndproc
                )
            except Exception:
                pass
            self._win_hotkey_hwnd = None
        elif self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass

        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()