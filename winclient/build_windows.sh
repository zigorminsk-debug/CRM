#!/usr/bin/env bash
# Сборка Windows-клиента (CRM-Windows.exe).
#
# Вариант 1 — установленный MinGW-w64 (Windows/MSYS2 или Linux с пакетом mingw-w64):
#     ./build_windows.sh mingw
#
# Вариант 2 — кросс-компиляция через zig (mingw не нужен):
#     pip install ziglang && ./build_windows.sh zig
#
# Автоматический выбор компилятора: ./build_windows.sh
# Результат: dist/CRM-Windows.exe — портативный исполняемый файл (x64 GUI, WinHTTP).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist

MODE="${1:-auto}"
OUT="${OUT:-dist/CRM-Windows.exe}"
SRC="src/main_win.cpp src/api.cpp src/validate.cpp src/http_win.cpp"
LIBS="-lwinhttp -lcomctl32 -lgdi32 -luser32 -lshell32 -lole32 -luuid"
FLAGS="-std=c++17 -O2 -DUNICODE -D_UNICODE -Wl,--subsystem,windows -Wl,-s"

rm -f "$OUT"
echo "== Сборка Windows-клиента (режим: $MODE) =="

build_mingw() {
  if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1; then
    x86_64-w64-mingw32-g++ $FLAGS -static $SRC -o "$OUT" $LIBS
    return 0
  fi
  if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && command -v g++ >/dev/null 2>&1; then
    g++ $FLAGS -static $SRC -o "$OUT" $LIBS
    return 0
  fi
  return 1
}

build_zig() {
  local zb=""
  command -v zig >/dev/null 2>&1 && zb="$(command -v zig)"
  if [[ -z "$zb" ]]; then
    zb="$(python3 -c 'import ziglang,os;print(os.path.join(os.path.dirname(ziglang.__file__),"zig"))' 2>/dev/null || true)"
  fi
  if [[ -z "$zb" || ! -x "$zb" ]]; then
    echo "Не найден zig. Установите: pip install ziglang" >&2
    return 1
  fi
  "$zb" c++ -target x86_64-windows-gnu $FLAGS -static $SRC -o "$OUT" $LIBS
}

case "$MODE" in
  mingw) build_mingw ;;
  zig)   build_zig ;;
  *)     build_mingw || build_zig ;;
esac

ls -la "$OUT"
python3 - "$OUT" <<'PY' || true
import struct, sys
d = open(sys.argv[1], 'rb').read()
e = struct.unpack_from('<I', d, 0x3c)[0]
assert d[e:e+4] == b'PE\0\0', 'не PE-файл'
subsys = struct.unpack_from('<H', d, e + 24 + 68)[0]
print("Проверка: PE x64, подсистема %d (2 = GUI), размер %d КБ" % (subsys, len(d)//1024))
PY
echo "Готово: $(pwd)/$OUT"
