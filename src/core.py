"""Pure movement / mental-command logic (no UI, I/O, or hardware)."""

from __future__ import annotations

import math
from typing import Any, Iterable

# Default Cortex ``mot`` layout when ``cols`` is unavailable (newer headsets: quaternion + ACC + MAG).
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
# Older headsets: gyroscope rates instead of quaternion (same ACC / MAG tail).
_MOT_COLS_11 = (
    "COUNTER_MEMS",
    "INTERPOLATED_MEMS",
    "GYROX",
    "GYROY",
    "GYROZ",
    "ACCX",
    "ACCY",
    "ACCZ",
    "MAGX",
    "MAGY",
    "MAGZ",
)

# Mental command actions mapped to movement (Cortex `com[0]` names).
COM_MAPPED_MENTAL_ACTIONS: tuple[str, ...] = ("push", "pull", "left", "right")


def build_mot_index(cols: list[str]) -> dict[str, int]:
    return {str(c): i for i, c in enumerate(cols)}


def _float_at(mot: list[Any], i: int) -> float:
    if i < 0 or i >= len(mot):
        return float("nan")
    v = mot[i]
    if v is None:
        return float("nan")
    return float(v)


def quaternion_to_pitch_roll(
    q0: float, q1: float, q2: float, q3: float
) -> tuple[float, float]:
    """Hamilton convention w, x, y, z = Q0..Q3. Returns (pitch, roll) in radians.

    Pitch is used for forward vs backward; roll for left vs right (``compute_motion_movements`` x/y).
    """
    w, x, y, z = q0, q1, q2, q3
    n = math.hypot(math.hypot(w, x), math.hypot(y, z))
    if n < 1e-10:
        return 0.0, 0.0
    w, x, y, z = w / n, x / n, y / n, z / n
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)
    return pitch, roll


def accel_to_pitch_roll(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Accelerometer-based tilt; returns (pitch, roll) in radians when static."""
    pitch = math.atan2(-ax, math.hypot(ay, az))
    roll = math.atan2(ay, az)
    return pitch, roll


def _tilt_from_cols(
    mot: list[Any], cols: list[str]
) -> tuple[float, float] | None:
    if len(cols) != len(mot):
        return None
    idx = build_mot_index(cols)
    # Prefer accelerometer for head lean: gravity direction vs device frame.
    if all(k in idx for k in ("ACCX", "ACCY", "ACCZ")):
        ax = _float_at(mot, idx["ACCX"])
        ay = _float_at(mot, idx["ACCY"])
        az = _float_at(mot, idx["ACCZ"])
        if all(math.isfinite(v) for v in (ax, ay, az)):
            p, r = accel_to_pitch_roll(ax, ay, az)
            return math.degrees(p), math.degrees(r)
    if all(k in idx for k in ("Q0", "Q1", "Q2", "Q3")):
        q0 = _float_at(mot, idx["Q0"])
        q1 = _float_at(mot, idx["Q1"])
        q2 = _float_at(mot, idx["Q2"])
        q3 = _float_at(mot, idx["Q3"])
        if all(math.isfinite(v) for v in (q0, q1, q2, q3)):
            p, r = quaternion_to_pitch_roll(q0, q1, q2, q3)
            return math.degrees(p), math.degrees(r)
    return None


def mot_to_tilt_xy(mot: list[Any], cols: list[str] | None) -> tuple[float, float]:
    """Map a Cortex ``mot`` array to (pitch°, roll°) for head lean.

    Uses ``ACCX/Y/Z`` first when present (gravity tilt); otherwise quaternion
    ``Q0``–``Q3``. When no column metadata is available, 12- and 11-length arrays
    use the standard Insight layouts from the Cortex API docs; shorter arrays keep
    the previous ``mot[-2]``, ``mot[-1]`` convention (synthetic / dev servers).
    """
    if not mot or len(mot) < 2:
        return 0.0, 0.0
    if cols is not None:
        out = _tilt_from_cols(mot, cols)
        if out is not None:
            return out
    if len(mot) == 12:
        out = _tilt_from_cols(mot, list(_MOT_COLS_12))
        if out is not None:
            return out
    if len(mot) == 11:
        out = _tilt_from_cols(mot, list(_MOT_COLS_11))
        if out is not None:
            return out
    return float(mot[-2] or 0), float(mot[-1] or 0)


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
    if threshold_global:
        t_fwd = t_back = t_left = t_right = float(threshold)
    else:
        m = movement_thresholds
        t_fwd = float(m["forward"])
        t_back = float(m["backward"])
        t_left = float(m["left"])
        t_right = float(m["right"])

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
) -> tuple[set[str], set[str]]:
    """Returns (movement labels for the pad UI, mental actions for COM keys)."""
    com_list = list(com)
    if len(com_list) < 2:
        return set(), set()

    action = str(com_list[0] or "neutral").lower()
    power = float(com_list[1] or 0)

    if power < power_threshold:
        return set(), set()

    mapping = {
        "push": "forward",
        "pull": "backward",
        "left": "left",
        "right": "right",
    }

    movement = mapping.get(action)
    if not movement:
        return set(), set()
    return {movement}, {action}
