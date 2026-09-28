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
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "server", "app", "data")

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
UA = "CRM-street-index/1.0 (contact: ziv@csl.by)"

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
                    print(f"  overpass: {ep} ok ({r.headers.get('Content-Length', '?')} байт)", flush=True)
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                print(f"  overpass: {ep} ошибка: {e}", flush=True)
                last = e
        time.sleep(20 * (attempt + 1))
    raise SystemExit(f"Overpass недоступен: {last}")


def rel_area_id(name_ru: str) -> int:
    q = ('[out:json][timeout:60];'
         f'rel["boundary"="administrative"]["admin_level"="6"]["name:ru"="{name_ru}"];out ids;')
    els = overpass(q).get("elements", [])
    if not els:
        raise SystemExit(f"Не найдена граница: {name_ru}")
    rid = els[0]["id"]
    print(f"  область «{name_ru}»: relation {rid}", flush=True)
    return 3600000000 + rid


def q_center(area: int, selector: str, timeout: int = 900) -> list:
    q = (f'[out:json][timeout:{timeout}];area({area})->.a;'
         f'way(area.a)["highway"]["name"]{selector};out center;')
    return overpass(q).get("elements", [])


def q_houses(area: int) -> list:
    q = (f'[out:json][timeout:1200];area({area})->.a;'
         '('
         'way(area.a)["building"]["addr:housenumber"]["addr:street"];'
         'node(area.a)["addr:housenumber"]["addr:street"];'
         ');out center;')
    return overpass(q).get("elements", [])


def q_raions(area: int) -> list:
    q = (f'[out:json][timeout:600];area({area})->.a;'
         'rel(area.a)["boundary"="administrative"]["admin_level"="7"];'
         '(._;>;);out body;')
    return overpass(q).get("elements", [])


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
    city = rel_area_id("Минск")
    region = rel_area_id("Минский район")

    print("2/6 Районы города (границы)...", flush=True)
    els = q_raions(city)
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in els if e["type"] == "node"}
    ways = {e["id"]: [nodes[n] for n in e.get("nodes", []) if n in nodes]
            for e in els if e["type"] == "way"}
    polygons = []   # (name, [rings])
    for e in els:
        if e["type"] == "relation" and e.get("tags", {}).get("name:ru"):
            rings = build_rings(e, ways)
            if rings:
                polygons.append((e["tags"].get("name:ru", ""), rings))
    print(f"  районов с полигонами: {len(polygons)}", flush=True)

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
                          "district": tags.get("addr:district", ""), "kind": kind}
    print(f"  населённых пунктов/микрорайонов: {len(streets)}", flush=True)

    print("4/6 Улицы города и района...", flush=True)
    for area in (city, region):
        for e in q_center(area, ""):
            tags = e.get("tags", {})
            name = tags.get("name:ru") or tags.get("name")
            c = e.get("center") or {}
            if not name or "lat" not in c:
                continue
            k = street_key(name)
            if k and k not in streets:
                streets[k] = {"name": name, "lat": c["lat"], "lon": c["lon"],
                              "district": district_of(c["lat"], c["lon"]), "kind": "street"}
    print(f"  всего улиц/НП: {len(streets)}", flush=True)

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
