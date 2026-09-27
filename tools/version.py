#!/usr/bin/env python3
"""Единая версия приложений CRM (Windows-клиент, Android, сервер).

Схема нумерации:  <MAJOR>.<MINOR из файла VERSION>.<номер сборки>
    1.0.57 — MAJOR.MINOR меняются вручную в файле VERSION (крупные изменения),
    номер сборки приходит из GitHub Actions (GITHUB_RUN_NUMBER), поэтому
    каждый релиз получает собственный уникальный номер.

Примеры:
    python3 tools/version.py                 # 1.0.57
    python3 tools/version.py --code          # 57           (versionCode для Android)
    python3 tools/version.py --quad          # 1,0,0,57     (версия для exe)
    python3 tools/version.py --json          # всё сразу, для скриптов сборки
    python3 tools/version.py --gh-output     # записать в $GITHUB_OUTPUT (Actions)
    CRM_BUILD_NUMBER=42 python3 tools/version.py    # своя сборка (локально)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")


def base_version() -> str:
    """MAJOR.MINOR из файла VERSION."""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            base = f.read().strip()
    except OSError:
        base = "1.0"
    parts = [p for p in base.split(".") if p.strip().isdigit()]
    if not parts:
        return "1.0"
    while len(parts) < 2:
        parts.append("0")
    return ".".join(parts[:2])


def build_number() -> int:
    """Номер сборки: явное значение → номер запуска Actions → число коммитов."""
    for env in ("CRM_BUILD_NUMBER", "GITHUB_RUN_NUMBER"):
        value = os.environ.get(env, "").strip()
        if value.isdigit():
            return int(value)
    try:
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


def info() -> dict:
    base = base_version()
    build = build_number()
    major, minor = (int(x) for x in base.split(".")[:2])
    version = f"{base}.{build}"
    return {
        "version": version,          # 1.0.57
        "base": base,                # 1.0
        "build": build,              # 57
        "version_code": build,       # versionCode для Android (растёт всегда)
        "quad": f"{major},{minor},0,{build % 65536}",   # FILEVERSION для exe
        "dot_quad": f"{major}.{minor}.0.{build}",       # VERSIONINFO, строка
        "tag": f"v{version}",
    }


def main() -> int:
    args = sys.argv[1:]
    data = info()

    if "--gh-output" in args:
        out_file = os.environ.get("GITHUB_OUTPUT")
        lines = "".join(f"{k}={v}\n" for k, v in data.items())
        if out_file:
            with open(out_file, "a", encoding="utf-8") as f:
                f.write(lines)
        else:
            sys.stdout.write(lines)
        return 0

    if "--json" in args:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    if "--code" in args:
        print(data["version_code"])
        return 0
    if "--quad" in args:
        print(data["quad"])
        return 0
    if "--tag" in args:
        print(data["tag"])
        return 0

    print(data["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
