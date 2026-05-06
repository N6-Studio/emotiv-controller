"""Calibration / review layouts (wiring stays on ``EmotivBridgeApp``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import toga
from toga.style import Pack
from toga.style.pack import CENTER, ROW, TOP

from ui_theme import (
    pack_action_button,
    pack_calibration_averages_line,
    pack_calibration_timer,
    pack_muted_body,
    pack_review_section_title,
    pack_section_title,
)

# ``motion`` uses Cortex ACC Y / ACC Z; ACC X is shown separately.
MOTION_AXIS_YZ: tuple[str, str] = ("ACC Y", "ACC Z")

_MOTION_VALUE_POS_COLOR = "#16a34a"
_MOTION_VALUE_NEG_COLOR = "#dc2626"
_MOTION_VALUE_ZERO_COLOR = "#6b7280"


def signed_value_color(value: float) -> str:
    """Green / red / gray for positive / negative / zero (live numeric readouts)."""
    if value > 0.0:
        return _MOTION_VALUE_POS_COLOR
    if value < 0.0:
        return _MOTION_VALUE_NEG_COLOR
    return _MOTION_VALUE_ZERO_COLOR


def build_live_motion_readout_row() -> tuple[toga.Box, list[toga.Label]]:
    """ACC X / Y / Z readout (movement uses Y and Z)."""
    value_labels: list[toga.Label] = []
    muted_caption = pack_muted_body(font_size=12)

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
        row.add(toga.Label(t, style=muted_caption))

    def add_acc_value(initial: str, *, sample: float) -> None:
        lab = toga.Label(
            initial,
            style=Pack(
                font_size=12,
                font_weight="bold",
                color=signed_value_color(sample),
            ),
        )
        row.add(lab)
        value_labels.append(lab)

    add_muted("ACC X=")
    add_acc_value("0.00", sample=0.0)
    add_muted(" · ACC Y=")
    add_acc_value("0.00", sample=0.0)
    add_muted(" · ACC Z=")
    add_acc_value("0.00", sample=0.0)

    return row, value_labels


@dataclass
class CalibrationViewRefs:
    """Widgets updated live during neutral calibration."""

    instruction_label: toga.Label
    start_button: toga.Button
    timer_label: toga.Label
    live_readout: toga.Box
    motion_value_labels: list[toga.Label]
    xy_label: toga.Label


@dataclass
class CalibrationReviewViewRefs:
    """Calibration verification screen widgets."""

    xy_label: toga.Label
    neutral_label: toga.Label


def build_calibration_screen(
    cal_content: toga.Box,
    *,
    on_cancel: Callable[[Optional[toga.Widget]], None],
    on_timer_press: Callable[[Optional[toga.Widget]], None],
) -> CalibrationViewRefs:
    """Assemble calibration-specific widgets (crosshair row is added separately)."""
    cal_content.add(
        toga.Label(
            "Calibration",
            style=pack_section_title(padding_bottom=4, text_align=CENTER),
        )
    )
    instruction_label = toga.Label(
        "After you start, hold a comfortable neutral head pose for the full 10 seconds.",
        style=pack_muted_body(padding_bottom=4, text_align=CENTER),
    )
    cal_content.add(instruction_label)
    cal_content.add(
        toga.Label(
            "Neutral is set to the average position recorded during the timer.",
            style=pack_muted_body(font_size=12, padding_bottom=8, text_align=CENTER),
        )
    )

    button_row = toga.Box(
        style=Pack(direction=ROW, alignment=CENTER, padding_bottom=8),
    )
    button_row.add(toga.Box(style=Pack(flex=1)))
    button_row.add(
        toga.Button(
            "Cancel",
            on_press=on_cancel,
            style=pack_action_button(gap_after=True),
        )
    )
    start_button = toga.Button(
        "Start 10 s timer",
        on_press=on_timer_press,
        style=pack_action_button(),
    )
    button_row.add(start_button)
    button_row.add(toga.Box(style=Pack(flex=1)))
    cal_content.add(button_row)

    timer_label = toga.Label(
        "—",
        style=pack_calibration_timer(padding_bottom=6, text_align=CENTER),
    )
    cal_content.add(timer_label)

    live_readout, motion_value_labels = build_live_motion_readout_row()
    cal_wrap = toga.Box(
        style=Pack(direction=ROW, alignment=CENTER, padding_bottom=6),
    )
    cal_wrap.add(toga.Box(style=Pack(flex=1)))
    cal_wrap.add(live_readout)
    cal_wrap.add(toga.Box(style=Pack(flex=1)))
    cal_content.add(cal_wrap)

    xy_label = toga.Label(
        "Averages appear while the timer runs.",
        style=pack_calibration_averages_line(padding_bottom=8, text_align=CENTER),
    )
    cal_content.add(xy_label)

    return CalibrationViewRefs(
        instruction_label=instruction_label,
        start_button=start_button,
        timer_label=timer_label,
        live_readout=live_readout,
        motion_value_labels=motion_value_labels,
        xy_label=xy_label,
    )


def build_calibration_review_content(
    review_content: toga.Box,
    *,
    neutral_acc_y: float,
    neutral_acc_z: float,
) -> CalibrationReviewViewRefs:
    """Assemble review labels (action buttons use ``build_calibration_review_action_row``)."""
    review_content.add(
        toga.Label(
            "Verify configuration",
            style=pack_review_section_title(padding_bottom=8, text_align=CENTER),
        )
    )
    ay_l, az_l = MOTION_AXIS_YZ
    xy_label = toga.Label(
        f"{ay_l}=0.00 · {az_l}=0.00",
        style=Pack(padding_bottom=6, font_size=14, text_align=CENTER),
    )
    review_content.add(xy_label)
    neutral_label = toga.Label(
        f"Neutral {ay_l}={neutral_acc_y:.4f} · {az_l}={neutral_acc_z:.4f}",
        style=pack_muted_body(padding_bottom=10, text_align=CENTER),
    )
    review_content.add(neutral_label)
    return CalibrationReviewViewRefs(xy_label=xy_label, neutral_label=neutral_label)


def build_calibration_review_action_row(
    on_cancel: Callable[[Optional[toga.Widget]], None],
    on_retry: Callable[[Optional[toga.Widget]], None],
    on_save: Callable[[Optional[toga.Widget]], None],
) -> toga.Box:
    row = toga.Box(
        style=Pack(direction=ROW, alignment=CENTER, padding_top=16, padding_left=12, padding_right=12)
    )
    row.add(toga.Box(style=Pack(flex=1)))
    row.add(
        toga.Button(
            "Cancel",
            on_press=on_cancel,
            style=pack_action_button(gap_after=True),
        )
    )
    row.add(
        toga.Button(
            "Retry",
            on_press=on_retry,
            style=pack_action_button(gap_after=True),
        )
    )
    row.add(toga.Button("Save", on_press=on_save, style=pack_action_button()))
    row.add(toga.Box(style=Pack(flex=1)))
    return row
