"""Tests for Cortex ``mot`` → ACC motion mapping (ACCY / ACCZ)."""

import math

import pytest

from core import (
    _MOT_COLS_12,
    mot_acc_xyz,
    mot_to_motion_xy,
)


def _mot12(acc_x: float, acc_y: float, acc_z: float) -> list[float]:
    """Build a 12-slot ``mot`` row with ACC at standard indices."""
    row = [0.0] * 12
    row[2:6] = [1.0, 0.0, 0.0, 0.0]
    row[6] = acc_x
    row[7] = acc_y
    row[8] = acc_z
    return row


def test_mot_to_motion_xy_legacy_short_array():
    assert mot_to_motion_xy([0, 0, 3.0, 4.0], None) == (3.0, 4.0)


def test_mot_acc_xyz_legacy_short_array():
    assert mot_acc_xyz([0, 0, 3.0, 4.0], None) is None


def test_mot_acc_xyz_twelve_elements_without_cols():
    ax, ay, az = 0.94, -0.31, -0.01
    mot = _mot12(ax, ay, az)
    assert mot_acc_xyz(mot, None) == pytest.approx((ax, ay, az))


def test_mot_to_motion_xy_twelve_elements_returns_acc_y_z():
    ax, ay, az = 0.735341, 0.627441, -0.015869
    mot = _mot12(ax, ay, az)
    x, y = mot_to_motion_xy(mot, None)
    assert x == pytest.approx(ay)
    assert y == pytest.approx(az)


def test_mot_acc_xyz_non_finite_returns_none():
    mot_nan = _mot12(1.0, float("nan"), 0.1)
    assert mot_acc_xyz(mot_nan, list(_MOT_COLS_12)) is None


def test_mot_to_motion_xy_non_finite_acc_falls_back_to_legacy_tail():
    """When ACC is unusable, motion matches tail convention like older gyro fixtures."""
    cols = list(_MOT_COLS_12)
    mot_nan = _mot12(1.0, float("nan"), 0.1)
    mot_nan[-2] = 2.5
    mot_nan[-1] = -3.0
    x, y = mot_to_motion_xy(mot_nan, cols)
    assert x == pytest.approx(2.5)
    assert y == pytest.approx(-3.0)


def test_mot_acc_xyz_legacy_layout_without_acc_columns_returns_none():
    cols = [
        "COUNTER_MEMS",
        "INTERPOLATED_MEMS",
        "GYROX",
        "GYROY",
        "GYROZ",
        "MAGX",
        "MAGY",
        "MAGZ",
        "FOO",
        "BAR",
        "BAZ",
    ]
    mot = list(range(11))
    assert mot_acc_xyz(mot, cols) is None


def test_mot_to_motion_xy_legacy_layout_falls_back_to_tail_when_named_cols():
    cols = [
        "COUNTER_MEMS",
        "INTERPOLATED_MEMS",
        "GYROX",
        "GYROY",
        "GYROZ",
        "MAGX",
        "MAGY",
        "MAGZ",
        "FOO",
        "BAR",
        "BAZ",
    ]
    mot = list(range(11))
    assert mot_to_motion_xy(mot, cols) == (float(mot[-2]), float(mot[-1]))


def test_mot_to_motion_xy_eleven_elements_without_cols_returns_legacy_tail():
    mot = list(range(11))
    assert mot_to_motion_xy(mot, None) == (float(mot[-2]), float(mot[-1]))


def test_headset_samples_sign_pattern():
    """Real headset checks: left Z−, right Z+, forward Y−, backward Y+."""
    sinistra = _mot12(0.936149, 0.12355, -0.352094)
    destra = _mot12(0.846783, 0.124527, 0.527408)
    avanti = _mot12(0.944451, -0.301795, -0.010255)
    indietro = _mot12(0.876084, 0.505921, -0.014162)

    ly_s, lz_s = mot_to_motion_xy(sinistra, None)
    ly_d, lz_d = mot_to_motion_xy(destra, None)
    ly_a, lz_a = mot_to_motion_xy(avanti, None)
    ly_i, lz_i = mot_to_motion_xy(indietro, None)

    assert lz_s < 0 and lz_d > 0
    assert ly_a < 0 and ly_i > 0
    assert math.isfinite(ly_s) and math.isfinite(lz_s)


def test_named_cols_resolve_acc():
    cols = [
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
    ]
    mot = _mot12(0.5, -0.2, 0.4)
    assert mot_to_motion_xy(mot, cols) == pytest.approx((-0.2, 0.4))
