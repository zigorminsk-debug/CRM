# -*- mode: python ; coding: utf-8 -*-
"""Spec сборки сервера CRM в автономный исполняемый файл (PyInstaller).

Результат: dist/CRM-Server.exe (Windows) или dist/CRM-Server (Linux).
Один файл, без установки Python: внутри вшиты веб-админка, мобильный клиент
(app/static), справочник улиц (app/data/minsk_streets.csv) и файлы версии
(VERSION, BUILD). База SQLite и папка раздачи создаются рядом с exe при запуске.

Запуск: server/build_exe.sh  (его же использует .github/workflows/release.yml)
"""
import os

SPEC_DIR = os.path.abspath(SPECPATH)   # .../server
ROOT = os.path.dirname(SPEC_DIR)       # корень репозитория

datas = [
    (os.path.join(SPEC_DIR, "app", "static"), "app/static"),
    (os.path.join(SPEC_DIR, "app", "data", "minsk_streets.csv"), "app/data"),
    (os.path.join(SPEC_DIR, "app", "data", "minsk_houses.csv.gz"), "app/data"),
    (os.path.join(ROOT, "VERSION"), "."),
    (os.path.join(SPEC_DIR, "build", "BUILD"), "."),
    (os.path.join(ROOT, "icons", "app.ico"), "."),
]

a = Analysis(
    ["run.py"],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "app",
        "app.main",
        "launcher",
        "tkinter",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
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
    name="CRM-Server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # окно управления (tkinter); консольный режим: CRM-Server.exe --no-gui
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "icons", "app.ico"),   # значок CRM-Server.exe
)
