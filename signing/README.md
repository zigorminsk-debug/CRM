# Ключи подписи и автообновление

Сборки подписываются **постоянными ключами** — это то, что позволяет ставить новую версию
поверх установленной без удаления приложения и без потери данных:

| Платформа | Файл ключа | Alias | Пароль | Срок действия |
|---|---|---|---|---|
| Android (APK) | `signing/android-release.p12` | `crm-engineer` | `crm-android-key-2026` | до 2056 г. |
| Windows (exe) | `signing/windows-signing.pfx` | `crm-windows` | `crm-windows-key-2026` | до 2056 г. |

Отпечатки сертификатов (SHA-256):

```
Android: 90:EE:6E:A5:7E:14:03:0B:C6:9B:DB:60:EE:E3:B3:84:34:5E:62:61:3E:CD:B3:3E:F4:98:0A:64:50:D5:81:3B
Windows: 3E:B5:C4:40:E2:A7:27:9D:89:64:4A:CD:AF:B4:CB:81:E1:41:6A:BE:6F:44:55:7E:05:99:AF:34:A3:B9:0B:DA
```

**Важно:** для Android ключ менять нельзя — Android разрешает установку обновления только
сборкой, подписанной тем же ключом. Если ключ потерять, обновление «поверх» станет невозможным
(придётся удалять приложение и ставить заново). Сохраните файл ключа в надёжном месте.

## Как пользоваться

**Быстро (как есть).** Ключи лежат в репозитории, поэтому CI собирает подписанные сборки сразу,
без настройки секретов. Пароли по умолчанию прописаны в `.github/workflows/release.yml`.

**Правильно (рекомендуется для эксплуатации).** Перенесите ключи в секреты репозитория и удалите
файлы из репозитория — workflow сам подставит их при сборке:

```bash
# 1. Добавить секреты (значения — base64-файлы ключей и пароли)
base64 -w0 signing/android-release.p12 | gh secret set ANDROID_KEYSTORE_BASE64 --repo <owner>/CRM
base64 -w0 signing/windows-signing.pfx  | gh secret set WINDOWS_PFX_BASE64   --repo <owner>/CRM
gh secret set ANDROID_KEYSTORE_PASSWORD --repo <owner>/CRM   # crm-android-key-2026
gh secret set ANDROID_KEY_PASSWORD      --repo <owner>/CRM   # crm-android-key-2026
gh secret set ANDROID_KEY_ALIAS         --repo <owner>/CRM   # crm-engineer
gh secret set WINDOWS_PFX_PASSWORD      --repo <owner>/CRM   # crm-windows-key-2026

# 2. Убрать ключи из репозитория
git rm --cached signing/android-release.p12 signing/windows-signing.pfx
echo "signing/*.p12" >> .gitignore
echo "signing/*.pfx" >> .gitignore

# 3. Собрать новый релиз (workflow подставит ключи из секретов)
gh workflow run "Сборка и релиз" --repo <owner>/CRM
```

Примечание про секреты в этом репозитории: у бота, работающего над этим проектом, нет прав на
API секретов GitHub, поэтому команды `gh secret set` нужно выполнить вам (или в GitHub →
Settings → Secrets and variables → Actions). Пока секреты не заданы, workflow работает
на ключах из репозитория и печатает предупреждение в журнале.

## Проверка подписи

```bash
# Windows-клиент (Linux/CI)
osslsigncode verify -in CRM-Windows.exe

# APK
keytool -printcert -jarfile CRM-Engineer.apk     # либо apksigner verify --print-certs CRM-Engineer.apk
```

## Замена ключа

* **Windows**: подпись exe не влияет на установку «поверх» — ключ можно менять свободно
  (SmartScreen только заново «привыкает» к новому сертификату).
* **Android**: смена ключа ломает автообновление. Если это неизбежно, включите в новой сборке
  «миграцию ключа» (APK Signing Key Rotation) или выпустите релиз с новым `applicationId`.

## Как устроено автообновление

1. При выпуске релиза CI кладёт в него файлы: `CRM-Windows.exe`, `CRM-Engineer.apk`,
   `update.json`, архивы сервера и исходников.
2. `update.json` — манифест: версия, номер сборки, ссылки и SHA-256 файлов. Его адрес постоянный:
   `https://github.com/<owner>/CRM/releases/latest/download/update.json`.
3. Сервер отдаёт эти сведения на `/api/updates`.
4. Windows-клиент и приложение инженера сравнивают номер сборки со своим, скачивают файл,
   сверяют SHA-256 и ставят новую версию поверх установленной:
   * exe — скрипт ждёт закрытия клиента, копирует новый файл и перезапускает приложение;
   * APK — системный установщик Android (подпись тем же ключом → обновление без удаления данных).
