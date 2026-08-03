# PyInstaller specification for a single-file GUI including the fixed Pi payload.

from pathlib import Path

from vpn_gateway_gui.installer import INSTALL_BUNDLE_FILES


ROOT = Path(SPECPATH)
deployment_data = [
    (str(ROOT / relative), str(Path(relative).parent) if Path(relative).parent != Path(".") else ".")
    for relative in INSTALL_BUNDLE_FILES
]

a = Analysis(
    [str(ROOT / "gui_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=deployment_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VpnGatewayManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
