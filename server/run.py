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
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    _print_addresses(host, port)
    try:
        if getattr(sys, "frozen", False):
            from app.main import app          # в exe модуль уже вшит — импортируем напрямую
            uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"))
        else:
            uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"))
    except PermissionError as e:
        # CRM-Server.exe лежит в папке без прав на запись (например, C:\Program Files):
        # рядом с собой он не может создать базу данных и папку раздачи.
        print(f"\nНе хватает прав записи в папку приложения: {e}")
        print("Перенесите CRM-Server.exe в папку с правами записи (например, C:\\CRM)")
        print("или укажите путь к базе переменной окружения CRM_DB.")
        if getattr(sys, "frozen", False):
            input("Нажмите Enter для выхода...")
        raise SystemExit(1)
