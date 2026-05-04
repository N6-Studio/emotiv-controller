"""Tests for Cortex ``mot`` → pitch/roll (degrees) mapping."""

import math

import pytest

from core import (
    _MOT_COLS_12,
    accel_to_horizontal_projection_deg,
    accel_to_pitch_roll,
    build_mot_index,
    mot_to_tilt_xy,
    quaternion_to_pitch_roll,
)


def test_build_mot_index():
    assert build_mot_index(["A", "B"]) == {"A": 0, "B": 1}


def test_quaternion_identity():
    p, r = quaternion_to_pitch_roll(1.0, 0.0, 0.0, 0.0)
    assert p == pytest.approx(0.0)
    assert r == pytest.approx(0.0)


def test_quaternion_pitch_only():
    pitch = 0.25
    w, x, y, z = math.cos(pitch / 2), 0.0, math.sin(pitch / 2), 0.0
    p, r = quaternion_to_pitch_roll(w, x, y, z)
    assert p == pytest.approx(pitch)
    assert r == pytest.approx(0.0, abs=1e-6)


def test_mot_to_tilt_xy_cortex_sample_with_cols():
    mot = [
        48,
        0,
        0.735341,
        0.255615,
        0.627441,
        -0.015869,
        0.948257,
        -0.354986,
        -0.083497,
        -44.656766,
        -86.970985,
        23.221568,
    ]
    cols = list(_MOT_COLS_12)
    pr = accel_to_pitch_roll(0.948257, -0.354986, -0.083497)
    expect_x = math.degrees(pr[0])
    expect_y = math.degrees(pr[1])
    x, y = mot_to_tilt_xy(mot, cols)
    assert x == pytest.approx(expect_x)
    assert y == pytest.approx(expect_y)
    assert x != pytest.approx(float(mot[-2]))
    assert y != pytest.approx(float(mot[-1]))


def test_mot_to_tilt_xy_twelve_elements_without_cols():
    mot = [
        48,
        0,
        0.735341,
        0.255615,
        0.627441,
        -0.015869,
        0.948257,
        -0.354986,
        -0.083497,
        -44.656766,
        -86.970985,
        23.221568,
    ]
    pr = accel_to_pitch_roll(0.948257, -0.354986, -0.083497)
    x, y = mot_to_tilt_xy(mot, None)
    assert x == pytest.approx(math.degrees(pr[0]))
    assert y == pytest.approx(math.degrees(pr[1]))


def test_mot_to_tilt_xy_legacy_short_array():
    assert mot_to_tilt_xy([0, 0, 3.0, 4.0], None) == (3.0, 4.0)


def test_mot_to_tilt_falls_back_to_quaternion_when_acc_not_finite():
    mot = [
        48,
        0,
        0.735341,
        0.255615,
        0.627441,
        -0.015869,
        float("nan"),
        float("nan"),
        float("nan"),
        -44.656766,
        -86.970985,
        23.221568,
    ]
    pr = quaternion_to_pitch_roll(0.735341, 0.255615, 0.627441, -0.015869)
    x, y = mot_to_tilt_xy(mot, list(_MOT_COLS_12))
    assert x == pytest.approx(math.degrees(pr[0]))
    assert y == pytest.approx(math.degrees(pr[1]))


def test_mot_to_tilt_xy_eleven_gyro_layout_accel_fallback():
    """Older headsets: no quaternion columns; ACC gives tilt."""
    mot = [
        14,
        0,
        8206,
        8187,
        8181,
        4235,
        8668,
        8128,
        8294,
        8237,
        7938,
    ]
    x, y = mot_to_tilt_xy(mot, None)
    assert math.isfinite(x) and math.isfinite(y)
    pr = accel_to_pitch_roll(4235.0, 8668.0, 8128.0)
    assert x == pytest.approx(math.degrees(pr[0]))
    assert y == pytest.approx(math.degrees(pr[1]))


def test_accel_vertical_rest_euler_and_horizontal_near_zero():
    ax, ay, az = 0.0, 0.0, 1.0
    p, r = accel_to_pitch_roll(ax, ay, az)
    assert p == pytest.approx(0.0)
    assert r == pytest.approx(0.0)
    hx, hy = accel_to_horizontal_projection_deg(ax, ay, az)
    assert hx == pytest.approx(0.0)
    assert hy == pytest.approx(0.0)


def test_horizontal_projection_differs_from_euler_on_cortex_sample():
    ax, ay, az = 0.948257, -0.354986, -0.083497
    ep = math.degrees(accel_to_pitch_roll(ax, ay, az)[0])
    er = math.degrees(accel_to_pitch_roll(ax, ay, az)[1])
    hx, hy = accel_to_horizontal_projection_deg(ax, ay, az)
    assert abs(hx - ep) > 0.5 or abs(hy - er) > 0.5


def test_mot_to_tilt_xy_horizontal_projection_mode():
    mot = [
        48,
        0,
        0.735341,
        0.255615,
        0.627441,
        -0.015869,
        0.948257,
        -0.354986,
        -0.083497,
        -44.656766,
        -86.970985,
        23.221568,
    ]
    cols = list(_MOT_COLS_12)
    exp = accel_to_horizontal_projection_deg(0.948257, -0.354986, -0.083497)
    x, y = mot_to_tilt_xy(mot, cols, tilt_mode="horizontal_projection")
    assert x == pytest.approx(exp[0])
    assert y == pytest.approx(exp[1])


def test_mot_to_tilt_horizontal_falls_back_to_quaternion_when_acc_not_finite():
    mot = [
        48,
        0,
        0.735341,
        0.255615,
        0.627441,
        -0.015869,
        float("nan"),
        float("nan"),
        float("nan"),
        -44.656766,
        -86.970985,
        23.221568,
    ]
    cols = list(_MOT_COLS_12)
    x_h, y_h = mot_to_tilt_xy(mot, cols, tilt_mode="horizontal_projection")
    x_e, y_e = mot_to_tilt_xy(mot, cols, tilt_mode="euler")
    assert x_h == pytest.approx(x_e)
    assert y_h == pytest.approx(y_e)
