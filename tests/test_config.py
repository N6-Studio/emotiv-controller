import json

import pytest


def test_load_config_missing_file(monkeypatch, tmp_path):
    import app as app_module

    monkeypatch.setattr(app_module, "CONFIG_PATH", tmp_path / "missing.json")
    cfg = app_module.load_config()
    assert cfg.neutral_x is None
    assert cfg.neutral_y is None


def test_load_save_round_trip(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    original = app_module.AppConfig(
        neutral_x=1.5,
        neutral_y=-2.0,
        keyboard_enabled=True,
        threshold_global=False,
    )
    app_module.save_config(original)
    loaded = app_module.load_config()
    assert loaded.neutral_x == 1.5
    assert loaded.neutral_y == -2.0
    assert loaded.keyboard_enabled is True
    assert loaded.threshold_global is False
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["neutral_x"] == 1.5


def test_load_config_corrupt_json(monkeypatch, tmp_path):
    import app as app_module

    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(app_module, "CONFIG_PATH", path)
    cfg = app_module.load_config()
    assert cfg.neutral_x is None


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
