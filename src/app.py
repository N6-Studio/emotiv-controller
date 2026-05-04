"""EMOTIV Movement bridge — public API for tests and Toga entry point."""

def _pynput_keyboard():
    from pynput import keyboard as pynput_keyboard

    return pynput_keyboard


_pynput_keyboard.__emotiv_default_pynput__ = True

from bridge_core import (
    APP_ENV_UI_KEYS,
    AppConfig,
    CONFIG_PATH,
    CortexClient,
    CortexEnv,
    DEFAULT_COM_KEY_BINDINGS,
    DEFAULT_COM_POWER_THRESHOLD,
    DEFAULT_THRESHOLD,
    MOVEMENTS,
    SimulatedKeyboard,
    apply_cortex_env_form_to_config,
    app_env_form_values,
    load_config,
    read_cortex_env,
    save_config,
)


def main() -> None:
    from toga_app import run_app

    run_app()


if __name__ == "__main__":
    main()
