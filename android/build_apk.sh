#!/usr/bin/env bash
# Сборка APK приложения инженера.
#
# Локально (нужны JDK 17 и Android SDK):
#     ANDROID_HOME=~/Android/Sdk ./build_apk.sh
# В CI этим скриптом пользуется .github/workflows/release.yml.
#
# Что делает:
#   1) берёт версию из tools/version.py (MAJOR.MINOR + номер сборки);
#   2) подписывает сборку постоянным ключом проекта (signing/android-release.p12):
#      пароль — из CRM_KEYSTORE_PASSWORD/CRM_KEY_PASSWORD или keystore.properties;
#   3) кладёт результат в dist/CRM-Engineer.apk и печатает SHA-256.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
mkdir -p dist

VER_JSON="$(python3 "$ROOT/tools/version.py" --json)"
VERSION="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['version'])" "$VER_JSON")"
VCODE="$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['version_code'])" "$VER_JSON")"

# ------------------------------------------------------------------ подпись
KEYSTORE="${CRM_KEYSTORE_FILE:-$ROOT/signing/android-release.p12}"
if [[ -f "$KEYSTORE" && -n "${CRM_KEYSTORE_PASSWORD:-}" ]]; then
  export CRM_KEYSTORE_FILE="$KEYSTORE"
  export CRM_KEY_ALIAS="${CRM_KEY_ALIAS:-crm-engineer}"
  export CRM_KEY_PASSWORD="${CRM_KEY_PASSWORD:-$CRM_KEYSTORE_PASSWORD}"
  echo "== Подпись: постоянный ключ $KEYSTORE (alias $CRM_KEY_ALIAS) =="
else
  echo "(!) Ключ подписи не задан — APK соберётся неподписанным и не встанет поверх установленного."
fi

echo "== Сборка APK: версия $VERSION (versionCode $VCODE) =="
GRADLE_CMD="${GRADLE_CMD:-}"
if [[ -z "$GRADLE_CMD" ]]; then
  if [[ -x ./gradlew ]]; then GRADLE_CMD="./gradlew"; else GRADLE_CMD="gradle"; fi
fi
"$GRADLE_CMD" --no-daemon assembleRelease \
  -PversionCode="$VCODE" -PversionName="$VERSION" "$@"

APK_SRC="app/build/outputs/apk/release/app-release.apk"
APK_UNSIGNED="app/build/outputs/apk/release/app-release-unsigned.apk"
OUT="dist/CRM-Engineer.apk"
if [[ -f "$APK_SRC" ]]; then
  cp "$APK_SRC" "$OUT"
elif [[ -f "$APK_UNSIGNED" ]]; then
  cp "$APK_UNSIGNED" "$OUT"
else
  echo "Не найден собранный APK ($APK_SRC)" >&2
  exit 1
fi

ls -l "$OUT"
python3 - "$OUT" "$VERSION" <<'PY'
import hashlib, sys, zipfile
path, version = sys.argv[1], sys.argv[2]
data = open(path, 'rb').read()
digest = hashlib.sha256(data).hexdigest()
signed = any(n.startswith('META-INF/') and n.upper().endswith(('.RSA', '.DSA', '.EC')) for n in zipfile.ZipFile(path).namelist())
print("APK: версия %s, размер %d КБ, подпись: %s" % (version, len(data) // 1024, "есть" if signed else "НЕТ"))
print("sha256: %s" % digest)
open('dist/version.txt', 'w', encoding='utf-8').write("%s\n%s\n" % (version, digest))
PY
echo "Готово: $(pwd)/$OUT"
