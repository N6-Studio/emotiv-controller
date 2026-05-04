import json
import ssl
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from queue import Queue, Empty
from typing import Callable, Optional

import websocket
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


def _default_pynput_keyboard():
    """Import pynput only when needed (headless Linux cannot import it at module load)."""
    from pynput import keyboard as pynput_keyboard

    return pynput_keyboard


def _pynput_keyboard():
    """Prefer ``app._pynput_keyboard`` when patched (tests); avoid recursion on default."""
    import sys

    app_mod = sys.modules.get("app")
    if app_mod is not None:
        fn = getattr(app_mod, "_pynput_keyboard", None)
        if fn is not None and getattr(fn, "__emotiv_default_pynput__", None) is not True:
            return fn()
    return _default_pynput_keyboard()


CONFIG_PATH = Path("config.json")


def _config_path() -> Path:
    """Use ``app.CONFIG_PATH`` when the app module is loaded (tests monkeypatch it)."""
    import sys

    app_mod = sys.modules.get("app")
    if app_mod is not None:
        p = getattr(app_mod, "CONFIG_PATH", None)
        if isinstance(p, Path):
            return p
    return CONFIG_PATH


DEFAULT_THRESHOLD = 5.0
DEFAULT_COM_POWER_THRESHOLD = 0.25
DEFAULT_COM_KEY_BINDINGS = {
    "push": "q",
    "pull": "e",
    "left": "r",
    "right": "f",
}

# Labels for the Cortex connection settings form (values live on ``AppConfig``).
APP_ENV_UI_KEYS = [
    "CORTEX_URL",
    "STREAMS",
    "EMOTIV_CLIENT_ID",
    "EMOTIV_CLIENT_SECRET",
    "EMOTIV_LICENSE",
    "EMOTIV_DEBIT",
]


@dataclass
class CortexEnv:
    cortex_url: str
    streams: list[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    license: str
    debit: int


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
    debug_mode: bool = False
    com_power_threshold: float = DEFAULT_COM_POWER_THRESHOLD
    key_bindings: dict = None
    com_key_bindings: dict = None
    cortex_url: str = "wss://localhost:6868"
    cortex_streams: str = "mot"
    emotiv_client_id: str = ""
    emotiv_client_secret: str = ""
    emotiv_license: str = ""
    emotiv_debit: int = 1

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
        com_defaults = dict(DEFAULT_COM_KEY_BINDINGS)
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
        if not isinstance(self.emotiv_debit, int):
            try:
                self.emotiv_debit = int(str(self.emotiv_debit).strip())
            except (TypeError, ValueError):
                self.emotiv_debit = 1


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppConfig()
        allowed = {f.name for f in fields(AppConfig)}
        filtered = {k: v for k, v in raw.items() if k in allowed}
        return AppConfig(**filtered)
    except Exception:
        return AppConfig()


def save_config(config: AppConfig):
    path = _config_path()
    path.write_text(
        json.dumps(asdict(config), indent=2),
        encoding="utf-8",
    )


def _str_or_default(value: Optional[str], default: str) -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s if s else default


def _optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def read_cortex_env(config: Optional[AppConfig] = None) -> CortexEnv:
    """Build ``CortexEnv`` from ``config`` or from ``load_config()``."""
    cfg = config if config is not None else load_config()
    cortex_url = _str_or_default(cfg.cortex_url, "wss://localhost:6868")
    streams_raw = _str_or_default(cfg.cortex_streams, "mot")
    streams = [s.strip() for s in streams_raw.split(",") if s.strip()]
    if not streams:
        streams = ["mot"]
    return CortexEnv(
        cortex_url=cortex_url,
        streams=streams,
        client_id=_optional_str(cfg.emotiv_client_id),
        client_secret=_optional_str(cfg.emotiv_client_secret),
        license=_str_or_default(cfg.emotiv_license, ""),
        debit=int(cfg.emotiv_debit),
    )


def app_env_form_values(config: AppConfig) -> dict[str, str]:
    """Values for the environment settings form from ``config``."""
    ce = read_cortex_env(config)
    return {
        "CORTEX_URL": ce.cortex_url,
        "STREAMS": ",".join(ce.streams),
        "EMOTIV_CLIENT_ID": ce.client_id or "",
        "EMOTIV_CLIENT_SECRET": ce.client_secret or "",
        "EMOTIV_LICENSE": ce.license,
        "EMOTIV_DEBIT": str(ce.debit),
    }


def apply_cortex_env_form_to_config(config: AppConfig, values: dict[str, str]) -> None:
    """Apply form dict (``APP_ENV_UI_KEYS``) onto ``config`` in place."""
    config.cortex_url = _str_or_default(values.get("CORTEX_URL"), "wss://localhost:6868")
    config.cortex_streams = _str_or_default(values.get("STREAMS"), "mot")
    config.emotiv_client_id = (values.get("EMOTIV_CLIENT_ID") or "").strip()
    config.emotiv_client_secret = (values.get("EMOTIV_CLIENT_SECRET") or "").strip()
    config.emotiv_license = (values.get("EMOTIV_LICENSE") or "").strip()
    try:
        config.emotiv_debit = int((values.get("EMOTIV_DEBIT") or "1").strip())
    except ValueError:
        config.emotiv_debit = 1


class SimulatedKeyboard:
    def __init__(self):
        self.controller = _pynput_keyboard().Controller()
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
        env = read_cortex_env()
        if not env.client_id or not env.client_secret:
            self.on_error(
                "Missing EMOTIV_CLIENT_ID or EMOTIV_CLIENT_SECRET "
                "(set in config.json under Settings → Environment variables)"
            )
            return

        self.ws_app = websocket.WebSocketApp(
            env.cortex_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_ws_error,
            on_close=self._on_close,
        )

        def _run_forever() -> None:
            url = env.cortex_url.strip()
            if url.lower().startswith("wss://"):
                self.ws_app.run_forever(
                    sslopt={
                        "cert_reqs": ssl.CERT_NONE,
                        "check_hostname": False,
                    }
                )
            else:
                self.ws_app.run_forever()

        websocket_thread = threading.Thread(target=_run_forever, daemon=True)
        websocket_thread.start()

        if not self.connected_event.wait(timeout=15):
            self.on_error("Cortex connection timeout")
            return

        try:
            self.initialize_cortex(env)
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

    def initialize_cortex(self, env: CortexEnv):
        self.on_status("Requesting access...")

        access = self.request_v2("requestAccess", {
            "clientId": env.client_id,
            "clientSecret": env.client_secret,
        })

        if not access.get("accessGranted"):
            raise RuntimeError("Access denied. Approve the app in EMOTIV Launcher.")

        self.on_status("Authorizing...")

        auth = self.request_v2("authorize", {
            "clientId": env.client_id,
            "clientSecret": env.client_secret,
            "license": env.license,
            "debit": env.debit,
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
            "streams": env.streams,
        })

        self.on_status(f"Ready · Headset {headset['id']}")

    def stop(self):
        self.stop_event.set()
        if self.ws_app:
            self.ws_app.close()


def _status_clears_connection_error_ui(status: str) -> bool:
    """Main-view status line is shared with Cortex progress and local UI hints."""
    if status.startswith("Keyboard presses "):
        return False
    if status.startswith("Keyboard shortcut is "):
        return False
    return True

