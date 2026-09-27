"""Бизнес-логика заявок: приём формы, назначение инженера, смена статусов, поиск, отчёты."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from . import db, geocode, routing, validation, zones

STATUS_LABEL = routing.STATUS_LABEL
PRIORITY_LABEL = routing.PRIORITY_LABEL
ACTIVE_STATUSES = ("new", "assigned", "in_progress", "pickup_office")


def next_number() -> str:
    year = datetime.now().year
    r = db.q1("SELECT COUNT(*) AS c FROM requests WHERE number LIKE ?", (f"ЗП-{year}-%",))
    n = (r["c"] if r else 0) + 1
    return f"ЗП-{year}-{n:05d}"


def _upsert_contractor(data: dict) -> int | None:
    """Контрагент ищется по УНП, а без УНП (частные лица) — по точному названию."""
    unp = validation.normalize_unp(data.get("unp", ""))
    name = (data.get("contractor") or "").strip()
    row = None
    if unp:
        row = db.q1("SELECT id, name FROM contractors WHERE unp=? ORDER BY id LIMIT 1", (unp,))
    if row is None and name:
        nl = name.lower()
        for r in db.q("SELECT id, name FROM contractors ORDER BY id"):
            if (r["name"] or "").strip().lower() == nl:
                row = r
                break
    fields = {
        "name": (data.get("contractor") or "").strip(),
        "bank_account": validation.normalize_account(data.get("bank_account", "")),
        "bank_name": (data.get("bank_name") or "").strip() or None,
        "address": (data.get("address") or "").strip(),
        "contact_person": (data.get("contact_person") or "").strip(),
        "phone": validation.normalize_phone(data.get("phone", "")),
        "email": (data.get("email") or "").strip() or None,
        "notes": (data.get("contractor_notes") or "").strip() or None,
    }
    if row:
        cid = row["id"]
        sets = ", ".join(f"{k}=?" for k, v in fields.items() if v not in (None, ""))
        vals = [v for v in fields.values() if v not in (None, "")]
        if sets:
            db.execute(f"UPDATE contractors SET {sets} WHERE id=?", (*vals, cid))
        return cid
    if not name:
        return None
    return db.execute(
        """INSERT INTO contractors(name,unp,bank_account,bank_name,address,contact_person,phone,email,notes,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (fields["name"], unp, fields["bank_account"], fields["bank_name"], fields["address"],
         fields["contact_person"], fields["phone"], fields["email"], fields["notes"], db.now()))


def resolve_work(work_id: int | None, work_code: str | None) -> dict:
    w = None
    if work_id:
        w = db.row2dict(db.q1("SELECT * FROM works WHERE id=? AND active=1", (work_id,)))
    if w is None and work_code:
        w = db.row2dict(db.q1("SELECT * FROM works WHERE code=? AND active=1", (work_code,)))
    if w is None:
        raise HTTPException(422, "Работа не найдена в справочнике — выберите из выпадающего списка")
    return w


