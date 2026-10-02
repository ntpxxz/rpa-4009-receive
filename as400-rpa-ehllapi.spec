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

# cover_splash.png = cover.png with the static "Loading.." text painted black
# so pyi_splash can draw animated status text over it cleanly.
# text_pos: top-left of the cleared area (x=630, y=625) in the 1531x862 image.
splash = Splash(
    'cover_splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(630, 625),
    text_size=13,
    text_color='#39FF14',
    text_default='Loading.',
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
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
    console=False,
    icon='black-box.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
