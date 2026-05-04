# -*- mode: python ; coding: utf-8 -*-
import os

# Set by scripts/build.sh (--debug): '1' = console for debugging; default = windowed (no console).
_emotiv_console = os.environ.get("EMOTIV_PYI_DEBUG", "0").strip().lower() in ("1", "true", "yes")

_spec_dir = os.path.abspath(os.path.dirname(SPEC))

_datas = [(os.path.join(_spec_dir, "assets", "app.ico"), "assets")]
_dotenv_src = os.path.join(_spec_dir, ".env")
if os.path.isfile(_dotenv_src):
    # Baked defaults for the frozen EXE; bridge_core loads from sys._MEIPASS.
    _datas.append((_dotenv_src, "."))

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
    name='EmotivController',
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
