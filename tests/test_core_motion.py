import pytest

from core import (
    compute_motion_movements,
    resolved_movement_thresholds,
    stable_keyboard_motion_movements,
)


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


def test_resolved_movement_thresholds_global():
    assert resolved_movement_thresholds(
        threshold_global=True,
        threshold=4.5,
        movement_thresholds=_thresholds(forward=1.0),
    ) == (4.5, 4.5, 4.5, 4.5)


def test_resolved_movement_thresholds_per_direction():
    m = _thresholds(forward=2.0, backward=10.0, left=3.0, right=4.0)
    assert resolved_movement_thresholds(
        threshold_global=False,
        threshold=99.0,
        movement_thresholds=m,
    ) == (2.0, 10.0, 3.0, 4.0)


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


def _keyboard_hysteresis_kwargs(**extra):
    base = dict(
        neutral_x=100.0,
        neutral_y=100.0,
        threshold_global=True,
        threshold=5.0,
        movement_thresholds=_thresholds(),
        hysteresis_frac=0.4,
    )
    base.update(extra)
    return base


def test_keyboard_hysteresis_holds_forward_across_small_backward_jitter():
    kw = _keyboard_hysteresis_kwargs()
    assert stable_keyboard_motion_movements(x=95.0, y=100.0, prev=set(), **kw) == {
        "forward",
    }
    assert stable_keyboard_motion_movements(x=96.5, y=100.0, prev={"forward"}, **kw) == {
        "forward",
    }
    assert stable_keyboard_motion_movements(x=97.5, y=100.0, prev={"forward"}, **kw) == set()


def test_keyboard_hysteresis_with_empty_prev_matches_raw_motion():
    kwargs = dict(
        neutral_x=0.0,
        neutral_y=0.0,
        threshold_global=True,
        threshold=5.0,
        movement_thresholds=_thresholds(),
        hysteresis_frac=0.4,
    )
    for pair in [
        (-5.0, 0.0),
        (5.0, 0.0),
        (0.0, -5.0),
        (0.0, 5.0),
        (-5.0, -5.0),
    ]:
        x, y = pair
        raw = compute_motion_movements(
            x, y, 0.0, 0.0, threshold_global=True, threshold=5.0, movement_thresholds=_thresholds()
        )
        assert stable_keyboard_motion_movements(x=x, y=y, prev=set(), **kwargs) == raw


def test_keyboard_hysteresis_backward_holds_on_small_reverse_jitter():
    kw = _keyboard_hysteresis_kwargs()
    assert stable_keyboard_motion_movements(x=105.0, y=100.0, prev=set(), **kw) == {
        "backward",
    }
    assert stable_keyboard_motion_movements(x=103.5, y=100.0, prev={"backward"}, **kw) == {
        "backward",
    }
