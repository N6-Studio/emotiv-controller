"""Toga (BeeWare) UI for the EMOTIV movement bridge."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
import sys
import threading
import time
import warnings
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Optional

import toga
from toga.constants import Baseline
from toga.fonts import SYSTEM, Font
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
    COM_MAPPED_MENTAL_ACTIONS,
    CortexClient,
    MOVEMENTS,
    SimulatedKeyboard,
    _status_clears_connection_error_ui,
    apply_cortex_env_form_to_config,
    apply_staged_update,
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
    mot_acc_xyz,
    mot_to_motion_xy,
    resolved_movement_thresholds,
    reticle_offset_acc_to_normalized,
    stable_keyboard_motion_movements,
)
from update_service import semver_less

from queue_utils import drain_queue
from calibration_ui import (
    CalibrationReviewViewRefs,
    CalibrationViewRefs,
    MOTION_AXIS_YZ,
    build_calibration_review_action_row,
    build_calibration_review_content,
    build_calibration_screen,
    build_live_motion_readout_row,
    signed_value_color,
)
from settings_ui import (
    build_env_settings_scroll,
    build_general_tab,
    build_mental_tab,
    build_motion_tab,
    env_intro_labels,
)
from ui_theme import (
    pack_action_button,
    pack_com_header,
    pack_com_hint,
    pack_error,
    pack_muted_body,
    pack_muted_small,
    pack_section_title,
    pack_status_line,
)
from ui_views import AppView
from win32_hotkey import win32_hotkey_thread_main

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
# Mental-command key badges on the main view (drawn canvas chips — avoids WinForms
# disabled-button chrome and unreliable Label panel fills).
_COM_KEY_BADGE_CANVAS_W = 44
_COM_KEY_BADGE_CANVAS_H = 22
_COM_KEY_BADGE_FILL = "#e5e7eb"
_COM_KEY_BADGE_BORDER = "#cbd5e1"
_COM_KEY_BADGE_TEXT = "#0f172a"
_COM_KEY_BADGE_TEXT_MUTED = "#64748b"
_COM_KEY_BADGE_FONT = Font(SYSTEM, 11)
_DPAD_ARROW_GLYPHS: dict[str, str] = {
    "forward": "\u2191",
    "left": "\u2190",
    "backward": "\u2193",
    "right": "\u2192",
}


def _paint_com_key_badge(canvas: toga.Canvas, text: str, *, muted: bool) -> None:
    """Fill a fixed-size chip with light gray, stroke, and centered key text."""
    ctx = canvas.context
    ctx.clear()
    w = float(_COM_KEY_BADGE_CANVAS_W)
    h = float(_COM_KEY_BADGE_CANVAS_H)
    with ctx.Fill(color=_COM_KEY_BADGE_FILL) as bg:
        bg.rect(0.0, 0.0, w, h)
    with ctx.Stroke(color=_COM_KEY_BADGE_BORDER, line_width=1.0) as edge:
        edge.rect(0.5, 0.5, w - 1.0, h - 1.0)
    fg = _COM_KEY_BADGE_TEXT_MUTED if muted else _COM_KEY_BADGE_TEXT
    x = max(3.0, (w - 6.5 * max(len(text), 1)) / 2.0)
    with ctx.Fill(color=fg) as glyph:
        glyph.write_text(
            text,
            x,
            h / 2.0,
            font=_COM_KEY_BADGE_FONT,
            baseline=Baseline.MIDDLE,
        )


@dataclass
class MainViewRefs:
    """Widget handles for the main screen only (cleared when switching views)."""

    error_label: Optional[toga.Label] = None
    status_label: Optional[toga.Label] = None
    keyboard_label: Optional[toga.Label] = None
    retry_button: Optional[toga.Button] = None
    connection_activity: Optional[toga.ActivityIndicator] = None
    connection_busy_fallback: Optional[toga.Label] = None
    main_motion_readout: Optional[toga.Box] = None
    motion_readout_value_labels: list[toga.Label] = field(default_factory=list)
    com_power_labels: Optional[dict[str, toga.Label]] = None
    com_power_bars: Optional[dict[str, toga.ProgressBar]] = None
    com_key_badges: Optional[dict[str, toga.Canvas]] = None
    com_threshold_hint: Optional[toga.Label] = None


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
        self.current_acc_x = 0.0
        # Pad highlights from mental commands (last ``com`` frame); head motion is recomputed each UI tick.
        self.com_pad_movements: set[str] = set()
        # Last motion/COM applied to the simulated keyboard so split stream packets (mot-only vs com-only)
        # do not release keys that should stay held until the next update for that stream.
        self._keyboard_last_motion: set[str] = set()
        self._keyboard_last_com_actions: set[str] = set()
        # Schmitt state for motion→keyboard only (UI pad still uses instant thresholds).
        self._keyboard_motion_stable: set[str] = set()

        self.calibration_active = False
        self.calibration_started_at: Optional[float] = None
        self.calibration_samples: list[tuple[float, float, float]] = []
        self.pending_neutral_x: Optional[float] = None
        self.pending_neutral_y: Optional[float] = None

        self.current_view: Optional[AppView] = None
        self._main_connection_busy = False
        self._busy_fallback_tick = 0
        self.movement_buttons: dict[str, toga.Label] = {}
        self._movement_pad_square_labels: list[toga.Label] = []
        self._movement_pad_cell_size: int = 0
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self._com_key_badge_display: dict[str, str] = {}

        self.connection_failed = False
        self._update_check_in_progress = False

        self.cortex: Optional[CortexClient] = None
        self.hotkey_listener = None

        self._cross_w = 400
        self._cross_h = 160

        self.main_column: Optional[toga.Box] = None
        self.cross_canvas: Optional[toga.Canvas] = None
        self.panel_host: Optional[toga.Box] = None

        # Last Cortex / connection line (not keyboard hints) so we can restore after Settings clears widgets.
        self._last_cortex_status: str = "Connecting..."
        self._main_view: Optional[MainViewRefs] = None
        self._calibration_refs: Optional[CalibrationViewRefs] = None
        self._calibration_review_refs: Optional[CalibrationReviewViewRefs] = None
        self._crosshair_pad_row: Optional[toga.Box] = None
        self._crosshair_pad_row_cap_h: Optional[int] = None

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
        if self.current_view in (AppView.SETTINGS, AppView.ENV_SETTINGS):
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
        self.config_data.keyboard_com_enabled = self.config_data.keyboard_enabled
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

    def start_shortcut_listener(self) -> None:
        self.hotkey_listener = None
        self._hotkey_win32_registered.clear()

        if sys.platform != "win32":
            self._install_pynput_hotkey()
            return

        self._hotkey_thread = threading.Thread(
            target=lambda: win32_hotkey_thread_main(
                keyboard_shortcut_queue=self.keyboard_shortcut_queue,
                control_queue=self._hotkey_control_queue,
                registered_event=self._hotkey_win32_registered,
            ),
            name="win32-hotkey",
            daemon=True,
        )
        self._hotkey_thread.start()

    def _clear_panel_host(self) -> None:
        if self.panel_host is None:
            return
        self.panel_host.clear()
        self._calibration_refs = None
        self._calibration_review_refs = None
        self._main_view = None
        self.movement_buttons = {}
        self._movement_pad_square_labels = []
        self._movement_pad_cell_size = 0
        self._com_key_badge_display.clear()
        self._crosshair_pad_row = None
        self._crosshair_pad_row_cap_h = None

    def _restart_cortex_client(self, *, clear_error_ui: bool, status_message: str) -> None:
        if self.cortex is not None:
            try:
                self.cortex.stop()
            except Exception:
                pass

        if clear_error_ui:
            self.connection_failed = False
            mv = self._main_view
            if mv is not None:
                if mv.retry_button is not None:
                    mv.retry_button.style.visibility = HIDDEN
                if mv.error_label is not None:
                    mv.error_label.text = ""

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
        if self.current_view not in (
            AppView.MAIN,
            AppView.CALIBRATION,
            AppView.CALIBRATION_REVIEW,
        ):
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
        self._crosshair_pad_row = visual_row
        return visual_row

    def _sync_crosshair_pad_row_max_height(self) -> None:
        """Cap the crosshair + D-pad row to half the window height (Pack has no max_height)."""
        row = self._crosshair_pad_row
        if row is None:
            return
        if self.current_view in (AppView.SETTINGS, AppView.ENV_SETTINGS):
            return
        try:
            win_h = int(self.main_window.size.height)
        except Exception:
            return
        cap = max(win_h // 2, 1)
        if cap == self._crosshair_pad_row_cap_h:
            return
        self._crosshair_pad_row_cap_h = cap
        row.style.update(height=cap, flex=0)

    def _sync_live_motion_readout(self, value_labels: list[toga.Label]) -> None:
        if len(value_labels) != 3:
            return
        ax, ay, az = self.current_acc_x, self.current_x, self.current_y
        for val, lab in zip((ax, ay, az), value_labels, strict=True):
            lab.text = f"{val:.4f}"
            lab.style.update(color=signed_value_color(val))

    def _sync_main_connection_activity(self, status: str) -> None:
        mv = self._main_view
        if mv is None:
            return
        low = status.lower()
        busy = "connecting" in low or "reconnecting" in low
        self._main_connection_busy = busy
        ai = mv.connection_activity
        if ai is not None:
            if busy:
                ai.start()
            else:
                ai.stop()
        fb = mv.connection_busy_fallback
        if fb is not None:
            fb.style.visibility = VISIBLE if busy else HIDDEN
            if not busy:
                fb.text = ""
                self._busy_fallback_tick = 0

    def _build_main_status_strip(self, refs: MainViewRefs) -> toga.Box:
        err_box = toga.Box(style=Pack(direction=COLUMN, padding_bottom=8))
        refs.error_label = toga.Label("", style=pack_error())
        err_box.add(refs.error_label)

        top = toga.Box(style=Pack(direction=ROW, padding_left=10, padding_right=10, padding_top=10, alignment=TOP))
        err_box.add(top)

        info_left = toga.Box(style=Pack(direction=ROW, flex=1, alignment=CENTER))
        top.add(info_left)
        # WinForms backend does not implement ActivityIndicator.
        if sys.platform != "win32":
            refs.connection_activity = toga.ActivityIndicator(style=Pack(width=22, height=22, padding_right=8))
            info_left.add(refs.connection_activity)
        else:
            refs.connection_busy_fallback = toga.Label(
                "",
                style=pack_status_line(width=28, padding_right=6, visibility=HIDDEN),
            )
            info_left.add(refs.connection_busy_fallback)

        refs.status_label = toga.Label(
            self._last_cortex_status,
            style=pack_status_line(flex=1),
        )
        info_left.add(refs.status_label)

        refs.keyboard_label = toga.Label(
            "",
            style=pack_status_line(text_align=RIGHT),
        )
        top.add(refs.keyboard_label)

        refs.retry_button = toga.Button(
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
        err_box.add(refs.retry_button)

        refs.main_motion_readout, refs.motion_readout_value_labels = build_live_motion_readout_row()
        err_box.add(refs.main_motion_readout)
        return err_box

    def _build_com_power_section(self, refs: MainViewRefs) -> toga.Box:
        com_box = toga.Box(
            style=Pack(
                direction=COLUMN,
                padding_left=8,
                padding_right=8,
                padding_top=12,
                padding_bottom=4,
            )
        )
        com_header = toga.Box(style=Pack(direction=ROW, alignment=TOP, padding_bottom=6))
        com_box.add(com_header)
        com_header.add(
            toga.Label("COM power", style=pack_com_header()),
        )
        com_header.add(toga.Box(style=Pack(flex=1)))
        refs.com_threshold_hint = toga.Label(
            "",
            style=pack_com_hint(text_align=RIGHT),
        )
        com_header.add(refs.com_threshold_hint)

        powers_row = toga.Box(style=Pack(direction=ROW, alignment=CENTER))
        com_box.add(powers_row)
        refs.com_power_labels = {}
        refs.com_key_badges = {}
        refs.com_power_bars = {}
        self._com_key_badge_display.clear()
        n_cmds = len(COM_MAPPED_MENTAL_ACTIONS)
        for idx, cmd in enumerate(COM_MAPPED_MENTAL_ACTIONS):
            col = toga.Box(style=Pack(direction=COLUMN, flex=1, alignment=CENTER))
            powers_row.add(col)
            row_top = toga.Box(style=Pack(direction=ROW, alignment=CENTER))
            col.add(row_top)
            row_top.add(
                toga.Label(
                    cmd,
                    style=pack_com_hint(padding_right=6),
                )
            )
            chip = toga.Canvas(
                style=Pack(
                    width=_COM_KEY_BADGE_CANVAS_W,
                    height=_COM_KEY_BADGE_CANVAS_H,
                ),
            )
            row_top.add(chip)
            refs.com_key_badges[cmd] = chip
            bound_key = str(self.config_data.com_key_bindings.get(cmd, "")).strip()
            if bound_key:
                chip.style.visibility = VISIBLE
                self._com_key_badge_display[cmd] = bound_key
                _paint_com_key_badge(chip, bound_key, muted=False)
            else:
                chip.style.visibility = HIDDEN
            row_top.add(toga.Box(style=Pack(flex=1)))
            vl = toga.Label(
                "0.00",
                style=Pack(
                    font_size=11,
                    font_weight="bold",
                    padding_right=4,
                ),
            )
            row_top.add(vl)
            refs.com_power_labels[cmd] = vl

            bar = toga.ProgressBar(
                max=1.0,
                value=0.0,
                style=Pack(padding_top=4, padding_left=2, padding_right=2, width=80),
            )
            col.add(bar)
            refs.com_power_bars[cmd] = bar

            if idx < n_cmds - 1:
                powers_row.add(
                    toga.Divider(
                        direction=toga.Divider.VERTICAL,
                        style=Pack(padding_left=4, padding_right=4, height=52),
                    )
                )
        return com_box

    def _build_main_body(self, refs: MainViewRefs) -> toga.Box:
        body = toga.Box(style=Pack(direction=COLUMN, flex=1))
        body.add(self._build_crosshair_pad_row(flex=8))
        body.add(self._build_com_power_section(refs))
        body.add(toga.Box(style=Pack(flex=1)))
        return body

    def show_main_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = AppView.MAIN
        self.calibration_active = False
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host
        self.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
        self.com_pad_movements = set()

        refs = MainViewRefs()
        ph.add(self._build_main_status_strip(refs))
        ph.add(toga.Divider(style=Pack(padding_top=4, padding_bottom=8, padding_left=8, padding_right=8)))
        ph.add(self._build_main_body(refs))
        self._main_view = refs
        self._sync_main_connection_activity(self._last_cortex_status)

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
        if self.current_view != AppView.CALIBRATION:
            return
        cr = self._calibration_refs
        if self.calibration_active and self.calibration_started_at is not None:
            self.calibration_active = False
            self.calibration_started_at = None
            self.calibration_samples = []
            if cr is not None:
                cr.timer_label.text = "—"
                cr.xy_label.text = "Averages appear while the timer runs."
                cr.start_button.text = "Start 10 s timer"
            return
        self.calibration_active = True
        self.calibration_started_at = time.time()
        self.calibration_samples = []
        if cr is not None:
            cr.start_button.text = "Reset timer"

    def show_calibration_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = AppView.CALIBRATION
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
        self._calibration_refs = build_calibration_screen(
            cal_content,
            on_cancel=lambda w: self.show_main_view(),
            on_timer_press=self._on_calibration_timer_button,
        )

        self._sync_crosshair_visibility()

    def show_calibration_review_view(self) -> None:
        self.current_view = AppView.CALIBRATION_REVIEW
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
        assert self.pending_neutral_x is not None and self.pending_neutral_y is not None
        self._calibration_review_refs = build_calibration_review_content(
            review_content,
            neutral_acc_y=self.pending_neutral_x,
            neutral_acc_z=self.pending_neutral_y,
        )

        ph.add(
            build_calibration_review_action_row(
                on_cancel=lambda w: self.show_main_view(),
                on_retry=lambda w: self.show_calibration_view(),
                on_save=lambda w: self.save_calibration(),
            )
        )

        self._sync_crosshair_visibility()

    def save_calibration(self, widget: Optional[toga.Widget] = None) -> None:
        self.config_data.neutral_x = self.pending_neutral_x
        self.config_data.neutral_y = self.pending_neutral_y
        save_config(self.config_data)
        self.show_main_view()

    def show_settings_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = AppView.SETTINGS
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
                style=pack_section_title(flex=1),
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

        def persist_and_main() -> None:
            save_config(self.config_data)
            self.show_main_view()

        def on_save_debug(debug: bool) -> None:
            self.config_data.debug_mode = debug
            save_config(self.config_data)
            self.show_main_view()

        tabs = toga.OptionContainer(
            content=[
                (
                    "General",
                    build_general_tab(
                        config_data=self.config_data,
                        on_save_debug=on_save_debug,
                        on_open_env=self.show_env_settings_view,
                        on_check_updates=self._on_check_for_updates,
                    ),
                ),
                (
                    "Motion",
                    build_motion_tab(config_data=self.config_data, on_save=persist_and_main),
                ),
                (
                    "Mental",
                    build_mental_tab(config_data=self.config_data, on_save=persist_and_main),
                ),
            ],
            style=Pack(flex=1),
        )
        screen.add(tabs)

        ph.add(screen)

        self._sync_crosshair_visibility()

    def show_env_settings_view(self, widget: Optional[toga.Widget] = None) -> None:
        self.current_view = AppView.ENV_SETTINGS
        self._clear_panel_host()
        assert self.panel_host is not None
        ph = self.panel_host

        title, blurb = env_intro_labels()
        ph.add(title)
        ph.add(blurb)

        scroll, env_inputs = build_env_settings_scroll(config_data=self.config_data)
        ph.add(scroll)

        def save_env(widget: Optional[toga.Widget] = None) -> None:
            raw = {k: env_inputs[k].value.strip() for k in env_inputs}
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
                style=pack_action_button(gap_after=True),
            )
        )
        br.add(toga.Button("Save", on_press=save_env, style=pack_action_button()))

        self._sync_crosshair_visibility()

    def process_stream_message(self, msg: dict) -> None:
        has_input = False
        com_movements: set[str] = set()

        if isinstance(msg.get("mot"), list):
            has_input = True
            mot = msg["mot"]
            if len(mot) >= 2:
                mot_cols = self.cortex.mot_cols if self.cortex is not None else None
                triple = mot_acc_xyz(mot, mot_cols)
                self.current_acc_x = triple[0] if triple is not None else 0.0
                px, py = mot_to_motion_xy(mot, mot_cols)
                self.current_x = px
                self.current_y = py
                nx = self.get_active_neutral_x()
                ny = self.get_active_neutral_y()
                if nx is None or ny is None:
                    self._keyboard_motion_stable = set()
                else:
                    cfg = self.config_data
                    self._keyboard_motion_stable = stable_keyboard_motion_movements(
                        x=self.current_x,
                        y=self.current_y,
                        neutral_x=float(nx),
                        neutral_y=float(ny),
                        prev=self._keyboard_motion_stable,
                        threshold_global=cfg.threshold_global,
                        threshold=float(cfg.threshold),
                        movement_thresholds=cfg.movement_thresholds,
                        hysteresis_frac=float(cfg.keyboard_motion_hysteresis_frac),
                    )
                self._keyboard_last_motion = set(self._keyboard_motion_stable)

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
            self._keyboard_last_com_actions = set(ca)
            self.com_pad_movements = set(com_movements)

        if not has_input:
            return

        self.sim_keyboard.sync(
            self._keyboard_last_motion,
            self._keyboard_last_com_actions,
            self.config_data,
        )

        if self.calibration_active:
            self.calibration_samples.append(
                (self.current_acc_x, self.current_x, self.current_y)
            )

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
        enabled = frozenset(
            a
            for a in COM_MAPPED_MENTAL_ACTIONS
            if str(self.config_data.com_key_bindings.get(a, "")).strip()
        )
        return mental_command_to_sets(
            com,
            power_threshold=float(self.config_data.com_power_threshold),
            enabled_actions=enabled,
        )

    def get_active_neutral_x(self) -> Optional[float]:
        if self.current_view == AppView.CALIBRATION_REVIEW and self.pending_neutral_x is not None:
            return self.pending_neutral_x
        return self.config_data.neutral_x

    def get_active_neutral_y(self) -> Optional[float]:
        if self.current_view == AppView.CALIBRATION_REVIEW and self.pending_neutral_y is not None:
            return self.pending_neutral_y
        return self.config_data.neutral_y

    def draw_crosshair(self) -> None:
        if self.cross_canvas is None:
            return
        if getattr(self.cross_canvas, "parent", None) is None:
            return
        if self.current_view in (AppView.SETTINGS, AppView.ENV_SETTINGS):
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
            # No calibration yet: show dot from absolute ACC (reference neutral at origin),
            # but keep neutral-relative UI (threshold bands) hidden.
            dx_acc_y = self.current_x
            dy_acc_z = self.current_y
            hx, hy = reticle_offset_acc_to_normalized(dx_acc_y, dy_acc_z, 0.0, 0.0)
        else:
            nx, ny = float(neutral_x), float(neutral_y)
            dx_acc_y = self.current_x - nx
            dy_acc_z = self.current_y - ny
            hx, hy = reticle_offset_acc_to_normalized(dx_acc_y, dy_acc_z, nx, ny)

        # Map ACC Y (forward/back) to **vertical** (forward = up); ACC Z (left/right) to **horizontal**.
        px = cx + max(-aim_half, min(aim_half, hy * aim_half))
        py = cy + max(-aim_half, min(aim_half, hx * aim_half))

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
            hy_fwd, _ = reticle_offset_acc_to_normalized(-t_fwd, 0.0, nx, ny)
            hy_back, _ = reticle_offset_acc_to_normalized(t_back, 0.0, nx, ny)
            _, hx_left = reticle_offset_acc_to_normalized(0.0, -t_left, nx, ny)
            _, hx_right = reticle_offset_acc_to_normalized(0.0, t_right, nx, ny)
            with ctx.Fill(color=_AIM_ACTIVATION_FILL) as band:
                x1 = cx + hx_left * aim_half
                w_left = x1 - left
                if w_left > 0:
                    band.rect(left, top, w_left, bottom - top)
                x0 = cx + hx_right * aim_half
                w_right = right - x0
                if w_right > 0:
                    band.rect(x0, top, w_right, bottom - top)
                y1 = cy + hy_fwd * aim_half
                h_top = y1 - top
                if h_top > 0:
                    band.rect(left, top, right - left, h_top)
                y0 = cy + hy_back * aim_half
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
        self._update_ui_crosshair_and_pad_layout()
        self._update_ui_motion_readouts_and_review_line()
        mv = self._main_view
        self._update_ui_main_dashboard(mv)
        self._update_ui_calibration_timer()

    def _update_ui_crosshair_and_pad_layout(self) -> None:
        self._sync_crosshair_pad_row_max_height()
        self.draw_crosshair()
        self._sync_movement_pad_cell_layout()
        motion_pad = self.map_motion(self.current_x, self.current_y)
        display_movements = motion_pad | self.com_pad_movements
        for movement, btn in self.movement_buttons.items():
            btn.text = self._movement_pad_label_text(movement)
            self._apply_movement_pad_style(btn, movement in display_movements)

    def _update_ui_motion_readouts_and_review_line(self) -> None:
        mv = self._main_view
        if mv is not None and mv.main_motion_readout is not None:
            self._sync_live_motion_readout(mv.motion_readout_value_labels)
        cr = self._calibration_refs
        if cr is not None:
            self._sync_live_motion_readout(cr.motion_value_labels)
        rr = self._calibration_review_refs
        if rr is not None:
            ay_l, az_l = MOTION_AXIS_YZ
            rr.xy_label.text = f"{ay_l}={self.current_x:.4f} · {az_l}={self.current_y:.4f}"

    def _tick_win32_connection_busy_fallback(self, mv: Optional[MainViewRefs]) -> None:
        fb = mv.connection_busy_fallback if mv else None
        if fb is None or not self._main_connection_busy:
            return
        self._busy_fallback_tick += 1
        fb.text = ("", ".", "..", "...")[self._busy_fallback_tick % 4]

    def _update_ui_main_dashboard(self, mv: Optional[MainViewRefs]) -> None:
        if mv is not None and mv.keyboard_label is not None:
            mv.keyboard_label.text = (
                "Keyboard presses: on"
                if self.config_data.keyboard_enabled
                else "Keyboard presses: off"
            )

        self._tick_win32_connection_busy_fallback(mv)

        if self.current_view == AppView.MAIN and mv is not None and mv.com_power_labels:
            thr = float(self.config_data.com_power_threshold)
            if mv.com_threshold_hint is not None:
                mv.com_threshold_hint.text = f"Activate after ≥ {thr:.2f}"
            if mv.com_key_badges is not None:
                for cmd in COM_MAPPED_MENTAL_ACTIONS:
                    key = str(self.config_data.com_key_bindings.get(cmd, "")).strip()
                    chip = mv.com_key_badges.get(cmd)
                    if chip is None:
                        continue
                    if not key:
                        chip.style.visibility = HIDDEN
                        self._com_key_badge_display.pop(cmd, None)
                        continue
                    chip.style.visibility = VISIBLE
                    if self._com_key_badge_display.get(cmd) == key:
                        continue
                    self._com_key_badge_display[cmd] = key
                    _paint_com_key_badge(chip, key, muted=False)
            bars = mv.com_power_bars or {}
            for cmd, lab in mv.com_power_labels.items():
                p = float(self.com_powers.get(cmd, 0.0))
                lab.text = f"{p:.2f}"
                pb = bars.get(cmd)
                if pb is not None:
                    pb.value = min(1.0, max(0.0, p))
                bound = bool(str(self.config_data.com_key_bindings.get(cmd, "")).strip())
                if not bound:
                    lab.style.color = "#d1d5db"
                else:
                    lab.style.color = "#0f766e" if p >= thr else "#111827"

    def _update_ui_calibration_timer(self) -> None:
        cr = self._calibration_refs
        if not self.calibration_active or self.calibration_started_at is None or cr is None:
            return
        elapsed = time.time() - self.calibration_started_at
        remaining = max(0, 10 - elapsed)

        cr.timer_label.text = str(int(remaining) + 1 if remaining > 0 else 0)

        if self.calibration_samples:
            n = len(self.calibration_samples)
            avg_ax = sum(t[0] for t in self.calibration_samples) / n
            avg_ay = sum(t[1] for t in self.calibration_samples) / n
            avg_az = sum(t[2] for t in self.calibration_samples) / n
            cr.xy_label.text = (
                f"avg ACC X={avg_ax:.4f} · ACC Y={avg_ay:.4f} · ACC Z={avg_az:.4f}"
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
                sum(t[1] for t in self.calibration_samples) / len(self.calibration_samples)
            )
            self.pending_neutral_y = (
                sum(t[2] for t in self.calibration_samples) / len(self.calibration_samples)
            )
            self.show_calibration_review_view()

    def _tick_once(self) -> None:
        drain_queue(self.stream_queue, self.process_stream_message)

        def handle_hotkey_ctrl(cmd: str) -> None:
            if cmd == "fallback_pynput":
                self._install_pynput_hotkey()
            elif cmd == "hint_ctrl_alt_k":
                self.status_queue.put(
                    "Keyboard shortcut is Ctrl+Alt+K (Ctrl+Shift+K is reserved by another app)."
                )

        drain_queue(self._hotkey_control_queue, handle_hotkey_ctrl)
        drain_queue(self.keyboard_shortcut_queue, lambda _: self._toggle_keyboard_via_shortcut())

        def handle_status(status: str) -> None:
            if not status.startswith("Keyboard presses ") and not status.startswith(
                "Keyboard shortcut is "
            ):
                self._last_cortex_status = status
            mv = self._main_view
            if mv is not None and mv.status_label is not None:
                mv.status_label.text = status
                self._sync_main_connection_activity(status)
            if _status_clears_connection_error_ui(status):
                self.connection_failed = False
                if mv is not None:
                    if mv.error_label is not None:
                        mv.error_label.text = ""
                    if mv.retry_button is not None:
                        mv.retry_button.style.visibility = HIDDEN

        drain_queue(self.status_queue, handle_status)

        def handle_error(error: str) -> None:
            mv = self._main_view
            if mv is not None:
                if mv.error_label is not None:
                    mv.error_label.text = error
                if mv.retry_button is not None:
                    mv.retry_button.style.visibility = VISIBLE
            print(error)
            self.connection_failed = True

        drain_queue(self.error_queue, handle_error)

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
        self._keyboard_last_motion.clear()
        self._keyboard_last_com_actions.clear()
        self._keyboard_motion_stable.clear()
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
