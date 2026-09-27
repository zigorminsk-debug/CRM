#!/usr/bin/env python3
"""Собирает файлы для раздачи в папку downloads/.

* CRM-Windows.exe        — клиент Windows (если уже собран в winclient/dist или передан аргументом)
* CRM-Windows-Client.zip — exe + краткая инструкция
* crm-server.zip         — сервер (Python) без базы, кэшей и виртуальных окружений
* crm-sources.zip        — исходники Windows-клиента и Android-приложения
* CRM-Engineer.apk       — копируется из android/dist (его собирает android/build_apk.sh или CI)
* README.txt             — инструкция для конечного пользователя

Запуск: python3 tools/pack_downloads.py
"""
from __future__ import annotations

import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "downloads")

EXCLUDE_DIRS = {".git", "__pycache__", ".venv", ".venv-srv", "build", "dist", "downloads",
                ".gradle", ".idea", "node_modules", "data", ".pytest_cache", "signing"}
EXCLUDE_SUFFIX = {".pyc", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log", ".apk", ".exe"}
EXCLUDE_NAMES = {"local.properties"}


def zadd(zf: zipfile.ZipFile, path: str, arcname: str) -> None:
    base = os.path.basename(path)
    if base in EXCLUDE_NAMES or os.path.splitext(base)[1] in EXCLUDE_SUFFIX:
        return
    zf.write(path, arcname)


def zip_tree(zf: zipfile.ZipFile, src_dir: str, arc_prefix: str = "") -> int:
    n = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            path = os.path.join(root, f)
            rel = os.path.relpath(path, src_dir)
            zadd(zf, path, os.path.join(arc_prefix, rel) if arc_prefix else rel)
            n += 1
    return n


README_TXT = """CRM — заявки на заправку картриджей и ремонт оргтехники
=========================================================

Файлы в этой папке:

  CRM-Windows.exe        — клиент для Windows (портативный, установка не требуется)
  CRM-Windows-Client.zip — тот же клиент с инструкцией
  crm-server.zip         — сервер (Python), разворачивается на машине с публичным IP
  crm-sources.zip        — исходники Windows-клиента и Android-приложения

Установка (5 минут)
-------------------
1. Сервер:
     распакуйте crm-server.zip
     python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
     PORT=8000 .venv/bin/python run.py
   Первый запуск создаёт базу, зоны, справочник работ и пользователей:
     admin / admin123        — диспетчерская (веб-админка http://IP:8000/admin)
     engineer / engineer123  — инженер (мобильное приложение http://IP:8000/m)

2. Windows-клиент:
     запустите CRM-Windows.exe → «Настройки сервера» → http://<IP сервера>:8000
     (адрес сохраняется в %APPDATA%\\CRM-Kartridzh\\client.ini)

3. Инженер:
     откройте на телефоне http://<IP сервера>:8000/m и добавьте страницу на главный экран,
     либо соберите APK из crm-sources.zip (папка android, Android Studio).

Порядок работы
--------------
* Диспетчер принимает заявку в Windows-клиенте: обязательные поля УНП, р/с, телефон
  (приводится к +375 XX XXX-XX-XX), контактное лицо, работа из выпадающего списка, адрес
  (переводится в координаты для Яндекс.Навигатора) и пояснение.
* Сервер по адресу определяет район, находит инженера, закреплённого за зоной,
  и отправляет заявку в его приложение.
* Инженер видит маршрут на день (срочные — первыми), нажимает «Поехали» — открывается
  Яндекс.Навигатор. На месте отмечает чекбокс «Готово на месте» либо «Забор в офис».
* При заборе в офис сервер ставит доставку заказчику на следующий рабочий день;
  если оборудование не готово — дата переносится.
* На время отпуска инженера зоны передаются замене, а незавершённые заявки
  перераспределяются автоматически (админка → «Отпуска и замены»).
* Вся история заявок доступна с поиском по контрагенту, УНП, р/с, телефону и периоду,
  с выгрузкой в CSV.

Обновления
----------
* Новые сборки собираются автоматически на GitHub (Actions) при каждом изменении и
  публикуются как релиз с собственным номером версии.
* Windows-клиент и приложение инженера проверяют обновления сами: при запуске спрашивают
  сервер (/api/updates), скачивают новую сборку из релиза и ставят её поверх установленной.
* Все сборки подписаны постоянным ключом проекта, поэтому обновление ставится «поверх»
  без удаления установленной версии; настройки и профиль инженера сохраняются.

Подробная документация — в README.md на сервере (/docs — описание API).
"""


def main() -> None:
    os.makedirs(OUT, exist_ok=True)

    # 1. exe (если собран)
    exe_src = os.path.join(ROOT, "winclient", "dist", "CRM-Windows.exe")
    exe = os.path.join(OUT, "CRM-Windows.exe")
    if os.path.exists(exe_src):
        shutil.copy2(exe_src, exe)
        print(f"  exe:  {exe} ({os.path.getsize(exe)//1024} КБ)")
    elif os.path.exists(exe):
        print(f"  exe:  уже на месте ({os.path.getsize(exe)//1024} КБ)")
    else:
        print("  exe:  не собран — выполните winclient/build_windows.sh")

    # 1.1 APK инженера (если собран)
    apk_src = os.path.join(ROOT, "android", "dist", "CRM-Engineer.apk")
    apk = os.path.join(OUT, "CRM-Engineer.apk")
    if os.path.exists(apk_src):
        shutil.copy2(apk_src, apk)
        print(f"  apk:  {apk} ({os.path.getsize(apk)//1024} КБ)")
    elif os.path.exists(apk):
        print(f"  apk:  уже на месте ({os.path.getsize(apk)//1024} КБ)")
    else:
        print("  apk:  не собран — выполните android/build_apk.sh (нужен Android SDK)")

    # 2. архив клиента Windows
    if os.path.exists(exe):
        with zipfile.ZipFile(os.path.join(OUT, "CRM-Windows-Client.zip"), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(exe, "CRM-Windows.exe")
            zf.writestr("README.txt", README_TXT)
            win_readme = os.path.join(ROOT, "winclient", "README.md")
            if os.path.exists(win_readme):
                zf.write(win_readme, "winclient-README.md")
        print("  zip:  CRM-Windows-Client.zip")

    # 3. сервер
    with zipfile.ZipFile(os.path.join(OUT, "crm-server.zip"), "w", zipfile.ZIP_DEFLATED) as zf:
        zip_tree(zf, os.path.join(ROOT, "server"), "crm-server")
        zf.writestr("crm-server/README.md", open(os.path.join(ROOT, "README.md"), encoding="utf-8").read())
        zf.writestr("README.txt", README_TXT)
        # версия сборки: сервер покажет её в /api/version и будет сравнивать с релизами
        version_file = os.path.join(ROOT, "VERSION")
        if os.path.exists(version_file):
            zf.write(version_file, "crm-server/VERSION")
        build = os.environ.get("CRM_BUILD_NUMBER", "").strip()
        if build.isdigit():
            zf.writestr("crm-server/BUILD", build)
    print("  zip:  crm-server.zip")

    # 4. исходники клиентов
    with zipfile.ZipFile(os.path.join(OUT, "crm-sources.zip"), "w", zipfile.ZIP_DEFLATED) as zf:
        zip_tree(zf, os.path.join(ROOT, "winclient"), "winclient")
        zip_tree(zf, os.path.join(ROOT, "android"), "android")
        zf.writestr("README.txt", README_TXT)
    print("  zip:  crm-sources.zip")

    # 5. инструкция
    with open(os.path.join(OUT, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(README_TXT)
    print(f"\nГотово. Файлы в {OUT}:")
    for f in sorted(os.listdir(OUT)):
        print(f"  {f:28s} {os.path.getsize(os.path.join(OUT, f))//1024:>6} КБ")
    print("\nРелизы с этими файлами публикует .github/workflows/release.yml (тег v<версия>).")


if __name__ == "__main__":
    main()
