#!/usr/bin/env bash
# Сборка сервера CRM в автономный исполняемый файл CRM-Server.exe (PyInstaller).
#
# Локально (нужен Python 3.11+ и pip):
#     ./build_exe.sh
# В CI этим скриптом пользуется .github/workflows/release.yml (windows-latest).
#
# Что делает:
#   1) ставит PyInstaller, если его нет;
#   2) берёт номер сборки из tools/version.py и пишет его в build/BUILD
#      (exe покажет версию в /api/health и /api/version);
#   3) собирает один файл по спецификации crm-server.spec: внутри сервер,
#      веб-админка, мобильный клиент, справочник улиц и версия;
#   4) печатает размер и контрольную сумму.
#
# Результат: dist/CRM-Server.exe (Windows) или dist/CRM-Server (Linux).
# База SQLite (data/) и папка раздачи (downloads/) создаются рядом с exe
# при первом запуске; путь к базе можно переопределить переменной CRM_DB.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
mkdir -p build dist

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  # python3 есть в Linux/macOS; на Windows-раннерах бывает заглушка — берём рабочий
  if command -v python3 >/dev/null 2>&1 && python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    PYTHON=python3
  else
    PYTHON=python
  fi
fi

# ------------------------------------------------------------------- PyInstaller
if ! "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
  echo "== Устанавливаю pyinstaller =="
  "$PYTHON" -m pip install --quiet pyinstaller
fi
echo "== PyInstaller $("$PYTHON" -m PyInstaller --version) =="

# ---------------------------------------------------------------- версия сборки
BUILD="$("$PYTHON" "$ROOT/tools/version.py" --code)"
echo "$BUILD" > build/BUILD
echo "== Версия: $("$PYTHON" "$ROOT/tools/version.py") (сборка $BUILD) =="

# -------------------------------------------------------------------- сборка
"$PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller \
  crm-server.spec

# ------------------------------------------------------------------- проверка
BIN="dist/CRM-Server"
if [ -f "dist/CRM-Server.exe" ]; then BIN="dist/CRM-Server.exe"; fi
if [ ! -f "$BIN" ]; then
  echo "(!) Сборка не удалась: $BIN не найден" >&2
  exit 1
fi

SIZE="$(du -h "$BIN" | cut -f1)"
SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
{
  echo "CRM-Server  версия $("$PYTHON" "$ROOT/tools/version.py")  сборка $BUILD"
  echo "sha256: $SHA"
} > dist/version.txt

echo
echo "Готово: $BIN ($SIZE)"
cat dist/version.txt
