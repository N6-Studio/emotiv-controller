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
from travertino.size import at_least

warnings.filterwarnings(
    "ignore",
    message=r"'asyncio\.iscoroutinefunction' is deprecated.*",
    category=DeprecationWarning,
    module=r"toga\.handlers",
)
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, HIDDEN, NONE, RIGHT, ROW, TOP, VISIBLE

from bridge_core import (
    APP_ENV_UI_KEYS,
    AppConfig,
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
from core import (
    compute_motion_movements,
    mot_quaternion,
    mot_to_tilt_xy,
    resolved_movement_thresholds,
    reticle_offset_deg_to_normalized,
)
from update_service import semver_less

# ``current_x`` / ``current_y`` are always quaternion-derived pitch° / roll° (forward-back / left-right).
_TILT_READOUT_AXES: tuple[str, str] = ("pitch", "roll")


def _stream_index_for_wxyz(cfg: AppConfig) -> tuple[int, int, int, int]:
    """Indices into Cortex ``(Q0,Q1,Q2,Q3)`` that populate Hamilton ``(w,x,y,z)``."""
    return (
        cfg.quaternion_map_w,
        cfg.quaternion_map_x,
        cfg.quaternion_map_y,
        cfg.quaternion_map_z,
    )


# Separator between quaternion segments (main readout row).
_MAIN_QUAT_SEP = " · "
_MOTION_VALUE_POS_COLOR = "#16a34a"
_MOTION_VALUE_NEG_COLOR = "#dc2626"
_MOTION_VALUE_ZERO_COLOR = "#6b7280"
_MOTION_QUAT_PLACEHOLDER_COLOR = "#9ca3af"


def _signed_value_color(value: float) -> str:
    """Green / red / gray for positive / negative / zero (used for live numeric readouts)."""
    if value > 0.0:
        return _MOTION_VALUE_POS_COLOR
    if value < 0.0:
        return _MOTION_VALUE_NEG_COLOR
    return _MOTION_VALUE_ZERO_COLOR


_CROSS = "#6b7280"
_DOT = "#14b8a6"
_DOT_BORDER = "#64748b"
_AIM_FRAME = "#52525b"
_AIM_ACTIVATION_FILL = "rgba(13, 148, 136, 0.12)"
_AIM_BOX_FRACTION = 0.46
# Minimum side length (px) for the main-view crosshair canvas with flex=1 (intrinsic hint).
_MAIN_CROSSHAIR_MIN = 280
# Non-arrow D-pad cells use a fully transparent background (Toga/Travertino rgba).
_DPAD_CELL_TRANSPARENT = "rgba(0, 0, 0, 0)"
_DPAD_ARROW_IDLE_BG = "#e5e7eb"
_DPAD_ARROW_GLYPHS: dict[str, str] = {
    "forward": "\u2191",
    "left": "\u2190",
    "backward": "\u2193",
    "right": "\u2192",
}


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
        self._last_quat: Optional[tuple[float, float, float, float]] = None
        # Pad highlights from mental commands (last ``com`` frame); head motion is recomputed each UI tick.
        self.com_pad_movements: set[str] = set()

        self.calibration_active = False
        self.calibration_started_at: Optional[float] = None
        self.calibration_samples: list[tuple[float, float]] = []
        self.pending_neutral_x: Optional[float] = None
        self.pending_neutral_y: Optional[float] = None

        self.current_view: Optional[str] = None
        self.movement_buttons: dict[str, toga.Label] = {}
        self._movement_pad_square_labels: list[toga.Label] = []
        self._movement_pad_cell_size: int = 0
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_power_labels: Optional[dict[str, toga.Label]] = None
        self.com_key_labels: Optional[dict[str, toga.Label]] = None
        self.com_threshold_hint: Optional[toga.Label] = None

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
        # Last Cortex / connection line (not keyboard hints) so we can restore after Settings clears widgets.
        self._last_cortex_status: str = "Connecting..."
        self.main_motion_readout: Optional[toga.Box] = None
        self._motion_readout_value_labels: list[toga.Label] = []
        self.calibration_live_readout: Optional[toga.Box] = None
        self._calibration_motion_value_labels: list[toga.Label] = []
        self.keyboard_label: Optional[toga.Label] = None
        self.calibration_instruction_label: Optional[toga.Label] = None
        self.calibration_start_button: Optional[toga.Button] = None
        self.timer_label: Optional[toga.Label] = None
        self.calibration_xy_label: Optional[toga.Label] = None
        self.review_xy_label: Optional[toga.Label] = None
        self.review_neutral_label: Optional[toga.Label] = None

    def startup(self) -> None:
        self.main_window = toga.MainWindow(
            title="EMOTIV Movement",
            size=(680, 580),
            resizable=True,
        )
        self.main_window.on_close = self._on_window_close

        self.cross_canvas = toga.Canvas(
            style=Pack(flex=1),
            on_resize=self._on_cross_resize,
        )
        self.panel_host = toga.Box(style=Pack(direction=COLUMN, flex=1))

        self.main_column = toga.Box(
            children=[self.panel_host],
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
                self.retry_connection,
                text="Reconnect headset",
                group=toga.Group.FILE,
                order=15,
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
        """Track allocated size for drawing. Do not fix pixel size here — ``flex=1`` must keep growing on resize."""
        w, h = max(int(width), 1), max(int(height), 1)
        self._cross_w = w
        self._cross_h = h

    def _sync_crosshair_visibility(self) -> None:
        """Hide on Settings; main / calibration / review = same square aim + D-pad row layout."""
        if self.cross_canvas is None:
            return
        if self.current_view in ("settings", "env_settings"):
            self.cross_canvas.intrinsic.width = None
            self.cross_canvas.intrinsic.height = None
            self.cross_canvas.style.update(visibility=HIDDEN, height=0, width=0, flex=0)
            return
        self.cross_canvas.intrinsic.width = at_least(_MAIN_CROSSHAIR_MIN)
        self.cross_canvas.intrinsic.height = at_least(_MAIN_CROSSHAIR_MIN)
        # Clear width/height from the settings branch (0×0 + HIDDEN) so layout runs again.
        self.cross_canvas.style.update(
            visibility=VISIBLE,
            flex=1,
            width=NONE,
            height=NONE,
        )

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
        self.calibration_start_button = None
        self.timer_label = None
        self.calibration_xy_label = None
        self.review_xy_label = None
        self.review_neutral_label = None
        self.main_motion_readout = None
        self._motion_readout_value_labels = []
        self.calibration_live_readout = None
        self._calibration_motion_value_labels = []
        self.status_label = None
        self.error_label = None
        self.retry_button = None
        self.keyboard_label = None
        self.movement_buttons = {}
        self._movement_pad_square_labels = []
        self._movement_pad_cell_size = 0
        self.com_power_labels = None
        self.com_key_labels = None
        self.com_threshold_hint = None

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

    def _pad_arrow_style(self, *, active: bool) -> Pack:
        """Arrow tiles only: idle = neutral box; active = pressed / highlighted."""
        if active:
            return Pack(
                flex=0,
                width=40,
                height=40,
                padding=4,
                text_align=CENTER,
                background_color="#0d9488",
                color="#ffffff",
                font_weight="bold",
                font_size=12,
            )
        return Pack(
            flex=0,
            width=40,
            height=40,
            padding=4,
            text_align=CENTER,
            background_color=_DPAD_ARROW_IDLE_BG,
            color="#6b7280",
            font_weight="bold",
            font_size=12,
        )

    def _pad_empty_cell_style(self) -> Pack:
        return Pack(
            flex=0,
            width=40,
            height=40,
            padding=4,
            text_align=CENTER,
            background_color=_DPAD_CELL_TRANSPARENT,
        )

    def _movement_pad_label_text(self, movement: str) -> str:
        """Arrow + bound key for each direction (matches ``config_data.key_bindings``)."""
        glyph = _DPAD_ARROW_GLYPHS[movement]
        raw = self.config_data.key_bindings.get(movement)
        if not raw:
            raw = MOVEMENTS[movement]["default_key"]
        key = str(raw)
        if len(key) == 1:
            key = key.upper()
        return f"{glyph}\n{key}"

    def _apply_movement_pad_style(self, btn: toga.Label, active: bool) -> None:
        """Arrow tiles: idle background vs highlighted when direction is active (pressed)."""
        if active:
            btn.style.update(background_color="#0d9488", color="#ffffff")
        else:
            btn.style.update(background_color=_DPAD_ARROW_IDLE_BG, color="#6b7280")

    def _sync_movement_pad_cell_layout(self) -> None:
        """Size all D-pad cells as squares from the crosshair canvas bounds (same row as pad)."""
        if not self._movement_pad_square_labels:
            return
        if self.current_view not in ("main", "calibration", "calibration_review"):
            return
        w, h = int(self._cross_w), int(self._cross_h)
        if w < 12 or h < 12:
            return
        s = max(24, min(w, h) // 3)
        if s == self._movement_pad_cell_size:
            return
        self._movement_pad_cell_size = s
        for lab in self._movement_pad_square_labels:
            lab.style.update(width=s, height=s, flex=0)

    def _add_dpad_cell(self, row: toga.Box, text: str, style: Pack, movement: Optional[str] = None) -> toga.Label:
        slot = toga.Box(style=Pack(flex=1, direction=COLUMN, alignment=CENTER))
        row.add(slot)
        lab = toga.Label(text, style=style)
        slot.add(lab)
        self._movement_pad_square_labels.append(lab)
        if movement is not None:
            self.movement_buttons[movement] = lab
        return lab

    def create_movement_pad(self, parent: toga.Box) -> None:
        self._movement_pad_square_labels = []
        self._movement_pad_cell_size = 0
        self.movement_buttons = {}

        pad = toga.Box(style=Pack(direction=COLUMN, flex=1))
        parent.add(pad)

        grid = toga.Box(style=Pack(direction=COLUMN, flex=1))
        pad.add(grid)

        rows: list[toga.Box] = [toga.Box(style=Pack(direction=ROW, flex=1)) for _ in range(3)]
        for r in rows:
            grid.add(r)

        self.movement_buttons = {}

        self._add_dpad_cell(rows[0], "", self._pad_empty_cell_style())
        self._add_dpad_cell(
            rows[0],
            self._movement_pad_label_text("forward"),
            self._pad_arrow_style(active=False),
            "forward",
        )
        self._add_dpad_cell(rows[0], "", self._pad_empty_cell_style())

        self._add_dpad_cell(
            rows[1],
            self._movement_pad_label_text("left"),
            self._pad_arrow_style(active=False),
            "left",
        )
        self._add_dpad_cell(rows[1], "", self._pad_empty_cell_style())
        self._add_dpad_cell(
            rows[1],
            self._movement_pad_label_text("right"),
            self._pad_arrow_style(active=False),
            "right",
        )

        self._add_dpad_cell(rows[2], "", self._pad_empty_cell_style())
        self._add_dpad_cell(
            rows[2],
            self._movement_pad_label_text("backward"),
            self._pad_arrow_style(active=False),
            "backward",
        )
        self._add_dpad_cell(rows[2], "", self._pad_empty_cell_style())

    def _build_crosshair_pad_row(self, *, flex: int) -> toga.Box:
        """Crosshair canvas + spacer + D-pad (same layout as main; reuse on calibration screens)."""
        visual_row = toga.Box(
            style=Pack(
                direction=ROW,
                alignment=CENTER,
                flex=flex,
                padding_left=8,
                padding_right=8,
                padding_top=4,
            )
        )
        visual_row.add(self.cross_canvas)
        visual_row.add(toga.Box(style=Pack(width=12, flex=0)))
        pad_host = toga.Box(style=Pack(padding=8, flex=1))
        visual_row.add(pad_host)
        self.create_movement_pad(pad_host)
        return visual_row

    def _build_live_motion_readout_row(self, lx: str, ly: str) -> tuple[toga.Box, list[toga.Label]]:
        """Row of labels so pitch/roll and each quaternion can use its own color."""
        value_labels: list[toga.Label] = []
        pack_muted = Pack(font_size=12, color="#6b7280")

        row = toga.Box(
            style=Pack(
                direction=ROW,
                padding_top=6,
                padding_left=10,
                padding_right=10,
                alignment=TOP,
            ),
        )

        def add_muted(t: str) -> None:
            row.add(toga.Label(t, style=pack_muted))

        def add_deg_value(initial: str, *, sample: float) -> None:
            lab = toga.Label(
                initial,
                style=Pack(
                    font_size=12,
                    font_weight="bold",
                    color=_signed_value_color(sample),
                ),
            )
            row.add(lab)
            value_labels.append(lab)

        add_muted(f"{lx}=")
        add_deg_value("0.00", sample=0.0)
        add_muted("°· ")
        add_muted(f"{ly}=")
        add_deg_value("0.00", sample=0.0)
        add_muted("°")

        for i in range(4):
            add_muted(_MAIN_QUAT_SEP)
            lab = toga.Label(
                f"Q{i}=—",
                style=Pack(
                    font_size=12,
                    font_weight="bold",
                    color=_MOTION_QUAT_PLACEHOLDER_COLOR,
                ),
            )
            row.add(lab)
            value_labels.append(lab)

        return row, value_labels

    def _sync_live_motion_readout(self, value_labels: list[toga.Label]) -> None:
        if len(value_labels) != 6:
            return
        cx, cy = self.current_x, self.current_y
        value_labels[0].text = f"{cx:.2f}"
        value_labels[0].style.update(color=_signed_value_color(cx))
        value_labels[1].text = f"{cy:.2f}"
        value_labels[1].style.update(color=_signed_value_color(cy))
        q = self._last_quat
        for j in range(4):
            lab = value_labels[2 + j]
            if q is not None:
                v = q[j]
                lab.text = f"Q{j}={v:.4f}"
                lab.style.update(color=_signed_value_color(v))
            else:
                lab.text = f"Q{j}=—"
                lab.style.update(color=_MOTION_QUAT_PLACEHOLDER_COLOR)

    def show_main_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "main"
        self.calibration_active = False
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_pad_movements = set()
        self._last_quat = None

        err_box = toga.Box(style=Pack(direction=COLUMN, padding_bottom=8))
        ph.add(err_box)

        self.error_label = toga.Label("", style=Pack(color="#b91c1c"))
        err_box.add(self.error_label)

        top = toga.Box(style=Pack(direction=ROW, padding_left=10, padding_right=10, padding_top=10, alignment=TOP))
        err_box.add(top)

        info_left = toga.Box(style=Pack(direction=COLUMN, flex=1))
        top.add(info_left)
        self.status_label = toga.Label(
            self._last_cortex_status,
            style=Pack(color="#6b7280", font_size=11),
        )
        info_left.add(self.status_label)

        self.keyboard_label = toga.Label(
            "",
            style=Pack(color="#6b7280", font_size=11, text_align=RIGHT),
        )
        top.add(self.keyboard_label)

        self.retry_button = toga.Button(
            "Retry connection",
            on_press=self.retry_connection,
            style=Pack(
                padding_top=6,
                padding_left=10,
                padding_right=10,
                flex=1,
                visibility=HIDDEN if not self.connection_failed else VISIBLE,
            ),
        )
        err_box.add(self.retry_button)

        lx, ly = _TILT_READOUT_AXES
        self.main_motion_readout, self._motion_readout_value_labels = self._build_live_motion_readout_row(
            lx, ly
        )
        err_box.add(self.main_motion_readout)

        body = toga.Box(style=Pack(direction=COLUMN, flex=1))
        ph.add(body)

        # Most extra vertical space goes here so the crosshair and D-pad grow and COM sits below.
        body.add(self._build_crosshair_pad_row(flex=8))

        com_box = toga.Box(
            style=Pack(
                direction=COLUMN,
                padding_left=8,
                padding_right=8,
                padding_top=12,
                padding_bottom=4,
            )
        )
        body.add(com_box)

        com_header = toga.Box(style=Pack(direction=ROW, alignment=TOP, padding_bottom=6))
        com_box.add(com_header)
        com_header.add(
            toga.Label("COM power", style=Pack(font_weight="bold", color="#6b7280")),
        )
        com_header.add(toga.Box(style=Pack(flex=1)))
        self.com_threshold_hint = toga.Label(
            "",
            style=Pack(font_size=10, color="#9ca3af", text_align=RIGHT),
        )
        com_header.add(self.com_threshold_hint)

        powers_row = toga.Box(style=Pack(direction=ROW, alignment=TOP))
        com_box.add(powers_row)
        self.com_power_labels = {}
        self.com_key_labels = {}
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            col = toga.Box(style=Pack(direction=ROW, flex=1, alignment=CENTER))
            powers_row.add(col)
            col.add(
                toga.Label(
                    cmd,
                    style=Pack(font_size=10, color="#9ca3af", padding_right=4),
                )
            )
            kl = toga.Label(
                "",
                style=Pack(font_size=10, color="#9ca3af", padding_right=6),
            )
            col.add(kl)
            self.com_key_labels[cmd] = kl
            vl = toga.Label(
                "0.00",
                style=Pack(
                    font_size=11,
                    font_weight="bold",
                    text_align=RIGHT,
                    flex=1,
                ),
            )
            col.add(vl)
            self.com_power_labels[cmd] = vl

        body.add(toga.Box(style=Pack(flex=1)))

        self._sync_crosshair_visibility()

    def _on_calibrate_pressed(self, widget: Optional[toga.Widget] = None) -> None:
        if self.cortex is None or not self.cortex.is_websocket_connected():
            self.main_window.info_dialog(
                "Cortex",
                "Not connected to the Cortex WebSocket. "
                "Start EMOTIV Launcher and ensure Cortex is reachable, then try again.",
            )
            return
        self.show_calibration_view()

    def _on_calibration_timer_button(self, widget: Optional[toga.Widget] = None) -> None:
        if self.current_view != "calibration":
            return
        if self.calibration_active and self.calibration_started_at is not None:
            self.calibration_active = False
            self.calibration_started_at = None
            self.calibration_samples = []
            if self.timer_label is not None:
                self.timer_label.text = "—"
            if self.calibration_xy_label is not None:
                self.calibration_xy_label.text = "Averages appear while the timer runs."
            if self.calibration_start_button is not None:
                self.calibration_start_button.text = "Start 10 s timer"
            return
        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []
        if self.calibration_start_button is not None:
            self.calibration_start_button.text = "Reset timer"

    def show_calibration_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = "calibration"
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        ph.add(self._build_crosshair_pad_row(flex=1))

        self.calibration_active = False
        self.calibration_started_at = None
        self.calibration_samples = []
        self.com_pad_movements = set()

        cal_content = toga.Box(
            style=Pack(
                direction=COLUMN,
                alignment=CENTER,
                padding_top=8,
                padding_bottom=4,
                padding_left=16,
                padding_right=16,
            )
        )
        ph.add(cal_content)

        cal_content.add(
            toga.Label(
                "Calibration",
                style=Pack(
                    font_size=22,
                    font_weight="bold",
                    padding_bottom=4,
                    text_align=CENTER,
                ),
            )
        )
        self.calibration_instruction_label = toga.Label(
            "After you start, hold a comfortable neutral head pose for the full 10 seconds.",
            style=Pack(color="#6b7280", padding_bottom=4, text_align=CENTER),
        )
        cal_content.add(self.calibration_instruction_label)
        cal_content.add(
            toga.Label(
                "Neutral is set to the average position recorded during the timer.",
                style=Pack(color="#6b7280", font_size=12, padding_bottom=8, text_align=CENTER),
            )
        )

        self.calibration_start_button = toga.Button(
            "Start 10 s timer",
            on_press=self._on_calibration_timer_button,
            style=_action_btn_style(),
        )
        cal_content.add(self.calibration_start_button)

        self.timer_label = toga.Label(
            "—",
            style=Pack(
                font_size=40,
                font_weight="bold",
                color="#14b8a6",
                padding_bottom=6,
                text_align=CENTER,
            ),
        )
        cal_content.add(self.timer_label)

        ax, ay = _TILT_READOUT_AXES
        self.calibration_live_readout, self._calibration_motion_value_labels = (
            self._build_live_motion_readout_row(ax, ay)
        )
        cal_wrap = toga.Box(
            style=Pack(direction=ROW, alignment=CENTER, padding_bottom=6),
        )
        cal_wrap.add(toga.Box(style=Pack(flex=1)))
        cal_wrap.add(self.calibration_live_readout)
        cal_wrap.add(toga.Box(style=Pack(flex=1)))
        cal_content.add(cal_wrap)

        self.calibration_xy_label = toga.Label(
            "Averages appear while the timer runs.",
            style=Pack(color="#4b5563", font_size=14, padding_bottom=8, text_align=CENTER),
        )
        cal_content.add(self.calibration_xy_label)

        cancel_row = toga.Box(
            style=Pack(direction=ROW, alignment=CENTER, padding_top=8, padding_bottom=8)
        )
        ph.add(cancel_row)
        cancel_row.add(toga.Box(style=Pack(flex=1)))
        cancel_row.add(
            toga.Button(
                "Cancel",
                on_press=lambda w: self.show_main_view(),
                style=_action_btn_style(),
            )
        )
        cancel_row.add(toga.Box(style=Pack(flex=1)))

        self._sync_crosshair_visibility()

    def show_calibration_review_view(self) -> None:
        self.current_view = "calibration_review"
        self.calibration_active = False
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        ph.add(self._build_crosshair_pad_row(flex=1))

        review_content = toga.Box(
            style=Pack(
                direction=COLUMN,
                alignment=CENTER,
                padding_top=12,
                padding_left=16,
                padding_right=16,
            )
        )
        ph.add(review_content)
        review_content.add(
            toga.Label(
                "Verify configuration",
                style=Pack(font_size=20, font_weight="bold", padding_bottom=8, text_align=CENTER),
            )
        )
        rx, ry = _TILT_READOUT_AXES
        self.review_xy_label = toga.Label(
            f"{rx}=0.00° · {ry}=0.00°",
            style=Pack(padding_bottom=6, font_size=14, text_align=CENTER),
        )
        review_content.add(self.review_xy_label)
        self.review_neutral_label = toga.Label(
            f"Neutral {rx}={self.pending_neutral_x:.2f}° · {ry}={self.pending_neutral_y:.2f}°",
            style=Pack(color="#6b7280", padding_bottom=10, text_align=CENTER),
        )
        review_content.add(self.review_neutral_label)

        row = toga.Box(style=Pack(direction=ROW, alignment=CENTER, padding_top=16, padding_left=12, padding_right=12))
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
        row.add(toga.Box(style=Pack(flex=1)))

        self._sync_crosshair_visibility()

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

        title_row = toga.Box(style=Pack(direction=ROW, alignment=CENTER, padding_bottom=12))
        title_row.add(
            toga.Label(
                "Settings",
                style=Pack(font_size=22, font_weight="bold", flex=1),
            )
        )
        title_row.add(
            toga.Button(
                "Close",
                on_press=lambda w: self.show_main_view(),
                style=Pack(font_size=13, padding_left=12, padding_right=12, padding_top=6, padding_bottom=6),
            )
        )
        screen.add(title_row)

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
        kb_com_sw = toga.Switch(
            "Keyboard presses for mental commands",
            value=self.config_data.keyboard_com_enabled,
        )
        form.add(kb_com_sw)
        form.add(
            toga.Label(
                "Only applies when keyboard presses are on. Tilt keys are unchanged.",
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

        form.add(
            toga.Label(
                "Quaternion → Hamilton (w,x,y,z): choose which Cortex Q0–Q3 slot fills each component.",
                style=Pack(font_weight="bold", padding_top=4),
            )
        )
        form.add(
            toga.Label(
                "Each of w,x,y,z must use a different slot (0=Q0 … 3=Q3). Invalid presets reset to 0,1,2,3.",
                style=Pack(color="#6b7280", font_size=10, padding_bottom=8),
            )
        )

        quat_inputs: dict[str, toga.NumberInput] = {}
        for label, key in (
            ("w from stream index", "quaternion_map_w"),
            ("x from stream index", "quaternion_map_x"),
            ("y from stream index", "quaternion_map_y"),
            ("z from stream index", "quaternion_map_z"),
        ):
            row = toga.Box(style=Pack(direction=ROW, padding_top=4))
            row.add(toga.Label(label, style=Pack(flex=1)))
            ni = toga.NumberInput(
                min=0,
                max=3,
                step=1,
                value=float(getattr(self.config_data, key)),
                style=Pack(width=72),
            )
            row.add(ni)
            form.add(row)
            quat_inputs[key] = ni

        tg_sw = toga.Switch("Single threshold for all movements", value=self.config_data.threshold_global)
        form.add(tg_sw)

        threshold_host = toga.Box(style=Pack(direction=COLUMN))
        form.add(threshold_host)

        global_row = toga.Box(style=Pack(direction=ROW, padding_top=6))
        global_row.add(toga.Label("Movement threshold (degrees)", style=Pack(flex=1)))
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
                    f"{MOVEMENTS[movement]['ui_name']} threshold ({MOVEMENTS[movement]['label']}, degrees)",
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
            self.config_data.keyboard_com_enabled = bool(kb_com_sw.value)
            self.config_data.debug_mode = bool(debug_sw.value)
            for attr in (
                "quaternion_map_w",
                "quaternion_map_x",
                "quaternion_map_y",
                "quaternion_map_z",
            ):
                try:
                    v = int(round(float(quat_inputs[attr].value)))
                except (TypeError, ValueError):
                    v = 0
                setattr(self.config_data, attr, v)
            self.config_data._normalize_quaternion_map()
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

        self._sync_crosshair_visibility()

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

        self._sync_crosshair_visibility()

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
                self._last_quat = mot_quaternion(mot, mot_cols)
                qmap = _stream_index_for_wxyz(self.config_data)
                px, py = mot_to_tilt_xy(mot, mot_cols, stream_index_for_wxyz=qmap)
                self.current_x = px
                self.current_y = py
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
        if getattr(self.cross_canvas, "parent", None) is None:
            return
        if self.current_view in ("settings", "env_settings"):
            return
        width = float(self._cross_w)
        height = float(self._cross_h)
        cx = width / 2
        cy = height / 2
        # Inscribe the aim in a square so it stays 1:1 when the canvas is a wide flex cell.
        sq = min(width, height)

        neutral_x = self.get_active_neutral_x()
        neutral_y = self.get_active_neutral_y()

        aim_half = sq * _AIM_BOX_FRACTION
        side_len = 2.0 * aim_half

        if neutral_x is None or neutral_y is None:
            # No calibration yet: show dot from absolute tilt (reference neutral at origin),
            # but keep neutral-relative UI (threshold bands) hidden.
            dx_pitch = self.current_x
            dy_roll = self.current_y
            hx, hy = reticle_offset_deg_to_normalized(dx_pitch, dy_roll, 0.0, 0.0)
        else:
            nx, ny = float(neutral_x), float(neutral_y)
            dx_pitch = self.current_x - nx
            dy_roll = self.current_y - ny
            hx, hy = reticle_offset_deg_to_normalized(dx_pitch, dy_roll, nx, ny)

        px = cx + max(-aim_half, min(aim_half, hx * aim_half))
        py = cy + max(-aim_half, min(aim_half, hy * aim_half))

        ctx = self.cross_canvas.context
        ctx.clear()

        if neutral_x is not None and neutral_y is not None:
            t_fwd, t_back, t_left, t_right = resolved_movement_thresholds(
                threshold_global=bool(self.config_data.threshold_global),
                threshold=float(self.config_data.threshold),
                movement_thresholds=self.config_data.movement_thresholds,
            )
            nx, ny = float(neutral_x), float(neutral_y)
            left, top = cx - aim_half, cy - aim_half
            right, bottom = cx + aim_half, cy + aim_half
            hx_fwd, _ = reticle_offset_deg_to_normalized(-t_fwd, 0.0, nx, ny)
            hx_back, _ = reticle_offset_deg_to_normalized(t_back, 0.0, nx, ny)
            _, hy_left = reticle_offset_deg_to_normalized(0.0, -t_left, nx, ny)
            _, hy_right = reticle_offset_deg_to_normalized(0.0, t_right, nx, ny)
            with ctx.Fill(color=_AIM_ACTIVATION_FILL) as band:
                x1 = cx + hx_fwd * aim_half
                w_left = x1 - left
                if w_left > 0:
                    band.rect(left, top, w_left, bottom - top)
                x0 = cx + hx_back * aim_half
                w_right = right - x0
                if w_right > 0:
                    band.rect(x0, top, w_right, bottom - top)
                y1 = cy + hy_left * aim_half
                h_top = y1 - top
                if h_top > 0:
                    band.rect(left, top, right - left, h_top)
                y0 = cy + hy_right * aim_half
                h_bot = bottom - y0
                if h_bot > 0:
                    band.rect(left, y0, right - left, h_bot)

        with ctx.Stroke(color=_AIM_FRAME, line_width=1.0) as frame:
            frame.rect(cx - aim_half, cy - aim_half, side_len, side_len)

        arm = max(16.0, sq / 22.0)
        dot_r = max(6.0, sq / 55.0)
        side = 2.0 * dot_r
        half = side / 2.0
        qx = px - half
        qy = py - half

        with ctx.Stroke(cx - arm, cy, color=_CROSS, line_width=2) as h:
            h.line_to(cx + arm, cy)
        with ctx.Stroke(cx, cy - arm, color=_CROSS, line_width=2) as v:
            v.line_to(cx, cy + arm)
        with ctx.Fill(color=_DOT) as fill:
            fill.rect(qx, qy, side, side)
        with ctx.Stroke(color=_DOT_BORDER, line_width=1.0) as border:
            border.rect(qx, qy, side, side)

    def update_ui(self) -> None:
        self.draw_crosshair()
        self._sync_movement_pad_cell_layout()

        tx, ty = _TILT_READOUT_AXES
        xy_text = f"{tx}={self.current_x:.2f}° · {ty}={self.current_y:.2f}°"
        if self.main_motion_readout is not None:
            self._sync_live_motion_readout(self._motion_readout_value_labels)
        if self.calibration_live_readout is not None:
            self._sync_live_motion_readout(self._calibration_motion_value_labels)
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
                self.com_threshold_hint.text = f"Activate after ≥ {thr:.2f}"
            if self.com_key_labels is not None:
                for cmd in COM_MAPPED_MENTAL_ACTIONS:
                    key = str(self.config_data.com_key_bindings.get(cmd, ""))
                    kl = self.com_key_labels.get(cmd)
                    if kl is not None:
                        kl.text = f"\u2192 {key}" if key else ""
            for cmd, lab in self.com_power_labels.items():
                p = float(self.com_powers.get(cmd, 0.0))
                lab.text = f"{p:.2f}"
                lab.style.color = "#0f766e" if p >= thr else "#111827"

        motion_pad = self.map_motion(self.current_x, self.current_y)
        display_movements = motion_pad | self.com_pad_movements
        for movement, btn in self.movement_buttons.items():
            btn.text = self._movement_pad_label_text(movement)
            self._apply_movement_pad_style(btn, movement in display_movements)

        if self.calibration_active and self.calibration_started_at is not None:
            elapsed = time.time() - self.calibration_started_at
            remaining = max(0, 10 - elapsed)

            if self.timer_label is not None:
                self.timer_label.text = str(int(remaining) + 1 if remaining > 0 else 0)

            if self.calibration_samples and self.calibration_xy_label is not None:
                avg_x = sum(x for x, _ in self.calibration_samples) / len(self.calibration_samples)
                avg_y = sum(y for _, y in self.calibration_samples) / len(self.calibration_samples)
                axn, ayn = _TILT_READOUT_AXES
                self.calibration_xy_label.text = (
                    f"avg {axn}={avg_x:.2f}° · avg {ayn}={avg_y:.2f}°"
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
                if not status.startswith("Keyboard presses ") and not status.startswith(
                    "Keyboard shortcut is "
                ):
                    self._last_cortex_status = status
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
