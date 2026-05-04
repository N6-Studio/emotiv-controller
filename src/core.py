"""Pure movement / mental-command logic (no UI, I/O, or hardware).

Accelerometer (``ACCX``/``ACCY``/``ACCZ``) semantics for tilt:

- Cortex exposes motion in the ``mot`` stream; column order for newer headsets
  matches ``_MOT_COLS_12`` (see Emotiv Cortex *Data Subscription* /
  *Motion* docs on https://emotiv.gitbook.io/cortex-api/ ).
- Public docs do not spell out every axis sign relative to the wearer's nose/ears;
  headband placement (e.g. EPOC X back vs top) can change the sensor frame
  relative to the head—validate with your headset if lean feels inverted.
- We treat the sample as **specific force** (gravity + motion); static head lean
  is inferred from the **direction** of the vector (``atan2`` ratios scale out
  uniform gain).

See ``accel_to_pitch_roll`` (Euler-style) vs ``accel_to_horizontal_projection_deg``
(bubble-style projection onto the nominal ZX/ZY planes).
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Literal

TiltMode = Literal["euler", "horizontal_projection"]

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
    """Accelerometer-based tilt; returns (pitch, roll) in radians when static.

    Pitch is nose-down positive in this decomposition; roll is ``atan2(ay, az)``.
    Same ACC frame as :func:`accel_to_horizontal_projection_deg`; verify signs
    against your headset if forward/back feel swapped.
    """
    pitch = math.atan2(-ax, math.hypot(ay, az))
    roll = math.atan2(ay, az)
    return pitch, roll


def _normalize_specific_force(ax: float, ay: float, az: float) -> tuple[float, float, float] | None:
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if not math.isfinite(n) or n < 1e-15:
        return None
    return ax / n, ay / n, az / n


def accel_to_horizontal_projection_deg(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Two-angle tilt from ACC direction using ZX and ZY planes (degrees).

    After unit-normalizing ``(ax, ay, az)`` as specific force, returns
    ``(atan2(nx, nz), atan2(ny, nz))`` in degrees. When ``|nz|`` is dominant at
    upright, this acts like a **bubble level** in X and Z and in Y and Z, with
    different coupling than Euler ``accel_to_pitch_roll``. Use for thresholds
    via the same neutral + ``compute_motion_movements`` pipeline when
    ``tilt_mode="horizontal_projection"`` (recalibrate neutral in that mode).
    """
    u = _normalize_specific_force(ax, ay, az)
    if u is None:
        return 0.0, 0.0
    nx, ny, nz = u
    return math.degrees(math.atan2(nx, nz)), math.degrees(math.atan2(ny, nz))


def accel_to_horizontal_polar_deg(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Azimuth and tilt magnitude (degrees) from horizontal gravity slice.

    Unit ``n = (ax,ay,az)/|a|``; ``h = hypot(nx, ny)``; returns
    ``(atan2(ny, nx)°, atan2(h, |nz|)°)``. Handy for diagnostics; movement
    thresholds still use :func:`mot_to_tilt_xy` with ``horizontal_projection``
    unless you wire this in yourself.
    """
    u = _normalize_specific_force(ax, ay, az)
    if u is None:
        return 0.0, 0.0
    nx, ny, nz = u
    h = math.hypot(nx, ny)
    azimuth = math.degrees(math.atan2(ny, nx))
    tilt = math.degrees(math.atan2(h, abs(nz)))
    return azimuth, tilt


def _tilt_from_cols(
    mot: list[Any],
    cols: list[str],
    *,
    tilt_mode: TiltMode = "euler",
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
            if tilt_mode == "horizontal_projection":
                return accel_to_horizontal_projection_deg(ax, ay, az)
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


def mot_to_tilt_xy(
    mot: list[Any],
    cols: list[str] | None,
    *,
    tilt_mode: TiltMode = "euler",
) -> tuple[float, float]:
    """Map a Cortex ``mot`` array to ``(x°, y°)`` for head lean / thresholds.

    ``tilt_mode="euler"`` (default): ``ACCX/Y/Z`` → pitch/roll via
    :func:`accel_to_pitch_roll`; else quaternion ``Q0``–``Q3``.

    ``tilt_mode="horizontal_projection"``: finite ACC →
    :func:`accel_to_horizontal_projection_deg`; quaternion fallback is still
    Euler pitch/roll (fusion frame), so prefer this mode when ACC is reliable.

    When no column metadata is available, 12- and 11-length arrays use the
    standard Insight layouts from the Cortex API docs; shorter arrays keep the
    previous ``mot[-2]``, ``mot[-1]`` convention (synthetic / dev servers).
    """
    if not mot or len(mot) < 2:
        return 0.0, 0.0
    if cols is not None:
        out = _tilt_from_cols(mot, cols, tilt_mode=tilt_mode)
        if out is not None:
            return out
    if len(mot) == 12:
        out = _tilt_from_cols(mot, list(_MOT_COLS_12), tilt_mode=tilt_mode)
        if out is not None:
            return out
    if len(mot) == 11:
        out = _tilt_from_cols(mot, list(_MOT_COLS_11), tilt_mode=tilt_mode)
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
