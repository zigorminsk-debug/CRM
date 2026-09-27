"""Пути приложения при запуске из исходников и из собранного exe (PyInstaller).

Когда сервер собран в CRM-Server.exe, модули и ресурсы (static, справочник улиц,
VERSION) распаковываются во временный каталог ``sys._MEIPASS`` — он доступен
только для чтения. Записываемые файлы (база SQLite, папка раздачи downloads)
кладутся рядом с exe, чтобы переживали перезапуск и обновление.
"""
from __future__ import annotations

import os
import socket
import sys


def is_frozen() -> bool:
    """True, если сервер запущен из собранного exe (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> str:
    """Каталог с ресурсами, вшитыми в exe (только чтение)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    # из исходников ресурсы лежат в самом каталоге app/
    return os.path.dirname(os.path.abspath(__file__))


def exe_dir() -> str:
    """Каталог, куда можно писать: рядом с exe; из исходников — каталог server/."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # server/app/_runtime.py → server/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def writable_dir(name: str) -> str:
    """Подкаталог рядом с exe (создаётся при необходимости)."""
    path = os.path.join(exe_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


def local_ips() -> list[str]:
    """IP-адреса этого компьютера в локальной сети (без внешних библиотек)."""
    ips: list[str] = []
    try:
        # UDP-сокет с connect() ничего не отправляет — ОС просто выбирает маршрут
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips
