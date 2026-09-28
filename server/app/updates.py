"""Версии приложений CRM и автообновление с GitHub.

Что здесь есть:
  * `version_info()`     — версия этого сервера (из файла VERSION и номера сборки);
  * `manifest_url()`     — постоянный адрес манифеста обновления (файл `update.json`
                           в последнем релизе GitHub — адрес не меняется от релиза к релизу);
  * `release_info()`     — манифест последнего релиза (clients: update.json) с кэшем;
  * `updates_payload()`  — ответ для `/api/updates`: текущая версия + что опубликовано.

Манифест формирует `.github/workflows/release.yml` скриптом `tools/make_update_json.py`
и прикладывает его к релизу; клиенты (exe и APK) сравнивают свой номер сборки с
`build` из манифеста и скачивают файл по полю `windows.url` / `android.url`.
Подпись: сборки Android и Windows подписываются постоянным ключом проекта
(`signing/`), поэтому обновление ставится поверх установленной версии.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.environ.get("CRM_UPDATE_REPO", "zigorminsk-debug/CRM")
MANIFEST_URL = os.environ.get(
    "CRM_UPDATE_MANIFEST",
    f"https://github.com/{REPO}/releases/latest/download/update.json",
)
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TTL = int(os.environ.get("CRM_UPDATE_TTL", "600"))       # кэш манифеста, секунд
TIMEOUT = float(os.environ.get("CRM_UPDATE_TIMEOUT", "6"))

# Каталоги, где может лежать файл версии: корень репозитория (VERSION) или
# корень распакованного серверного дистрибутива (crm-server/VERSION, crm-server/BUILD).
# В собранном exe (PyInstaller) VERSION/BUILD вшиты в ресурсы (sys._MEIPASS),
# а переопределяющие файлы можно положить рядом с самим exe.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOTS = [os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE))]
if getattr(sys, "frozen", False):
    _bundle = getattr(sys, "_MEIPASS", _HERE)
    _ROOTS = [_bundle, os.path.dirname(os.path.abspath(sys.executable))]
_ROOT = _ROOTS[1]

_cache: dict = {"at": 0.0, "data": None, "error": ""}


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def _read_file(name: str, default: str = "") -> str:
    """Первое найденное значение файла (VERSION/BUILD) в корне репозитория или дистрибутива."""
    for root in _ROOTS:
        value = _read_text(os.path.join(root, name))
        if value:
            return value
    return default


def base_version() -> str:
    """MAJOR.MINOR из файла VERSION в корне проекта."""
    base = os.environ.get("CRM_VERSION_BASE") or _read_file("VERSION", "1.0")
    parts = base.split(".")
    return ".".join((parts + ["0", "0"])[:2])


def build_number() -> int:
    """Номер сборки: переменная окружения → файл сборки → число коммитов (локальный запуск)."""
    for env in ("CRM_BUILD_NUMBER", "GITHUB_RUN_NUMBER"):
        value = os.environ.get(env, "").strip()
        if value.isdigit():
            return int(value)
    for name in ("BUILD", "data/build.txt"):
        value = _read_file(name)
        if value.isdigit():
            return int(value)
    try:
        import subprocess
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=_ROOT,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    except (OSError, ValueError):
        pass
    for root in _ROOTS:
        value = _read_text(os.path.join(root, "server", "data", "build.txt"))
        if value.isdigit():
            return int(value)
    return 0


def version_info() -> dict:
    """Версия сервера и клиентов, которые он раздаёт."""
    build = build_number()
    base = base_version()
    full = os.environ.get("CRM_VERSION", "").strip() or (f"{base}.{build}" if build else base)
    return {
        "app": "Cartridge Engineer — заявки на заправку картриджей и ремонт оргтехники",
        "base": base,
        "build": build,
        "version": full,
        "tag": f"v{full}" if build else "",
        "repo": REPO,
    }


def manifest_url() -> str:
    return MANIFEST_URL


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"CRM-server/{version_info()['version']}",
        "Accept": "application/json",
    })
    token = os.environ.get("CRM_UPDATE_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def release_info(force: bool = False) -> dict:
    """Манифест последнего релиза. Кэшируется на TTL секунд, ошибки не роняют сервер."""
    now = time.time()
    if not force and _cache["data"] and now - _cache["at"] < TTL:
        return _cache["data"]

    data, error = None, ""
    try:
        data = _fetch(MANIFEST_URL)          # основной путь: файл релиза update.json
        data["source"] = "release asset"
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            rel = _fetch(API_URL)            # запасной путь: GitHub API
            data = {
                "version": str(rel.get("tag_name", "")).lstrip("v"),
                "tag": rel.get("tag_name", ""),
                "notes": rel.get("body", ""),
                "released_at": rel.get("published_at", ""),
                "page": rel.get("html_url", ""),
                "source": "github api",
            }
            assets = {a.get("name", ""): a for a in rel.get("assets", [])}
            for key, name in (("windows", "CRM-Windows.exe"), ("android", "CRM-Engineer.apk")):
                if name in assets:
                    data[key] = {"file": name, "url": assets[name].get("browser_download_url", ""),
                                 "size": assets[name].get("size", 0)}
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc2:
            error = f"{error} | {type(exc2).__name__}: {exc2}"

    if data is not None:
        _cache.update(at=now, data=data, error="")
    elif _cache["data"]:
        return _cache["data"]
    else:
        _cache.update(at=now, data=None, error=error)
    return data or {}


def updates_payload(force: bool = False) -> dict:
    """Ответ `/api/updates`: что стоит на сервере и что опубликовано на GitHub."""
    cur = version_info()
    latest = release_info(force=force)
    latest_build = int(latest.get("build") or 0) if str(latest.get("build", "")).isdigit() else 0
    return {
        "current": cur,
        "latest": latest or None,
        "update_available": bool(latest) and latest_build > cur["build"],
        "manifest_url": MANIFEST_URL,
        "repo": REPO,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": _cache.get("error", ""),
    }
