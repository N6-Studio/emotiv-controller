import pytest

from core import compute_motion_movements


def _thresholds(**kwargs):
    base = {
        "forward": 5.0,
        "backward": 5.0,
        "left": 5.0,
        "right": 5.0,
    }
    base.update(kwargs)
    return base


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (0.0, 0.0, set()),
        (5.0, 0.0, {"backward"}),
        (-5.0, 0.0, {"forward"}),
        (0.0, 5.0, {"right"}),
        (0.0, -5.0, {"left"}),
        (-5.0, -5.0, {"forward", "left"}),
        (5.0, 5.0, {"backward", "right"}),
    ],
)
def test_global_threshold_at_origin(x, y, expected):
    out = compute_motion_movements(
        x,
        y,
        0.0,
        0.0,
        threshold_global=True,
        threshold=5.0,
        movement_thresholds=_thresholds(),
    )
    assert out == expected


def test_global_boundary_inclusive_forward():
    """x == neutral - t triggers forward (<=)."""
    assert compute_motion_movements(
        95.0,
        0.0,
        100.0,
        0.0,
        threshold_global=True,
        threshold=5.0,
        movement_thresholds=_thresholds(),
    ) == {"forward"}


def test_per_movement_thresholds():
    m = _thresholds(forward=2.0, backward=10.0, left=3.0, right=4.0)
    assert compute_motion_movements(
        97.0,
        0.0,
        100.0,
        0.0,
        threshold_global=False,
        threshold=99.0,
        movement_thresholds=m,
    ) == {"forward"}
    assert compute_motion_movements(
        111.0,
        0.0,
        100.0,
        0.0,
        threshold_global=False,
        threshold=99.0,
        movement_thresholds=m,
    ) == {"backward"}
    assert compute_motion_movements(
        100.0,
        96.0,
        100.0,
        100.0,
        threshold_global=False,
        threshold=99.0,
        movement_thresholds=m,
    ) == {"left"}
    assert compute_motion_movements(
        100.0,
        105.0,
        100.0,
        100.0,
        threshold_global=False,
        threshold=99.0,
        movement_thresholds=m,
    ) == {"right"}


def test_x_axis_mutually_exclusive_forward_or_backward():
    """elif on x: cannot be both forward and backward."""
    out = compute_motion_movements(
        100.0,
        0.0,
        0.0,
        0.0,
        threshold_global=True,
        threshold=5.0,
        movement_thresholds=_thresholds(),
    )
    assert out == {"backward"}
    assert "forward" not in out
