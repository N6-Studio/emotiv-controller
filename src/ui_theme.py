"""Reusable Toga ``Pack`` presets for typography and muted/error accents."""

from __future__ import annotations

from toga.style import Pack


def pack_status_line(**extra: object) -> Pack:
    """Main dashboard status / COM caption tone (#6b7280 @ 11px)."""
    return Pack(color="#6b7280", font_size=11, **extra)


def pack_error(**extra: object) -> Pack:
    """Inline error text on the main strip."""
    return Pack(color="#b91c1c", **extra)


def pack_section_title(**extra: object) -> Pack:
    """Large settings/calibration screen headings."""
    return Pack(font_size=22, font_weight="bold", **extra)


def pack_review_section_title(**extra: object) -> Pack:
    """Slightly smaller verify/review heading."""
    return Pack(font_size=20, font_weight="bold", **extra)


def pack_muted_body(**extra: object) -> Pack:
    """Calibration/review body copy and secondary labels."""
    return Pack(color="#6b7280", **extra)


def pack_muted_small(**extra: object) -> Pack:
    """Helper lines under switches (10px)."""
    return Pack(color="#6b7280", font_size=10, **extra)


def pack_com_header(**extra: object) -> Pack:
    """COM strip heading tone."""
    return Pack(font_weight="bold", color="#6b7280", **extra)


def pack_com_hint(**extra: object) -> Pack:
    """COM threshold hint (muted #9ca3af)."""
    return Pack(font_size=10, color="#9ca3af", **extra)


def pack_calibration_timer(**extra: object) -> Pack:
    """Large teal countdown."""
    return Pack(
        font_size=40,
        font_weight="bold",
        color="#14b8a6",
        **extra,
    )


def pack_calibration_averages_line(**extra: object) -> Pack:
    """Running averages line during calibration."""
    return Pack(color="#4b5563", font_size=14, **extra)


def pack_action_button(*, gap_after: bool = False) -> Pack:
    """Larger primary actions; ``gap_after`` when another button sits to the right."""
    return Pack(
        font_size=14,
        font_weight="bold",
        padding_top=12,
        padding_bottom=12,
        padding_left=16,
        padding_right=10 if gap_after else 18,
    )
