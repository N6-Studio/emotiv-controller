"""Tests for motion reticle normalization (pitch/roll offsets vs quaternion limits)."""

import pytest

from core import (
    TILT_PITCH_MAX_DEG,
    TILT_PITCH_MIN_DEG,
    TILT_ROLL_MAX_DEG,
    TILT_ROLL_MIN_DEG,
    reticle_offset_deg_to_normalized,
)


def test_reticle_neutral_at_origin_full_span_pitch():
    hx_p90, _ = reticle_offset_deg_to_normalized(90.0, 0.0, 0.0, 0.0)
    hx_m90, _ = reticle_offset_deg_to_normalized(-90.0, 0.0, 0.0, 0.0)
    hx_0, hy_0 = reticle_offset_deg_to_normalized(0.0, 0.0, 0.0, 0.0)
    assert hx_p90 == pytest.approx(1.0)
    assert hx_m90 == pytest.approx(-1.0)
    assert hx_0 == pytest.approx(0.0)
    assert hy_0 == pytest.approx(0.0)


def test_reticle_neutral_at_origin_full_span_roll():
    _, hy_180 = reticle_offset_deg_to_normalized(0.0, 180.0, 0.0, 0.0)
    _, hy_m180 = reticle_offset_deg_to_normalized(0.0, -180.0, 0.0, 0.0)
    assert hy_180 == pytest.approx(1.0)
    assert hy_m180 == pytest.approx(-1.0)


def test_reticle_asymmetric_neutral_pitch_30():
    """Neutral pitch 30°: +60° to max pitch and -120° to min pitch fill ±1."""
    ny = 30.0
    hx_up, _ = reticle_offset_deg_to_normalized(60.0, 0.0, ny, 0.0)
    hx_down, _ = reticle_offset_deg_to_normalized(-120.0, 0.0, ny, 0.0)
    hx_rest, _ = reticle_offset_deg_to_normalized(0.0, 0.0, ny, 0.0)
    assert hx_up == pytest.approx(1.0)
    assert hx_down == pytest.approx(-1.0)
    assert hx_rest == pytest.approx(0.0)


def test_reticle_pitch_clamped_beyond_physical_range():
    """Beyond reachable pitch from neutral; output clamps to ±1."""
    hx, _ = reticle_offset_deg_to_normalized(100.0, 0.0, 0.0, 0.0)
    assert hx == pytest.approx(1.0)


def test_constants_match_quaternion_decomposition_ranges():
    assert TILT_PITCH_MIN_DEG == -90.0
    assert TILT_PITCH_MAX_DEG == 90.0
    assert TILT_ROLL_MIN_DEG == -180.0
    assert TILT_ROLL_MAX_DEG == 180.0
