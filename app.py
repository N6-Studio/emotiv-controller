import json
import os
import ssl
import threading
import time
from dataclasses import dataclass, asdict
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
    keyboard_enabled: bool = False
    com_power_threshold: float = DEFAULT_COM_POWER_THRESHOLD
    key_bindings: dict = None

    def __post_init__(self):
        if self.key_bindings is None:
            self.key_bindings = {
                movement: data["default_key"]
                for movement, data in MOVEMENTS.items()
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
        self.geometry("420x460")
        self.minsize(420, 460)
        self.maxsize(520, 560)

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

        self.configure(bg="#111111")

        self.canvas = tk.Canvas(
            self,
            width=420,
            height=460,
            bg="#111111",
            highlightthickness=0,
        )
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.content = tk.Frame(self, bg="#171717")
        self.content.place(relx=0.5, rely=0.5, anchor="center", width=370, height=410)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.start_shortcut_listener()
        self.show_main_view()

        self.cortex = CortexClient(
            on_stream=lambda msg: self.stream_queue.put(msg),
            on_status=lambda msg: self.status_queue.put(msg),
            on_error=lambda msg: self.error_queue.put(msg),
        )
        self.cortex.start()

        self.after(30, self.tick)

    def start_shortcut_listener(self):
        def toggle():
            self.config_data.keyboard_enabled = not self.config_data.keyboard_enabled
            save_config(self.config_data)
            self.status_queue.put(
                "Tastiera simulata attiva"
                if self.config_data.keyboard_enabled
                else "Tastiera simulata disattivata"
            )

        self.hotkey_listener = pynput_keyboard.GlobalHotKeys({
            "<ctrl>+<shift>+k": toggle,
        })
        self.hotkey_listener.start()

    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def show_main_view(self):
        self.current_view = "main"
        self.calibration_active = False
        self.clear_content()

        tk.Label(
            self.content,
            text="Controllo movimenti",
            fg="white",
            bg="#171717",
            font=("Arial", 18, "bold"),
        ).pack(pady=(18, 4))

        self.status_label = tk.Label(
            self.content,
            text="Connessione...",
            fg="#bbbbbb",
            bg="#171717",
            font=("Arial", 10),
        )
        self.status_label.pack(pady=(0, 8))

        self.xy_label = tk.Label(
            self.content,
            text="x=0 · y=0",
            fg="#dddddd",
            bg="#171717",
            font=("Arial", 11),
        )
        self.xy_label.pack(pady=(0, 10))

        self.create_movement_pad(self.content)

        button_row = tk.Frame(self.content, bg="#171717")
        button_row.pack(pady=16)

        tk.Button(
            button_row,
            text="Inizializza",
            command=self.show_calibration_view,
            width=14,
        ).grid(row=0, column=0, padx=5)

        tk.Button(
            button_row,
            text="Impostazioni",
            command=self.show_settings_view,
            width=14,
        ).grid(row=0, column=1, padx=5)

        self.keyboard_label = tk.Label(
            self.content,
            text="",
            fg="#bbbbbb",
            bg="#171717",
            font=("Arial", 10),
        )
        self.keyboard_label.pack(pady=(2, 0))

    def show_calibration_view(self):
        self.current_view = "calibration"
        self.clear_content()

        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []

        tk.Label(
            self.content,
            text="Inizializzazione",
            fg="white",
            bg="#171717",
            font=("Arial", 18, "bold"),
        ).pack(pady=(24, 8))

        tk.Label(
            self.content,
            text="Resta in posizione neutra per 10 secondi.",
            fg="#dddddd",
            bg="#171717",
            font=("Arial", 11),
            wraplength=320,
        ).pack(pady=(0, 18))

        self.timer_label = tk.Label(
            self.content,
            text="10",
            fg="#7CFC98",
            bg="#171717",
            font=("Arial", 48, "bold"),
        )
        self.timer_label.pack(pady=(0, 12))

        self.calibration_xy_label = tk.Label(
            self.content,
            text="x medio=0 · y medio=0",
            fg="#bbbbbb",
            bg="#171717",
            font=("Arial", 11),
        )
        self.calibration_xy_label.pack(pady=(0, 20))

        tk.Button(
            self.content,
            text="Annulla",
            command=self.show_main_view,
            width=16,
        ).pack()

    def show_calibration_review_view(self):
        self.current_view = "calibration_review"
        self.calibration_active = False
        self.clear_content()

        tk.Label(
            self.content,
            text="Verifica configurazione",
            fg="white",
            bg="#171717",
            font=("Arial", 17, "bold"),
        ).pack(pady=(18, 4))

        self.review_xy_label = tk.Label(
            self.content,
            text="x=0 · y=0",
            fg="#dddddd",
            bg="#171717",
            font=("Arial", 11),
        )
        self.review_xy_label.pack(pady=(0, 6))

        self.review_neutral_label = tk.Label(
            self.content,
            text=f"Neutro x={self.pending_neutral_x:.2f} · y={self.pending_neutral_y:.2f}",
            fg="#bbbbbb",
            bg="#171717",
            font=("Arial", 10),
        )
        self.review_neutral_label.pack(pady=(0, 8))

        self.create_movement_pad(self.content)

        button_row = tk.Frame(self.content, bg="#171717")
        button_row.pack(pady=16)

        tk.Button(
            button_row,
            text="Annulla",
            command=self.show_main_view,
            width=10,
        ).grid(row=0, column=0, padx=4)

        tk.Button(
            button_row,
            text="Salva",
            command=self.save_calibration,
            width=10,
        ).grid(row=0, column=1, padx=4)

        tk.Button(
            button_row,
            text="Riprova",
            command=self.show_calibration_view,
            width=10,
        ).grid(row=0, column=2, padx=4)

    def show_settings_view(self):
        self.current_view = "settings"
        self.clear_content()

        tk.Label(
            self.content,
            text="Impostazioni",
            fg="white",
            bg="#171717",
            font=("Arial", 18, "bold"),
        ).pack(pady=(20, 14))

        keyboard_var = tk.BooleanVar(value=self.config_data.keyboard_enabled)

        keyboard_check = tk.Checkbutton(
            self.content,
            text="Abilita tastiera simulata",
            variable=keyboard_var,
            fg="white",
            bg="#171717",
            selectcolor="#171717",
            activebackground="#171717",
            activeforeground="white",
            font=("Arial", 11),
        )
        keyboard_check.pack(anchor="w", padx=34, pady=(0, 8))

        tk.Label(
            self.content,
            text="Scorciatoia: Ctrl + Shift + K",
            fg="#bbbbbb",
            bg="#171717",
            font=("Arial", 10),
        ).pack(anchor="w", padx=38, pady=(0, 16))

        form = tk.Frame(self.content, bg="#171717")
        form.pack(pady=4)

        tk.Label(
            form,
            text="Soglia movimenti",
            fg="white",
            bg="#171717",
            font=("Arial", 11),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=8)

        threshold_var = tk.DoubleVar(value=self.config_data.threshold)

        threshold_spin = ttk.Spinbox(
            form,
            from_=1,
            to=50,
            increment=0.5,
            textvariable=threshold_var,
            width=8,
        )
        threshold_spin.grid(row=0, column=1, padx=6, pady=8)

        tk.Label(
            form,
            text="Soglia potenza com",
            fg="white",
            bg="#171717",
            font=("Arial", 11),
        ).grid(row=1, column=0, sticky="w", padx=6, pady=8)

        com_var = tk.DoubleVar(value=self.config_data.com_power_threshold)

        com_spin = ttk.Spinbox(
            form,
            from_=0,
            to=1,
            increment=0.05,
            textvariable=com_var,
            width=8,
        )
        com_spin.grid(row=1, column=1, padx=6, pady=8)

        def save_settings():
            self.config_data.keyboard_enabled = bool(keyboard_var.get())
            self.config_data.threshold = float(threshold_var.get())
            self.config_data.com_power_threshold = float(com_var.get())
            save_config(self.config_data)
            self.show_main_view()

        button_row = tk.Frame(self.content, bg="#171717")
        button_row.pack(pady=24)

        tk.Button(
            button_row,
            text="Indietro",
            command=self.show_main_view,
            width=12,
        ).grid(row=0, column=0, padx=5)

        tk.Button(
            button_row,
            text="Salva",
            command=save_settings,
            width=12,
        ).grid(row=0, column=1, padx=5)

    def create_movement_pad(self, parent):
        pad = tk.Frame(parent, bg="#171717")
        pad.pack(pady=6)

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
                bg="#333333",
                fg="#999999",
                font=("Arial", 16, "bold"),
                relief="ridge",
                bd=2,
            )
            btn.grid(row=pos[0], column=pos[1], padx=5, pady=5)
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
        threshold = self.config_data.threshold

        if neutral_x is None or neutral_y is None:
            return set()

        movements = set()

        if x <= neutral_x - threshold:
            movements.add("forward")
        elif x >= neutral_x + threshold:
            movements.add("backward")

        if y <= neutral_y - threshold:
            movements.add("left")
        elif y >= neutral_y + threshold:
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
                btn.config(bg="#16a34a", fg="white")
            else:
                btn.config(bg="#333333", fg="#999999")

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

        self.canvas.create_line(
            cx - 22,
            cy,
            cx + 22,
            cy,
            fill="#444444",
            width=2,
            tags="crosshair",
        )
        self.canvas.create_line(
            cx,
            cy - 22,
            cx,
            cy + 22,
            fill="#444444",
            width=2,
            tags="crosshair",
        )

        self.canvas.create_oval(
            px - 8,
            py - 8,
            px + 8,
            py + 8,
            fill="#7CFC98",
            outline="",
            tags="crosshair",
        )

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
        except Empty:
            pass

        try:
            while True:
                error = self.error_queue.get_nowait()
                if hasattr(self, "status_label"):
                    self.status_label.config(text=error)
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

        try:
            self.hotkey_listener.stop()
        except Exception:
            pass

        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()