def create_request(data: dict, actor: str = "client", source: str = "win") -> dict:
    """Приём заявки из формы Windows-клиента: валидация -> геокодирование -> зона -> инженер."""
    codes = {w["code"] for w in db.rows2dicts(db.q("SELECT code FROM works WHERE active=1"))}
    norm, errors = validation.validate_request_form(data, codes)
    if errors:
        raise HTTPException(422, " | ".join(errors))

    work = resolve_work(data.get("work_id"), data.get("work_code"))

    lat, lon, zone_id, precision, geo_msg = data.get("lat"), data.get("lon"), None, None, None
    if lat is None or lon is None:
        geo = geocode.resolve(data["address"])
        if not geo.get("ok"):
            raise HTTPException(422, geo.get("message") or "Не удалось определить адрес")
        lat, lon = geo["lat"], geo["lon"]
        zone_id = geo["zone_id"]
        precision = geo["precision"]
        geo_msg = geo.get("message")
    else:
        lat, lon = float(lat), float(lon)
        zone_id = data.get("zone_id")
        if not zone_id:
            z = zones.match_zone_by_text(data["address"]) or zones.nearest_zone(lat, lon)
            zone_id = z["id"] if z else None
        else:
            zone_id = int(zone_id)

    engineer = zones.effective_engineer(zone_id) if zone_id else None
    # по умолчанию планируем на сегодня; при «заборе в офис» сервис сам поставит след. рабочий день
    planned = data.get("planned_date") or date.today().isoformat()

    contractor_id = _upsert_contractor({**data, "unp": norm["unp"]})
    number = next_number()
    rid = db.execute(
        """INSERT INTO requests(number,created_at,created_by,contractor_id,contractor,unp,bank_account,
             contact_person,phone,work_id,priority,address,lat,lon,zone_id,comment,equipment,serial,
             status,engineer_id,assigned_by,assigned_at,planned_date,minutes_planned,source,time_from,time_to)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (number, db.now(), actor, contractor_id, norm["contractor"], norm["unp"], norm["bank_account"],
         norm["contact_person"], norm["phone"], work["id"], norm["priority"], norm["address"], lat, lon,
         zone_id, data.get("comment") or "", data.get("equipment") or None, data.get("serial") or None,
         "assigned" if engineer else "new", engineer["id"] if engineer else None,
         "auto:zone" if engineer else None, db.now() if engineer else None,
         planned, work["default_minutes"], source,
         data.get("time_from") or None, data.get("time_to") or None))

    _event(rid, actor, None, "assigned" if engineer else "new",
           f"Заявка принята ({'Windows-клиент' if source == 'win' else source})",
           {"zone_id": zone_id, "precision": precision, "engineer_id": engineer["id"] if engineer else None})
    if engineer:
        db.push_event(engineer["id"], "task.new", rid, {"number": number, "address": norm["address"],
                                                         "priority": norm["priority"]})

    result = get_request(rid)
    result["_assignment_message"] = (
        f"Передана инженеру {engineer['full_name']} по зоне" if engineer else
        "Не найден инженер для зоны — заявка в очереди нераспределённых")
    result["_geo_message"] = geo_msg
    return result


def _event(request_id: int, actor: str | None, from_status: str | None, to_status: str | None,
           comment: str = "", meta: dict | None = None) -> None:
    import json
    db.execute(
        """INSERT INTO request_events(request_id,at,actor,from_status,to_status,comment,meta)
           VALUES(?,?,?,?,?,?,?)""",
        (request_id, db.now(), actor, from_status, to_status, comment, json.dumps(meta or {}, ensure_ascii=False)))


def get_request(rid: int, with_events: bool = True) -> dict:
    r = db.row2dict(db.q1(
        """SELECT r.*, w.name AS work_name, w.code AS work_code, w.site_kind, w.default_minutes,
                  z.name AS zone_name, e.full_name AS engineer_name, e.phone AS engineer_phone,
                  c.name AS contractor_name, c.bank_name
           FROM requests r
           JOIN works w ON w.id=r.work_id
           LEFT JOIN zones z ON z.id=r.zone_id
           LEFT JOIN engineers e ON e.id=r.engineer_id
           LEFT JOIN contractors c ON c.id=r.contractor_id
           WHERE r.id=?""", (rid,)))
    if not r:
        raise HTTPException(404, "Заявка не найдена")
    r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
    r["priority_label"] = PRIORITY_LABEL.get(r["priority"], r["priority"])
    r["phone_formatted"] = validation.format_phone(r["phone"])
    r["nav_intent"] = (f"yandexnavi://build_route_on_map?lat_to={r['lat']}&lon_to={r['lon']}"
                       if r["lat"] and r["lon"] else None)
    r["nav_url"] = (f"https://yandex.ru/maps/?rtext={r['lat']},{r['lon']}&rtt=auto"
                    if r["lat"] and r["lon"] else None)
    r["deliveries"] = db.rows2dicts(db.q("SELECT * FROM deliveries WHERE request_id=? ORDER BY id", (rid,)))
    if with_events:
        r["events"] = db.rows2dicts(db.q(
            "SELECT * FROM request_events WHERE request_id=? ORDER BY id DESC", (rid,)))
    return r


def search(filters: dict, limit: int = 200, offset: int = 0) -> dict:
    """Поиск по истории заявок: контрагент, УНП, р/с, статус, инженер, зона, период, текст."""
    where, args = [], []
    if filters.get("contractor"):
        where.append("(r.contractor LIKE ? OR c.name LIKE ?)")
        args += [f"%{filters['contractor']}%"] * 2
    if filters.get("unp"):
        where.append("r.unp LIKE ?")
        args.append(f"%{validation.normalize_unp(filters['unp'])}%")
    if filters.get("bank_account"):
        where.append("REPLACE(r.bank_account,' ','') LIKE ?")
        args.append(f"%{validation.normalize_account(filters['bank_account'])}%")
    if filters.get("phone"):
        where.append("r.phone LIKE ?")
        args.append(f"%{validation.normalize_phone(filters['phone'])}%")
    if filters.get("status"):
        statuses = [s for s in str(filters["status"]).split(",") if s]
        where.append("r.status IN (%s)" % ",".join("?" * len(statuses)))
        args += statuses
    if filters.get("engineer_id"):
        where.append("r.engineer_id=?")
        args.append(int(filters["engineer_id"]))
    if filters.get("zone_id"):
        where.append("r.zone_id=?")
        args.append(int(filters["zone_id"]))
    if filters.get("priority"):
        where.append("r.priority=?")
        args.append(filters["priority"])
    if filters.get("date_from"):
        where.append("date(r.created_at) >= date(?)")
        args.append(filters["date_from"])
    if filters.get("date_to"):
        where.append("date(r.created_at) <= date(?)")
        args.append(filters["date_to"])
    if filters.get("day"):
        where.append("r.planned_date = ?")
        args.append(filters["day"])
    if filters.get("number"):
        where.append("r.number LIKE ?")
        args.append(f"%{filters['number']}%")
    if filters.get("q"):
        # поиск «по всему»: адрес, пояснение, контактное лицо, номер заявки, телефон и контрагент
        where.append("(r.address LIKE ? OR r.comment LIKE ? OR r.contact_person LIKE ? OR r.number LIKE ?"
                     " OR r.phone LIKE ? OR r.contractor LIKE ?)")
        qv = f"%{filters['q']}%"
        phone_q = f"%{validation.normalize_phone(filters['q'])}%" if validation.normalize_phone(filters['q']) else qv
        args += [qv, qv, qv, qv, phone_q, qv]
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"""FROM requests r
               JOIN works w ON w.id=r.work_id
               LEFT JOIN zones z ON z.id=r.zone_id
               LEFT JOIN engineers e ON e.id=r.engineer_id
               LEFT JOIN contractors c ON c.id=r.contractor_id
               {sql_where}"""
    total = db.q1("SELECT COUNT(*) AS c " + base, args)["c"]
    order = filters.get("order") or "r.id DESC"
    if order not in ("r.id DESC", "r.id ASC", "r.priority", "r.planned_date DESC", "r.created_at DESC"):
        order = "r.id DESC"
    rows = db.rows2dicts(db.q(
        f"""SELECT r.id, r.number, r.created_at, r.contractor, r.unp, r.bank_account, r.contact_person,
                   r.phone, r.priority, r.status, r.address, r.lat, r.lon, r.planned_date, r.done_at,
                   r.time_from, r.time_to,
                   r.comment, r.visit_result, w.name AS work_name, z.name AS zone_name,
                   e.full_name AS engineer_name {base}
            ORDER BY {order} LIMIT ? OFFSET ?""", (*args, limit, offset)))
    for r in rows:
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
        r["priority_label"] = PRIORITY_LABEL.get(r["priority"], r["priority"])
        r["phone_formatted"] = validation.format_phone(r["phone"])
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


def contractor_history(value: str, kind: str = "contractor") -> dict:
    """История по контрагенту / УНП / расчётному счёту."""
    if kind == "unp":
        f = {"unp": validation.normalize_unp(value)}
    elif kind == "account":
        f = {"bank_account": validation.normalize_account(value)}
    else:
        f = {"contractor": value}
    res = search(f, limit=500)
    agg = db.q1(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status IN ('done_onsite','delivered','closed') THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN status IN ('new','assigned','in_progress','pickup_office') THEN 1 ELSE 0 END) AS active,
                  SUM(CASE WHEN status='pickup_office' THEN 1 ELSE 0 END) AS pickup
           FROM requests WHERE """ + ("unp=?" if kind == "unp" else "REPLACE(bank_account,' ','')=?" if kind == "account" else "contractor LIKE ?"),
        (f.get("unp") or f.get("bank_account") or f"%{value}%",))
    return {"query": value, "kind": kind, "summary": dict(agg) if agg else {}, "items": res["items"]}


