# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs


a = Analysis(
    ['app/main.py'],
    pathex=['app'],
    binaries=collect_dynamic_libs('vosk'),     # AUPedia's speech recognition loads libvosk.dll from its folder
    datas=[('app/assets', 'assets'),
           # run by an external Python with PyTorch, so it ships as a plain file
           ('app/pdfannotator/latex/hf_worker.py', 'pdfannotator/latex'),
           # the signing web page, installed in the user's Google account
           ('app/pdfannotator/cloud/apps_script', 'pdfannotator/cloud/apps_script'),
           # the signing-file page (pdf-lib itself ships in assets/js)
           ('app/pdfannotator/cloud/signing_file', 'pdfannotator/cloud/signing_file'),
           # the signature service, installed into the owner's Cloudflare account
           ('app/pdfannotator/cloud/sign_service', 'pdfannotator/cloud/sign_service'),
           # AUPedia answers "how does this work?" from the guide
           ('README.md', '.')],
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtMultimedia', 'anthropic', 'vosk', 'vosk.vosk_cffi', '_cffi_backend'],
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
    [],
    exclude_binaries=True,
    name='AupedeanAnnotator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='app/assets/aupedean_annotator.ico',
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
    name='AupedeanAnnotator',
)
