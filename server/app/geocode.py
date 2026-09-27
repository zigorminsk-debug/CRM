"""Адрес -> координаты (для Яндекс.Навигатора) + определение зоны.

Провайдеры по приоритету:
  1. Яндекс.Геокодер (нужен ключ в настройке yandex_geocoder_key) — точнее всего по РБ.
  2. Nominatim/OpenStreetMap — бесплатно, без ключа, с кэшем и лимитом 1 запрос/сек.
  3. Локальный справочник районов — координаты центра района (последняя страховка).
Все результаты кэшируются в таблице geo_cache.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

from . import db, zones

UA = "CRM-CartridgeService/1.0 (Minsk; contact: admin@example.by)"
_last_nominatim = 0.0


def _http_json(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cache_get(query: str) -> dict | None:
    r = db.q1("SELECT * FROM geo_cache WHERE query=?", (query.strip().lower(),))
    if not r:
        return None
    return {"lat": r["lat"], "lon": r["lon"], "zone_id": r["zone_id"],
            "provider": r["provider"], "raw": r["raw"], "cached": True}


def _cache_put(query: str, lat: float, lon: float, zone_id: int | None, provider: str, raw: dict) -> None:
    db.execute(
        """INSERT INTO geo_cache(query,lat,lon,zone_id,provider,raw,created_at) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(query) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, zone_id=excluded.zone_id,
                                            provider=excluded.provider, raw=excluded.raw""",
        (query.strip().lower(), lat, lon, zone_id, provider, json.dumps(raw, ensure_ascii=False), db.now()))


def normalize_address(addr: str) -> str:
    a = (addr or "").strip()
    a = re.sub(r"\s+", " ", a)
    a = a.replace("ул.", "улица").replace("пр-т", "проспект").replace("пр.", "проспект")
    a = re.sub(r"\bг\.\s*", "город ", a)
    a = re.sub(r"\bд\.\s*", "дом ", a)
    a = re.sub(r"\bкв\.\s*", "квартира ", a)
    a = re.sub(r"\bкорп\.\s*", "корпус ", a)
    if "минск" not in a.lower() and "район" not in a.lower() and "область" not in a.lower():
        a = "Минск, " + a
    return a


def _region_from_nominatim(data: dict) -> str:
    a = data.get("address", {}) or {}
    parts = [
        a.get("city_district") or "", a.get("suburb") or "", a.get("district") or "",
        a.get("county") or "", a.get("city") or "", a.get("town") or "", a.get("village") or "",
        a.get("municipality") or "", data.get("display_name", "") or "",
    ]
    return ", ".join(p for p in parts if p)


# ------------------------------------------------- локальный индекс адресов

_DESIGNATORS = (
    "улица", "ул", "проспект", "пр-т", "пр", "переулок", "пер", "тракт", "шоссе", "ш",
    "бульвар", "б-р", "площадь", "пл", "проезд", "дом", "д", "корпус", "корп", "кв", "квартира",
    "аг", "агрогородок", "город", "г", "поселок", "посёлок", "п", "район", "р-н", "область", "обл",
    "микрорайон", "мкр",
)


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[.,;()\"'/\\№#-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def street_key(name: str) -> str:
    """«ул. Притыцкого» -> «притыцкого» (ключ для поиска)."""
    t = _norm(name)
    words = [w for w in t.split() if w not in _DESIGNATORS and len(w) > 2]
    return " ".join(words)


def load_street_index(csv_path: str | None = None) -> int:
    """Загружает локальный справочник улиц/населённых пунктов в БД."""
    csv_path = csv_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "minsk_streets.csv")
    if not os.path.exists(csv_path):
        return 0
    # если индекс уже заполнен — ДОЗАГРУЖАЕМ только отсутствующие улицы:
    # новые строки CSV попадают и в существующие базы (иначе у улицы, добавленной
    # в справочник, нет шанса появиться у тех, кто уже пользуется сервером)
    existing = {r["name"] for r in db.q("SELECT name FROM street_index")} if db.q1("SELECT 1 FROM street_index LIMIT 1") else set()
    mode = "upsert" if existing else "load"
    n = 0
    with open(csv_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 3:
                continue
            name, lat, lon = parts[0], parts[1], parts[2]
            district = parts[3] if len(parts) > 3 else None
            kind = parts[4] if len(parts) > 4 else "street"
            if mode == "upsert":
                if name in existing:
                    continue
                existing.add(name)
            try:
                db.execute(
                    """INSERT INTO street_index(name,key,lat,lon,district,kind,source,created_at)
                       VALUES(?,?,?,?,?,?,'bundled',?)""",
                    (name, street_key(name), float(lat.replace(",", ".")), float(lon.replace(",", ".")),
                     district, kind, db.now()))
                n += 1
            except (ValueError, TypeError):
                continue
    db.audit("system", "street_index.load", {"rows": n})
    return n


def local_lookup(address: str) -> dict | None:
    """Поиск по локальному индексу: самое длинное совпадение ключа улицы/нас. пункта."""
    t = _norm(address)
    if not t:
        return None
    tokens = set(t.split())
    best, best_score = None, 0
    for row in db.q("SELECT * FROM street_index"):
        key = row["key"]
        if not key:
            continue
        words = key.split()
        # совпадение только по целым словам: «Ленина» не должно ловиться на «Ленинградскую»
        if not all(w in tokens for w in words):
            continue
        score = len(key)
        if row["district"] and street_key(row["district"]) in tokens:
            score += 6
        if score > best_score:
            best, best_score = row, score
    if not best:
        return None
    approx = best["kind"] in ("microdistrict", "settlement")
    return {"lat": best["lat"], "lon": best["lon"], "provider": "local_index",
            "text": best["name"] + ((" (" + best["district"] + ")") if best["district"] else ""),
            "district": best["district"] or "", "raw": {"street": best["name"], "kind": best["kind"]},
            "approx": True, "precision": "settlement" if approx else "street"}


def geocode_yandex(address: str) -> dict | None:
    key = db.setting("yandex_geocoder_key")
    if not key:
        return None
    url = ("https://geocode-maps.yandex.ru/1.x/?format=json&results=1&apikey=" + urllib.parse.quote(key)
           + "&geocode=" + urllib.parse.quote(address))
    try:
        data = _http_json(url)
        members = data["response"]["GeoObjectCollection"]["featureMember"]
        if not members:
            return None
        obj = members[0]["GeoObject"]
        lon, lat = [float(x) for x in obj["Point"]["pos"].split()]
        meta = obj["metaDataProperty"]["GeocoderMetaData"]
        text = meta.get("text", "")
        comps = meta.get("Address", {}).get("Components", [])
        district = ""
        for c in comps:
            if c.get("kind") in ("district", "area"):
                district = c.get("name", "")
        return {"lat": lat, "lon": lon, "provider": "yandex", "text": text,
                "district": district, "raw": data}
    except Exception as exc:  # noqa: BLE001
        db.audit("system", "geocode.yandex_error", {"address": address, "error": str(exc)})
        return None


def geocode_nominatim(address: str) -> dict | None:
    global _last_nominatim
    wait = 1.1 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    params = {
        "q": address,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 1,
        "countrycodes": "by",
        "accept-language": "ru",
        # рамка: Минск + Минская область
        "viewbox": "26.0,52.4,29.6,54.9",
        "bounded": 1,
    }
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    try:
        _last_nominatim = time.time()
        data = _http_json(url, timeout=15)
    except Exception as exc:  # noqa: BLE001
        db.audit("system", "geocode.nominatim_error", {"address": address, "error": str(exc)})
        return None
    if not data:
        return None
    item = data[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "provider": "nominatim",
            "text": item.get("display_name", ""), "district": _region_from_nominatim(item), "raw": item}


def resolve(address: str, use_cache: bool = True) -> dict:
    """Главная функция: адрес -> координаты + зона.

    Возвращает: {ok, lat, lon, zone_id, zone_name, engineer, provider, precision, message}
    precision: exact | district | manual
    """
    address = (address or "").strip()
    message = None
    if not address:
        return {"ok": False, "message": "Пустой адрес", "precision": None}

    if use_cache:
        c = _cache_get(address)
        if c and c.get("lat"):
            return _finish(address, c["lat"], c["lon"], c.get("zone_id"), c.get("provider") or "cache",
                           c.get("raw") or {}, "exact", None)

    query = normalize_address(address)
    res = geocode_yandex(query) or geocode_nominatim(query) or local_lookup(address)
    if res:
        precision = res.get("precision") or "exact"
        z = zones.resolve_zone(res["lat"], res["lon"], res.get("district", ""),
                               res.get("text", "") + " " + address)
        if precision != "exact":
            message = ("Координаты определены по локальному справочнику ("
                       + {"street": "улица", "settlement": "населённый пункт"}.get(precision, "район")
                       + "): " + (res.get("text") or "") + ". Уточните точку при необходимости.")
        _cache_put(address, res["lat"], res["lon"], z["id"] if z else None, res["provider"],
                   {"text": res.get("text"), "district": res.get("district")})
        return _finish(address, res["lat"], res["lon"], z["id"] if z else None, res["provider"], res, precision, message)

    # страховка: только район известен
    z = zones.match_zone_by_text(address)
    if z:
        _cache_put(address, z["lat"], z["lon"], z["id"], "zone_center", {"note": "только район"})
        return _finish(address, z["lat"], z["lon"], z["id"], "zone_center", {}, "district",
                       "Точный адрес не найден, координаты установлены по центру района — уточните точку вручную")
    return {"ok": False, "message": (
            "Адрес не найден: " + address + ". Уточните написание, например: "
            "\u00abг. Минск, ул. Фабрициуса, 9\u00bb или \u00abМинский район, аг. Колодищи, ул. Минская, 5\u00bb. "
            "Для точного распознавания любых адресов добавьте ключ Яндекс.Геокодера "
            "(Админка → Настройки) — без него работает офлайн-справочник и OpenStreetMap."),
            "precision": None}

def _finish(address: str, lat: float, lon: float, zone_id: int | None, provider: str,
            raw: dict, precision: str, message: str | None) -> dict:
    z = zones.zone(zone_id) if zone_id else None
    eng = zones.effective_engineer(zone_id) if zone_id else None
    return {
        "ok": True, "address": address, "lat": round(lat, 6), "lon": round(lon, 6),
        "zone_id": zone_id, "zone_name": z["name"] if z else None,
        "engineer_id": eng["id"] if eng else None,
        "engineer_name": eng["full_name"] if eng else None,
        "engineer_via": eng.get("via") if eng else None,
        "provider": provider, "precision": precision, "message": message,
        "yandex_url": yandex_nav_url(lat, lon),
        "raw": raw,
    }


def yandex_nav_url(lat: float, lon: float, from_lat: float | None = None, from_lon: float | None = None) -> str:
    """Ссылка для Яндекс.Навигатора / Яндекс.Карт."""
    rtext = ""
    if from_lat is not None and from_lon is not None:
        rtext = f"{from_lat},{from_lon}~"
    return f"https://yandex.ru/maps/?rtext={rtext}{lat},{lon}&rtt=auto"


def nav_intent(lat: float, lon: float) -> str:
    """Android-интент для Яндекс.Навигатора (открывается из приложения инженера)."""
    return f"yandexnavi://build_route_on_map?lat_to={lat}&lon_to={lon}"
