import json

import pytest


def test_load_config_missing_file(monkeypatch, tmp_path):
    import app as app_module

    monkeypatch.setattr(app_module, "CONFIG_PATH", tmp_path / "missing.json")
    cfg = app_module.load_config()
    assert cfg.neutral_x is None
    assert cfg.neutral_y is None
    assert cfg.debug_mode is False


def test_appconfig_debug_mode_default():
    from app import AppConfig

    assert AppConfig().debug_mode is False


def test_keyboard_motion_hysteresis_default_and_clamp():
    from app import AppConfig
    from core import DEFAULT_KEYBOARD_MOTION_HYSTERESIS_FRAC

    assert AppConfig().keyboard_motion_hysteresis_frac == pytest.approx(
        DEFAULT_KEYBOARD_MOTION_HYSTERESIS_FRAC
    )
    assert AppConfig(keyboard_motion_hysteresis_frac=-0.5).keyboard_motion_hysteresis_frac == 0.0
    assert AppConfig(keyboard_motion_hysteresis_frac=9.0).keyboard_motion_hysteresis_frac == 1.0


def test_keyboard_motion_hysteresis_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.AppConfig(keyboard_motion_hysteresis_frac=0.25)
    app_module.save_config(cfg)
    loaded = app_module.load_config()
    assert loaded.keyboard_motion_hysteresis_frac == pytest.approx(0.25)


def test_minimal_config_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"threshold": 0.08}), encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.threshold == pytest.approx(0.08)
    app_module.save_config(cfg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "quaternion_map_w" not in raw


def test_load_save_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    original = app_module.AppConfig(
        neutral_x=0.12,
        neutral_y=-0.02,
        keyboard_enabled=True,
        threshold_global=False,
        debug_mode=True,
    )
    app_module.save_config(original)
    loaded = app_module.load_config()
    assert loaded.neutral_x == pytest.approx(0.12)
    assert loaded.neutral_y == pytest.approx(-0.02)
    assert loaded.keyboard_enabled is True
    assert loaded.threshold_global is False
    assert loaded.debug_mode is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["neutral_x"] == pytest.approx(0.12)
    assert raw["debug_mode"] is True


def test_load_config_corrupt_json(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x is None


def test_load_config_clears_legacy_neutral_outside_acc_range(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 49.826,
                "neutral_y": -1.12,
                "threshold": 0.12,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x is None
    assert cfg.neutral_y is None


def test_load_config_keeps_neutral_within_acc_range(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 0.12,
                "neutral_y": -0.35,
                "threshold": 0.12,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x == pytest.approx(0.12)
    assert cfg.neutral_y == pytest.approx(-0.35)


def test_load_config_migrates_legacy_degree_threshold(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "threshold": 10.0,
                "movement_thresholds": {
                    "forward": 10.0,
                    "left": 10.0,
                    "backward": 10.0,
                    "right": 10.0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.threshold == pytest.approx(app_module.DEFAULT_THRESHOLD)
    for m in ("forward", "backward", "left", "right"):
        assert cfg.movement_thresholds[m] == pytest.approx(app_module.DEFAULT_THRESHOLD)


def test_load_config_ignores_unknown_json_keys(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        '{"debug_mode": true, "future_field": 99, "cortex_url": "wss://x"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.debug_mode is True
    assert cfg.cortex_url == "wss://x"
    assert not hasattr(cfg, "future_field")


def test_appconfig_com_key_bindings_blank_disables():
    from app import AppConfig

    cfg = AppConfig(
        key_bindings={
            "forward": "w",
            "left": "a",
            "backward": "s",
            "right": "d",
        },
        com_key_bindings={
            "push": "x",
            "pull": "",
            "lift": "  ",
            "drop": "z",
        },
    )
    assert cfg.com_key_bindings["push"] == "x"
    assert cfg.com_key_bindings["pull"] == ""
    assert cfg.com_key_bindings["lift"] == ""
    assert cfg.com_key_bindings["drop"] == "z"


def test_appconfig_com_key_bindings_missing_keys_use_defaults():
    from app import AppConfig

    cfg = AppConfig(
        key_bindings={
            "forward": "w",
            "left": "a",
            "backward": "s",
            "right": "d",
        },
        com_key_bindings={"push": "x"},
    )
    assert cfg.com_key_bindings["push"] == "x"
    assert cfg.com_key_bindings["pull"] == "e"
    assert cfg.com_key_bindings["lift"] == "r"
    assert cfg.com_key_bindings["drop"] == "f"


def test_appconfig_partial_movement_thresholds_use_base():
    from app import AppConfig

    cfg = AppConfig(
        threshold=0.2,
        threshold_global=False,
        movement_thresholds={"forward": 0.08},
    )
    assert cfg.movement_thresholds["forward"] == pytest.approx(0.08)
    assert cfg.movement_thresholds["backward"] == pytest.approx(0.2)
    assert cfg.movement_thresholds["left"] == pytest.approx(0.2)
    assert cfg.movement_thresholds["right"] == pytest.approx(0.2)


def test_load_config_drops_obsolete_tilt_mode_key(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 0.1,
                "neutral_y": 0.05,
                "tilt_mode": "horizontal_projection",
                "threshold": 0.12,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x == pytest.approx(0.1)
    assert cfg.neutral_y == pytest.approx(0.05)
    assert not hasattr(cfg, "tilt_mode")
