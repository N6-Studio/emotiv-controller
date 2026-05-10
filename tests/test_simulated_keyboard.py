from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def keyboard_controller():
    # Patch app._pynput_keyboard, not pynput: resolving pynput.keyboard.* imports
    # the display backend and fails on headless Linux CI.
    fake_mod = MagicMock()
    instance = MagicMock()
    fake_mod.Controller.return_value = instance
    with patch("app._pynput_keyboard", return_value=fake_mod):
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


def test_sync_hold_repeated_sync_single_physical_press(keyboard_controller):
    """Active movements map to one ``press`` until cleared; repeated sync must not re-press."""
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.key_bindings["forward"] = "w"

    kb.sync({"forward"}, set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1
    assert keyboard_controller.release.call_count == 0

    kb.sync(set(), set(), cfg)
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
    cfg.keyboard_com_enabled = True
    cfg.keyboard_mental_key_mode = "hold"
    cfg.key_bindings["forward"] = "w"
    cfg.com_key_bindings["push"] = "p"

    kb.sync({"forward"}, {"push"}, cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert keys_pressed == {"w", "p"}

    kb.sync(set(), set(), cfg)
    assert kb.pressed_movements == set()
    assert kb.pressed_com_actions == set()


def test_sync_motion_press_rising_edge_only(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "press"
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    keyboard_controller.press.reset_mock()
    keyboard_controller.release.reset_mock()

    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1
    assert keyboard_controller.release.call_count == 1

    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1
    assert keyboard_controller.release.call_count == 1


def test_sync_mental_press_default_rising_edge_only(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_com_enabled = True
    cfg.key_bindings["forward"] = "w"
    cfg.com_key_bindings["push"] = "p"

    kb.sync(set(), set(), cfg)
    keyboard_controller.press.reset_mock()
    keyboard_controller.release.reset_mock()

    kb.sync(set(), {"push"}, cfg)
    assert keyboard_controller.press.call_count == 1
    assert keyboard_controller.release.call_count == 1

    kb.sync(set(), {"push"}, cfg)
    assert keyboard_controller.press.call_count == 1
    assert keyboard_controller.release.call_count == 1


def test_sync_mental_press_first_sync_skips_tap_when_already_active(keyboard_controller):
    """Guards bogus taps when keyboard is enabled while COM is already above threshold."""
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_com_enabled = True
    cfg.com_key_bindings["push"] = "p"

    kb.sync(set(), {"push"}, cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert "p" not in keys_pressed


def test_release_all_clears_tap_prev(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "press"
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    cfg.keyboard_enabled = False
    kb.sync(set(), set(), cfg)

    cfg.keyboard_enabled = True
    kb.sync(set(), set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2


def test_sync_com_keys_suppressed_when_keyboard_com_disabled(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_com_enabled = False
    cfg.key_bindings["forward"] = "w"
    cfg.com_key_bindings["push"] = "p"

    kb.sync({"forward"}, {"push"}, cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert keys_pressed == {"w"}

    kb.sync(set(), set(), cfg)
    assert kb.pressed_movements == set()
    assert kb.pressed_com_actions == set()
