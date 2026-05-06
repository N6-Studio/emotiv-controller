"""Canonical identifiers for top-level app screens."""

from __future__ import annotations

from enum import Enum


class AppView(str, Enum):
    MAIN = "main"
    CALIBRATION = "calibration"
    CALIBRATION_REVIEW = "calibration_review"
    SETTINGS = "settings"
    ENV_SETTINGS = "env_settings"
