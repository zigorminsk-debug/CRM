"""Построение маршрута инженера на день: приоритет срочности + ближайший сосед."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import db, zones

PRIORITY_WEIGHT = {"emergency": 0, "urgent": 1, "normal": 2, "planned": 3}
PRIORITY_LABEL = {"emergency": "Аварийная", "urgent": "Срочная", "normal": "Обычная", "planned": "Плановая"}
STATUS_LABEL = {
    "new": "Новая", "assigned": "Назначена инженеру", "in_progress": "В работе (поехали)",
    "done_onsite": "Выполнено на месте", "pickup_office": "Забор в офис",
    "postponed": "Перенесена", "cancelled": "Отменена", "closed": "Закрыта", "delivered": "Доставлено",
}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _start_point(engineer: dict | None) -> tuple[float, float]:
    if engineer and engineer.get("base_lat") and engineer.get("base_lon"):
        return engineer["base_lat"], engineer["base_lon"]
    lat = db.setting("office_lat")
    lon = db.setting("office_lon")
    if lat and lon:
        return float(lat), float(lon)
    return 53.9045, 27.5615  # центр Минска


def _speed_kmh(leg_km: float) -> float:
    """Средняя скорость с учётом город/область."""
    return 22.0 if leg_km < 15 else 55.0


def day_tasks(engineer_id: int, day: date) -> list[dict]:
    rows = db.rows2dicts(db.q(
        """
        SELECT r.*, w.name AS work_name, w.site_kind, w.default_minutes,
               c.contact_person AS c_contact, c.name AS contractor_name
        FROM requests r
        JOIN works w ON w.id = r.work_id
        LEFT JOIN contractors c ON c.id = r.contractor_id
        WHERE r.engineer_id = ?
          AND r.status IN ('new','assigned','in_progress','postponed')
          AND COALESCE(r.planned_date, ?) <= ?
        ORDER BY r.id
        """, (engineer_id, day.isoformat(), day.isoformat())))
    return rows


def _minutes_for(task: dict) -> int:
    return int(task.get("minutes_planned") or task.get("default_minutes") or 60)


def plan_day(engineer_id: int, day: date | None = None, persist: bool = False) -> dict:
    """Маршрут на день. Сортировка: срочность, затем ближайший сосед."""
    day = day or date.today()
    eng = db.row2dict(db.q1("SELECT * FROM engineers WHERE id=?", (engineer_id,)))
    if not eng:
        return {"ok": False, "error": "Инженер не найден"}

    tasks = day_tasks(engineer_id, day)
    if not tasks:
        return {"ok": True, "day": day.isoformat(), "engineer": eng, "route": [], "total_km": 0.0,
                "total_minutes": 0, "total_hours": 0.0, "overflow": [],
                "deliveries": deliveries_for(engineer_id, day)}

    start = _start_point(eng)
    start_clock = db.setting("workday_start", "09:00")
    lunch = 45
    t = datetime.combine(day, datetime.strptime(start_clock, "%H:%M").time())

    # задачи с координатами; без координат — в конец списка
    with_coords = [x for x in tasks if x.get("lat") and x.get("lon")]
    no_coords = [x for x in tasks if not (x.get("lat") and x.get("lon"))]

    # 1) жёсткая приоритизация: аварийные и срочные идут первыми
    with_coords.sort(key=lambda x: (PRIORITY_WEIGHT.get(x["priority"], 9), x["id"]))

    ordered: list[dict] = []
    emergency = [x for x in with_coords if x["priority"] in ("emergency", "urgent")]
    rest = [x for x in with_coords if x["priority"] not in ("emergency", "urgent")]

    cur = start
    for bucket in (emergency, rest):
        left = bucket[:]
        while left:
            nxt = min(left, key=lambda x: haversine(cur[0], cur[1], x["lat"], x["lon"]))
            left.remove(nxt)
            ordered.append(nxt)
            cur = (nxt["lat"], nxt["lon"])

    route = []
    cur = start
    total_km = 0.0
    total_min = 0
    overflow = []
    day_end = datetime.combine(day, datetime.strptime(db.setting("workday_end", "18:00"), "%H:%M").time())
    for idx, task in enumerate(ordered + no_coords, start=1):
        lat, lon = task.get("lat"), task.get("lon")
        leg = haversine(cur[0], cur[1], lat, lon) if lat and lon else 0.0
        drive_min = int(round(leg / _speed_kmh(leg) * 60)) if leg else 0
        t += timedelta(minutes=drive_min)
        total_km += leg
        work_min = _minutes_for(task)
        arrive = t
        t += timedelta(minutes=work_min)
        total_min += drive_min + work_min
        if t.hour >= 16:  # первая половина следующего дня уходит на обед, считаем грубо
            t += timedelta(minutes=lunch)
        item = {
            **{k: task[k] for k in ("id", "number", "priority", "status", "address", "lat", "lon",
                                    "contact_person", "phone", "comment", "contractor_name", "unp",
                                    "bank_account", "work_name", "equipment", "serial", "planned_date",
                                    "contractor")},
            "order": idx,
            "priority_label": PRIORITY_LABEL.get(task["priority"], task["priority"]),
            "status_label": STATUS_LABEL.get(task["status"], task["status"]),
            "leg_km": round(leg, 2),
            "drive_minutes": drive_min,
            "work_minutes": work_min,
            "eta": arrive.strftime("%H:%M"),
            "eta_finish": (arrive + timedelta(minutes=work_min)).strftime("%H:%M"),
            "nav_url": f"https://yandex.ru/maps/?rtext={cur[0]},{cur[1]}~{lat},{lon}&rtt=auto" if lat and lon else None,
            "nav_intent": f"yandexnavi://build_route_on_map?lat_to={lat}&lon_to={lon}" if lat and lon else None,
        }
        if t > day_end and task["priority"] not in ("emergency",):
            item["overflow"] = True
            overflow.append(item)
        route.append(item)
        if lat and lon:
            cur = (lat, lon)

    deliveries = deliveries_for(engineer_id, day)
    result = {
        "ok": True, "day": day.isoformat(),
        "engineer": {"id": eng["id"], "full_name": eng["full_name"], "phone": eng["phone"]},
        "start": {"lat": start[0], "lon": start[1], "name": db.setting("office_address", "Офис")},
        "route": route, "overflow": overflow, "deliveries": deliveries,
        "total_km": round(total_km, 1), "total_minutes": total_min,
        "total_hours": round(total_min / 60.0, 1),
        "built_at": db.now(),
    }
    if persist:
        import json
        db.execute("INSERT INTO route_plans(engineer_id,day,built_at,payload) VALUES(?,?,?,?)",
                   (engineer_id, day.isoformat(), db.now(), json.dumps(result, ensure_ascii=False)))
    return result


def deliveries_for(engineer_id: int, day: date) -> list[dict]:
    rows = db.q(
        """SELECT d.*, r.number, r.address, r.lat, r.lon, r.contractor, r.contact_person, r.phone,
                  r.work_id, w.name AS work_name
           FROM deliveries d JOIN requests r ON r.id=d.request_id
           JOIN works w ON w.id=r.work_id
           WHERE d.engineer_id=? AND d.scheduled_date<=? AND d.status IN ('scheduled','postponed')
           ORDER BY d.scheduled_date, d.id""", (engineer_id, day.isoformat()))
    out = []
    for d in rows:
        item = dict(d)
        item["kind"] = "delivery"
        item["nav_intent"] = f"yandexnavi://build_route_on_map?lat_to={d['lat']}&lon_to={d['lon']}" if d["lat"] else None
        out.append(item)
    return out


def all_engineers_routes(day: date | None = None) -> list[dict]:
    day = day or date.today()
    res = []
    for e in db.rows2dicts(db.q("SELECT * FROM engineers WHERE active=1 ORDER BY full_name")):
        p = plan_day(e["id"], day)
        res.append({"engineer": e, "plan": p})
    return res


def workload_forecast(day: date | None = None) -> list[dict]:
    """Загрузка инженеров на день (минуты) — для контроля перегрузки."""
    day = day or date.today()
    out = []
    for e in db.rows2dicts(db.q("SELECT * FROM engineers WHERE active=1 ORDER BY full_name")):
        plan = plan_day(e["id"], day)
        cnt = len(plan.get("route", []))
        out.append({"engineer_id": e["id"], "full_name": e["full_name"], "tasks": cnt,
                    "minutes": plan.get("total_minutes", 0), "km": plan.get("total_km", 0),
                    "overload": plan.get("total_minutes", 0) > 540})
    return out


def schedule_delivery(request_id: int, engineer_id: int | None, day: date | None = None,
                      comment: str = "Выдача заказчику на следующий рабочий день") -> int:
    """После «забор в офис» ставим доставку заказчику на следующий рабочий день."""
    base = day or date.today()
    target = zones.next_working_day(base)
    did = db.execute(
        """INSERT INTO deliveries(request_id,engineer_id,scheduled_date,status,comment,created_at,updated_at)
           VALUES(?,?,?, 'scheduled', ?,?,?)""",
        (request_id, engineer_id, target.isoformat(), comment, db.now(), db.now()))
    db.audit("system", "delivery.schedule", {"request_id": request_id, "date": target.isoformat()})
    return did


def postpone_delivery(delivery_id: int, days: int = 1, comment: str = "", actor: str = "engineer") -> dict:
    """Перенос доставки (оборудование не готово) на следующий рабочий день."""
    d = db.row2dict(db.q1("SELECT * FROM deliveries WHERE id=?", (delivery_id,)))
    if not d:
        return {"ok": False, "error": "Доставка не найдена"}
    cur = zones.parse_day(d["scheduled_date"])
    target = cur
    for _ in range(max(1, days)):
        target = zones.next_working_day(target)
    db.execute("""UPDATE deliveries SET scheduled_date=?, status='postponed', postpone_count=postpone_count+1,
                  comment=?, updated_at=? WHERE id=?""",
               (target.isoformat(), comment or d["comment"], db.now(), delivery_id))
    db.execute("UPDATE requests SET status='pickup_office' WHERE id=?", (d["request_id"],))
    db.execute("""INSERT INTO request_events(request_id,at,actor,from_status,to_status,comment)
                  VALUES(?,?,?,?,?,?)""",
               (d["request_id"], db.now(), actor, None, None,
                f"Доставка перенесена с {cur.isoformat()} на {target.isoformat()}: {comment}"))
    db.audit(actor, "delivery.postpone", {"delivery_id": delivery_id, "from": cur.isoformat(), "to": target.isoformat()})
    return {"ok": True, "delivery_id": delivery_id, "scheduled_date": target.isoformat()}
