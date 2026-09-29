#!/usr/bin/env python3
"""Сборка локального адресного индекса (улицы + ДОМА с координатами) из OpenStreetMap.

Что делает:
  1. Находит области OSM: г. Минск и Минский район (relation admin_level=6).
  2. Тянет named-улицы (highway+name) обеих областей -> center.
  3. Тянет ДОМА: way[building][addr:housenumber][addr:street] и node[addr:housenumber]
     -> center (по городу и району).
  4. Границы районов города (admin_level=7) собирает в полигоны (склейка колец)
     и определяет принадлежность каждой улицы (point-in-polygon, even-odd).
  5. Пишет:
       server/app/data/minsk_streets.csv     name;lat;lon;district;type
       server/app/data/minsk_houses.csv.gz   street;house;lat;lon

Запуск (нужен интернет): python3 tools/build_street_index.py
Данные: (c) OpenStreetMap contributors, ODbL 1.0 — https://www.openstreetmap.org/copyright
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "server", "app", "data")

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
UA = "CRM-street-index/2.4 (contact: ziv@csl.by)"

_DESIGNATORS = (
    "улица", "ул", "проспект", "пр-т", "пр", "переулок", "пер", "тракт", "шоссе", "ш",
    "бульвар", "б-р", "площадь", "пл", "проезд", "дом", "д", "корпус", "корп", "кв",
    "аг", "агрогородок", "город", "г", "поселок", "посёлок", "п", "район", "р-н",
    "область", "обл", "микрорайон", "мкр",
)


def norm(t: str) -> str:
    t = (t or "").lower().replace("ё", "е")
    t = re.sub(r"[.,;()\"'/\\№#-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def street_key(name: str) -> str:
    words = [w for w in norm(name).split() if w not in _DESIGNATORS and len(w) > 2]
    return " ".join(words)


def overpass(query: str) -> dict:
    """Запрос к Overpass с перебором зеркал и повторами."""
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(ep, data=data,
                                             headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=900) as r:
                    body = r.read().decode("utf-8", "replace")
                    print(f"  overpass: {ep} ok ({len(body)} байт)", flush=True)
                    data = json.loads(body)
                    # Overpass при перегрузке отвечает 200 с ПУСТЫМ elements и
                    # remark "runtime error ..." — такой ответ считаем ошибкой
                    remark = data.get("remark", "")
                    if remark:
                        print(f"  overpass: remark: {remark}", flush=True)
                    if not data.get("elements") and remark:
                        raise RuntimeError(f"пустой ответ: {remark}")
                    return data
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"  overpass: {ep} ошибка: {e} | q: {query[:140]!r}", flush=True)
                last = e
        time.sleep(20 * (attempt + 1))
    raise SystemExit(f"Overpass недоступен: {last}")


def rel_area_id(name_ru: str, names_be: tuple = ()) -> int:
    """Ищет границу по нескольким именам и уровням; печатает всех кандидатов."""
    variants = tuple({name_ru, *names_be})
    levels = ("4", "5", "6", "7", "8")
    conds = "".join(f'rel["boundary"="administrative"]["admin_level"="{l}"]["name:ru"~"^{v}$"];'
                    for v in variants for l in levels)
    conds += "".join(f'rel["boundary"="administrative"]["admin_level"="{l}"]["name"~"^{v}$"];'
                     for v in variants for l in levels)
    q = '[out:json][timeout:120];(' + conds + ');out ids tags;'
    els = overpass(q).get("elements", [])
    for e in els:
        t = e.get("tags", {})
        print(f"  кандидат: rel {e['id']} | name:ru={t.get('name:ru')!r} | name={t.get('name')!r} | "
              f"admin_level={t.get('admin_level')}", flush=True)
    want = {v.lower() for v in variants}
    for e in els:
        t = e.get("tags", {})
        if {t.get("name:ru", "").lower(), t.get("name", "").lower()} & want:
            print(f"  область «{name_ru}»: relation {e['id']}", flush=True)
            return 3600000000 + e["id"]
    raise SystemExit(f"Не найдена граница: {name_ru} (кандидаты выше)")


# большие области Overpass не успевает посчитать одним запросом, а кириллические
# диапазоны в regex движок отвергает (400) — режем по типам дорог (латиница)
_KIND_GROUPS = [
    "motorway|trunk|primary|secondary|tertiary",
    "residential",
    "living_street|unclassified",
    "service",
    "pedestrian|footway|cycleway|path|track",
    "road|busway|construction",
]


def q_streets(area: int) -> list:
    """Именованные улицы области, порциями по типам дорог.

    Провал одной группы не валит сбор: недостающие улицы лучше пустоты."""
    out = []
    for kinds in _KIND_GROUPS:
        q = (f'[out:json][timeout:900];area({area})->.a;'
             f'way(area.a)["highway"]["name"]["highway"~"^({kinds})$"];out center;')
        try:
            out.extend(overpass(q).get("elements", []))
        except SystemExit as exc:  # noqa: BLE001
            print(f"  ! группа {kinds} не собралась: {exc}", flush=True)
        time.sleep(2)
    return out


def q_houses(area: int) -> list:
    q = (f'[out:json][timeout:1200];area({area})->.a;'
         '('
         'way(area.a)["building"]["addr:housenumber"]["addr:street"];'
         'node(area.a)["addr:housenumber"]["addr:street"];'
         ');out center;')
    return overpass(q).get("elements", [])


def q_admin_list(area: int) -> list:
    """Все административные границы внутри области: id, уровень, имя, центр."""
    q = (f'[out:json][timeout:300];area({area})->.a;'
         'rel(area.a)["boundary"="administrative"]["admin_level"];out ids tags center;')
    return overpass(q).get("elements", [])


def q_geom(rel_id: int) -> dict | None:
    """Геометрия одного отношения (инлайн, без рекурсии путей)."""
    q = f'[out:json][timeout:300];rel({rel_id});out geom;'
    els = overpass(q).get("elements", [])
    return els[0] if els else None


def rings_from_geom(rel: dict) -> list:
    """Кольца из инлайн-геометрии членов отношения (out geom).

    Узлы смежных участков границ в OSM не всегда совпадают точь-в-точь,
    поэтому концы склеиваем по округлению до 5 знаков (~0.5 м)."""
    lines = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("role") in ("outer", ""):
            g = m.get("geometry") or []
            pts = [(round(pt["lon"], 5), round(pt["lat"], 5))
                   for pt in g if pt.get("lon") is not None]
            if len(pts) >= 3:
                lines.append(pts)
    ends = {}
    for idx, pts in enumerate(lines):
        ends.setdefault(pts[0], []).append((idx, "start"))
        ends.setdefault(pts[-1], []).append((idx, "end"))
    rings, used = [], [False] * len(lines)
    for i in range(len(lines)):
        if used[i]:
            continue
        ring = list(lines[i])
        used[i] = True
        for _side in ("tail", "head"):
            while ring[0] != ring[-1]:
                end = ring[-1]
                nxt = next(((j, sd) for j, sd in ends.get(end, []) if not used[j]), None)
                if not nxt:
                    break
                j, sd = nxt
                used[j] = True
                w = lines[j]
                ring.extend(w[1:] if sd == "start" else list(reversed(w))[1:])
            if ring[0] == ring[-1]:
                break
            # попытка дорастить в начало
            head = next(((j, sd) for j, sd in ends.get(ring[0], []) if not used[j]), None)
            if not head:
                break
            j, sd = head
            used[j] = True
            w = lines[j]
            ring = (w[:-1] if sd == "end" else list(reversed(w))[1:]) + ring
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


_BE_WORDS = {
    "Першамайскі": "Первомайский", "Савецкі": "Советский", "Цэнтральны": "Центральный",
    "Кастрычніцкі": "Октябрьский", "Ленінскі": "Ленинский", "Калінінскі": "Калининский",
    "Заводскі": "Заводской", "Партызанскі": "Партизанский", "Маскоўскі": "Московский",
    "Фрунзенскі": "Фрунзенский", "раён": "район", "гарадскі": "городской",
}


def ru_name(name: str) -> str:
    """Белорусское написание -> русское (для названий районов в справочнике)."""
    if not name:
        return name
    words = [_BE_WORDS.get(w, w) for w in name.split()]
    t = " ".join(words)
    return (t.replace("і", "и").replace("ў", "у")
             .replace("ы", "и").replace("ґ", "г"))


def ring_area(rings: list) -> float:
    """Грубая площадь мультиполигона (для сортировки от мелких к крупным)."""
    a = 0.0
    for ring in rings:
        ssum = 0.0
        n = len(ring)
        for i in range(n - 1):
            x1, y1 = ring[i]
            x2, y2 = ring[i + 1]
            ssum += x1 * y2 - x2 * y1
        a += abs(ssum) / 2
    return a


def build_rings(rel: dict, ways: dict) -> list:
    """Собирает внешние кольца мультиполигона из путей-участников."""
    outer = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("role") in ("outer", ""):
            nd = ways.get(m["ref"])
            if nd and len(nd) >= 3:
                outer.append(nd)
    rings, used = [], [False] * len(outer)
    for i in range(len(outer)):
        if used[i]:
            continue
        ring = list(outer[i])
        used[i] = True
        changed = True
        while changed and (ring[0] != ring[-1]):
            changed = False
            for j in range(len(outer)):
                if used[j]:
                    continue
                w = outer[j]
                if w[0] == ring[-1]:
                    ring.extend(w[1:]); used[j] = True; changed = True
                elif w[-1] == ring[-1]:
                    ring.extend(list(reversed(w))[1:]); used[j] = True; changed = True
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring; used[j] = True; changed = True
                elif w[0] == ring[0]:
                    ring = list(reversed(w))[1:] + ring; used[j] = True; changed = True
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def inside(pt: tuple, ring: list) -> bool:
    """Even-odd point-in-polygon."""
    x, y = pt
    c = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                c = not c
    return c


def main() -> None:
    t0 = time.time()
    print("1/6 Области OSM...", flush=True)
    city = rel_area_id("Минск", ("Мінск", "минск"))
    region = rel_area_id("Минский район", ("Мінскі раён",))

    print("2/6 Районы города (границы)...", flush=True)
    city_rel_id = city - 3600000000
    region_rel_id = region - 3600000000
    rels = q_admin_list(city)
    for r in rels:
        t = r.get("tags", {})
        print(f"  граница в городе: rel {r['id']} L{t.get('admin_level')} "
              f"{t.get('name:ru') or t.get('name')!r}", flush=True)
    ids = []
    for r in rels:
        t = r.get("tags", {})
        try:
            lvl = int(t.get("admin_level", "0"))
        except (TypeError, ValueError):
            lvl = 0
        # только внутригородские границы (районы города): страна/область/город/район
        # (уровни ниже 8) тянут геометрию всей страны — Overpass отвечает 400
        if r["id"] in (city_rel_id, region_rel_id) or lvl < 8:
            continue
        ids.append(r["id"])
    polygons = []   # (name, [rings]) — сортировка: от мелких к крупным
    for rid in ids:
        rel = q_geom(rid)
        if not rel:
            continue
        t = rel.get("tags", {})
        nm = t.get("name:ru") or ru_name(t.get("name", ""))
        rings = rings_from_geom(rel)
        if nm and rings:
            polygons.append((nm, rings, ring_area(rings)))
        else:
            print(f"  ! нет полигона: rel {rid} {nm!r} (колец: {len(rings)})", flush=True)
    polygons.sort(key=lambda x: x[2])
    print(f"  районов с полигонами: {len(polygons)}: "
          + ", ".join(p[0] for p in polygons), flush=True)
    report = ["== Полигоны районов города =="] + [f"{p[0]} (площадь ~{p[2]:.4f})" for p in polygons]

    def district_of(lat: float, lon: float) -> str:
        pt = (lon, lat)
        for name, rings in polygons:
            k = sum(1 for r in rings if inside(pt, r))
            if k % 2 == 1:
                return name
        return ""

    print("3/6 Населённые пункты района...", flush=True)
    streets = {}
    q_np = (f'[out:json][timeout:300];area({region})->.a;'
            '(node(area.a)["place"]["name"];way(area.a)["place"]["name"];);out center;')
    for e in overpass(q_np).get("elements", []):
        tags = e.get("tags", {})
        name = tags.get("name:ru") or tags.get("name")
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        if not name or c.get("lat") is None:
            continue
        if tags.get("place") in ("suburb", "neighbourhood", "quarter"):
            kind = "microdistrict"
        elif tags.get("place") in ("city", "town", "village", "hamlet", "borough"):
            kind = "settlement"
        else:
            continue
        k = street_key(name)
        if k and k not in streets:
            streets[k] = {"name": name, "lat": c["lat"], "lon": c["lon"],
                          "district": "Минский район", "kind": kind}
    print(f"  населённых пунктов/микрорайонов: {len(streets)}", flush=True)

    print("4/6 Улицы города и района...", flush=True)
    n_city = n_reg = 0
    for area, is_city in ((city, True), (region, False)):
        for e in q_streets(area):
            tags = e.get("tags", {})
            name = tags.get("name:ru") or tags.get("name")
            c = e.get("center") or {}
            if not name or "lat" not in c:
                continue
            k = street_key(name)
            if k and k not in streets:
                dist = district_of(c["lat"], c["lon"]) if is_city else "Минский район"
                streets[k] = {"name": name, "lat": c["lat"], "lon": c["lon"],
                              "district": dist, "kind": "street"}
                if is_city:
                    n_city += 1
                else:
                    n_reg += 1
    print(f"  улиц города: {n_city}, улиц района: {n_reg}, всего улиц/НП: {len(streets)}",
          flush=True)
    report.append(f"== Улицы: города {n_city}, района {n_reg}, НП {len(streets) - n_city - n_reg} ==")
    no_d = [v["name"] for v in streets.values() if not v["district"]]
    report.append(f"== Улиц без района: {len(no_d)} ==")
    report.extend(no_d[:40])

    print("5/6 Дома...", flush=True)
    houses = {}
    for area in (city, region):
        for e in q_houses(area):
            tags = e.get("tags", {})
            st = tags.get("addr:street", "")
            hn = tags.get("addr:housenumber", "").strip().lower()
            c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
            if not st or not hn or not c.get("lat"):
                continue
            k = street_key(st)
            if not k:
                continue
            houses.setdefault(k, {}).setdefault(hn, (c["lat"], c["lon"]))
    total_h = sum(len(v) for v in houses.values())
    print(f"  домов: {total_h} (у улиц: {len(houses)})", flush=True)

    print("6/6 Запись файлов...", flush=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    sp = os.path.join(DATA_DIR, "minsk_streets.csv")
    with open(sp, "w", encoding="utf-8") as f:
        f.write("# name;lat;lon;district;type — OpenStreetMap contributors, ODbL; сборка %s\n"
                % time.strftime("%Y-%m-%d"))
        for k, v in sorted(streets.items()):
            f.write(f"{v['name']};{v['lat']:.6f};{v['lon']:.6f};{v['district']};{v['kind']}\n")
    rep_dir = os.path.normpath(os.path.join(DATA_DIR, "..", "..", "..", ".ci"))
    os.makedirs(rep_dir, exist_ok=True)
    with open(os.path.join(rep_dir, "harvest-report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    hp = os.path.join(DATA_DIR, "minsk_houses.csv.gz")
    with gzip.open(hp, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write("# street;house;lat;lon — OpenStreetMap contributors, ODbL; сборка %s\n"
                % time.strftime("%Y-%m-%d"))
        for k in sorted(houses):
            name = streets.get(k, {}).get("name") or k
            for hn, (lat, lon) in sorted(houses[k].items()):
                f.write(f"{name};{hn};{lat:.6f};{lon:.6f}\n")
    print("Готово за %.1f мин: %s (%d улиц), %s (%d домов)"
          % ((time.time() - t0) / 60, sp, len(streets), hp, total_h), flush=True)


if __name__ == "__main__":
    sys.exit(main())
