"""Tests for Cortex ``mot`` → pitch/roll (degrees) mapping."""

import math

import pytest

from core import (
    _MOT_COLS_12,
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
    x, y = mot_to_tilt_xy(mot, cols)
    assert math.isfinite(x) and math.isfinite(y)
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
    x, y = mot_to_tilt_xy(mot, None)
    assert math.isfinite(x) and math.isfinite(y)


def test_mot_to_tilt_xy_legacy_short_array():
    assert mot_to_tilt_xy([0, 0, 3.0, 4.0], None) == (3.0, 4.0)


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
