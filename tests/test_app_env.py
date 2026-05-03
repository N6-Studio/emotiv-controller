import os
from pathlib import Path

import pytest


def test_write_read_app_env_round_trip(tmp_path):
    import app as app_module

    path = tmp_path / "app.env"
    values = {
        "CORTEX_URL": "wss://localhost:6868",
        "STREAMS": "mot,com",
        "EMOTIV_CLIENT_ID": "id1",
        "EMOTIV_CLIENT_SECRET": "secret1",
        "EMOTIV_LICENSE": "",
        "EMOTIV_DEBIT": "1",
        "COM_POWER_THRESHOLD": "0.3",
    }
    app_module.write_app_env_file(path, values)
    loaded = app_module.read_app_env_file_dict(path)
    assert loaded == values


def test_read_app_env_missing_file(tmp_path):
    import app as app_module

    path = tmp_path / "missing.env"
    loaded = app_module.read_app_env_file_dict(path)
    assert all(loaded[k] == "" for k in app_module.APP_ENV_UI_KEYS)


def test_read_app_env_ignores_unknown_and_comments(tmp_path):
    import app as app_module

    path = tmp_path / "app.env"
    path.write_text(
        "# comment\n"
        "FOO=bar\n"
        "CORTEX_URL=wss://x\n"
        "not a valid line\n",
        encoding="utf-8",
    )
    loaded = app_module.read_app_env_file_dict(path)
    assert loaded["CORTEX_URL"] == "wss://x"
    assert loaded["EMOTIV_CLIENT_ID"] == ""


def test_format_env_file_line_quotes_when_needed():
    import app as app_module

    line = app_module.format_env_file_line("STREAMS", "mot, com")
    assert line.startswith("STREAMS=")
    assert '"' in line


def test_reload_app_env_into_os(tmp_path, monkeypatch):
    import app as app_module

    path = tmp_path / "app.env"
    monkeypatch.setattr(app_module, "APP_ENV_PATH", path)
    prev = os.environ.get("CORTEX_URL")
    try:
        path.write_text("CORTEX_URL=wss://reload.test/ws\n", encoding="utf-8")
        app_module.reload_app_env_into_os(path)
        assert os.environ.get("CORTEX_URL") == "wss://reload.test/ws"
    finally:
        if prev is None:
            os.environ.pop("CORTEX_URL", None)
        else:
            os.environ["CORTEX_URL"] = prev


def test_read_cortex_env_reflects_os(monkeypatch):
    import app as app_module

    monkeypatch.setenv("CORTEX_URL", "wss://cortex.example")
    monkeypatch.setenv("STREAMS", "mot")
    monkeypatch.setenv("EMOTIV_CLIENT_ID", "a")
    monkeypatch.setenv("EMOTIV_CLIENT_SECRET", "b")
    monkeypatch.setenv("EMOTIV_LICENSE", "lic")
    monkeypatch.setenv("EMOTIV_DEBIT", "2")
    ce = app_module.read_cortex_env()
    assert ce.cortex_url == "wss://cortex.example"
    assert ce.streams == ["mot"]
    assert ce.client_id == "a"
    assert ce.client_secret == "b"
    assert ce.license == "lic"
    assert ce.debit == 2
