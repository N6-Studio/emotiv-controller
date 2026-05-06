"""Pure movement / mental-command logic (no UI, I/O, or hardware).

Head-tilt for WASD movement is derived **exclusively** from the Cortex ``mot``
stream's quaternion columns ``Q0, Q1, Q2, Q3``. They are rearranged into a
Hamilton quaternion ``(w, x, y, z)`` (see :func:`hamilton_wxyz_from_stream_quat`)
before :func:`quaternion_to_pitch_roll`. Pitch drives forward/backward; roll
drives left/right.

Older EMOTIV headsets that expose ``GYROX/Y/Z`` instead of quaternions are not
supported for tilt computation here — they will fall through to the synthetic
``mot[-2], mot[-1]`` legacy fallback used by dev/test fixtures.

See https://emotiv.gitbook.io/cortex-api/data-subscription/data-sample-object#motion
for the column layout of newer headsets.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

# Which ``mot`` quaternion slot (0=Q0 … 3=Q3) feeds each Hamilton component when mapping is default identity.
DEFAULT_STREAM_INDEX_FOR_WXYZ: tuple[int, int, int, int] = (0, 1, 2, 3)

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


def hamilton_wxyz_from_stream_quat(
    q_stream: tuple[float, float, float, float],
    stream_index_for_wxyz: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    """Build ``(w, x, y, z)`` from Cortex ``(Q0, Q1, Q2, Q3)`` order in ``q_stream``.

    ``stream_index_for_wxyz`` is ``(i_w, i_x, i_y, i_z)`` with each index in
    ``{0, 1, 2, 3}``, assigning which stream component feeds each Hamilton part.
    Caller must ensure indices form a permutation (validated in app config).
    """
    iw, ix, iy, iz = stream_index_for_wxyz
    q = q_stream
    return (q[iw], q[ix], q[iy], q[iz])


def quaternion_to_pitch_roll(
    q0: float, q1: float, q2: float, q3: float
) -> tuple[float, float]:
    """Hamilton convention w, x, y, z. Returns (pitch, roll) in radians.

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


# Degrees: ranges implied by :func:`quaternion_to_pitch_roll` (``asin`` / ``atan2``).
TILT_PITCH_MIN_DEG = -90.0
TILT_PITCH_MAX_DEG = 90.0
TILT_ROLL_MIN_DEG = -180.0
TILT_ROLL_MAX_DEG = 180.0


def reticle_offset_deg_to_normalized(
    dx_pitch_deg: float,
    dy_roll_deg: float,
    neutral_pitch_deg: float,
    neutral_roll_deg: float,
) -> tuple[float, float]:
    """Map pitch/roll **offsets from neutral** (degrees) to reticle coordinates in ``[-1, 1]``.

    ``(0, 0)`` is always the calibrated neutral. ``±1`` on an axis is the global
    quaternion-derived limit in that direction: pitch toward ``TILT_PITCH_*`` and
    roll toward ``TILT_ROLL_*``. Uses separate spans for negative vs positive delta
    so the aim box uses the full physically reachable range from the current neutral.
    """
    if dx_pitch_deg <= 0.0:
        span_neg = neutral_pitch_deg - TILT_PITCH_MIN_DEG
        if span_neg <= 1e-15:
            hx = 0.0
        else:
            hx = max(-1.0, dx_pitch_deg / span_neg)
    else:
        span_pos = TILT_PITCH_MAX_DEG - neutral_pitch_deg
        if span_pos <= 1e-15:
            hx = 0.0
        else:
            hx = min(1.0, dx_pitch_deg / span_pos)

    if dy_roll_deg <= 0.0:
        span_neg = neutral_roll_deg - TILT_ROLL_MIN_DEG
        if span_neg <= 1e-15:
            hy = 0.0
        else:
            hy = max(-1.0, dy_roll_deg / span_neg)
    else:
        span_pos = TILT_ROLL_MAX_DEG - neutral_roll_deg
        if span_pos <= 1e-15:
            hy = 0.0
        else:
            hy = min(1.0, dy_roll_deg / span_pos)

    return hx, hy


def _quat_tuple_from_mot(mot: list[Any], cols: list[str]) -> tuple[float, float, float, float] | None:
    """Raw ``(Q0, Q1, Q2, Q3)`` from ``mot`` when ``cols`` names quaternion slots; else ``None``."""
    if len(cols) != len(mot):
        return None
    idx = build_mot_index(cols)
    if not all(k in idx for k in ("Q0", "Q1", "Q2", "Q3")):
        return None
    q0 = _float_at(mot, idx["Q0"])
    q1 = _float_at(mot, idx["Q1"])
    q2 = _float_at(mot, idx["Q2"])
    q3 = _float_at(mot, idx["Q3"])
    if not all(math.isfinite(v) for v in (q0, q1, q2, q3)):
        return None
    return (q0, q1, q2, q3)


def _tilt_from_cols(
    mot: list[Any],
    cols: list[str],
    *,
    stream_index_for_wxyz: tuple[int, int, int, int] = DEFAULT_STREAM_INDEX_FOR_WXYZ,
) -> tuple[float, float] | None:
    quat = _quat_tuple_from_mot(mot, cols)
    if quat is None:
        return None
    w, x, y, z = hamilton_wxyz_from_stream_quat(quat, stream_index_for_wxyz)
    p, r = quaternion_to_pitch_roll(w, x, y, z)
    return math.degrees(p), math.degrees(r)


def mot_to_tilt_xy(
    mot: list[Any],
    cols: list[str] | None,
    *,
    stream_index_for_wxyz: tuple[int, int, int, int] = DEFAULT_STREAM_INDEX_FOR_WXYZ,
) -> tuple[float, float]:
    """Map a Cortex ``mot`` array to ``(pitch°, roll°)`` for head lean / thresholds.

    Quaternions ``Q0``-``Q3`` are converted via :func:`quaternion_to_pitch_roll`
    and returned in degrees. When ``cols`` is missing, a 12-element ``mot``
    array is assumed to follow the standard newer-headset layout (see
    ``_MOT_COLS_12``). Anything else falls through to the legacy
    ``(mot[-2], mot[-1])`` convention used by synthetic / dev fixtures.
    """
    if not mot or len(mot) < 2:
        return 0.0, 0.0
    if cols is not None:
        out = _tilt_from_cols(mot, cols, stream_index_for_wxyz=stream_index_for_wxyz)
        if out is not None:
            return out
    if len(mot) == 12:
        out = _tilt_from_cols(
            mot, list(_MOT_COLS_12), stream_index_for_wxyz=stream_index_for_wxyz
        )
        if out is not None:
            return out
    return float(mot[-2] or 0), float(mot[-1] or 0)


def mot_quaternion(
    mot: list[Any],
    cols: list[str] | None,
) -> tuple[float, float, float, float] | None:
    """Raw Cortex quaternion ``(Q0, Q1, Q2, Q3)`` when available, else ``None``.

    Uses the same column resolution as :func:`mot_to_tilt_xy` (named ``cols``,
    or 12-element default layout). Legacy gyro / short arrays yield ``None``.
    """
    if not mot or len(mot) < 2:
        return None
    if cols is not None:
        out = _quat_tuple_from_mot(mot, cols)
        if out is not None:
            return out
    if len(mot) == 12:
        return _quat_tuple_from_mot(mot, list(_MOT_COLS_12))
    return None


def resolved_movement_thresholds(
    *,
    threshold_global: bool,
    threshold: float,
    movement_thresholds: dict[str, float],
) -> tuple[float, float, float, float]:
    """Pitch/roll threshold degrees matching :func:`compute_motion_movements`.

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