def change_status(rid: int, status: str, actor: str, comment: str = "", visit_result: str | None = None,
                  minutes_spent: int | None = None) -> dict:
    r = get_request(rid, with_events=False)
    allowed = {
        "assigned": {"new", "postponed", "assigned", "in_progress"},
        "in_progress": {"assigned", "new", "postponed", "in_progress"},
        # «Готово» инженер может отметить сразу после назначения — не только в пути
        "done_onsite": {"new", "assigned", "in_progress", "postponed"},
        "pickup_office": {"new", "assigned", "in_progress", "postponed"},
        "postponed": {"assigned", "new", "postponed"},
        "cancelled": ACTIVE_STATUSES,
        "closed": {"done_onsite", "delivered"},
        "delivered": {"pickup_office"},
    }
    if status not in STATUS_LABEL:
        raise HTTPException(422, f"Неизвестный статус: {status}")
    if r["status"] not in allowed.get(status, set()):
        raise HTTPException(409, f"Недопустимый переход: {r['status_label']} → {STATUS_LABEL.get(status, status)}")

    sets, vals = ["status=?"], [status]
    if status in ("done_onsite", "delivered", "closed"):
        sets.append("done_at=?")
        vals.append(db.now())
    if visit_result:
        sets.append("visit_result=?")
        vals.append(visit_result)
    db.execute(f"UPDATE requests SET {', '.join(sets)} WHERE id=?", (*vals, rid))
    _event(rid, actor, r["status"], status, comment or f"Статус изменён: {STATUS_LABEL.get(status, status)}")

    if status == "pickup_office":
        # забор в офис -> доставка заказчику на следующий рабочий день
        existing = db.q1("SELECT id FROM deliveries WHERE request_id=? AND status IN ('scheduled','postponed')", (rid,))
        if not existing:
            did = routing.schedule_delivery(rid, r["engineer_id"], date.today(),
                                            "Забор выполнен. Выдача заказчику на следующий рабочий день")
            d = db.q1("SELECT * FROM deliveries WHERE id=?", (did,))
            _event(rid, actor, None, None, f"Доставка заказчику запланирована на {d['scheduled_date']} (следующий рабочий день)")
        else:
            d = db.q1("SELECT * FROM deliveries WHERE id=?", (existing["id"],))
        if r["engineer_id"]:
            db.push_event(r["engineer_id"], "task.pickup", rid,
                          {"delivery_date": d["scheduled_date"], "number": r["number"]})
    if status == "done_onsite" and r["engineer_id"]:
        db.push_event(r["engineer_id"], "task.done", rid, {"number": r["number"]})
    if status in ("postponed",) and r["engineer_id"]:
        db.push_event(r["engineer_id"], "task.postponed", rid, {"number": r["number"], "comment": comment})
    db.audit(actor, "request.status", {"id": rid, "from": r["status"], "to": status, "minutes_spent": minutes_spent})
    res = get_request(rid)
    if status == "pickup_office":
        res["next_delivery"] = res["deliveries"][-1] if res["deliveries"] else None
    return res


