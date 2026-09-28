#!/usr/bin/env python3
"""Запуск сервера CRM.

    python3 run.py                # порт 8000
    PORT=9000 python3 run.py      # другой порт
    CRM_DB=/var/lib/crm.sqlite3 python3 run.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"CRM server: http://{host}:{port}  (админка /admin, инженер /m, docs /docs)")
    uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"))
