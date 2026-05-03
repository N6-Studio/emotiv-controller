"""Pure movement / mental-command logic (no UI, I/O, or hardware)."""

from __future__ import annotations

from typing import Iterable

# Mental command actions mapped to movement (Cortex `com[0]` names).
COM_MAPPED_MENTAL_ACTIONS: tuple[str, ...] = ("push", "pull", "left", "right")


def compute_motion_movements(
    x: float,
    y: float,
    neutral_x: float,
    neutral_y: float,
    *,
    threshold_global: bool,
    threshold: float,
    movement_thresholds: dict[str, float],
) -> set[str]:
    """Return abstract movement names active for the given pose vs neutral."""
    if threshold_global:
        t_fwd = t_back = t_left = t_right = float(threshold)
    else:
        m = movement_thresholds
        t_fwd = float(m["forward"])
        t_back = float(m["backward"])
        t_left = float(m["left"])
        t_right = float(m["right"])

    movements: set[str] = set()

    if x <= neutral_x - t_fwd:
        movements.add("forward")
    elif x >= neutral_x + t_back:
        movements.add("backward")

    if y <= neutral_y - t_left:
        movements.add("left")
    elif y >= neutral_y + t_right:
        movements.add("right")

    return movements


def mental_command_to_sets(
    com: Iterable,
    *,
    power_threshold: float,
) -> tuple[set[str], set[str]]:
    """Returns (movement labels for the pad UI, mental actions for COM keys)."""
    com_list = list(com)
    if len(com_list) < 2:
        return set(), set()

    action = str(com_list[0] or "neutral").lower()
    power = float(com_list[1] or 0)

    if power < power_threshold:
        return set(), set()

    mapping = {
        "push": "forward",
        "pull": "backward",
        "left": "left",
        "right": "right",
    }

    movement = mapping.get(action)
    if not movement:
        return set(), set()
    return {movement}, {action}
