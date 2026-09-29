# Восстановление работы: релизы, откат, процедуры

## Карта репозитория

| Путь | Назначение |
|---|---|
| `server/` | FastAPI-сервер + веб-клиенты; `run.py`, `launcher.py` (фон/Shift), `crm-server.spec` (PyInstaller, вкомпилирует CSV/gz справочника) |
| `server/app/data/` | справочник адресов (см. `docs/ADDRESS-INDEX.md`) |
| `android/` | приложение инженера (APK в релизах — `CRM-Engineer.apk`) |
| `clientapp/`, `winclient/`, `installer/` | Windows-обёртки и NSIS-установщики |
| `tools/` | сборщик справочника, звуки, иконки |
| `.github/workflows/release.yml` | сборка и релиз (11 ассетов) на каждый push в ветку |
| `.github/workflows/street-index.yml` | сбор справочника адресов из OSM |
| `.ci/` | отчёт/логи сбора справочника |
| `server/tests/e2e_test.py` | сквозной тест (56 проверок) |
| `docs/` | эта документация |

## Обновление у пользователя

1. **Сервер (Windows)**: скачать `CRM-Server.exe` из последнего релиза
   (`https://github.com/zigorminsk-debug/CRM/releases/latest`), заменить файл,
   запустить (сервер уходит в фон, окно управления — по Shift).
2. **Телефон (инженер)**: `CRM-Engineer.apk` из того же релиза. Дальше приложение
   обновляется само (сервер отдаёт manifest; если сервер в отстое/stale — качает
   с GitHub напрямую).
3. После обновления сервера: админка → **«Обновить координаты заявок»**
   (`POST /api/admin/geocode/refresh`) — перегеокодирует активные заявки по
   новому справочнику.
4. Проверка: `/api/health` → 200; маршрут в Картах должен строиться из заявок.

## Если нужно откатиться

- В релизах лежат предыдущие версии (`releases/tag/vX.Y.Z`) — скачать старый
  `CRM-Server.exe`/`CRM-Engineer.apk` и заменить. База (`CRM_DB`) совместима.
- Откат репозитория: `git revert` либо работа от предыдущего тега; ветка
  рабочая — `arena/01a0e2c9-crm`. CI собирает релиз из каждого пуша автоматически.
- Справочник адресов можно откатить отдельно: `git revert <commit «Address index
  refresh…»>` — и пуш (соберётся новый релиз).

## Если упал сервер/сборка

- **Релиз не собрался**: `gh run list` → найти красный run; чаще всего это
  случайный сбой CI — лечится новым пустым пушем (например, правка README).
  Логи CI из песочницы недоступны — смотреть аннотации:
  `gh api repos/zigorminsk-debug/CRM/check-runs/<job_id>/annotations`.
- **Сбор справочника упал**: лог уже закоммичен ботом —
  `git pull && cat .ci/last-harvest.log` (внутри видно точный запрос и ошибку
  Overpass; типовые случаи — в `docs/ADDRESS-INDEX.md`). Перезапуск: любое
  изменение `tools/build_street_index.py` + пуш. Если данных вообще нет —
  сервер работает: поиск идёт через внешние геокодеры и зоны по умолчанию.
- **GitHub-токен слетел (401 Bad credentials)**: переподключить GitHub в Arena;
  CI и обновления у пользователя в это время работают автономно.

## Локальный запуск для проверки

```bash
python3 -m venv /tmp/crmvenv && /tmp/crmvenv/bin/pip install fastapi uvicorn pydantic python-multipart
cd server && CRM_DB=/tmp/fresh.sqlite3 /tmp/crmvenv/bin/python run.py --port 8147 --no-gui &
/tmp/crmvenv/bin/python tests/e2e_test.py http://127.0.0.1:8147   # ожидание 56/56
curl -s http://127.0.0.1:8147/api/health
```

Важно: песочница разработки теряет /tmp и иногда локальный HEAD между сессиями —
сверяться с `origin/arena/01a0e2c9-crm` перед коммитом (подробности в
`docs/WORKLOG.md` → «Ловушки инфраструктуры»).

## Контакты

**Zakharevich Igor · +375 29 337-14-12 · ziv@csl.by**
Разработчик указан в блоке «О программе» (4 места) и в файлах:
`android/.../MainActivity.kt`, `server/app/main.py`, `server/launcher.py`,
`tools/pack_downloads.py`.
