#!/usr/bin/env python3
"""Запуск сервера CRM.

    python3 run.py                # порт 8000
    PORT=9000 python3 run.py      # другой порт
    CRM_DB=/var/lib/crm.sqlite3 python3 run.py

Тот же файл — точка входа для собранного CRM-Server.exe (PyInstaller):
двойной щелчок поднимает сервер на http://0.0.0.0:8000, Python не нужен.
"""
import multiprocessing
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402

# Журнал uvicorn без ANSI-цветов: классическая консоль Windows (conhost без VT)
# показывает их мусором вида «←[32mINFO←[0m».
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _console_tweaks() -> None:
    """Windows: выключаем QuickEdit у консоли собранного exe.

    QuickEdit — случайный клик мышью по окну включает выделение текста
    (в заголовке появляется «Выбрать») и замораживает вывод процесса:
    сервер висит и не отвечает, хотя запущен. Для окна сервера это вредно.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE = 0x0040
            ENABLE_EXTENDED_FLAGS = 0x0080
            new_mode = ((mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS) & 0xFFFFFFFF
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def _local_ips() -> list[str]:
    """IP-адреса этого компьютера в локальной сети (без внешних библиотек)."""
    ips: list[str] = []
    try:
        # UDP-сокет с connect() ничего не отправляет — ОС просто выбирает маршрут
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
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


def _print_addresses(host: str, port: int) -> None:
    """Понятная стартовая строка: куда заходить браузером (0.0.0.0 в браузере не открывается)."""
    if host != "0.0.0.0":
        print(f"CRM server: http://{host}:{port}  (админка /admin, инженер /m, docs /docs)")
        return
    print(f"CRM server запущен, порт {port}. Открывайте в браузере:")
    print(f"  на этом компьютере:   http://127.0.0.1:{port}")
    ips = _local_ips()
    if ips:
        for ip in ips:
            print(f"  с телефонов и других ПК: http://{ip}:{port}")
    else:
        print("  с других устройств:   http://<IP этого компьютера>:" f"{port}  (узнать: ipconfig)")
    print("  разделы: /admin — админка, /m — приложение инженера, /docs — описание API")
    if getattr(sys, "frozen", False) and os.name == "nt":
        print("Если с другого устройства адрес не открывается — разрешите порт "
              f"{port} во входящих правилах брандмауэра Windows.")

if __name__ == "__main__":
    multiprocessing.freeze_support()          # корректный запуск собранного exe в Windows
    # Windows: вывод exe часто перенаправлен в файл/консоль с однобайтовой кодировкой
    # (cp1252/cp866) — кириллица в сообщениях уронит print с UnicodeEncodeError.
    # Принудительно переводим потоки вывода на UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    _console_tweaks()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    _print_addresses(host, port)
    try:
        if getattr(sys, "frozen", False):
            from app.main import app          # в exe модуль уже вшит — импортируем напрямую
            uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"),
                        log_config=LOG_CONFIG)
        else:
            uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"),
                        log_config=LOG_CONFIG)
    except PermissionError as e:
        # CRM-Server.exe лежит в папке без прав на запись (например, C:\Program Files):
        # рядом с собой он не может создать базу данных и папку раздачи.
        print(f"\nНе хватает прав записи в папку приложения: {e}")
        print("Перенесите CRM-Server.exe в папку с правами записи (например, C:\\CRM)")
        print("или укажите путь к базе переменной окружения CRM_DB.")
        if getattr(sys, "frozen", False):
            input("Нажмите Enter для выхода...")
        raise SystemExit(1)
