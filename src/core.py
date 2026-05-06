"""Pure movement and mental-command parsing (no UI, I/O, or hardware).

Head-tilt for WASD movement is derived from the Cortex ``mot`` stream accelerometer
columns ``ACCX``, ``ACCY``, ``ACCZ``. Forward/backward follows **ACCY** (negative
forward, positive backward); left/right follows **ACCZ** (negative left, positive
right). ``ACCX`` is available for display but not used for movement thresholds.

Mental commands come from the Cortex ``com`` stream (``act``, ``pow``); they drive
configurable keyboard bindings only and do not map onto movement directions.

Layouts without named ACC columns fall through to the synthetic ``mot[-2], mot[-1]``
legacy convention used by dev/test fixtures.

See https://emotiv.gitbook.io/cortex-api/data-subscription/data-sample-object#motion
for the column layout of newer headsets.
"""

from __future__ import annotations

import math
from typing import Any, Container, Iterable

# Default Cortex ``mot`` layout when ``cols`` is unavailable (MEMS counter + quaternion + ACC + MAG).
_MOT_COLS_12 = (
    "COUNTER_MEMS",
    "INTERPOLATED_MEMS",
    "Q0",
    "Q1",
    "Q2",
    "Q3",
    "ACCX",
    "ACCY",
    "ACCZ",
    "MAGX",
    "MAGY",
    "MAGZ",
)

# Reticle normalization: plausible ACC ranges per axis (Cortex-scale units, ~±1g).
ACC_Y_MIN = -1.0
ACC_Y_MAX = 1.0
ACC_Z_MIN = -1.0
ACC_Z_MAX = 1.0

# Mental command actions (Cortex ``com`` act names) exposed for UI key bindings.
COM_MAPPED_MENTAL_ACTIONS: tuple[str, ...] = ("push", "pull", "lift", "drop")


def build_mot_index(cols: list[str]) -> dict[str, int]:
    return {str(c): i for i, c in enumerate(cols)}


def _float_at(mot: list[Any], i: int) -> float:
    if i < 0 or i >= len(mot):
        return float("nan")
    v = mot[i]
    if v is None:
        return float("nan")
    return float(v)


def reticle_offset_acc_to_normalized(
    dx_acc_y: float,
    dy_acc_z: float,
    neutral_acc_y: float,
    neutral_acc_z: float,
) -> tuple[float, float]:
    """Map **ACCY / ACCZ offsets from neutral** to reticle coordinates in ``[-1, 1]``.

    ``(0, 0)`` is the calibrated neutral. ``±1`` on an axis reaches toward
    ``ACC_Y_*`` / ``ACC_Z_*``. Uses separate spans for negative vs positive delta.
    """
    if dx_acc_y <= 0.0:
        span_neg = neutral_acc_y - ACC_Y_MIN
        if span_neg <= 1e-15:
            hx = 0.0
        else:
            hx = max(-1.0, dx_acc_y / span_neg)
    else:
        span_pos = ACC_Y_MAX - neutral_acc_y
        if span_pos <= 1e-15:
            hx = 0.0
        else:
            hx = min(1.0, dx_acc_y / span_pos)

    if dy_acc_z <= 0.0:
        span_neg = neutral_acc_z - ACC_Z_MIN
        if span_neg <= 1e-15:
            hy = 0.0
        else:
            hy = max(-1.0, dy_acc_z / span_neg)
    else:
        span_pos = ACC_Z_MAX - neutral_acc_z
        if span_pos <= 1e-15:
            hy = 0.0
        else:
            hy = min(1.0, dy_acc_z / span_pos)

    return hx, hy


def _acc_xyz_from_cols(mot: list[Any], cols: list[str]) -> tuple[float, float, float] | None:
    """``(ACCX, ACCY, ACCZ)`` when columns match and values are finite; else ``None``."""
    if len(cols) != len(mot):
        return None
    idx = build_mot_index(cols)
    if not all(k in idx for k in ("ACCX", "ACCY", "ACCZ")):
        return None
    ax = _float_at(mot, idx["ACCX"])
    ay = _float_at(mot, idx["ACCY"])
    az = _float_at(mot, idx["ACCZ"])
    if not all(math.isfinite(v) for v in (ax, ay, az)):
        return None
    return (ax, ay, az)


def mot_acc_xyz(mot: list[Any], cols: list[str] | None) -> tuple[float, float, float] | None:
    """Raw Cortex ``(ACCX, ACCY, ACCZ)`` when available, else ``None``.

    Uses named ``cols``, or the standard 12-element layout when ``cols`` is
    ``None`` and ``len(mot) == 12``. Legacy short arrays yield ``None``.
    """
    if not mot or len(mot) < 2:
        return None
    if cols is not None:
        out = _acc_xyz_from_cols(mot, cols)
        if out is not None:
            return out
    if len(mot) == 12:
        return _acc_xyz_from_cols(mot, list(_MOT_COLS_12))
    return None


def mot_to_motion_xy(mot: list[Any], cols: list[str] | None) -> tuple[float, float]:
    """Map ``mot`` to ``(motion_x, motion_y)`` for thresholds and WASD logic.

    Returns ``(ACCY, ACCZ)`` when accelerometer columns resolve (forward/back on
    ``motion_x``, left/right on ``motion_y``). Otherwise ``(mot[-2], mot[-1])``.
    """
    if not mot or len(mot) < 2:
        return 0.0, 0.0
    triple = mot_acc_xyz(mot, cols)
    if triple is not None:
        _ax, ay, az = triple
        return ay, az
    return float(mot[-2] or 0), float(mot[-1] or 0)


def resolved_movement_thresholds(
    *,
    threshold_global: bool,
    threshold: float,
    movement_thresholds: dict[str, float],
) -> tuple[float, float, float, float]:
    """Thresholds in ACCY/ACCZ units matching :func:`compute_motion_movements`.

    Returns ``(t_fwd, t_back, t_left, t_right)`` for forward / backward / left / right.
    """
    if threshold_global:
        t = float(threshold)
        return (t, t, t, t)
    m = movement_thresholds
    return (
        float(m["forward"]),
        float(m["backward"]),
        float(m["left"]),
        float(m["right"]),
    )


def compute_motion_movements(
    x: float,
    y: float,
    neutral_x: float,
    neutral_y: float,
    *,
    threshold_global: bool,
    threshold: float,
    movement_thresholds: dict[str, float],
) -> set[str]:
    """Return abstract movement names active for the given pose vs neutral."""
    t_fwd, t_back, t_left, t_right = resolved_movement_thresholds(
        threshold_global=threshold_global,
        threshold=threshold,
        movement_thresholds=movement_thresholds,
    )

    movements: set[str] = set()

    if x <= neutral_x - t_fwd:
        movements.add("forward")
    elif x >= neutral_x + t_back:
        movements.add("backward")

    if y <= neutral_y - t_left:
        movements.add("left")
    elif y >= neutral_y + t_right:
        movements.add("right")

    return movements


def mental_command_to_sets(
    com: Iterable,
    *,
    power_threshold: float,
    enabled_actions: Container[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Returns ``(set(), mental_actions)`` — COM never contributes movement labels.

    When ``enabled_actions`` is set, commands whose Cortex action name is not in
    that container are ignored (no simulated COM key path).
    """
    com_list = list(com)
    if len(com_list) < 2:
        return set(), set()

    action = str(com_list[0] or "neutral").lower()
    power = float(com_list[1] or 0)

    if power < power_threshold:
        return set(), set()

    if enabled_actions is not None and action not in enabled_actions:
        return set(), set()

    if action not in COM_MAPPED_MENTAL_ACTIONS:
        return set(), set()
    return set(), {action}
