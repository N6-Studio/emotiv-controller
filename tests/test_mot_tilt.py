"""Tests for Cortex ``mot`` → quaternion-based pitch/roll (degrees) mapping."""

import math

import pytest

from core import (
    _MOT_COLS_12,
    build_mot_index,
    mot_quaternion,
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


def test_mot_to_tilt_xy_legacy_short_array():
    assert mot_to_tilt_xy([0, 0, 3.0, 4.0], None) == (3.0, 4.0)


def test_mot_quaternion_user_sample_matches_stream():
    mot = [
        20,
        0,
        0.308196,
        0.582581,
        -0.390076,
        0.643005,
        0.981565,
        -0.167989,
        0.019534,
        -75.353924,
        61.721796,
        -3.624854,
    ]
    q = mot_quaternion(mot, None)
    assert q is not None
    assert q == pytest.approx((0.308196, 0.582581, -0.390076, 0.643005))


def test_mot_quaternion_legacy_and_non_finite_return_none():
    assert mot_quaternion([0, 0, 3.0, 4.0], None) is None
    mot_nan = [
        48,
        0,
        float("nan"),
        float("nan"),
        float("nan"),
        float("nan"),
        0.948257,
        -0.354986,
        -0.083497,
        -44.656766,
        -86.970985,
        23.221568,
    ]
    assert mot_quaternion(mot_nan, list(_MOT_COLS_12)) is None


def test_mot_quaternion_eleven_element_gyro_layout_returns_none():
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
    assert mot_quaternion(mot, None) is None


def test_mot_to_tilt_xy_user_sample_uses_quaternion():
    """User-provided sample: quaternion drives pitch/roll, ACC is ignored."""
    mot = [
        20,
        0,
        0.308196,
        0.582581,
        -0.390076,
        0.643005,
        0.981565,
        -0.167989,
        0.019534,
        -75.353924,
        61.721796,
        -3.624854,
    ]
    pr = quaternion_to_pitch_roll(0.308196, 0.582581, -0.390076, 0.643005)
    expect_x = math.degrees(pr[0])
    expect_y = math.degrees(pr[1])
    x, y = mot_to_tilt_xy(mot, None)
    assert x == pytest.approx(expect_x)
    assert y == pytest.approx(expect_y)


def test_mot_to_tilt_xy_cortex_sample_with_cols_uses_quaternion():
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
    pr = quaternion_to_pitch_roll(0.735341, 0.255615, 0.627441, -0.015869)
    expect_x = math.degrees(pr[0])
    expect_y = math.degrees(pr[1])
    x, y = mot_to_tilt_xy(mot, cols)
    assert x == pytest.approx(expect_x)
    assert y == pytest.approx(expect_y)


def test_mot_to_tilt_xy_twelve_elements_without_cols_uses_quaternion():
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
    pr = quaternion_to_pitch_roll(0.735341, 0.255615, 0.627441, -0.015869)
    x, y = mot_to_tilt_xy(mot, None)
    assert x == pytest.approx(math.degrees(pr[0]))
    assert y == pytest.approx(math.degrees(pr[1]))


def test_mot_to_tilt_xy_returns_legacy_tail_when_quaternion_not_finite():
    mot = [
        48,
        0,
        float("nan"),
        float("nan"),
        float("nan"),
        float("nan"),
        0.948257,
        -0.354986,
        -0.083497,
        -44.656766,
        -86.970985,
        23.221568,
    ]
    cols = list(_MOT_COLS_12)
    x, y = mot_to_tilt_xy(mot, cols)
    assert x == pytest.approx(float(mot[-2]))
    assert y == pytest.approx(float(mot[-1]))


def test_mot_to_tilt_xy_eleven_gyro_layout_returns_legacy_tail():
    """Older 11-col headsets have no quaternions → falls through to legacy tail."""
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
    assert x == pytest.approx(float(mot[-2]))
    assert y == pytest.approx(float(mot[-1]))
