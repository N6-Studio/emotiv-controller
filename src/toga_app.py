"""Toga (BeeWare) UI for the EMOTIV movement bridge."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import warnings
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional

import toga

warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.iscoroutinefunction' is deprecated.*",
    category=DeprecationWarning,
    module=r"toga\.handlers",
)
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, HIDDEN, ROW, TOP, VISIBLE

from bridge_core import (
    APP_ENV_UI_KEYS,
    COM_MAPPED_MENTAL_ACTIONS,
    CONFIG_PATH,
    CortexClient,
    DEFAULT_COM_KEY_BINDINGS,
    MOVEMENTS,
    SimulatedKeyboard,
    _status_clears_connection_error_ui,
    apply_cortex_env_form_to_config,
    apply_staged_update,
    app_env_form_values,
    check_update_available,
    download_and_verify,
    get_app_version,
    get_update_manifest_url,
    load_config,
    mental_command_to_sets,
    save_config,
)
from core import compute_motion_movements, mot_to_tilt_xy
from update_service import semver_less

_CROSS = "#6b7280"
_DOT = "#14b8a6"


def _action_btn_style(*, gap_after: bool = False) -> Pack:
    """Larger primary actions; use gap_after on buttons that have another button to their right."""
    return Pack(
        font_size=14,
        font_weight="bold",
        padding_top=12,
        padding_bottom=12,
        padding_left=16,
        padding_right=10 if gap_after else 18,
    )


def _icon() -> Optional[toga.Icon]:
    if getattr(sys, "frozen", False):
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            p = Path(mei) / "assets" / "app.ico"
        else:
            p = Path(sys.executable).resolve().parent / "assets" / "app.ico"
    else:
        p = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    if p.is_file():
        try:
            return toga.Icon(str(p))
        except Exception:
            return None
    return None


class EmotivBridgeApp(toga.App):
    def __init__(self) -> None:
        kw: dict[str, Any] = dict(
            formal_name="EMOTIV Movement",
            app_id="studio.n6.emotiv.movement",
            author="N6 Studio",
        )
        ic = _icon()
        if ic is not None:
            kw["icon"] = ic
        super().__init__(**kw)
        self._tick_task: Optional[asyncio.Task] = None
        self._shutdown_complete = False

        self.config_data = load_config()
        self.sim_keyboard = SimulatedKeyboard()

        self.stream_queue: Queue = Queue()
        self.status_queue: Queue = Queue()
        self.error_queue: Queue = Queue()
        self.keyboard_shortcut_queue: Queue = Queue()
        self._hotkey_control_queue: Queue = Queue()
        self._hotkey_thread: Optional[threading.Thread] = None
        self._hotkey_win32_registered = threading.Event()

        self.current_x = 0.0
        self.current_y = 0.0
        # Pad highlights from mental commands (last ``com`` frame); head motion is recomputed each UI tick.
        self.com_pad_movements: set[str] = set()

        self.calibration_active = False
        self.calibration_started_at: Optional[float] = None
        self.calibration_samples: list[tuple[float, float]] = []
        self.pending_neutral_x: Optional[float] = None
        self.pending_neutral_y: Optional[float] = None

        self.current_view: Optional[str] = None
        self.movement_buttons: dict[str, toga.Label] = {}
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_power_labels: Optional[dict[str, toga.Label]] = None
        self.com_threshold_hint: Optional[toga.Label] = None
        self.com_name_labels: list[toga.Label] = []

        self.connection_failed = False
        self.retry_button: Optional[toga.Button] = None
        self._update_check_in_progress = False

        self.cortex: Optional[CortexClient] = None
        self.hotkey_listener = None

        self._cross_w = 400
        self._cross_h = 160

        self.main_column: Optional[toga.Box] = None
        self.cross_canvas: Optional[toga.Canvas] = None
        self.panel_host: Optional[toga.Box] = None

        self.error_label: Optional[toga.Label] = None
        self.status_label: Optional[toga.Label] = None
        self.xy_label: Optional[toga.Label] = None
        self.keyboard_label: Optional[toga.Label] = None
        self.calibration_instruction_label: Optional[toga.Label] = None
        self.timer_label: Optional[toga.Label] = None
        self.calibration_xy_label: Optional[toga.Label] = None
        self.review_xy_label: Optional[toga.Label] = None
        self.review_neutral_label: Optional[toga.Label] = None

    def startup(self) -> None:
        self.main_window = toga.MainWindow(
            title="EMOTIV Movement",
            size=(600, 520),
            resizable=True,
        )
        self.main_window.on_close = self._on_window_close

        self.cross_canvas = toga.Canvas(
            style=Pack(height=140, flex=0),
            on_resize=self._on_cross_resize,
        )
        self.panel_host = toga.Box(style=Pack(direction=COLUMN, flex=1))

        self.main_column = toga.Box(
            children=[self.cross_canvas, self.panel_host],
            style=Pack(direction=COLUMN, flex=1),
        )
        self.main_window.content = self.main_column

        self.commands.add(
            toga.Command(
                self._on_calibrate_pressed,
                text="Calibrate",
                group=toga.Group.FILE,
                order=10,
            ),
            toga.Command(
                self.show_settings_view,
                text="Settings",
                group=toga.Group.FILE,
                order=20,
            ),
        )

        self.show_main_view()
        self.start_shortcut_listener()

        self.cortex = CortexClient(
            on_stream=lambda msg: self.stream_queue.put(msg),
            on_status=lambda msg: self.status_queue.put(msg),
            on_error=lambda msg: self.error_queue.put(msg),
        )
        self.cortex.start()

        self.main_window.show()

    def on_running(self) -> None:
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def on_exit(self) -> bool:
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._shutdown_resources()
        return True

    def _on_window_close(self, window: toga.Window, **kwargs: Any) -> bool:
        self._shutdown_resources()
        return True

    def invoke_later(self, fn: Callable[[], None]) -> None:
        try:
            self.loop.call_soon_threadsafe(fn)
        except RuntimeError:
            try:
                fn()
            except Exception:
                pass

    def _on_cross_resize(self, widget: toga.Canvas, width: int, height: int, **kwargs: Any) -> None:
        self._cross_w = max(width, 1)
        self._cross_h = max(height, 1)

    def _pynput_keyboard(self):
        from pynput import keyboard as pynput_keyboard

        return pynput_keyboard

    def _toggle_keyboard_via_shortcut(self) -> None:
        self.config_data.keyboard_enabled = not self.config_data.keyboard_enabled
        save_config(self.config_data)
        self.status_queue.put(
            "Keyboard presses on"
            if self.config_data.keyboard_enabled
            else "Keyboard presses off"
        )

    def _install_pynput_hotkey(self) -> None:
        if self.hotkey_listener is not None:
            return
        cb = lambda: self.invoke_later(self._toggle_keyboard_via_shortcut)
        self.hotkey_listener = self._pynput_keyboard().GlobalHotKeys({
            "<ctrl>+<shift>+k": cb,
            "<ctrl>+<alt>+k": cb,
        })
        self.hotkey_listener.start()

    def _win32_hotkey_thread_main(self) -> None:
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

    def start_shortcut_listener(self) -> None:
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

    def _clear_panel_host(self) -> None:
        if self.panel_host is None:
            return
        self.panel_host.clear()
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
        self.com_name_labels = []

    def _restart_cortex_client(self, *, clear_error_ui: bool, status_message: str) -> None:
        if self.cortex is not None:
            try:
                self.cortex.stop()
            except Exception:
                pass

        if clear_error_ui:
            self.connection_failed = False
            if self.retry_button is not None:
                self.retry_button.style.visibility = HIDDEN
            if self.error_label is not None:
                self.error_label.text = ""

        self.cortex = CortexClient(
            on_stream=lambda msg: self.stream_queue.put(msg),
            on_status=lambda msg: self.status_queue.put(msg),
            on_error=lambda msg: self.error_queue.put(msg),
        )
        self.cortex.start()
        self.status_queue.put(status_message)

    def retry_connection(self, widget: Optional[toga.Button] = None) -> None:
        self._restart_cortex_client(
            clear_error_ui=True,
            status_message="Reconnecting...",
        )

    def _pad_style(self, *, active: bool) -> Pack:
        if active:
            return Pack(
                padding=10,
                width=52,
                height=44,
                text_align=CENTER,
                background_color="#0d9488",
                color="#ffffff",
                font_weight="bold",
                font_size=16,
            )
        return Pack(
            padding=10,
            width=52,
            height=44,
            text_align=CENTER,
            background_color="#e5e7eb",
            color="#6b7280",
            font_weight="bold",
            font_size=16,
        )

    def _apply_movement_pad_style(self, btn: toga.Label, active: bool) -> None:
        """Update pad appearance in place so the native backend reliably repaints."""
        if active:
            btn.style.update(background_color="#0d9488", color="#ffffff")
        else:
            btn.style.update(background_color="#e5e7eb", color="#6b7280")

    def create_movement_pad(self, parent: toga.Box) -> None:
        pad = toga.Box(style=Pack(direction=COLUMN))
        parent.add(pad)

        grid = toga.Box(style=Pack(direction=COLUMN))
        pad.add(grid)

        positions = {
            "forward": (0, 1),
            "left": (1, 0),
            "backward": (1, 1),
            "right": (1, 2),
        }
        rows: list[toga.Box] = [toga.Box(style=Pack(direction=ROW)) for _ in range(2)]
        for r in rows:
            grid.add(r)

        self.movement_buttons = {}
        for movement, pos in positions.items():
            lab = toga.Label(
                MOVEMENTS[movement]["label"],
                style=self._pad_style(active=False),
            )
            rows[pos[0]].add(lab)
            self.movement_buttons[movement] = lab

    def show_main_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "main"
        self.calibration_active = False
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_pad_movements = set()

        err_box = toga.Box(style=Pack(direction=COLUMN, padding_bottom=8))
        ph.add(err_box)

        self.error_label = toga.Label("", style=Pack(color="#b91c1c"))
        err_box.add(self.error_label)

        self.retry_button = toga.Button(
            "Retry connection",
            on_press=self.retry_connection,
            style=Pack(padding_top=6, visibility=HIDDEN if not self.connection_failed else VISIBLE),
        )
        err_box.add(self.retry_button)

        top = toga.Box(style=Pack(direction=ROW, padding=10))
        ph.add(top)

        info = toga.Box(style=Pack(direction=COLUMN, flex=1))
        top.add(info)
        self.status_label = toga.Label("Connecting...", style=Pack(color="#6b7280", font_size=11))
        info.add(self.status_label)
        self.xy_label = toga.Label(
            "pitch=0.00° · roll=0.00°", style=Pack(padding_top=6, font_size=12)
        )
        info.add(self.xy_label)

        body = toga.Box(style=Pack(direction=COLUMN, flex=1))
        ph.add(body)

        pad_row = toga.Box(style=Pack(direction=ROW, alignment=TOP))
        body.add(pad_row)

        pad_host = toga.Box(style=Pack(padding=8))
        pad_row.add(pad_host)
        self.create_movement_pad(pad_host)

        com_box = toga.Box(style=Pack(direction=COLUMN, padding_left=16, padding_right=8))
        pad_row.add(com_box)
        com_box.add(toga.Label("COM power", style=Pack(font_weight="bold", color="#6b7280", padding_bottom=6)))

        inner = toga.Box(style=Pack(direction=COLUMN))
        com_box.add(inner)
        self.com_power_labels = {}
        self.com_name_labels = []
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            row = toga.Box(style=Pack(direction=ROW))
            inner.add(row)
            nl = toga.Label(cmd, style=Pack(flex=1, font_size=11, color="#9ca3af"))
            vl = toga.Label("0.00", style=Pack(font_size=11, font_weight="bold", width=48, text_align="right"))
            row.add(nl)
            row.add(vl)
            self.com_name_labels.append(nl)
            self.com_power_labels[cmd] = vl

        self.com_threshold_hint = toga.Label("", style=Pack(font_size=10, color="#9ca3af", padding_top=4))
        com_box.add(self.com_threshold_hint)

        body.add(toga.Box(style=Pack(flex=1)))

        self.keyboard_label = toga.Label("", style=Pack(color="#6b7280", font_size=11, padding=8))
        body.add(self.keyboard_label)

    def _on_calibrate_pressed(self, widget: Optional[toga.Widget] = None) -> None:
        if self.cortex is None or not self.cortex.is_websocket_connected():
            self.main_window.info_dialog(
                "Cortex",
                "Not connected to the Cortex WebSocket. "
                "Start EMOTIV Launcher and ensure Cortex is reachable, then try again.",
            )
            return
        self.show_calibration_view()

    def show_calibration_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "calibration"
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []
        self.com_pad_movements = set()

        ph.add(toga.Label("Calibration", style=Pack(font_size=22, font_weight="bold", padding_top=20, padding_bottom=10)))
        self.calibration_instruction_label = toga.Label(
            "Hold a neutral head position for 10 seconds.",
            style=Pack(color="#6b7280", padding_bottom=16, text_align="center"),
        )
        ph.add(self.calibration_instruction_label)

        self.timer_label = toga.Label("10", style=Pack(font_size=40, font_weight="bold", color="#14b8a6", padding_bottom=12))
        ph.add(self.timer_label)

        self.calibration_xy_label = toga.Label(
            "avg pitch=0.00° · avg roll=0.00°",
            style=Pack(color="#6b7280", padding_bottom=20),
        )
        ph.add(self.calibration_xy_label)

        cancel_row = toga.Box(style=Pack(direction=ROW, padding_top=8, padding_bottom=12))
        ph.add(cancel_row)
        cancel_row.add(toga.Box(style=Pack(flex=1)))
        cancel_row.add(
            toga.Button(
                "Cancel",
                on_press=lambda w: self.show_main_view(),
                style=_action_btn_style(),
            )
        )

    def show_calibration_review_view(self) -> None:
        self.current_view = "calibration_review"
        self.calibration_active = False
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        ph.add(toga.Label("Verify configuration", style=Pack(font_size=20, font_weight="bold", padding_top=16, padding_bottom=8)))
        self.review_xy_label = toga.Label(
            "pitch=0.00° · roll=0.00°", style=Pack(padding_bottom=6)
        )
        ph.add(self.review_xy_label)
        self.review_neutral_label = toga.Label(
            f"Neutral pitch={self.pending_neutral_x:.2f}° · roll={self.pending_neutral_y:.2f}°",
            style=Pack(color="#6b7280", padding_bottom=10),
        )
        ph.add(self.review_neutral_label)

        self.create_movement_pad(ph)

        row = toga.Box(style=Pack(direction=ROW, padding_top=16, padding_left=12, padding_right=12))
        ph.add(row)
        row.add(toga.Box(style=Pack(flex=1)))
        row.add(
            toga.Button(
                "Cancel",
                on_press=lambda w: self.show_main_view(),
                style=_action_btn_style(gap_after=True),
            )
        )
        row.add(
            toga.Button(
                "Retry",
                on_press=lambda w: self.show_calibration_view(),
                style=_action_btn_style(gap_after=True),
            )
        )
        row.add(toga.Button("Save", on_press=lambda w: self.save_calibration(), style=_action_btn_style()))

    def save_calibration(self, widget: Optional[toga.Widget] = None) -> None:
        self.config_data.neutral_x = self.pending_neutral_x
        self.config_data.neutral_y = self.pending_neutral_y
        save_config(self.config_data)
        self.show_main_view()

    def show_settings_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "settings"
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        screen = toga.Box(
            style=Pack(
                direction=COLUMN,
                flex=1,
                padding_top=16,
                padding_bottom=16,
                padding_left=16,
                padding_right=16,
            ),
        )

        screen.add(
            toga.Label(
                "Settings",
                style=Pack(font_size=22, font_weight="bold", padding_bottom=12),
            )
        )

        form = toga.Box(style=Pack(direction=COLUMN))
        screen.add(form)

        kb_sw = toga.Switch(
            "Keyboard presses",
            value=self.config_data.keyboard_enabled,
        )
        form.add(kb_sw)
        form.add(
            toga.Label(
                "Shortcut: Ctrl + Shift + K · or Ctrl + Alt + K if the first is in use",
                style=Pack(color="#6b7280", font_size=10, padding_bottom=12),
            )
        )
        debug_sw = toga.Switch(
            "Debug mode (update diagnostics)",
            value=self.config_data.debug_mode,
        )
        form.add(debug_sw)
        form.add(
            toga.Label(
                "When on, writes detailed logs during in-app update install (Python + updater script).",
                style=Pack(color="#6b7280", font_size=10, padding_bottom=12),
            )
        )

        tg_sw = toga.Switch("Single threshold for all movements", value=self.config_data.threshold_global)
        form.add(tg_sw)

        threshold_host = toga.Box(style=Pack(direction=COLUMN))
        form.add(threshold_host)

        global_row = toga.Box(style=Pack(direction=ROW, padding_top=6))
        global_row.add(toga.Label("Movement threshold", style=Pack(flex=1)))
        thr_global = toga.NumberInput(
            min=1,
            max=50,
            step=0.5,
            value=self.config_data.threshold,
            style=Pack(width=100),
        )
        global_row.add(thr_global)

        per_box = toga.Box(style=Pack(direction=COLUMN))
        per_inputs: dict[str, toga.NumberInput] = {}
        for movement in MOVEMENTS:
            row = toga.Box(style=Pack(direction=ROW, padding_top=4))
            row.add(
                toga.Label(
                    f"{MOVEMENTS[movement]['ui_name']} threshold ({MOVEMENTS[movement]['label']})",
                    style=Pack(flex=1),
                )
            )
            ni = toga.NumberInput(
                min=1,
                max=50,
                step=0.5,
                value=self.config_data.movement_thresholds[movement],
                style=Pack(width=100),
            )
            row.add(ni)
            per_box.add(row)
            per_inputs[movement] = ni

        def refresh_threshold_mode(*_: Any) -> None:
            threshold_host.clear()
            if tg_sw.value:
                threshold_host.add(global_row)
            else:
                threshold_host.add(per_box)

        tg_sw.on_change = lambda w: refresh_threshold_mode()
        refresh_threshold_mode()

        form.add(toga.Label("Mental command power threshold", style=Pack(padding_top=12)))
        com_row = toga.Box(style=Pack(direction=ROW))
        com_row.add(toga.Box(style=Pack(flex=1)))
        com_thr = toga.NumberInput(
            min=0,
            max=1,
            step=0.05,
            value=self.config_data.com_power_threshold,
            style=Pack(width=100),
        )
        com_row.add(com_thr)
        form.add(com_row)

        form.add(
            toga.Label(
                "Mental command keys (held while COM power is above threshold)",
                style=Pack(color="#6b7280", font_size=10, padding_top=12, padding_bottom=4),
            )
        )
        com_entries: dict[str, toga.TextInput] = {}
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            row = toga.Box(style=Pack(direction=ROW, padding_top=4))
            row.add(toga.Label(cmd, style=Pack(width=80)))
            te = toga.TextInput(
                value=str(self.config_data.com_key_bindings.get(cmd, "")),
                style=Pack(flex=1),
            )
            row.add(te)
            form.add(row)
            com_entries[cmd] = te

        def save_settings(widget: Optional[toga.Widget] = None) -> None:
            self.config_data.keyboard_enabled = bool(kb_sw.value)
            self.config_data.debug_mode = bool(debug_sw.value)
            self.config_data.threshold_global = bool(tg_sw.value)
            self.config_data.threshold = float(thr_global.value)
            for m, inp in per_inputs.items():
                self.config_data.movement_thresholds[m] = float(inp.value)
            self.config_data.com_power_threshold = float(com_thr.value)
            defaults = dict(DEFAULT_COM_KEY_BINDINGS)
            for cmd in COM_MAPPED_MENTAL_ACTIONS:
                raw = com_entries[cmd].value.strip()
                self.config_data.com_key_bindings[cmd] = raw if raw else defaults[cmd]
            save_config(self.config_data)
            self.show_main_view()

        screen.add(
            toga.Button(
                "Environment variables…",
                on_press=lambda w: self.show_env_settings_view(),
                style=Pack(padding_top=14),
            )
        )

        ver_box = toga.Box(style=Pack(direction=COLUMN, padding_top=10))
        screen.add(ver_box)
        ver_box.add(toga.Label(f"Version {get_app_version()}", style=Pack(color="#6b7280", font_size=10)))
        ver_box.add(toga.Button("Check for updates", on_press=self._on_check_for_updates))

        br = toga.Box(style=Pack(direction=ROW, padding_top=20, padding_bottom=12))
        screen.add(br)
        br.add(toga.Box(style=Pack(flex=1)))
        br.add(
            toga.Button(
                "Back",
                on_press=lambda w: self.show_main_view(),
                style=_action_btn_style(gap_after=True),
            )
        )
        br.add(toga.Button("Save", on_press=save_settings, style=_action_btn_style()))

        ph.add(screen)

    def show_env_settings_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "env_settings"
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        ph.add(toga.Label("Environment variables", style=Pack(font_size=22, font_weight="bold", padding_top=16, padding_bottom=8)))
        ph.add(
            toga.Label(
                f"Values are saved to {CONFIG_PATH.name} with your other settings. "
                "Saving reconnects Cortex with the updated connection.",
                style=Pack(color="#6b7280", font_size=10, text_align="center", padding_bottom=10),
            )
        )

        inner = toga.Box(style=Pack(direction=COLUMN, padding=8))
        scroll = toga.ScrollContainer(content=inner, style=Pack(flex=1, height=260), horizontal=False, vertical=True)
        ph.add(scroll)

        initial = app_env_form_values(self.config_data)
        env_inputs: dict[str, toga.TextInput] = {}
        for key in APP_ENV_UI_KEYS:
            row = toga.Box(style=Pack(direction=ROW, padding_top=6))
            row.add(toga.Label(key, style=Pack(width=180)))
            ti = toga.TextInput(value=initial[key], style=Pack(flex=1))
            row.add(ti)
            inner.add(row)
            env_inputs[key] = ti

        def save_env(widget: Optional[toga.Widget] = None) -> None:
            raw = {k: env_inputs[k].value.strip() for k in APP_ENV_UI_KEYS}
            try:
                int(raw["EMOTIV_DEBIT"])
            except ValueError:
                self.main_window.error_dialog("Invalid value", "EMOTIV_DEBIT must be an integer.")
                return
            apply_cortex_env_form_to_config(self.config_data, raw)
            save_config(self.config_data)
            self._restart_cortex_client(
                clear_error_ui=True,
                status_message="Reconnecting after env update...",
            )
            self.show_settings_view()

        br = toga.Box(style=Pack(direction=ROW, padding_top=16, padding_left=12, padding_right=12, padding_bottom=12))
        ph.add(br)
        br.add(toga.Box(style=Pack(flex=1)))
        br.add(
            toga.Button(
                "Back",
                on_press=lambda w: self.show_settings_view(),
                style=_action_btn_style(gap_after=True),
            )
        )
        br.add(toga.Button("Save", on_press=save_env, style=_action_btn_style()))

    def process_stream_message(self, msg: dict) -> None:
        has_input = False
        motion_detected: set[str] = set()
        com_movements: set[str] = set()
        com_actions: set[str] = set()

        if isinstance(msg.get("mot"), list):
            has_input = True
            mot = msg["mot"]
            if len(mot) >= 2:
                mot_cols = self.cortex.mot_cols if self.cortex is not None else None
                self.current_x, self.current_y = mot_to_tilt_xy(mot, mot_cols)
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
            self.com_pad_movements = set(com_movements)

        if not has_input:
            return

        self.sim_keyboard.sync(motion_detected, com_actions, self.config_data)

        if self.calibration_active:
            self.calibration_samples.append((self.current_x, self.current_y))

    def map_motion(self, x: float, y: float) -> set[str]:
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

    def map_mental_command(self, com: list) -> tuple[set[str], set[str]]:
        return mental_command_to_sets(
            com,
            power_threshold=float(self.config_data.com_power_threshold),
        )

    def get_active_neutral_x(self) -> Optional[float]:
        if self.current_view == "calibration_review" and self.pending_neutral_x is not None:
            return self.pending_neutral_x
        return self.config_data.neutral_x

    def get_active_neutral_y(self) -> Optional[float]:
        if self.current_view == "calibration_review" and self.pending_neutral_y is not None:
            return self.pending_neutral_y
        return self.config_data.neutral_y

    def draw_crosshair(self) -> None:
        if self.cross_canvas is None:
            return
        width = float(self._cross_w)
        height = float(self._cross_h)
        cx = width / 2
        cy = height / 2

        neutral_x = self.get_active_neutral_x()
        neutral_y = self.get_active_neutral_y()

        if neutral_x is None or neutral_y is None:
            dx = 0.0
            dy = 0.0
        else:
            dx = self.current_y - float(neutral_y)
            dy = self.current_x - float(neutral_x)

        scale = 7.0
        max_radius_x = width * 0.38
        max_radius_y = height * 0.38
        px = cx + max(-max_radius_x, min(max_radius_x, dx * scale))
        py = cy + max(-max_radius_y, min(max_radius_y, dy * scale))

        arm = max(16.0, min(width, height) / 22.0)
        dot_r = max(6.0, min(width, height) / 55.0)

        ctx = self.cross_canvas.context
        ctx.clear()
        with ctx.Stroke(cx - arm, cy, color=_CROSS, line_width=2) as h:
            h.line_to(cx + arm, cy)
        with ctx.Stroke(cx, cy - arm, color=_CROSS, line_width=2) as v:
            v.line_to(cx, cy + arm)
        with ctx.Fill(color=_DOT) as fill:
            fill.arc(x=px, y=py, radius=dot_r)

    def update_ui(self) -> None:
        self.draw_crosshair()

        xy_text = f"pitch={self.current_x:.2f}° · roll={self.current_y:.2f}°"
        if self.xy_label is not None:
            self.xy_label.text = xy_text
        if self.review_xy_label is not None:
            self.review_xy_label.text = xy_text

        if self.keyboard_label is not None:
            self.keyboard_label.text = (
                "Keyboard presses: on"
                if self.config_data.keyboard_enabled
                else "Keyboard presses: off"
            )

        if self.current_view == "main" and self.com_power_labels:
            thr = float(self.config_data.com_power_threshold)
            if self.com_threshold_hint is not None:
                self.com_threshold_hint.text = f"Activate if power ≥ {thr:.2f}"
            if len(self.com_name_labels) == len(COM_MAPPED_MENTAL_ACTIONS):
                for cmd, nl in zip(COM_MAPPED_MENTAL_ACTIONS, self.com_name_labels):
                    key = str(self.config_data.com_key_bindings.get(cmd, ""))
                    nl.text = f"{cmd} → {key}"
            for cmd, lab in self.com_power_labels.items():
                p = float(self.com_powers.get(cmd, 0.0))
                lab.text = f"{p:.2f}"
                lab.style.color = "#0f766e" if p >= thr else "#111827"

        motion_pad = self.map_motion(self.current_x, self.current_y)
        display_movements = motion_pad | self.com_pad_movements
        for movement, btn in self.movement_buttons.items():
            self._apply_movement_pad_style(btn, movement in display_movements)

        if self.calibration_active:
            if self.calibration_started_at is None:
                return
            elapsed = time.time() - self.calibration_started_at
            remaining = max(0, 10 - elapsed)

            if self.timer_label is not None:
                self.timer_label.text = str(int(remaining) + 1 if remaining > 0 else 0)

            if self.calibration_samples and self.calibration_xy_label is not None:
                avg_x = sum(x for x, _ in self.calibration_samples) / len(self.calibration_samples)
                avg_y = sum(y for _, y in self.calibration_samples) / len(self.calibration_samples)
                self.calibration_xy_label.text = (
                    f"avg pitch={avg_x:.2f}° · avg roll={avg_y:.2f}°"
                )

            if elapsed >= 10:
                if not self.calibration_samples:
                    self.main_window.error_dialog(
                        "Error",
                        "No motion data received during calibration.",
                    )
                    self.show_main_view()
                    return

                self.pending_neutral_x = (
                    sum(x for x, _ in self.calibration_samples) / len(self.calibration_samples)
                )
                self.pending_neutral_y = (
                    sum(y for _, y in self.calibration_samples) / len(self.calibration_samples)
                )
                self.show_calibration_review_view()

    def _tick_once(self) -> None:
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
                if self.status_label is not None:
                    self.status_label.text = status
                if _status_clears_connection_error_ui(status):
                    if self.error_label is not None:
                        self.error_label.text = ""
                    self.connection_failed = False
                    if self.retry_button is not None:
                        self.retry_button.style.visibility = HIDDEN
        except Empty:
            pass

        try:
            while True:
                error = self.error_queue.get_nowait()
                if self.error_label is not None:
                    self.error_label.text = error
                print(error)
                self.connection_failed = True
                if self.retry_button is not None:
                    self.retry_button.style.visibility = VISIBLE
        except Empty:
            pass

        self.update_ui()

    async def _tick_loop(self) -> None:
        try:
            while True:
                self._tick_once()
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            raise

    def _on_check_for_updates(self, widget: Optional[toga.Widget] = None) -> None:
        if self._update_check_in_progress:
            return
        if not getattr(sys, "frozen", False):
            self.main_window.info_dialog(
                "Check for updates",
                "In-app updates apply only to the packaged Windows executable.",
            )
            return
        if sys.platform != "win32":
            self.main_window.info_dialog(
                "Check for updates",
                "In-app updates are only supported on Windows.",
            )
            return
        if not get_update_manifest_url():
            self.main_window.info_dialog(
                "Check for updates",
                "Updates are not configured for this build.",
            )
            return
        self._update_check_in_progress = True

        def work() -> None:
            try:
                url = get_update_manifest_url()
                is_newer, manifest, err = check_update_available(url)

                def finish() -> None:
                    self._update_check_finished(is_newer, dict(manifest), err)

                self.invoke_later(finish)
            except Exception as e:

                def fail() -> None:
                    self._update_check_finished(False, {}, str(e))

                self.invoke_later(fail)

        threading.Thread(target=work, daemon=True).start()

    def _update_check_finished(self, is_newer: bool, manifest: dict, err: Optional[str]) -> None:
        self._update_check_in_progress = False
        if err:
            self.main_window.error_dialog("Check for updates", f"Update check failed:\n{err}")
            return
        if not is_newer:
            installed = get_app_version()
            published = manifest.get("version", "?")
            lines = [
                "You are up to date.",
                "",
                f"Installed: {installed}",
                f"Published in update feed: {published}",
            ]
            if published != "?" and semver_less(published, installed):
                lines.extend(
                    [
                        "",
                        "Your build is newer than the version listed in the update feed. "
                        "The manifest may not have been updated for this release yet.",
                    ]
                )
            self.main_window.info_dialog("Check for updates", "\n".join(lines))
            return
        latest = manifest["version"]

        def on_confirm(window: toga.Window, result: bool, **kwargs: Any) -> None:
            if not result:
                return
            self._update_check_in_progress = True

            def download_work() -> None:
                try:
                    staged = download_and_verify(manifest)
                    apply_staged_update(staged, debug=bool(self.config_data.debug_mode))

                    def ok() -> None:
                        self._update_install_queued_exit()

                    self.invoke_later(ok)
                except Exception as e:

                    def bad() -> None:
                        self._update_download_failed(str(e))

                    self.invoke_later(bad)

            threading.Thread(target=download_work, daemon=True).start()

        self.main_window.question_dialog(
            "Check for updates",
            f"Version {latest} is available (you have {get_app_version()}).\n\n"
            "Download and install now? The app will close and restart.",
            on_result=on_confirm,
        )

    def _update_download_failed(self, msg: str) -> None:
        self._update_check_in_progress = False
        self.main_window.error_dialog(
            "Check for updates",
            f"Download or install failed:\n{msg}",
        )

    def _update_install_queued_exit(self) -> None:
        self._update_check_in_progress = False
        self.main_window.info_dialog(
            "Check for updates",
            "The update is ready. This window will close and the app will restart automatically.",
            on_result=lambda w, r, **k: os._exit(0),
        )

    def _shutdown_resources(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.sim_keyboard.release_all(self.config_data)
        if self.cortex is not None:
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
                user32.PostThreadMessageW(wintypes.DWORD(tid), WM_QUIT, 0, 0)
            self._hotkey_thread.join(timeout=3.0)

        self._hotkey_thread = None
        self._hotkey_win32_registered.clear()

        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass


def run_app() -> None:
    EmotivBridgeApp().main_loop()
