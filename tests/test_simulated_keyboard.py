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


class FakeClock:
    """Deterministic clock: ``read()`` returns ``now``; ``advance(dt)`` moves it forward."""

    def __init__(self, start: float = 0.0):
        self.now = float(start)

    def read(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += float(dt)


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
    cfg.keyboard_com_enabled = False
    cfg.key_bindings["forward"] = "w"

    kb.press("forward", "w")
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.release.call_count == 1
    assert "forward" not in kb.pressed_movements


def test_sync_mental_only_without_motion_keyboard(keyboard_controller):
    """Mental COM keys work when motion keyboard output is disabled."""
    from app import AppConfig, SimulatedKeyboard

    kb = SimulatedKeyboard()
    cfg = AppConfig()
    cfg.keyboard_enabled = False
    cfg.keyboard_com_enabled = True
    cfg.keyboard_mental_key_mode = "hold"
    cfg.key_bindings["forward"] = "w"
    cfg.com_key_bindings["push"] = "p"

    kb.sync({"forward"}, {"push"}, cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert keys_pressed == {"p"}

    kb.sync(set(), set(), cfg)
    assert kb.pressed_com_actions == set()


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


def test_sync_motion_spam_first_sync_skips_tap_when_already_active(keyboard_controller):
    """First sync after enable shouldn't spam if motion is already above threshold."""
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "spam"
    cfg.keyboard_motion_repeat_interval_ms = 100
    cfg.key_bindings["forward"] = "w"

    kb.sync({"forward"}, set(), cfg)
    keys_pressed = {c.args[0] for c in keyboard_controller.press.call_args_list}
    assert "w" not in keys_pressed


def test_sync_motion_spam_taps_on_rising_edge_then_repeats(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "spam"
    cfg.keyboard_motion_repeat_interval_ms = 100
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    keyboard_controller.press.reset_mock()
    keyboard_controller.release.reset_mock()

    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.05)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.05)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2

    clock.advance(0.01)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2

    clock.advance(0.10)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 3


def test_sync_motion_spam_respects_motion_repeat_interval_ms(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "spam"
    cfg.keyboard_motion_repeat_interval_ms = 50
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    keyboard_controller.press.reset_mock()

    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.05)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2

    clock.advance(0.05)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 3


def test_sync_motion_spam_falling_edge_resets_timer(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "spam"
    cfg.keyboard_motion_repeat_interval_ms = 100
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.01)
    kb.sync(set(), set(), cfg)
    assert "forward" not in kb._repeat_last_tap_motion

    clock.advance(0.01)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2


def test_sync_mental_spam_repeats_via_repeated_syncs(keyboard_controller):
    """Mental-command spam ticks every ``sync()`` call, even if no new ``com`` arrives."""
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_com_enabled = True
    cfg.keyboard_mental_key_mode = "spam"
    cfg.keyboard_mental_repeat_interval_ms = 100
    cfg.com_key_bindings["push"] = "p"

    kb.sync(set(), set(), cfg)
    keyboard_controller.press.reset_mock()

    kb.sync(set(), {"push"}, cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.05)
    kb.sync(set(), {"push"}, cfg)
    assert keyboard_controller.press.call_count == 1

    clock.advance(0.06)
    kb.sync(set(), {"push"}, cfg)
    assert keyboard_controller.press.call_count == 2


def test_release_all_clears_spam_state(keyboard_controller):
    from app import AppConfig, SimulatedKeyboard

    clock = FakeClock()
    kb = SimulatedKeyboard(clock=clock.read)
    cfg = AppConfig()
    cfg.keyboard_enabled = True
    cfg.keyboard_motion_key_mode = "spam"
    cfg.keyboard_motion_repeat_interval_ms = 100
    cfg.key_bindings["forward"] = "w"

    kb.sync(set(), set(), cfg)
    kb.sync({"forward"}, set(), cfg)
    clock.advance(0.10)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 2
    assert kb._repeat_last_tap_motion.get("forward") is not None

    cfg.keyboard_enabled = False
    kb.sync(set(), set(), cfg)
    assert kb._repeat_last_tap_motion == {}
    assert kb._repeat_last_tap_com == {}

    cfg.keyboard_enabled = True
    kb.sync(set(), set(), cfg)
    clock.advance(0.20)
    kb.sync({"forward"}, set(), cfg)
    assert keyboard_controller.press.call_count == 3


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