def reassign(rid: int, engineer_id: int, actor: str, reason: str = "", day: str | None = None) -> dict:
    r = get_request(rid, with_events=False)
    eng = db.row2dict(db.q1("SELECT * FROM engineers WHERE id=?", (engineer_id,)))
    if not eng:
        raise HTTPException(404, "Инженер не найден")
    db.execute("UPDATE requests SET engineer_id=?, assigned_by=?, assigned_at=?, status=CASE WHEN status='new' THEN 'assigned' ELSE status END WHERE id=?",
               (engineer_id, actor, db.now(), rid))
    _event(rid, actor, r["status"], "assigned" if r["status"] == "new" else r["status"],
           f"Переназначено на {eng['full_name']}. {reason}".strip())
    db.push_event(engineer_id, "task.new", rid, {"number": r["number"], "address": r["address"],
                                                 "priority": r["priority"], "reassigned": True})
    db.audit(actor, "request.reassign", {"id": rid, "from": r["engineer_id"], "to": engineer_id, "reason": reason})
    return get_request(rid)


def postpone(rid: int, new_day: str, actor: str, reason: str = "") -> dict:
    r = get_request(rid, with_events=False)
    zones.parse_day(new_day)  # проверка формата
    db.execute("UPDATE requests SET planned_date=?, status=CASE WHEN status IN ('done_onsite','delivered','closed') THEN status ELSE 'postponed' END WHERE id=?",
               (new_day, rid))
    _event(rid, actor, r["status"], "postponed", f"Перенос на {new_day}. {reason}".strip())
    if r["engineer_id"]:
        db.push_event(r["engineer_id"], "task.postponed", rid, {"number": r["number"], "date": new_day, "reason": reason})
    return get_request(rid)


