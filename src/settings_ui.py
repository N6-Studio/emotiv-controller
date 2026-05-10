"""Settings / environment-variable tab layouts (wiring stays on ``EmotivBridgeApp``)."""

from __future__ import annotations

from typing import Any, Callable, Optional

import toga
from bridge_core import (
    APP_ENV_UI_KEYS,
    COM_MAPPED_MENTAL_ACTIONS,
    CONFIG_PATH,
    KEYBOARD_KEY_MODE_HOLD,
    KEYBOARD_KEY_MODE_PRESS,
    KEYBOARD_KEY_MODE_SPAM,
    MAX_KEYBOARD_REPEAT_INTERVAL_MS,
    MIN_KEYBOARD_REPEAT_INTERVAL_MS,
    MOVEMENTS,
    AppConfig,
    app_env_form_values,
    get_app_version,
)
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, ROW

from ui_theme import pack_action_button, pack_muted_small


_KEY_MODE_LABELS = {
    KEYBOARD_KEY_MODE_HOLD: "Hold",
    KEYBOARD_KEY_MODE_PRESS: "Press",
    KEYBOARD_KEY_MODE_SPAM: "Spam",
}
_KEY_MODE_FROM_LABEL = {v: k for k, v in _KEY_MODE_LABELS.items()}


def _key_mode_word(mode: str) -> str:
    return _KEY_MODE_LABELS.get(mode, "Hold")


def _mode_from_label(label: object) -> str:
    return _KEY_MODE_FROM_LABEL.get(str(label), KEYBOARD_KEY_MODE_HOLD)


def inherit_key_mode_option_label(global_mode: str) -> str:
    """First option in per-key mode selectors (matches saved global default)."""
    return f"(Default: {_key_mode_word(global_mode)})"


