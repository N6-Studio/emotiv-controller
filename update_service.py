"""
In-app update helpers for the frozen Windows EXE (HTTPS manifest + staged replace).
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:
    from _release_info import APP_VERSION as _REL_APP_VERSION
    from _release_info import UPDATE_MANIFEST_URL as _REL_MANIFEST_URL
except ImportError:
    _REL_APP_VERSION = "0.0.0-dev"
    _REL_MANIFEST_URL = ""


def get_app_version() -> str:
    return (_REL_APP_VERSION or "0.0.0-dev").strip() or "0.0.0-dev"


def get_update_manifest_url() -> str:
    env = (os.environ.get("EMOTIV_UPDATE_MANIFEST_URL") or "").strip()
    if env:
        return env
    return (_REL_MANIFEST_URL or "").strip()


def parse_semver_tuple(version: str) -> tuple[int, int, int]:
    """Parse MAJOR.MINOR.PATCH (numeric segments only; extra suffixes truncated)."""
    s = (version or "").strip()
    parts = s.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        n = ""
        for c in p:
            if c.isdigit():
                n += c
            else:
                break
        nums.append(int(n) if n else 0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def semver_less(a: str, b: str) -> bool:
    return parse_semver_tuple(a) < parse_semver_tuple(b)


def validate_manifest(raw: dict[str, Any]) -> dict[str, str]:
    try:
        version = str(raw["version"]).strip()
        download_url = str(raw["download_url"]).strip()
        sha256 = str(raw["sha256"]).strip().lower()
    except (KeyError, TypeError) as e:
        raise ValueError("Manifest must include version, download_url, and sha256") from e

    if not version or not download_url or not sha256:
        raise ValueError("Manifest fields must be non-empty")

    if not download_url.lower().startswith("https://"):
        raise ValueError("download_url must use https")

    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("sha256 must be a 64-character lowercase hex string")

    return {"version": version, "download_url": download_url, "sha256": sha256}


def fetch_json_https(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"EmotivMovementBridge/{get_app_version()}"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def fetch_latest_manifest(manifest_url: str) -> dict[str, str]:
    data = fetch_json_https(manifest_url)
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a JSON object")
    return validate_manifest(data)


def download_file_https(url: str, dest: Path, timeout: int = 120) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("download_url must use https")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"EmotivMovementBridge/{get_app_version()}"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def launch_windows_updater(
    *,
    current_exe: Path,
    staged_exe: Path,
    pid: int,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError("In-place update is only supported on Windows")

    fd, ps1_path = tempfile.mkstemp(prefix="emotiv_update_", suffix=".ps1", text=False)
    os.close(fd)
    ps1 = Path(ps1_path)
    invocation = (
        f"$ProcessId = {int(pid)}\n"
        f"$CurrentExe = {repr(str(current_exe))}\n"
        f"$StagedExe = {repr(str(staged_exe))}\n"
        f"$ScriptPath = {repr(str(ps1))}\n"
    )
    body = r"""
$ErrorActionPreference = 'Stop'
while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    Start-Sleep -Milliseconds 300
}
$parent = Split-Path -LiteralPath $CurrentExe
$leaf = Split-Path -Leaf $CurrentExe
$backup = Join-Path $parent ($leaf + '.old')
if (Test-Path -LiteralPath $backup) {
    Remove-Item -LiteralPath $backup -Force
}
Rename-Item -LiteralPath $CurrentExe -NewName ($leaf + '.old') -Force
Move-Item -LiteralPath $StagedExe -Destination $CurrentExe -Force
Start-Process -FilePath $CurrentExe
Remove-Item -LiteralPath $ScriptPath -Force -ErrorAction SilentlyContinue
""".strip()
    ps1.write_text(invocation + "\n" + body + "\n", encoding="utf-8")

    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
        ],
        close_fds=True,
        creationflags=flags,
    )


def check_update_available(manifest_url: str) -> tuple[bool, dict[str, str], Optional[str]]:
    """
    Returns (is_newer, manifest_dict, error_message).
    If error_message is set, is_newer is False and manifest may be empty.
    """
    try:
        m = fetch_latest_manifest(manifest_url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        return False, {}, str(e)

    current = get_app_version()
    latest = m["version"]
    if not semver_less(current, latest):
        return False, m, None
    return True, m, None


def download_and_verify(manifest: dict[str, str]) -> Path:
    safe_ver = "".join(
        c if c.isalnum() or c in "._-" else "_"
        for c in manifest["version"]
    ) or "unknown"
    dest = Path(tempfile.gettempdir()) / f"emotiv_bridge_update_{safe_ver}.exe"
    download_file_https(manifest["download_url"], dest)
    digest = sha256_file(dest)
    if digest != manifest["sha256"].lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValueError("Downloaded file hash does not match manifest (sha256)")
    return dest


def apply_staged_update(staged_exe: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("apply_staged_update requires a frozen executable")
    current = Path(sys.executable).resolve()
    launch_windows_updater(current_exe=current, staged_exe=staged_exe.resolve(), pid=os.getpid())