def stats(day_from: str | None = None, day_to: str | None = None) -> dict:
    day_from = day_from or (date.today() - timedelta(days=30)).isoformat()
    day_to = day_to or date.today().isoformat()
    args = (day_from, day_to)
    by_status = db.rows2dicts(db.q(
        "SELECT status, COUNT(*) AS c FROM requests WHERE date(created_at) BETWEEN date(?) AND date(?) GROUP BY status", args))
    by_priority = db.rows2dicts(db.q(
        "SELECT priority, COUNT(*) AS c FROM requests WHERE date(created_at) BETWEEN date(?) AND date(?) GROUP BY priority", args))
    by_engineer = db.rows2dicts(db.q(
        """SELECT COALESCE(e.full_name,'— не распределено —') AS engineer, COUNT(*) AS total,
                  SUM(CASE WHEN r.status IN ('done_onsite','delivered','closed') THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN r.status IN ('new','assigned','in_progress','pickup_office') THEN 1 ELSE 0 END) AS active
           FROM requests r LEFT JOIN engineers e ON e.id=r.engineer_id
           WHERE date(r.created_at) BETWEEN date(?) AND date(?)
           GROUP BY r.engineer_id ORDER BY total DESC""", args))
    by_zone = db.rows2dicts(db.q(
        """SELECT COALESCE(z.name,'—') AS zone, COUNT(*) AS c FROM requests r LEFT JOIN zones z ON z.id=r.zone_id
           WHERE date(r.created_at) BETWEEN date(?) AND date(?) GROUP BY r.zone_id ORDER BY c DESC""", args))
    by_work = db.rows2dicts(db.q(
        """SELECT w.name AS work, COUNT(*) AS c FROM requests r JOIN works w ON w.id=r.work_id
           WHERE date(r.created_at) BETWEEN date(?) AND date(?) GROUP BY r.work_id ORDER BY c DESC LIMIT 15""", args))
    for row in by_status:
        row["label"] = STATUS_LABEL.get(row["status"], row["status"])
    for row in by_priority:
        row["label"] = PRIORITY_LABEL.get(row["priority"], row["priority"])
    unassigned = db.q1("SELECT COUNT(*) AS c FROM requests WHERE engineer_id IS NULL")["c"]
    deliveries = db.rows2dicts(db.q(
        """SELECT d.scheduled_date, COUNT(*) AS c FROM deliveries d WHERE d.status IN ('scheduled','postponed')
           AND d.scheduled_date BETWEEN ? AND ? GROUP BY d.scheduled_date ORDER BY d.scheduled_date""", args))
    return {"period": {"from": day_from, "to": day_to}, "by_status": by_status, "by_priority": by_priority,
            "by_engineer": by_engineer, "by_zone": by_zone, "by_work": by_work,
            "unassigned": unassigned, "deliveries": deliveries,
            "workload": routing.workload_forecast(date.today())}


def export_csv(filters: dict) -> str:
    data = search(filters, limit=10000)
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM для Excel
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Номер", "Создана", "Контрагент", "УНП", "Р/с", "Контактное лицо", "Телефон", "Работа",
                "Приоритет", "Статус", "Район", "Инженер", "Адрес", "Широта", "Долгота", "План. дата",
                "Выполнено", "Пояснение"])
    for r in data["items"]:
        w.writerow([r["number"], r["created_at"], r["contractor"], r["unp"], r["bank_account"],
                    r["contact_person"], r["phone"], r["work_name"], r["priority_label"], r["status_label"],
                    r["zone_name"] or "", r["engineer_name"] or "", r["address"], r["lat"], r["lon"],
                    r["planned_date"], r["done_at"] or "", (r["comment"] or "").replace("\n", " ")])
    return buf.getvalue()
