# -*- mode: python ; coding: utf-8 -*-

import PySide6
from pathlib import Path

# 1. 提取需要的资源
datas = [
    ('app/resources', 'app/resources'),
]

# 2. 只取中文翻译文件，不取 PySide6 底下的所有无关文件
pyside_dir = Path(PySide6.__file__).parent
translation_file = pyside_dir / 'translations' / 'qtbase_zh_CN.qm'
if translation_file.exists():
    datas.append((str(translation_file), 'PySide6/translations'))

a = Analysis(
    ['verthandi.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['Crypto.Cipher.AES', 'psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization', 'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets', 'PySide6.QtQuick', 'PySide6.QtQml',
        'PySide6.QtSql', 'PySide6.QtTest'
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Verthandi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Verthandi',
)
