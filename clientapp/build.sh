#!/usr/bin/env bash
# Сборка CRM-Client.exe — лаунчер кабинета клиента (одно окно браузера --app).
# Результат: dist/CRM-Client.exe
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
mkdir -p dist

MODE="${1:-auto}"
OUT="${OUT:-dist/CRM-Client.exe}"
SRC="main.cpp"
LIBS="-lwinhttp -luser32 -lshell32 -lgdi32"
FLAGS="-std=c++17 -O2 -DUNICODE -D_UNICODE -municode -Wl,--subsystem,windows -Wl,-s"

VER_JSON="$(python3 "$ROOT/tools/version.py" --json)"
VERSION="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['version'])" "$VER_JSON")"
BUILD="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['build'])" "$VER_JSON")"
FLAGS="$FLAGS -DCRM_CLIENT_VERSION=\"$VERSION\" -DCRM_CLIENT_BUILD=$BUILD"

zig_bin() {
  command -v zig >/dev/null 2>&1 && { command -v zig; return 0; }
  python3 -c 'import ziglang,os;print(os.path.join(os.path.dirname(ziglang.__file__),"zig"))' 2>/dev/null || true
}

echo "== Сборка CRM-Client + CRM-Web (режим: $MODE, версия: $VERSION) =="
rm -f "$OUT" dist/CRM-Web.exe

build_mingw() {
  local src="${1:-$SRC}"
  if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1; then
    x86_64-w64-mingw32-g++ $FLAGS -static "$src" -o "$OUT" $LIBS
    return 0
  fi
  return 1
}
build_zig() {
  local src="${1:-$SRC}"
  local zb; zb="$(zig_bin)"
  if [[ -z "$zb" || ! -x "$zb" ]]; then
    echo "Не найден zig. Установите: pip install ziglang" >&2
    return 1
  fi
  "$zb" c++ -target x86_64-windows-gnu $FLAGS -static "$src" -o "$OUT" $LIBS
}

build_one() {
  local src="$1" out="$2"
  OUT="$out"
  if [[ "$MODE" == mingw ]]; then build_mingw "$src"
  elif [[ "$MODE" == zig ]]; then build_zig "$src"
  else build_mingw "$src" || build_zig "$src"
  fi
}

build_one "$SRC" "$OUT"                       # CRM-Client.exe
build_one "web.cpp" "dist/CRM-Web.exe"        # лаунчер ярлыков админка/диспетчер

ls -la dist/
echo "Версия: $VERSION"
