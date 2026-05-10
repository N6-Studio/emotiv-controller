"""Keyboard snapshot behavior when Cortex sends ``mot`` and ``com`` on separate frames."""

from unittest.mock import MagicMock, patch

import pytest

from core import COM_MAPPED_MENTAL_ACTIONS


@pytest.fixture
def bare_stream_app():
    from app import AppConfig
    from toga_app import EmotivBridgeApp

    app = object.__new__(EmotivBridgeApp)
    cfg = AppConfig()
    cfg.neutral_x = 0.5
    cfg.neutral_y = 0.0
    cfg.threshold_global = True
    cfg.threshold = 0.15
    cfg.movement_thresholds = {
        "forward": 0.15,
        "left": 0.15,
        "backward": 0.15,
        "right": 0.15,
    }
    cfg.keyboard_enabled = True
    cfg.keyboard_com_enabled = True
    cfg.key_bindings = {
        "forward": "w",
        "left": "a",
        "backward": "s",
        "right": "d",
    }
    cfg.com_key_bindings = dict(cfg.com_key_bindings)
    cfg.com_key_bindings["push"] = "p"
    cfg.com_power_threshold = 0.2

    app.config_data = cfg
    app.sim_keyboard = MagicMock()
    app.cortex = MagicMock(mot_cols=None)
    app.current_acc_x = 0.0
    app.current_x = 0.0
    app.current_y = 0.0
    app.com_powers = {a: 0.0 for a in COM_MAPPED_MENTAL_ACTIONS}
    app.com_pad_movements = set()
    app._keyboard_last_motion = set()
    app._keyboard_last_com_actions = set()
    app._keyboard_motion_stable = set()
    app.calibration_active = False
    app.calibration_samples = []
    app.current_view = None
    return app


_MOT_STUB = [0] * 12


@patch("toga_app.mot_acc_xyz", return_value=(1.0, 0.2, 0.0))
@patch("toga_app.mot_to_motion_xy", return_value=(0.2, 0.0))
def test_mot_frame_then_com_frame_keeps_both_for_sync(
    _mot_xy, _mot_acc, bare_stream_app
):
    app = bare_stream_app
    app.process_stream_message({"mot": _MOT_STUB})
    app.process_stream_message({"com": ["push", 1.0]})

    motion, com_actions, cfg = app.sim_keyboard.sync.call_args[0]
    assert motion == {"forward"}
    assert com_actions == {"push"}
    assert cfg is app.config_data


@patch("toga_app.mot_acc_xyz", return_value=(1.0, 0.2, 0.0))
@patch("toga_app.mot_to_motion_xy", return_value=(0.2, 0.0))
def test_com_frame_then_mot_frame_keeps_both_for_sync(
    _mot_xy, _mot_acc, bare_stream_app
):
    app = bare_stream_app
    app.process_stream_message({"com": ["push", 1.0]})
    app.process_stream_message({"mot": _MOT_STUB})

    motion, com_actions, _cfg = app.sim_keyboard.sync.call_args[0]
    assert motion == {"forward"}
    assert com_actions == {"push"}
