#!/usr/bin/env python3
"""Формирует update.json — манифест автообновления клиентов CRM.

Манифест публикуется как файл релиза на GitHub, поэтому у него постоянный адрес:
    https://github.com/<owner>/<repo>/releases/latest/download/update.json

Windows-клиент и приложение инженера при старте сравнивают свою версию со значением
в манифесте и, если появилась новая, предлагают обновиться поверх установленного
(подпись — постоянным ключом, см. signing/README.md).

Пример:
    python3 tools/make_update_json.py \
        --version 1.0.57 --build 57 --repo zigorminsk-debug/CRM --tag v1.0.57 \
        --dir dist --notes "Исправлен поиск по телефону"
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os

FILES = {
    "windows": ("CRM-Windows.exe", "Клиент для Windows (приём заявок)"),
    "android": ("CRM-Engineer.apk", "Приложение инженера для Android"),
}


def file_info(path: str, url: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    return {
        "file": os.path.basename(path),
        "url": url,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Манифест автообновления CRM")
    p.add_argument("--version", required=True, help="версия, например 1.0.57")
    p.add_argument("--build", type=int, required=True, help="номер сборки (versionCode)")
    p.add_argument("--repo", required=True, help="owner/repo, например zigorminsk-debug/CRM")
    p.add_argument("--tag", required=True, help="тег релиза, например v1.0.57")
    p.add_argument("--dir", default="dist", help="каталог со собранными файлами")
    p.add_argument("--notes", default="", help="что нового (одной строкой)")
    p.add_argument("--out", default="", help="куда записать манифест (по умолчанию <dir>/update.json)")
    p.add_argument("--min-code", type=int, default=1, help="минимальная поддерживаемая версия")
    args = p.parse_args()

    base_url = f"https://github.com/{args.repo}/releases/download/{args.tag}"
    manifest = {
        "app": "Cartridge Engineer — заявки на заправку картриджей и ремонт оргтехники",
        "version": args.version,
        "build": args.build,
        "tag": args.tag,
        "released_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "min_build": args.min_code,
        "notes": args.notes,
        "page": f"https://github.com/{args.repo}/releases/tag/{args.tag}",
        "latest_page": f"https://github.com/{args.repo}/releases/latest",
    }

    for key, (name, title) in FILES.items():
        path = os.path.join(args.dir, name)
        if not os.path.isfile(path):
            print(f"  {key}: {name} — нет в {args.dir}, пропущен")
            continue
        info = file_info(path, f"{base_url}/{name}")
        info["title"] = title
        manifest[key] = info
        print(f"  {key}: {name}, {info['size'] // 1024} КБ, sha256={info['sha256'][:16]}…")

    out = args.out or os.path.join(args.dir, "update.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Манифест автообновления: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
