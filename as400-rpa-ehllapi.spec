# -*- mode: python ; coding: utf-8 -*-
#
# Build (must use 32-bit Python — ehlapi32.dll is 32-bit):
#   py32\python.exe -m pip install pyinstaller
#   py32\python.exe -m PyInstaller as400-rpa-ehllapi.spec
#
# ehlapi32.dll is NOT bundled — loaded at runtime from IBM i Access installation:
#   C:\Program Files (x86)\IBM\Client Access\Emulator\ehlapi32.dll
# Deploy .exe + .env together; py32\ is not needed on the user's machine.

a = Analysis(
    ['as400-rpa.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pyodbc'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PIL', 'pyautogui', 'pytesseract', 'pygetwindow', 'tkinter',
              'numpy', 'pandas', 'matplotlib'],
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
    name='as400-rpa-ehllapi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
