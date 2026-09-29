# Индекс адресов: Минск и Минский район (улицы, дома, принадлежность)

Назначение: мгновенный офлайн-поиск адреса с координатами и правильной
принадлежностью к районам города / нас. пунктам Минского района, без внешних
геокодеров. Источник данных: OpenStreetMap (© OpenStreetMap contributors, ODbL 1.0).

## Файлы

| Путь | Что это |
|---|---|
| `server/app/data/minsk_streets.csv` | улицы и НП: `name;lat;lon;district;type` (type: street / settlement / microdistrict) |
| `server/app/data/minsk_houses.csv.gz` | дома: `street;house;lat;lon` (street — ключ в нижнем регистре) |
| `server/app/data/minsk_houses.csv.gz.placeholder` | заглушка (удаляется workflow из индекса при первом сборе) |
| `.ci/harvest-report.txt` | отчёт последнего сбора (полигоны, счётчики) |
| `.ci/last-harvest.log` | полный лог — только при провале сбора |
| `tools/build_street_index.py` | сборщик (harvester), версия в строке `UA = "CRM-street-index/2.x …"` |
| `.github/workflows/street-index.yml` | автоматический сбор и коммит данных |

## Как работает поиск (server/app/geocode.py)

1. Адрес нормализуется (`_norm`), обозначения («ул», «вуліца», «пр-т»…) отбрасываются.
2. Ключ улицы — `street_key()`: без обозначений + `_be2ru` (і→и, ў→у, ы→и,
   «-скага/-цкага» → «-ского/-цкого»). Работает для обеих сторон: и для адреса,
   и для справочника.
3. `_key_variants()` — варианты первых гласных (е↔я, и↔ы) по всем позициям:
   связывает «Дзержинского»↔«Дзяржынскага», «Притыцкого»↔«Прытыцкага».
4. Совпадение — по целым словам (ключ улицы целиком в токенах адреса), победа —
   самое длинное совпадение; улица всегда приоритетнее НП.
5. Если в адресе есть номер дома и улица найдена — `house_lookup()` ищет точный
   дом (`house_index`), при промахе — тот же числовой блок (123 ≈ 123к2/123а).
   Найденный дом → `provider=local_house`, precision `house` → resolve отдаёт
   `exact` (без плашки неточности).
6. Зона: район из справочника → явно названный регион → ближайший центр зоны →
   текст адреса (`server/app/zones.py`).

Загрузка в БД: при старте сервера (`seed.py`) — идемпотентно, маркер версии
в `settings` (size:mtime). Улицы: upsert + удаление bundled-строк, которых
больше нет в CSV. Дома: перезагрузка при изменении gz.

## Сбор данных (tools/build_street_index.py)

Идентификаторы областей Overpass: `3600000000 + relation_id`.
Проверенные id: **Минск = rel 59195 (admin_level=4!)**, **Минский район = rel
59190 (уровень 6)**, районы города — **уровень 9**: Октябрьский 59199, Ленинский
59202, Московский 59208, Заводской 59209, Фрунзенский 59246, Центральный 59249,
Советский 59250, Первомайский 59252.

Шаги сбора:
1. `q_admin_list(city)` — все границы города (ids/tags/center) + печать в лог.
2. Геометрия районов — **по одному** `rel(id);out geom;` (инлайн-геометрия,
   без рекурсии `(._;>;)` — та даёт 400). Кольца склеиваются со снапом до
   5 знаков (узлы границ не совпадают точь-в-точь). Точка улицы → район по
   even-odd, полигоны сортируются от мелких к крупным. Если полигонов 0 —
   пауза 60 с и повтор; если <5 — сбор ПРОВАЛИВАЕТСЯ (без районов коммитить нельзя).
3. НП района: `node/way[place][name]` → settlement (city/town/village/hamlet/
   borough) или microdistrict (suburb/neighbourhood/quarter), всем —
   district «Минский район».
4. Улицы: `way[highway][name]` **порциями по типам дорог** (motorway…tertiary,
   residential, living_street/unclassified, service, pedestrian/footway/…,
   road/busway/construction) — один большой запрос Overpass не успевает, а
   кириллические буквенные диапазоны дают 400. Провал одной группы не валит сбор.
   Городские улицы получают район по полигону, районные — «Минский район».
5. Дома: `way[building][addr:housenumber][addr:street]` + node-аналог,
   `out center`, отдельно город/район (счётчики в отчёт).
6. Запись CSV + gz + отчёта. **Гейты качества**: полигонов ≥5, домов ≥10000,
   улиц ≥800, иначе SystemExit (и коммит лога).

Перезапуск вручную: push любого изменения `tools/build_street_index.py`
(триггер `paths`) — либо `gh workflow run street-index.yml` с default-ветки.
Ретраи Overpass: 3 раунда × 4 зеркала (de, kumi.systems, osm.jp), паузы 20/40/60 с.

## Диагностика Overpass

| Симптом | Причина | Что делать |
|---|---|---|
| HTTP 400 на regex | кириллический диапазон `["name"~"^[А-Д]"]` | не использовать кириллицу в regex |
| HTTP 400 на union | `(._;>;)` по крупным отношениям | `out geom` по одному отношению |
| HTTP 429 | лимит запросов | ретраи (уже в коде), пауза 2 с между группами |
| HTTP 504 | перегрузка зеркала | ретраи, следующее зеркало |
| 200, но пусто + `remark` | runtime error Overpass | код считает это ошибкой и ретраит |
| кольцо не склеилось | узлы смежных участков не совпадают | снап 5 знаков (уже в коде); может остаться 7/8 — тогда улицы одного района без district, зоны дадут ближайший центр |

## Проверка качества после сбора

```bash
head -10 .ci/harvest-report.txt
grep -c "" server/app/data/minsk_streets.csv
zcat server/app/data/minsk_houses.csv.gz | grep -vc '^#'
grep -E "^(Фабрициуса|проспект Дзержинского);" server/app/data/minsk_streets.csv
```

Локальный смоук (сеть не нужна):

```bash
python3 -m venv /tmp/crmvenv && /tmp/crmvenv/bin/pip install fastapi uvicorn pydantic python-multipart
cd server && CRM_DB=/tmp/fresh.sqlite3 /tmp/crmvenv/bin/python - <<'EOF'
import sys; sys.path.insert(0, '.')
from app import geocode, seed
seed.seed()
for a in ("Минск, ул. Дзержинского, д. 123",
          "Минск, ул. Фабрициуса, д. 9, оф. 2",
          "Минский район, аг. Колодищи, ул. Минская, 5"):
    g = geocode.resolve(a, use_cache=False)
    print(a, "→", {k: g.get(k) for k in ("ok","lat","lon","precision","zone_name")})
EOF
```

Полный e2e: `/tmp/crmvenv/bin/python server/tests/e2e_test.py http://127.0.0.1:8147`
(сервер: `cd server && CRM_DB=/tmp/fresh.sqlite3 /tmp/crmvenv/bin/python run.py --port 8147 --no-gui`).
Ожидание: 56/56.
