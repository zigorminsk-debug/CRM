#!/usr/bin/env bash
# Сборка Windows-клиента (CRM-Windows.exe) с номером версии, ресурсами и подписью.
#
# Режимы компилятора:
#     ./build_windows.sh mingw    — установленный MinGW-w64 (Windows/MSYS2 или Linux + mingw-w64)
#     ./build_windows.sh zig      — кросс-компиляция через zig (mingw не нужен): pip install ziglang
#     ./build_windows.sh          — автоматический выбор
#
# Что делает:
#   1) берёт номер версии из tools/version.py  (MAJOR.MINOR + номер сборки Actions);
#   2) собирает ресурсы src/main.rc (версия файла, название, значок манифеста) компилятором
#      ресурсов (x86_64-w64-mingw32-windres / zig rc) и вшивает их в exe;
#   3) подписывает exe постоянным ключом проекта, если он доступен:
#         SIGN_PFX=signing/windows-signing.pfx SIGN_PFX_PASS=... [SIGNTOOL=signtool] ./build_windows.sh
#      (osslsigncode в Linux/CI, signtool.exe в Windows);
#   4) проверяет, что получился PE x64 с графической подсистемой, и печатает контрольную сумму.
#
# Результат: dist/CRM-Windows.exe и dist/version.txt (версия + sha256).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
mkdir -p dist

MODE="${1:-auto}"
OUT="${OUT:-dist/CRM-Windows.exe}"
SRC="src/main_win.cpp src/api.cpp src/validate.cpp src/hash.cpp src/http_win.cpp"
LIBS="-lwinhttp -lcomctl32 -lgdi32 -luser32 -lshell32 -lole32 -luuid"
FLAGS="-std=c++17 -O2 -DUNICODE -D_UNICODE -Wl,--subsystem,windows -Wl,-s"

# ---------------------------------------------------------------- версия сборки
VER_JSON="$(python3 "$ROOT/tools/version.py" --json)"
VERSION="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['version'])" "$VER_JSON")"
FILEVERSION="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['quad'])" "$VER_JSON")"
BUILD="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['build'])" "$VER_JSON")"
FLAGS="$FLAGS -DCRM_CLIENT_VERSION=\"$VERSION\" -DCRM_CLIENT_BUILD=$BUILD"

# --------------------------------------------------------------- ресурсы (версия)
RC_SRC="src/main.rc"
if [[ ! -f "$RC_SRC" ]]; then
  sed -e "s/@FILEVERSION@/$FILEVERSION/g" \
      -e "s/@VERSION@/$VERSION/g" \
      -e "s/@COMPANY@/CRM Kartridzh/g" "src/main.rc.in" > "$RC_SRC"
fi
RC_OBJ="dist/CRM-Windows.res"

compile_resources() {
  if command -v x86_64-w64-mingw32-windres >/dev/null 2>&1; then
    x86_64-w64-mingw32-windres -c 65001 -O coff "src/main.rc" "$RC_OBJ"
    return 0
  fi
  local zb; zb="$(zig_bin || true)"
  if [[ -n "$zb" ]]; then
    ( cd src && "$zb" rc /c 65001 main.rc "../$RC_OBJ" )
    return 0
  fi
  echo "(ресурсы: компилятор .rc не найден — exe соберётся без номера версии в свойствах файла)"
  return 1
}

zig_bin() {
  command -v zig >/dev/null 2>&1 && { command -v zig; return 0; }
  python3 -c 'import ziglang,os;print(os.path.join(os.path.dirname(ziglang.__file__),"zig"))' 2>/dev/null || true
}

echo "== Сборка Windows-клиента (режим: $MODE, версия: $VERSION) =="
rm -f "$OUT"
RES_ARG=""
if compile_resources; then RES_ARG="$RC_OBJ"; fi

build_mingw() {
  if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1; then
    x86_64-w64-mingw32-g++ $FLAGS -static $SRC $RES_ARG -o "$OUT" $LIBS
    return 0
  fi
  if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && command -v g++ >/dev/null 2>&1; then
    g++ $FLAGS -static $SRC $RES_ARG -o "$OUT" $LIBS
    return 0
  fi
  return 1
}

build_zig() {
  local zb; zb="$(zig_bin)"
  if [[ -z "$zb" || ! -x "$zb" ]]; then
    echo "Не найден zig. Установите: pip install ziglang" >&2
    return 1
  fi
  "$zb" c++ -target x86_64-windows-gnu $FLAGS -static $SRC $RES_ARG -o "$OUT" $LIBS
}

