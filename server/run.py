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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402

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
    print(f"CRM server: http://{host}:{port}  (админка /admin, инженер /m, docs /docs)")
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
