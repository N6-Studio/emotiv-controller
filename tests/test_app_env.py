from app import (
    AppConfig,
    apply_cortex_env_form_to_config,
    app_env_form_values,
    read_cortex_env,
)


def test_read_cortex_env_from_config():
    cfg = AppConfig(
        cortex_url="wss://cortex.example/ws",
        cortex_streams="mot, com",
        emotiv_client_id="a",
        emotiv_client_secret="b",
        emotiv_license="lic",
        emotiv_debit=2,
    )
    ce = read_cortex_env(cfg)
    assert ce.cortex_url == "wss://cortex.example/ws"
    assert ce.streams == ["mot", "com"]
    assert ce.client_id == "a"
    assert ce.client_secret == "b"
    assert ce.license == "lic"
    assert ce.debit == 2


def test_read_cortex_env_blank_strings_use_defaults():
    cfg = AppConfig(
        cortex_url="",
        cortex_streams="  ",
        emotiv_client_id="",
        emotiv_client_secret="",
        emotiv_license="",
        emotiv_debit=1,
    )
    ce = read_cortex_env(cfg)
    assert ce.cortex_url == "wss://localhost:6868"
    assert ce.streams == ["mot"]
    assert ce.client_id is None
    assert ce.client_secret is None
    assert ce.license == ""
    assert ce.debit == 1


def test_app_env_form_values_round_trip():
    cfg = AppConfig(
        cortex_url="wss://x",
        cortex_streams="mot",
        emotiv_client_id="id",
        emotiv_client_secret="sec",
        emotiv_license="L",
        emotiv_debit=3,
    )
    form = app_env_form_values(cfg)
    assert form["CORTEX_URL"] == "wss://x"
    assert form["STREAMS"] == "mot"
    assert form["EMOTIV_CLIENT_ID"] == "id"
    assert form["EMOTIV_CLIENT_SECRET"] == "sec"
    assert form["EMOTIV_LICENSE"] == "L"
    assert form["EMOTIV_DEBIT"] == "3"


def test_apply_cortex_env_form_to_config():
    cfg = AppConfig()
    apply_cortex_env_form_to_config(
        cfg,
        {
            "CORTEX_URL": "wss://new",
            "STREAMS": "mot,com",
            "EMOTIV_CLIENT_ID": "x",
            "EMOTIV_CLIENT_SECRET": "y",
            "EMOTIV_LICENSE": "z",
            "EMOTIV_DEBIT": "4",
        },
    )
    assert cfg.cortex_url == "wss://new"
    assert cfg.cortex_streams == "mot,com"
    assert cfg.emotiv_client_id == "x"
    assert cfg.emotiv_client_secret == "y"
    assert cfg.emotiv_license == "z"
    assert cfg.emotiv_debit == 4