case "$MODE" in
  mingw) build_mingw ;;
  zig)   build_zig ;;
  *)     build_mingw || build_zig ;;
esac

ls -la "$OUT"
python3 - "$OUT" "$VERSION" <<'PY'
import hashlib, struct, sys
path, version = sys.argv[1], sys.argv[2]
data = open(path, 'rb').read()
e = struct.unpack_from('<I', data, 0x3c)[0]
assert data[e:e+4] == b'PE\0\0', 'не PE-файл'
machine = struct.unpack_from('<H', data, e + 4)[0]
subsys = struct.unpack_from('<H', data, e + 24 + 68)[0]
opt = struct.unpack_from('<H', data, e + 24)[0]
ddd = e + 24 + (112 if opt == 0x20b else 96)
res = struct.unpack_from('<II', data, ddd + 2 * 8)   # каталог ресурсов
print("Проверка: PE x64 (machine 0x%04x), подсистема %d (2 = GUI), ресурсы: %s, размер %d КБ"
      % (machine, subsys, "есть" if res[0] else "нет", len(data) // 1024))
print("Версия: %s, sha256: %s" % (version, hashlib.sha256(data).hexdigest()))
open('dist/version.txt', 'w', encoding='utf-8').write(
    "%s\n%s\n" % (version, hashlib.sha256(data).hexdigest()))
PY

# ------------------------------------------------------------------- подпись exe
# Подпись постоянным ключом: osslsigncode (Linux/CI) или signtool.exe (Windows).
# Если метка времени недоступна — подписываем без неё; если подписи нет совсем — предупреждаем.
SIGN_PFX="${SIGN_PFX:-$ROOT/signing/windows-signing.pfx}"
SIGN_PFX_PASS="${SIGN_PFX_PASS:-}"
CRM_UPDATE_REPO="${CRM_UPDATE_REPO:-zigorminsk-debug/CRM}"

sign_exe() {
  if command -v osslsigncode >/dev/null 2>&1; then
    echo "== Подпись: osslsigncode ($(basename "$SIGN_PFX")) =="
    local base_args=(-pkcs12 "$SIGN_PFX" -pass "$SIGN_PFX_PASS" -n "CRM — приём заявок"
                     -i "https://github.com/$CRM_UPDATE_REPO" -h sha256)
    if osslsigncode sign "${base_args[@]}" -ts http://timestamp.digicert.com \
         -in "$OUT" -out "$OUT.signed" ||
       osslsigncode sign "${base_args[@]}" -in "$OUT" -out "$OUT.signed"; then
      mv "$OUT.signed" "$OUT"
      return 0
    fi
    return 1
  fi
  local st=""
  command -v signtool >/dev/null 2>&1 && st="$(command -v signtool)"
  [[ -z "$st" ]] && command -v signtool.exe >/dev/null 2>&1 && st="$(command -v signtool.exe)"
  if [[ -n "$st" ]]; then
    echo "== Подпись: signtool ($(basename "$SIGN_PFX")) =="
    "$st" sign /f "$SIGN_PFX" /p "$SIGN_PFX_PASS" /fd sha256 /tr http://timestamp.digicert.com \
          /td sha256 /d "CRM — приём заявок" "$OUT" && return 0
  fi
  return 1
}

if [[ -n "$SIGN_PFX_PASS" && -f "$SIGN_PFX" ]]; then
  sign_exe || echo "(!) Подписать exe не удалось (нет osslsigncode/signtool или неверный пароль ключа)"
elif [[ -z "$SIGN_PFX_PASS" ]]; then
  echo "(подпись не задана: не передан SIGN_PFX_PASS)"
else
  echo "(!) Ключ $SIGN_PFX не найден — exe не подписан"
fi

python3 - "$OUT" <<'SIGNCHECK'
import struct, sys
data = open(sys.argv[1], 'rb').read()
e = struct.unpack_from('<I', data, 0x3c)[0]
opt = struct.unpack_from('<H', data, e + 24)[0]
ddd = e + 24 + (112 if opt == 0x20b else 96)
sec = struct.unpack_from('<II', data, ddd + 4 * 8)     # каталог сертификатов (Authenticode)
print("Подпись: %s" % ("есть, сертификат %.1f КБ" % (sec[1] / 1024.0) if sec[0] else "нет"))
SIGNCHECK
echo "Готово: $(pwd)/$OUT"

