import pytest

import update_service as us


def test_parse_semver_tuple():
    assert us.parse_semver_tuple("1.2.3") == (1, 2, 3)
    assert us.parse_semver_tuple("10.0") == (10, 0, 0)
    assert us.parse_semver_tuple("2.1.0-beta") == (2, 1, 0)


def test_semver_less():
    assert us.semver_less("1.0.0", "1.0.1") is True
    assert us.semver_less("1.0.1", "1.0.0") is False
    assert us.semver_less("1.0.0", "1.0.0") is False


def test_validate_manifest_ok():
    m = us.validate_manifest(
        {
            "version": "1.0.0",
            "download_url": "https://cdn.example.com/app.exe",
            "sha256": "a" * 64,
        }
    )
    assert m["version"] == "1.0.0"
    assert m["download_url"].startswith("https://")
    assert m["sha256"] == "a" * 64


@pytest.mark.parametrize(
    "raw,msg",
    [
        ({}, "Manifest must include"),
        (
            {"version": "", "download_url": "https://x/y", "sha256": "a" * 64},
            "non-empty",
        ),
        (
            {"version": "1", "download_url": "http://x", "sha256": "a" * 64},
            "https",
        ),
        (
            {"version": "1", "download_url": "https://x", "sha256": "zz" * 32},
            "sha256",
        ),
    ],
)
def test_validate_manifest_errors(raw, msg):
    with pytest.raises(ValueError, match=msg):
        us.validate_manifest(raw)


def test_check_update_available_newer(monkeypatch):
    monkeypatch.setattr(us, "get_app_version", lambda: "1.0.0")

    def fake_fetch(_url):
        return {
            "version": "1.1.0",
            "download_url": "https://example.com/a.exe",
            "sha256": "b" * 64,
        }

    monkeypatch.setattr(us, "fetch_latest_manifest", fake_fetch)
    newer, m, err = us.check_update_available("https://example.com/manifest.json")
    assert err is None
    assert newer is True
    assert m["version"] == "1.1.0"


def test_check_update_available_up_to_date(monkeypatch):
    monkeypatch.setattr(us, "get_app_version", lambda: "2.0.0")

    def fake_fetch(_url):
        return {
            "version": "1.9.9",
            "download_url": "https://example.com/a.exe",
            "sha256": "c" * 64,
        }

    monkeypatch.setattr(us, "fetch_latest_manifest", fake_fetch)
    newer, m, err = us.check_update_available("https://example.com/manifest.json")
    assert err is None
    assert newer is False
    assert m["version"] == "1.9.9"
