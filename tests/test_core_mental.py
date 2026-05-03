import pytest

from core import mental_command_to_sets


def test_power_below_threshold():
    assert mental_command_to_sets(["push", 0.24], power_threshold=0.25) == (set(), set())


def test_power_at_threshold_active():
    m, a = mental_command_to_sets(["push", 0.25], power_threshold=0.25)
    assert m == {"forward"}
    assert a == {"push"}


def test_mapped_actions():
    assert mental_command_to_sets(["pull", 1.0], power_threshold=0.0) == (
        {"backward"},
        {"pull"},
    )
    assert mental_command_to_sets(["left", 0.5], power_threshold=0.1) == (
        {"left"},
        {"left"},
    )
    assert mental_command_to_sets(["right", 0.5], power_threshold=0.1) == (
        {"right"},
        {"right"},
    )


def test_neutral_and_unknown():
    assert mental_command_to_sets(["neutral", 1.0], power_threshold=0.0) == (set(), set())
    assert mental_command_to_sets(["lift", 1.0], power_threshold=0.0) == (set(), set())


def test_short_com_list():
    assert mental_command_to_sets([], power_threshold=0.0) == (set(), set())
    assert mental_command_to_sets(["push"], power_threshold=0.0) == (set(), set())


def test_missing_power_defaults_to_zero():
    assert mental_command_to_sets(["push", None], power_threshold=0.5) == (set(), set())
    assert mental_command_to_sets(["push", ""], power_threshold=0.0) == (
        {"forward"},
        {"push"},
    )