def is_inherit_key_mode_selection(value: object) -> bool:
    s = str(value or "").strip()
    return s.startswith("(Default")


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

    kb_row = toga.Box(style=Pack(direction=ROW, alignment=CENTER))
    kb_motion_sw = toga.Switch(
        "Motion keyboard",
        value=config_data.keyboard_enabled,
    )
    kb_mental_sw = toga.Switch(
        "Mental keyboard",
        value=config_data.keyboard_com_enabled,
    )
    kb_row.add(kb_motion_sw)
    kb_row.add(kb_mental_sw)
    box.add(kb_row)
    box.add(
        toga.Label(
            "Shortcut: Ctrl + Shift + K · or Ctrl + Alt + K if the first is in use "
            "(turns both motion and mental keyboard output on or off together). "
            "You can turn Motion and Mental on or off independently here.",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    mode_sel_pack = Pack(width=168)
    interval_input_pack = Pack(width=90)
    motion_mode_global = toga.Selection(
        items=["Hold", "Press", "Spam"],
        value=_key_mode_word(config_data.keyboard_motion_key_mode),
        style=mode_sel_pack,
    )
    motion_mode_row = toga.Box(style=Pack(direction=ROW, padding_top=8))
    motion_mode_row.add(
        toga.Label(
            "Default motion key behavior",
            style=Pack(flex=1),
        )
    )
    motion_mode_row.add(motion_mode_global)
    box.add(motion_mode_row)
    box.add(
        toga.Label(
            "Hold: keep key down while leaning. "
            "Press: tap once each time a direction activates. "
            "Spam: re-tap at the configured interval while above threshold.",
            style=pack_muted_small(padding_bottom=8),
        )
    )

    motion_repeat_default_row = toga.Box(style=Pack(direction=ROW, padding_top=4))
    motion_repeat_default_row.add(
        toga.Label(
            "Spam interval (ms)",
            style=Pack(flex=1),
        )
    )
    motion_repeat_default = toga.NumberInput(
        min=MIN_KEYBOARD_REPEAT_INTERVAL_MS,
        max=MAX_KEYBOARD_REPEAT_INTERVAL_MS,
        step=10,
        value=int(config_data.keyboard_motion_repeat_interval_ms),
        style=interval_input_pack,
    )
    motion_repeat_default_row.add(motion_repeat_default)
    box.add(motion_repeat_default_row)
    box.add(
        toga.Label(
            "Used for all motion keys whose behavior is Spam.",
            style=pack_muted_small(padding_bottom=8),
        )
    )

    box.add(
        toga.Label(
            "Motion keys (simulated keyboard)",
            style=pack_muted_small(padding_top=12, padding_bottom=4),
        )
    )
    motion_entries: dict[str, toga.TextInput] = {}
    motion_mode_per: dict[str, toga.Selection] = {}
    motion_inherit_label = inherit_key_mode_option_label(config_data.keyboard_motion_key_mode)
    for movement in MOVEMENTS:
        row = toga.Box(style=Pack(direction=ROW, padding_top=4))
        row.add(
            toga.Label(
                f"{MOVEMENTS[movement]['ui_name']} ({MOVEMENTS[movement]['label']} default)",
                style=Pack(flex=1),
            )
        )
        raw = config_data.key_bindings.get(movement)
        if not raw:
            raw = MOVEMENTS[movement]["default_key"]
        te = toga.TextInput(
            value=str(raw),
            style=Pack(flex=1),
        )
        row.add(te)
        ov = config_data.keyboard_motion_key_modes.get(movement)
        if ov in _KEY_MODE_LABELS:
            msel_val = _key_mode_word(ov)
        else:
            msel_val = motion_inherit_label
        msel = toga.Selection(
            items=[motion_inherit_label, "Hold", "Press", "Spam"],
            value=msel_val,
            style=mode_sel_pack,
        )
        motion_mode_per[movement] = msel
        row.add(msel)
        box.add(row)
        motion_entries[movement] = te

    def refresh_motion_per_inherit_labels(widget: Optional[toga.Widget] = None) -> None:
        mode = _mode_from_label(motion_mode_global.value)
        label = inherit_key_mode_option_label(mode)
        for sel in motion_mode_per.values():
            cur = sel.value
            using_default = is_inherit_key_mode_selection(cur)
            sel.items = [label, "Hold", "Press", "Spam"]
            sel.value = label if using_default else cur

    motion_mode_global.on_change = refresh_motion_per_inherit_labels

    box.add(
        toga.Label(
            "Single character or pynput key name (e.g. w, left). Leave blank to restore default.",
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

    hysteresis_row = toga.Box(style=Pack(direction=ROW, padding_top=12))
    hysteresis_row.add(
        toga.Label(
            "Keyboard motion hysteresis",
            style=Pack(flex=1),
        )
    )
    hysteresis_input = toga.NumberInput(
        min=0.0,
        max=1.0,
        step=0.05,
        value=float(config_data.keyboard_motion_hysteresis_frac),
        style=Pack(width=100),
    )
    hysteresis_row.add(hysteresis_input)
    box.add(hysteresis_row)
    box.add(
        toga.Label(
            "Fraction of each movement threshold used as deadband for simulated keys "
            "(0 = none, higher = fewer rapid press/release cycles near the edges).",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    box.add(toga.Box(style=Pack(flex=1)))

    def save_motion(widget: Optional[toga.Widget] = None) -> None:
        config_data.keyboard_enabled = bool(kb_motion_sw.value)
        config_data.keyboard_com_enabled = bool(kb_mental_sw.value)
        config_data.keyboard_motion_hysteresis_frac = float(hysteresis_input.value)
        for movement in MOVEMENTS:
            s = motion_entries[movement].value.strip()
            if not s:
                s = MOVEMENTS[movement]["default_key"]
            config_data.key_bindings[movement] = s
        config_data.keyboard_motion_key_mode = _mode_from_label(motion_mode_global.value)
        mm: dict[str, str] = {}
        for movement in MOVEMENTS:
            sel = motion_mode_per[movement].value
            if is_inherit_key_mode_selection(sel):
                continue
            mm[movement] = _mode_from_label(sel)
        config_data.keyboard_motion_key_modes = mm
        try:
            config_data.keyboard_motion_repeat_interval_ms = int(
                motion_repeat_default.value
            )
        except (TypeError, ValueError):
            pass
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

    box.add(
        toga.Label(
            "Turn mental keyboard output on or off with the Mental keyboard switch on the Motion tab.",
            style=pack_muted_small(padding_bottom=12),
        )
    )

    box.add(toga.Label("Mental command power threshold", style=Pack(padding_top=4)))
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

    mental_mode_sel_pack = Pack(width=168)
    mental_interval_input_pack = Pack(width=90)
    mental_mode_global = toga.Selection(
        items=["Hold", "Press", "Spam"],
        value=_key_mode_word(config_data.keyboard_mental_key_mode),
        style=mental_mode_sel_pack,
    )
    mental_mode_row = toga.Box(style=Pack(direction=ROW, padding_top=12))
    mental_mode_row.add(
        toga.Label(
            "Default mental key behavior",
            style=Pack(flex=1),
        )
    )
    mental_mode_row.add(mental_mode_global)
    box.add(mental_mode_row)
    box.add(
        toga.Label(
            "Hold: key stays down while the command is active above the power threshold. "
            "Press: tap once when the command crosses the threshold. "
            "Spam: re-tap at the configured interval while above threshold.",
            style=pack_muted_small(padding_bottom=8),
        )
    )

    mental_repeat_default_row = toga.Box(style=Pack(direction=ROW, padding_top=4))
    mental_repeat_default_row.add(
        toga.Label(
            "Spam interval (ms)",
            style=Pack(flex=1),
        )
    )
    mental_repeat_default = toga.NumberInput(
        min=MIN_KEYBOARD_REPEAT_INTERVAL_MS,
        max=MAX_KEYBOARD_REPEAT_INTERVAL_MS,
        step=10,
        value=int(config_data.keyboard_mental_repeat_interval_ms),
        style=mental_interval_input_pack,
    )
    mental_repeat_default_row.add(mental_repeat_default)
    box.add(mental_repeat_default_row)
    box.add(
        toga.Label(
            "Used for all mental command keys whose behavior is Spam.",
            style=pack_muted_small(padding_bottom=8),
        )
    )

    box.add(
        toga.Label(
            "Mental command keys",
            style=pack_muted_small(padding_top=12, padding_bottom=4),
        )
    )
    com_entries: dict[str, toga.TextInput] = {}
    mental_mode_per: dict[str, toga.Selection] = {}
    mental_inherit_label = inherit_key_mode_option_label(config_data.keyboard_mental_key_mode)
    for cmd in COM_MAPPED_MENTAL_ACTIONS:
        row = toga.Box(style=Pack(direction=ROW, padding_top=4))
        row.add(toga.Label(cmd, style=Pack(width=80)))
        te = toga.TextInput(
            value=str(config_data.com_key_bindings.get(cmd, "")),
            style=Pack(flex=1),
        )
        row.add(te)
        ov_m = config_data.keyboard_mental_key_modes.get(cmd)
        if ov_m in _KEY_MODE_LABELS:
            msel_val = _key_mode_word(ov_m)
        else:
            msel_val = mental_inherit_label
        msel = toga.Selection(
            items=[mental_inherit_label, "Hold", "Press", "Spam"],
            value=msel_val,
            style=mental_mode_sel_pack,
        )
        mental_mode_per[cmd] = msel
        row.add(msel)
        box.add(row)
        com_entries[cmd] = te

    def refresh_mental_per_inherit_labels(widget: Optional[toga.Widget] = None) -> None:
        mode = _mode_from_label(mental_mode_global.value)
        label = inherit_key_mode_option_label(mode)
        for sel in mental_mode_per.values():
            cur = sel.value
            using_default = is_inherit_key_mode_selection(cur)
            sel.items = [label, "Hold", "Press", "Spam"]
            sel.value = label if using_default else cur

    mental_mode_global.on_change = refresh_mental_per_inherit_labels

    box.add(toga.Box(style=Pack(flex=1)))

    def save_mental(widget: Optional[toga.Widget] = None) -> None:
        config_data.com_power_threshold = float(com_thr.value)
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            config_data.com_key_bindings[cmd] = com_entries[cmd].value.strip()
        config_data.keyboard_mental_key_mode = _mode_from_label(mental_mode_global.value)
        cm: dict[str, str] = {}
        for cmd in COM_MAPPED_MENTAL_ACTIONS:
            sel = mental_mode_per[cmd].value
            if is_inherit_key_mode_selection(sel):
                continue
            cm[cmd] = _mode_from_label(sel)
        config_data.keyboard_mental_key_modes = cm
        try:
            config_data.keyboard_mental_repeat_interval_ms = int(
                mental_repeat_default.value
            )
        except (TypeError, ValueError):
            pass
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
