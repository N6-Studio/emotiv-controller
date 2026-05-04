"""EMOTIV Movement bridge — public API for tests and Toga entry point."""

def _pynput_keyboard():
    from pynput import keyboard as pynput_keyboard

    return pynput_keyboard


_pynput_keyboard.__emotiv_default_pynput__ = True

from bridge_core import (
    APP_ENV_PATH,
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
    app_env_form_values,
    format_env_file_line,
    load_config,
    read_app_env_file_dict,
    read_cortex_env,
    reload_app_env_into_os,
    save_config,
    write_app_env_file,
)


def main() -> None:
    from toga_app import run_app

    run_app()


if __name__ == "__main__":
    main()
