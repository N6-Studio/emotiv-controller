# -*- mode: python ; coding: utf-8 -*-
import os
import re
from pathlib import Path

# Set by scripts/build.sh (--debug): '1' = console for debugging; default = windowed (no console).
_emotiv_console = os.environ.get("EMOTIV_PYI_DEBUG", "0").strip().lower() in ("1", "true", "yes")

_spec_dir = os.path.abspath(os.path.dirname(SPEC))

_datas = [(os.path.join(_spec_dir, "assets", "app.ico"), "assets")]

_ver = os.environ.get("EMOTIV_PYI_APP_VERSION", "").strip()
if not _ver:
    _vf = Path(_spec_dir) / "VERSION"
    if _vf.is_file():
        _ver = _vf.read_text(encoding="utf-8").strip()
if not _ver:
    _ver = "0.0.0-dev"
_ver_safe = re.sub(r'[<>:"/\\|?*]', "_", _ver)
_exe_name = f"EmotivController-{_ver_safe}"

a = Analysis(
    [os.path.join(_spec_dir, "src", "app.py")],
    pathex=[os.path.join(_spec_dir, "src"), _spec_dir],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'bridge_core',
        'toga_app',
        'toga',
        'toga_winforms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=_exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_emotiv_console,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(_spec_dir, "assets", "app.ico")],
)
