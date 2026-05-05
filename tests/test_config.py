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


def test_appconfig_invert_axes_default_off():
    from app import AppConfig

    cfg = AppConfig()
    assert cfg.invert_pitch is False
    assert cfg.invert_roll is False
    assert cfg.swap_pitch_roll_axes is True


def test_load_save_invert_axes_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    original = app_module.AppConfig(
        invert_pitch=True,
        invert_roll=True,
        swap_pitch_roll_axes=False,
    )
    app_module.save_config(original)
    loaded = app_module.load_config()
    assert loaded.invert_pitch is True
    assert loaded.invert_roll is True
    assert loaded.swap_pitch_roll_axes is False
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["invert_pitch"] is True
    assert raw["invert_roll"] is True
    assert raw["swap_pitch_roll_axes"] is False


def test_minimal_config_without_invert_keys_gets_them_on_save(monkeypatch, tmp_path):
    """Older config files omitting invert_* / swap_* still round-trip with keys after save."""
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"threshold": 7.0}), encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.invert_pitch is False
    assert cfg.invert_roll is False
    assert cfg.swap_pitch_roll_axes is True
    app_module.save_config(cfg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "invert_pitch" in raw
    assert "invert_roll" in raw
    assert "swap_pitch_roll_axes" in raw
    assert raw["invert_pitch"] is False
    assert raw["invert_roll"] is False
    assert raw["swap_pitch_roll_axes"] is True


def test_load_save_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    original = app_module.AppConfig(
        neutral_x=1.5,
        neutral_y=-2.0,
        keyboard_enabled=True,
        threshold_global=False,
        debug_mode=True,
    )
    app_module.save_config(original)
    loaded = app_module.load_config()
    assert loaded.neutral_x == 1.5
    assert loaded.neutral_y == -2.0
    assert loaded.keyboard_enabled is True
    assert loaded.threshold_global is False
    assert loaded.debug_mode is True
    assert loaded.swap_pitch_roll_axes is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["neutral_x"] == 1.5
    assert raw["debug_mode"] is True
    assert raw["swap_pitch_roll_axes"] is True


def test_load_config_corrupt_json(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x is None


def test_load_config_clears_legacy_neutral_outside_degree_range(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 49.826,
                "neutral_y": -1.12,
                "threshold": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x is None
    assert cfg.neutral_y is None


def test_load_config_keeps_neutral_within_degree_range(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 2.5,
                "neutral_y": -1.1,
                "threshold": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x == pytest.approx(2.5)
    assert cfg.neutral_y == pytest.approx(-1.1)


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


def test_appconfig_com_key_bindings_merge_blank():
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
            "left": "  ",
            "right": "z",
        },
    )
    assert cfg.com_key_bindings["push"] == "x"
    assert cfg.com_key_bindings["pull"] == "e"
    assert cfg.com_key_bindings["left"] == "r"
    assert cfg.com_key_bindings["right"] == "z"


def test_appconfig_partial_movement_thresholds_use_base():
    from app import AppConfig

    cfg = AppConfig(
        threshold=10.0,
        threshold_global=False,
        movement_thresholds={"forward": 1.0},
    )
    assert cfg.movement_thresholds["forward"] == 1.0
    assert cfg.movement_thresholds["backward"] == 10.0
    assert cfg.movement_thresholds["left"] == 10.0
    assert cfg.movement_thresholds["right"] == 10.0


def test_load_config_drops_obsolete_tilt_mode_key(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "neutral_x": 1.0,
                "neutral_y": 2.0,
                "tilt_mode": "horizontal_projection",
                "threshold": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x == pytest.approx(1.0)
    assert cfg.neutral_y == pytest.approx(2.0)
    assert not hasattr(cfg, "tilt_mode")
