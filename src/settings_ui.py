"""Settings / environment-variable tab layouts (wiring stays on ``EmotivBridgeApp``)."""

from __future__ import annotations

from typing import Any, Callable, Optional

import toga
from bridge_core import (
    APP_ENV_UI_KEYS,
    COM_MAPPED_MENTAL_ACTIONS,
    CONFIG_PATH,
    MOVEMENTS,
    AppConfig,
    app_env_form_values,
    get_app_version,
)
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from ui_theme import pack_action_button, pack_muted_small


def settings_tab_save_row(on_save: Callable[[Optional[toga.Widget]], None]) -> toga.Box:
    row = toga.Box(style=Pack(direction=ROW, padding_top=20, padding_bottom=12))
    row.add(toga.Box(style=Pack(flex=1)))
    row.add(toga.Button("Save", on_press=on_save, style=pack_action_button()))
    return row


def build_general_tab(
    *,
    config_data: AppConfig,
    on_save_debug: Callable[[bool], None],
    on_open_env: Callable[[Optional[toga.Widget]], None],
    on_check_updates: Callable[[Optional[toga.Widget]], None],
) -> toga.Box:
    box = toga.Box(
        style=Pack(direction=COLUMN, padding_top=12, padding_bottom=12, padding_left=12, padding_right=12, flex=1),
    )

    debug_sw = toga.Switch(
        "Debug mode (update diagnostics)",
        value=config_data.debug_mode,
    )
    box.add(debug_sw)
    box.add(
        toga.Label(
            "When on, writes detailed logs during in-app update install (Python + updater script).",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    box.add(
        toga.Button(
            "Environment variables…",
            on_press=on_open_env,
            style=Pack(padding_top=4),
        )
    )

    ver_box = toga.Box(style=Pack(direction=COLUMN, padding_top=14))
    box.add(ver_box)
    ver_box.add(toga.Label(f"Version {get_app_version()}", style=pack_muted_small()))
    ver_box.add(toga.Button("Check for updates", on_press=on_check_updates))

    box.add(toga.Box(style=Pack(flex=1)))

    def save_general(widget: Optional[toga.Widget] = None) -> None:
        on_save_debug(bool(debug_sw.value))

    box.add(settings_tab_save_row(save_general))
    return box


def build_motion_tab(
    *,
    config_data: AppConfig,
    on_save: Callable[[], None],
) -> toga.Box:
    box = toga.Box(
        style=Pack(direction=COLUMN, padding_top=12, padding_bottom=12, padding_left=12, padding_right=12, flex=1),
    )

    kb_sw = toga.Switch(
        "Keyboard presses",
        value=config_data.keyboard_enabled,
    )
    box.add(kb_sw)
    box.add(
        toga.Label(
            "Shortcut: Ctrl + Shift + K · or Ctrl + Alt + K if the first is in use",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    tg_sw = toga.Switch(
        "Single threshold for all movements",
        value=config_data.threshold_global,
    )
    box.add(tg_sw)

    threshold_host = toga.Box(style=Pack(direction=COLUMN))
    box.add(threshold_host)

    global_row = toga.Box(style=Pack(direction=ROW, padding_top=6))
    global_row.add(
        toga.Label(
            "Movement threshold (ACC units, ACCY/ACCZ)",
            style=Pack(flex=1),
        )
    )
    thr_global = toga.NumberInput(
        min=0.01,
        max=1.0,
        step=0.01,
        value=config_data.threshold,
        style=Pack(width=100),
    )
    global_row.add(thr_global)

    per_box = toga.Box(style=Pack(direction=COLUMN))
    per_inputs: dict[str, toga.NumberInput] = {}
    for movement in MOVEMENTS:
        row = toga.Box(style=Pack(direction=ROW, padding_top=4))
        row.add(
            toga.Label(
                f"{MOVEMENTS[movement]['ui_name']} threshold ({MOVEMENTS[movement]['label']}, ACC)",
                style=Pack(flex=1),
            )
        )
        ni = toga.NumberInput(
            min=0.01,
            max=1.0,
            step=0.01,
            value=config_data.movement_thresholds[movement],
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

    box.add(toga.Box(style=Pack(flex=1)))

    def save_motion(widget: Optional[toga.Widget] = None) -> None:
        config_data.keyboard_enabled = bool(kb_sw.value)
        config_data.threshold_global = bool(tg_sw.value)
        config_data.threshold = float(thr_global.value)
        for m, inp in per_inputs.items():
            config_data.movement_thresholds[m] = float(inp.value)
        on_save()

    box.add(settings_tab_save_row(save_motion))
    return box


def build_mental_tab(
    *,
    config_data: AppConfig,
    on_save: Callable[[], None],
) -> toga.Box:
    box = toga.Box(
        style=Pack(direction=COLUMN, padding_top=12, padding_bottom=12, padding_left=12, padding_right=12, flex=1),
    )

    kb_com_sw = toga.Switch(
        "Keyboard presses for mental commands",
        value=config_data.keyboard_com_enabled,
    )
    box.add(kb_com_sw)
    box.add(
        toga.Label(
            "Only applies when keyboard presses are on. Tilt keys are unchanged.",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    box.add(toga.Label("Mental command power threshold", style=Pack(padding_top=12)))
    com_row = toga.Box(style=Pack(direction=ROW))
    com_row.add(toga.Box(style=Pack(flex=1)))
    com_thr = toga.NumberInput(
        min=0,
        max=1,
        step=0.05,
        value=config_data.com_power_threshold,
        style=Pack(width=100),
    )
    com_row.add(com_thr)
    box.add(com_row)

    box.add(
        toga.Label(
            "Mental command keys (held while COM power is above threshold)",
            style=pack_muted_small(padding_top=12, padding_bottom=4),
        )
    )
    com_entries: dict[str, toga.TextInput] = {}
    for cmd in COM_MAPPED_MENTAL_ACTIONS:
        row = toga.Box(style=Pack(direction=ROW, padding_top=4))
        row.add(toga.Label(cmd, style=Pack(width=80)))
        te = toga.TextInput(
            value=str(config_data.com_key_bindings.get(cmd, "")),
            style=Pack(flex=1),
        )
        row.add(te)
        box.add(row)
        com_entries[cmd] = te

    box.add(toga.Box(style=Pack(flex=1)))

    def save_mental(widget: Optional[toga.Widget] = None) -> None:
        config_data.keyboard_com_enabled = bool(kb_com_sw.value)
        config_data.com_power_threshold = float(com_thr.value)
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            config_data.com_key_bindings[cmd] = com_entries[cmd].value.strip()
        on_save()

    box.add(settings_tab_save_row(save_mental))
    return box


def build_env_settings_scroll(
    *,
    config_data: AppConfig,
) -> tuple[toga.ScrollContainer, dict[str, toga.TextInput]]:
    inner = toga.Box(style=Pack(direction=COLUMN, padding=8))
    scroll = toga.ScrollContainer(content=inner, style=Pack(flex=1, height=260), horizontal=False, vertical=True)

    initial = app_env_form_values(config_data)
    env_inputs: dict[str, toga.TextInput] = {}
    for key in APP_ENV_UI_KEYS:
        row = toga.Box(style=Pack(direction=ROW, padding_top=6))
        row.add(toga.Label(key, style=Pack(width=180)))
        ti = toga.TextInput(value=initial[key], style=Pack(flex=1))
        row.add(ti)
        inner.add(row)
        env_inputs[key] = ti

    return scroll, env_inputs


def env_intro_labels() -> tuple[toga.Label, toga.Label]:
    title = toga.Label(
        "Environment variables",
        style=Pack(font_size=22, font_weight="bold", padding_top=16, padding_bottom=8),
    )
    blurb = toga.Label(
        f"Values are saved to {CONFIG_PATH.name} with your other settings. "
        "Saving reconnects Cortex with the updated connection.",
        style=pack_muted_small(text_align="center", padding_bottom=10),
    )
    return title, blurb
