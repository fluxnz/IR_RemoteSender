# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for IR Remote Sender

from pathlib import Path
import re


def _bump_build_version(version_file: Path) -> str:
    version_file.parent.mkdir(parents=True, exist_ok=True)

    if not version_file.exists():
        version_file.write_text("v2.0\n", encoding="utf-8")
        return "v2.0"

    current = version_file.read_text(encoding="utf-8").strip() or "v2.0"
    match = re.fullmatch(r"v(\d+)\.(\d+)", current)
    if not match:
        major, build = 2, 0
    else:
        major, build = int(match.group(1)), int(match.group(2))

    next_version = f"v{major}.{build + 1}"
    version_file.write_text(next_version + "\n", encoding="utf-8")
    return next_version


_ROOT = Path.cwd()
_VERSION_FILE = _ROOT / "conf" / "app_version.txt"
_BUILT_VERSION = _bump_build_version(_VERSION_FILE)
print(f"Building IR Remote Sender {_BUILT_VERSION}")

a = Analysis(
    ['Remote.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('images', 'images'),
        ('conf', 'conf'),
    ],
    hiddenimports=['pystray', 'PIL', 'serial', 'serial.tools', 'serial.tools.list_ports', 'keyboard'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Remote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    contents_directory='.',
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='images/app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Remote',
)
