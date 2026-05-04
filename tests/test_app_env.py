import os
import sys
from pathlib import Path

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


def test_bundled_dotenv_path_none_when_not_frozen(monkeypatch):
    import bridge_core as bc

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert bc._bundled_dotenv_path() is None


def test_bundled_dotenv_path_when_frozen_and_file(tmp_path, monkeypatch):
    import bridge_core as bc

    mei = tmp_path / "meipass"
    mei.mkdir()
    dotenv = mei / ".env"
    dotenv.write_text("X=1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(mei), raising=False)
    assert bc._bundled_dotenv_path() == dotenv


def test_bundled_dotenv_path_none_when_missing_in_bundle(tmp_path, monkeypatch):
    import bridge_core as bc

    mei = tmp_path / "meipass"
    mei.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(mei), raising=False)
    assert bc._bundled_dotenv_path() is None


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


def test_read_cortex_env_blank_strings_use_defaults(monkeypatch):
    """Blank values from app.env / .env must not hide code defaults in the UI."""
    import app as app_module

    for k in (
        "CORTEX_URL",
        "STREAMS",
        "EMOTIV_CLIENT_ID",
        "EMOTIV_CLIENT_SECRET",
        "EMOTIV_LICENSE",
        "EMOTIV_DEBIT",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CORTEX_URL", "")
    monkeypatch.setenv("STREAMS", "  ")
    monkeypatch.setenv("EMOTIV_CLIENT_ID", "")
    monkeypatch.setenv("EMOTIV_CLIENT_SECRET", "")
    monkeypatch.setenv("EMOTIV_LICENSE", "")
    monkeypatch.setenv("EMOTIV_DEBIT", "")
    ce = app_module.read_cortex_env()
    assert ce.cortex_url == "wss://localhost:6868"
    assert ce.streams == ["mot"]
    assert ce.client_id is None
    assert ce.client_secret is None
    assert ce.license == ""
    assert ce.debit == 1
