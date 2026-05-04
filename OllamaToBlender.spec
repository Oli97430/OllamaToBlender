# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OllamaToBlender.

Build:
    pyinstaller --noconfirm OllamaToBlender.spec

Output:
    dist/OllamaToBlender.exe   (Windows, single-file, windowed)
    dist/OllamaToBlender       (Linux/macOS)
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
HERE = Path(SPECPATH).resolve()

datas = [
    (str(HERE / "assets" / "logo.png"), "assets"),
    (str(HERE / "assets" / "logo.ico"), "assets"),
    (str(HERE / "assets" / "logo_32.png"), "assets"),
    (str(HERE / "assets" / "logo_64.png"), "assets"),
    (str(HERE / "assets" / "logo_128.png"), "assets"),
    (str(HERE / "assets" / "blender_mcp_addon.py"), "assets"),
]
# customtkinter ships a JSON theme tree it loads at runtime
datas += collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy.testing", "pytest", "tornado"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon_path = str(HERE / "assets" / "logo.ico") if sys.platform == "win32" else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OllamaToBlender",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # windowed app — no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
