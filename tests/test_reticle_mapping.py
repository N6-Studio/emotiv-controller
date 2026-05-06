"""Tests for motion reticle normalization (ACC offsets vs axis limits)."""

import pytest

from core import (
    ACC_Y_MAX,
    ACC_Y_MIN,
    ACC_Z_MAX,
    ACC_Z_MIN,
    reticle_offset_acc_to_normalized,
)


def test_reticle_neutral_at_origin_full_span_acc_y():
    hx_p1, _ = reticle_offset_acc_to_normalized(1.0, 0.0, 0.0, 0.0)
    hx_m1, _ = reticle_offset_acc_to_normalized(-1.0, 0.0, 0.0, 0.0)
    hx_0, hy_0 = reticle_offset_acc_to_normalized(0.0, 0.0, 0.0, 0.0)
    assert hx_p1 == pytest.approx(1.0)
    assert hx_m1 == pytest.approx(-1.0)
    assert hx_0 == pytest.approx(0.0)
    assert hy_0 == pytest.approx(0.0)


def test_reticle_neutral_at_origin_full_span_acc_z():
    _, hy_1 = reticle_offset_acc_to_normalized(0.0, 1.0, 0.0, 0.0)
    _, hy_m1 = reticle_offset_acc_to_normalized(0.0, -1.0, 0.0, 0.0)
    assert hy_1 == pytest.approx(1.0)
    assert hy_m1 == pytest.approx(-1.0)


def test_reticle_asymmetric_neutral_acc_y():
    ny = 0.3
    hx_up, _ = reticle_offset_acc_to_normalized(0.7, 0.0, ny, 0.0)
    hx_down, _ = reticle_offset_acc_to_normalized(-1.3, 0.0, ny, 0.0)
    hx_rest, _ = reticle_offset_acc_to_normalized(0.0, 0.0, ny, 0.0)
    assert hx_up == pytest.approx(1.0)
    assert hx_down == pytest.approx(-1.0)
    assert hx_rest == pytest.approx(0.0)


def test_reticle_acc_y_clamped_beyond_physical_range():
    hx, _ = reticle_offset_acc_to_normalized(2.0, 0.0, 0.0, 0.0)
    assert hx == pytest.approx(1.0)


def test_constants_acc_limits():
    assert ACC_Y_MIN == -1.0
    assert ACC_Y_MAX == 1.0
    assert ACC_Z_MIN == -1.0
    assert ACC_Z_MAX == 1.0
