from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def keyboard_controller():
    with patch("pynput.keyboard.Controller") as ctor_mock:
        instance = MagicMock()
        ctor_mock.return_value = instance
        yield instance


def test_press_release_idempotent(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.key_bindings["forward"] = "w"

    kb.press("forward", "w")
    kb.press("forward", "w")
    assert keyboard_controller.press.call_count == 1

    kb.release("forward", "w")
    kb.release("forward", "w")
    assert keyboard_controller.release.call_count == 1


def test_shared_physical_key_refcount(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.key_bindings["forward"] = "w"
    cfg.key_bindings["backward"] = "w"

    kb.press("forward", "w")
    kb.press("backward", "w")
    assert keyboard_controller.press.call_count == 1

    kb.release("forward", "w")
    assert keyboard_controller.release.call_count == 0

    kb.release("backward", "w")
    assert keyboard_controller.release.call_count == 1


def test_sync_keyboard_disabled_releases_all(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = False
    cfg.key_bindings["forward"] = "w"

    kb.press("forward", "w")
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.release.call_count == 1
    assert "forward" not in kb.pressed_movements


def test_sync_motion_and_com(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.key_bindings["forward"] = "w"
    cfg.com_key_bindings["push"] = "p"

    kb.sync({"forward"}, {"push"}, cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert keys_pressed == {"w", "p"}

    kb.sync(set(), set(), cfg)
    assert kb.pressed_movements == set()
    assert kb.pressed_com_actions == set()
