"""Зоны обслуживания, закрепление инженеров и перераспределение на время отпуска."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import db

# name, lat, lon, aliases для сопоставления адреса/района
CITY_ZONES = [
    ("Заводской",   53.8700, 27.6550, ["заводской", "заводской район", "шабаны", "ангарская"]),
    ("Ленинский",   53.8850, 27.5250, ["ленинский", "ленинский район", "малиновка", "курасовщина"]),
    ("Московский",  53.9250, 27.4600, ["московский", "московский район", "юго-запад", "веснянка", "брилевичи"]),
    ("Октябрьский", 53.8700, 27.6000, ["октябрьский", "октябрьский район", "курасовщина", "малиновка"]),
    ("Партизанский", 53.8800, 27.6350, ["партизанский", "партизанский район", "тракторный завод", "долгобродская"]),
    ("Первомайский", 53.9450, 27.6300, ["первомайский", "первомайский район", "зеленый луг", "уручье", "серебрянка"]),
    ("Советский",   53.9350, 27.5900, ["советский", "советский район", "восток", "зеленый луг"]),
    ("Фрунзенский", 53.9200, 27.5000, ["фрунзенский", "фрунзенский район", "каменная горка", "красная горка", "сухарево", "кунцевщина", "малиновка"]),
    ("Центральный", 53.9050, 27.5600, ["центральный", "центральный район", "центр", "немига", "раковское предместье"]),
]

REGION_ZONES = [
    ("Березинский",    53.8333, 28.9833, ["березино", "березинский"]),
    ("Борисовский",    54.2278, 28.5050, ["борисов", "борисовский"]),
    ("Вилейский",      54.4917, 26.9167, ["вилейка", "вилейский"]),
    ("Воложинский",    54.0906, 26.5300, ["воложин", "воложинский"]),
    ("Дзержинский",    53.6833, 27.1333, ["дзержинск", "дзержинский", "фаниполь"]),
    ("Клецкий",        53.0333, 26.6333, ["клецк", "клецкий"]),
    ("Копыльский",     53.1500, 27.0833, ["копыль", "копыльский"]),
    ("Крупский",       54.3167, 29.1333, ["крупки", "крупский"]),
    ("Логойский",      54.2000, 27.8500, ["логойск", "логойский"]),
    ("Любанский",      52.8000, 28.0000, ["любань", "любанский"]),
    ("Минский",        53.9200, 27.6000, ["минский район", "минский р-н", "колодищи", "боровляны", "гатово", "сеница", "мачулищи", "фаниполь", "заславль", "ратомка"]),
    ("Молодечненский", 54.3167, 26.8500, ["молодечно", "молодечненский"]),
    ("Мядельский",     54.8833, 26.9333, ["мядель", "мядельский", "нарочь"]),
    ("Несвижский",     53.2167, 26.6667, ["несвиж", "несвижский"]),
    ("Пуховичский",    53.5167, 28.1500, ["пуховичи", "пуховичский", "марьина горка", "рудensk"]),
    ("Слуцкий",        53.0333, 27.5500, ["слуцк", "слуцкий"]),
    ("Смолевичский",   54.0333, 28.0833, ["смолевичи", "смолевичский", "жуковка"]),
    ("Солигорский",    52.7833, 27.5333, ["солигорск", "солигорский", "старобин"]),
    ("Стародорожский", 53.0333, 28.2667, ["старые дороги", "стародорожский"]),
    ("Столбцовский",   53.4833, 26.7333, ["столбцы", "столбцовский"]),
    ("Узденский",      53.4667, 27.2167, ["узда", "узденский"]),
    ("Червенский",     53.7000, 28.4333, ["червень", "червенский", "смиловичи"]),
]


def ensure_zones() -> None:
    conn = db.get_conn()
    have = {r["code"] for r in db.q("SELECT code FROM zones")}
    for name, lat, lon, _ in CITY_ZONES:
        code = "MSK-" + name
        if code not in have:
            conn.execute(
                "INSERT INTO zones(code,name,kind,lat,lon) VALUES(?,?,?,?,?)",
                (code, name + " район (Минск)", "city", lat, lon),
            )
    for name, lat, lon, _ in REGION_ZONES:
        code = "REG-" + name
        if code not in have:
            conn.execute(
                "INSERT INTO zones(code,name,kind,lat,lon) VALUES(?,?,?,?,?)",
                (code, name + " район", "region", lat, lon),
            )
    conn.commit()


def all_zones() -> list[dict]:
    return db.rows2dicts(db.q("SELECT * FROM zones ORDER BY kind, name"))


def zone(zone_id: int) -> dict | None:
    return db.row2dict(db.q1("SELECT * FROM zones WHERE id=?", (zone_id,)))


def match_zone_by_text(text: str) -> dict | None:
    """Определяет зону по названию района/города в адресе."""
    t = (text or "").lower().replace("ё", "е")
    if not t.strip():
        return None
    best, best_len = None, 0
    for name, _lat, _lon, aliases in CITY_ZONES + REGION_ZONES:
        keys = [name.lower().replace("ё", "е")] + [a.replace("ё", "е") for a in aliases]
        for k in keys:
            if k and k in t and len(k) > best_len:
                best, best_len = name, len(k)
    if best is None:
        return None
    return db.row2dict(db.q1("SELECT * FROM zones WHERE name LIKE ?", (best + "%",)))


_DISTRICT_STOP = {"район", "р-н", "минск", "минска", "область", "обл", "город", "г", "аг", "агрогородок"}


def district_key(name: str) -> str:
    """«Фрунзенский район (Минск)» -> «фрунзенский»."""
    import re as _re
    t = (name or "").lower().replace("ё", "е")
    t = _re.sub(r"[^a-zа-я\s]", " ", t)
    words = [w for w in t.split() if w and w not in _DISTRICT_STOP]
    return words[0] if words else ""


def match_zone_by_district(district_text: str) -> dict | None:
    """Точное сопоставление района: «Московский» -> «Московский район (Минск)»."""
    key = district_key(district_text)
    if not key:
        return None
    for z in all_zones():
        if district_key(z["name"]) == key:
            return z
    return None


def resolve_zone(lat: float | None, lon: float | None, district_text: str = "", address: str = "") -> dict | None:
    """Зона заявки: сначала по названию района, затем по координатам, затем по тексту адреса."""
    z = match_zone_by_district(district_text)
    if z:
        return z
    z_text = match_zone_by_text(address)
    # регион, явно названный в адресе («Минский район, аг. Колодищи»), важнее
    # ближайшего центра города: пригородные точки (Колодищи, Боровляны) иначе
    # уезжали в городские районы, до центров которых просто ближе
    if z_text and z_text.get("kind") == "region":
        return z_text
    if lat is not None and lon is not None:
        z = nearest_zone(lat, lon)
        if z:
            return z
    return z_text


def nearest_zone(lat: float, lon: float, kind: str | None = None, max_km: float = 60.0) -> dict | None:
    """Ближайший центр зоны (страховка, когда геокодер не вернул район)."""
    import math
    best, best_d = None, 1e9
    for z in all_zones():
        if kind and z["kind"] != kind:
            continue
        d = math.hypot((z["lat"] - lat) * 111.0, (z["lon"] - lon) * 111.0 * math.cos(math.radians(lat)))
        if d < best_d:
            best, best_d = z, d
    if best and best_d <= max_km:
        return best
    return None


# ------------------------------------------------ рабочие дни

_EXTRA_HOLIDAYS_DEFAULT = []  # заполняется в справочнике holidays


def is_working_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return db.q1("SELECT 1 FROM holidays WHERE day=?", (d.isoformat(),)) is None


def next_working_day(d: date) -> date:
    d = d + timedelta(days=1)
    while not is_working_day(d):
        d += timedelta(days=1)
    return d


def add_business_days(d: date, n: int) -> date:
    for _ in range(max(0, n)):
        d = next_working_day(d)
    return d


def parse_day(value: str | None, default: date | None = None) -> date:
    if not value:
        return default or date.today()
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


# ------------------------------------- закрепление и перераспределение

def assignments_for_date(day: date) -> list[dict]:
    d = day.isoformat()
    return db.rows2dicts(db.q(
        """
        SELECT za.*, e.full_name AS engineer_name, z.name AS zone_name, z.code AS zone_code
        FROM zone_assignments za
        JOIN engineers e ON e.id = za.engineer_id
        JOIN zones z     ON z.id = za.zone_id
        WHERE za.engineer_id != 0
          AND za.date_from <= ?
          AND (za.date_to IS NULL OR za.date_to >= ?)
        ORDER BY za.zone_id, za.is_primary DESC, za.date_from DESC
        """, (d, d)))


def absent_engineers(day: date) -> dict[int, int | None]:
    """{engineer_id: replacement_engineer_id} — кто отсутствует в этот день."""
    d = day.isoformat()
    res: dict[int, int | None] = {}
    for r in db.q("SELECT * FROM absences WHERE date_from <= ? AND date_to >= ?", (d, d)):
        res[r["engineer_id"]] = r["replacement_engineer_id"]
    return res


def effective_engineer(zone_id: int | None, day: date | None = None) -> dict | None:
    """Кто отвечает за зону в конкретный день с учётом отпусков и замен.

    Логика:
      1. Основной закреплённый инженер.
      2. Если он в отпуске — его замена по записи отпуска.
      3. Иначе — любой другой закреплённый за зоной инженер (резерв/dubler).
      4. Иначе — дежурный инженер из настроек.
    """
    if not zone_id:
        return None
    day = day or date.today()
    d = day.isoformat()
    rows = db.rows2dicts(db.q(
        """
        SELECT za.engineer_id, za.is_primary, za.reason, e.full_name, e.phone, e.base_lat, e.base_lon
        FROM zone_assignments za JOIN engineers e ON e.id=za.engineer_id
        WHERE za.zone_id=? AND za.date_from<=? AND (za.date_to IS NULL OR za.date_to>=?)
          AND e.active=1
        ORDER BY za.is_primary DESC, za.date_from DESC
        """, (zone_id, d, d)))

    absent = absent_engineers(day)
    for r in rows:
        if r["engineer_id"] not in absent:
            return {"id": r["engineer_id"], "full_name": r["full_name"], "phone": r["phone"],
                    "base_lat": r["base_lat"], "base_lon": r["base_lon"], "via": "assignment"}
        repl = absent[r["engineer_id"]]
        if repl:
            e = db.row2dict(db.q1("SELECT id,full_name,phone,base_lat,base_lon FROM engineers WHERE id=? AND active=1", (repl,)))
            if e:
                e["via"] = "replacement"
                return e
    for r in rows:  # резерв: инженер есть, но он в отпуске — проверим ещё раз с заменой из других зон
        if r["engineer_id"] in absent:
            continue
    duty = db.setting("duty_engineer_id")
    if duty:
        e = db.row2dict(db.q1("SELECT id,full_name,phone,base_lat,base_lon FROM engineers WHERE id=? AND active=1", (duty,)))
        if e and e["id"] not in absent:
            e["via"] = "duty"
            return e
    return None


def coverage(day: date | None = None) -> list[dict]:
    """Карта зон на день: кто за что отвечает (для админки и контроля отпусков)."""
    day = day or date.today()
    out = []
    for z in all_zones():
        eng = effective_engineer(z["id"], day)
        primary = db.row2dict(db.q1(
            """SELECT e.full_name, e.id FROM zone_assignments za JOIN engineers e ON e.id=za.engineer_id
               WHERE za.zone_id=? AND za.is_primary=1 AND za.date_from<=? AND (za.date_to IS NULL OR za.date_to>=?)
               ORDER BY za.date_from DESC LIMIT 1""", (z["id"], day.isoformat(), day.isoformat())))
        out.append({**z, "engineer": eng, "primary": primary})
    return out


def assign_zone(zone_id: int, engineer_id: int, date_from: str, date_to: str | None,
                actor: str = "admin", reason: str = "Закрепление зоны", is_primary: int = 1) -> int:
    """Закрепить/перераспределить зону. Старая запись закрывается автоматически."""
    with db.tx() as conn:
        if is_primary:
            conn.execute(
                """UPDATE zone_assignments SET date_to=date(?, '-1 day')
                   WHERE zone_id=? AND is_primary=1 AND (date_to IS NULL OR date_to >= ?)""",
                (date_from, zone_id, date_from))
        cur = conn.execute(
            """INSERT INTO zone_assignments(zone_id,engineer_id,date_from,date_to,is_primary,reason,created_at,created_by)
               VALUES(?,?,?,?,?,?,?,?)""",
            (zone_id, engineer_id, date_from, date_to, is_primary, reason, db.now(), actor))
        rid = cur.lastrowid
    db.audit(actor, "zone.assign", {"zone_id": zone_id, "engineer_id": engineer_id,
                                    "from": date_from, "to": date_to, "reason": reason})
    return rid


def add_absence(engineer_id: int, date_from: str, date_to: str, kind: str,
                replacement_engineer_id: int | None, comment: str | None, actor: str = "admin") -> int:
    """Отпуск/больничный + замена. Заявки в зонах отсутствующего уходят замене."""
    aid = db.execute(
        """INSERT INTO absences(engineer_id,date_from,date_to,kind,replacement_engineer_id,comment,created_at,created_by)
           VALUES(?,?,?,?,?,?,?,?)""",
        (engineer_id, date_from, date_to, kind, replacement_engineer_id, comment, db.now(), actor))
    db.audit(actor, "absence.add", {"engineer_id": engineer_id, "from": date_from, "to": date_to,
                                    "kind": kind, "replacement": replacement_engineer_id})
    if replacement_engineer_id:
        for za in db.q("SELECT * FROM zone_assignments WHERE engineer_id=? AND is_primary=1", (engineer_id,)):
            assign_zone(za["zone_id"], replacement_engineer_id, date_from, date_to, actor,
                        f"Перераспределение на период отсутствия ({kind}) #{aid}", is_primary=1)
    return aid


def remove_absence(aid: int, actor: str = "admin") -> dict:
    """Удаляет запись об отсутствии и возвращает зоны прежнему инженеру."""
    a = db.row2dict(db.q1("SELECT * FROM absences WHERE id=?", (aid,)))
    if not a:
        return {"ok": False, "error": "Запись об отсутствии не найдена"}
    marker = f"#{aid}"
    rows = db.rows2dicts(db.q("SELECT * FROM zone_assignments WHERE reason LIKE ?", (f"%{marker}%",)))
    zone_ids = [r["zone_id"] for r in rows]
    prev_day = (date.fromisoformat(a["date_from"][:10]) - timedelta(days=1)).isoformat()
    with db.tx() as conn:
        conn.execute("DELETE FROM zone_assignments WHERE reason LIKE ?", (f"%{marker}%",))
        for zid in zone_ids:
            conn.execute("""UPDATE zone_assignments SET date_to=NULL
                            WHERE zone_id=? AND engineer_id=? AND is_primary=1 AND date_to=?""",
                         (zid, a["engineer_id"], prev_day))
        conn.execute("DELETE FROM absences WHERE id=?", (aid,))
    db.audit(actor, "absence.delete", {"id": aid, "zones_returned": zone_ids})
    return {"ok": True, "zones_returned": zone_ids}


def reassign_requests_of_absent_engineers(day: date | None = None) -> int:
    """Перебрасывает незавершённые заявки отсутствующего инженера на замену."""
    day = day or date.today()
    absent = absent_engineers(day)
    moved = 0
    for eng_id, repl in absent.items():
        if not repl:
            for z in db.rows2dicts(db.q("SELECT DISTINCT zone_id FROM requests WHERE engineer_id=? AND status IN ('new','assigned','in_progress')", (eng_id,))):
                eng = effective_engineer(z["zone_id"], day)
                if eng and eng["id"] != eng_id:
                    moved += _move_requests(eng_id, eng["id"], z["zone_id"], day)
            continue
        for z in db.rows2dicts(db.q("SELECT DISTINCT zone_id FROM requests WHERE engineer_id=? AND status IN ('new','assigned','in_progress')", (eng_id,))):
            moved += _move_requests(eng_id, repl, z["zone_id"], day)
    return moved


def _move_requests(from_engineer: int, to_engineer: int, zone_id: int, day: date) -> int:
    rows = db.q("""SELECT id FROM requests WHERE engineer_id=? AND zone_id=?
                   AND status IN ('new','assigned','in_progress')""", (from_engineer, zone_id))
    for r in rows:
        db.execute("UPDATE requests SET engineer_id=? WHERE id=?", (to_engineer, r["id"]))
        db.execute("INSERT INTO request_events(request_id,at,actor,from_status,to_status,comment) VALUES(?,?,?,?,?,?)",
                   (r["id"], db.now(), "system", None, None,
                    f"Перераспределено на другого инженера из-за отсутствия (зона {zone_id})"))
    if rows:
        db.audit("system", "requests.reassign", {"from": from_engineer, "to": to_engineer,
                                                 "zone_id": zone_id, "count": len(rows)})
    return len(rows)
