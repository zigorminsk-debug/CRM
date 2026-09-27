"""CRM «Заправка картриджей и ремонт оргтехники» — сервер приёма и обработки заявок.

Роли:
  * admin / operator  — веб-админка (зоны, инженеры, отпуска, история, отчёты)
  * engineer          — мобильное приложение (маршрут на день, чекбоксы готовности)
Windows-клиент подаёт заявки через /api/requests (публичный контур с ключом при необходимости).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, geocode, requests_service as rs, routing, seed, validation, zones

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DOWNLOAD_DIR = os.environ.get("CRM_DOWNLOAD_DIR", os.path.join(os.path.dirname(BASE_DIR), "..", "downloads"))

app = FastAPI(
    title="CRM: заявки на заправку картриджей и ремонт оргтехники",
    version="1.0.0",
    description="Сервер приёма заявок (Windows-клиент), распределения по зонам, маршрутизации (Android) и истории.",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"], expose_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.get_conn()
    auth.ensure_default_admin()
    seed.seed()
    seed.demo_requests()
    db.push_event(None, "server.started", None, {"version": app.version})


# =====================================================================
#  Модели запросов
# =====================================================================

class LoginIn(BaseModel):
    username: str
    password: str
    device: str = ""


class RequestIn(BaseModel):
    """Форма заявки (Windows-клиент). Обязательные поля помечены Field(...)."""
    contractor: str = Field(..., description="Наименование контрагента")
    unp: str = Field(..., description="УНП, 9 цифр с контролем")
    bank_account: str = Field(..., description="Расчётный счёт (IBAN BY… или 13 знаков)")
    contact_person: str = Field(..., description="Контактное лицо")
    phone: str = Field(..., description="Телефон, приводится к +375 XX XXX-XX-XX")
    work_id: int | None = None
    work_code: str | None = None
    priority: str = "normal"
    address: str = Field(..., description="Адрес: Минск / Минский район, переводится в координаты")
    lat: float | None = None
    lon: float | None = None
    zone_id: int | None = None
    comment: str = ""
    equipment: str | None = None
    serial: str | None = None
    email: str | None = None
    bank_name: str | None = None
    planned_date: str | None = None


class StatusIn(BaseModel):
    status: str
    comment: str = ""
    visit_result: str | None = None
    minutes_spent: int | None = None


class ReassignIn(BaseModel):
    engineer_id: int
    reason: str = ""


class PostponeIn(BaseModel):
    new_day: str
    reason: str = ""


class ZoneAssignIn(BaseModel):
    zone_id: int
    engineer_id: int
    date_from: str
    date_to: str | None = None
    reason: str = "Закрепление зоны"
    is_primary: int = 1


class AbsenceIn(BaseModel):
    engineer_id: int
    date_from: str
    date_to: str
    kind: str = "vacation"
    replacement_engineer_id: int | None = None
    comment: str = ""


class EngineerIn(BaseModel):
    full_name: str
    phone: str | None = None
    base_lat: float | None = None
    base_lon: float | None = None
    active: int = 1
    notes: str | None = None


class UserIn(BaseModel):
    username: str
    password: str
    role: str
    full_name: str | None = None
    engineer_id: int | None = None


class WorkIn(BaseModel):
    code: str
    name: str
    category: str
    site_kind: str = "onsite"
    default_minutes: int = 60
    active: int = 1


# =====================================================================
#  Служебные эндпоинты
# =====================================================================

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": db.now(), "version": app.version,
            "db": os.path.basename(db.DB_PATH), "zone_count": len(zones.all_zones())}


@app.get("/api/config")
def public_config() -> dict:
    return {
        "company_name": db.setting("company_name", ""),
        "office_phone": db.setting("office_phone", ""),
        "priorities": [{"code": k, "label": v} for k, v in routing.PRIORITY_LABEL.items()],
        "statuses": [{"code": k, "label": v} for k, v in routing.STATUS_LABEL.items()],
        "yandex_geocoder_enabled": bool(db.setting("yandex_geocoder_key")),
    }


@app.post("/api/auth/login")
def api_login(inp: LoginIn) -> dict:
    return auth.login(inp.username, inp.password, inp.device)


@app.post("/api/auth/logout")
def api_logout(user: dict = Depends(auth.current_user)) -> dict:
    auth.logout(user["token"])
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(user: dict = Depends(auth.current_user)) -> dict:
    return user


# =====================================================================
#  Справочники
# =====================================================================

@app.get("/api/works")
def api_works(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM works" + (" WHERE active=1" if active_only else "") + " ORDER BY category, name"
    return db.rows2dicts(db.q(sql))


@app.get("/api/priority")
def api_priority() -> list[dict]:
    return [{"code": k, "label": v} for k, v in routing.PRIORITY_LABEL.items()]


@app.get("/api/geo/resolve")
def api_geo_resolve(address: str = Query(..., min_length=3), refresh: bool = False) -> dict:
    """Адрес -> координаты + район + ответственный инженер (для Яндекс.Навигатора)."""
    return geocode.resolve(address, use_cache=not refresh)


@app.get("/api/geo/zones")
def api_zones() -> list[dict]:
    return zones.all_zones()


@app.get("/api/geo/coverage")
def api_coverage(day: str | None = None) -> list[dict]:
    return zones.coverage(zones.parse_day(day) if day else date.today())


@app.get("/api/contractors")
def api_contractors(q: str = "", limit: int = 20) -> list[dict]:
    if not q:
        return db.rows2dicts(db.q("SELECT * FROM contractors ORDER BY id DESC LIMIT ?", (limit,)))
    like = f"%{q}%"
    return db.rows2dicts(db.q(
        """SELECT * FROM contractors WHERE name LIKE ? OR unp LIKE ? OR REPLACE(bank_account,' ','') LIKE ?
           OR contact_person LIKE ? ORDER BY id DESC LIMIT ?""", (like, like, like, like, limit)))


# =====================================================================
#  Заявки
# =====================================================================

@app.post("/api/requests", status_code=201)
def api_create_request(inp: RequestIn, request: Request) -> dict:
    """Приём заявки из формы Windows-клиента."""
    data = inp.model_dump()
    actor = request.headers.get("X-Client-Name") or "windows-client"
    token = request.headers.get("Authorization", "")
    if token.lower().startswith("bearer "):
        try:
            actor = auth.current_user(request)["username"]
        except HTTPException:
            pass
    return rs.create_request(data, actor=actor, source="win")


@app.get("/api/requests")
def api_search_requests(
    contractor: str | None = None, unp: str | None = None, bank_account: str | None = None,
    phone: str | None = None, status: str | None = None, engineer_id: int | None = None,
    zone_id: int | None = None, priority: str | None = None, date_from: str | None = None,
    date_to: str | None = None, day: str | None = None, number: str | None = None,
    q: str | None = None, order: str | None = None, limit: int = 200, offset: int = 0,
) -> dict:
    """История заявок с поиском по контрагенту, УНП, р/с, телефону, периоду и тексту."""
    return rs.search(locals(), limit=min(limit, 1000), offset=offset)


@app.get("/api/requests/export.csv")
def api_export(contractor: str | None = None, unp: str | None = None, bank_account: str | None = None,
               date_from: str | None = None, date_to: str | None = None, status: str | None = None) -> StreamingResponse:
    data = rs.export_csv(locals())
    return StreamingResponse(io.BytesIO(data.encode("utf-8")), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="requests.csv"'})


@app.get("/api/requests/{rid}")
def api_get_request(rid: int) -> dict:
    return rs.get_request(rid)


@app.post("/api/requests/{rid}/status")
def api_status(rid: int, inp: StatusIn, user: dict = Depends(auth.current_user)) -> dict:
    return rs.change_status(rid, inp.status, user["username"], inp.comment, inp.visit_result, inp.minutes_spent)


@app.post("/api/requests/{rid}/reassign")
def api_reassign(rid: int, inp: ReassignIn, user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    return rs.reassign(rid, inp.engineer_id, user["username"], inp.reason)


@app.post("/api/requests/{rid}/postpone")
def api_postpone(rid: int, inp: PostponeIn, user: dict = Depends(auth.current_user)) -> dict:
    return rs.postpone(rid, inp.new_day, user["username"], inp.reason)


@app.get("/api/history/contractor")
def api_history_contractor(value: str, kind: str = "contractor") -> dict:
    """История заявок по контрагенту / УНП / расчётному счёту."""
    return rs.contractor_history(value, kind)


# =====================================================================
#  Кабинет инженера (мобильное приложение / APK)
# =====================================================================

class GoIn(BaseModel):
    lat: float | None = None
    lon: float | None = None


class DoneIn(BaseModel):
    result: str = Field(..., description="onsite — готово на месте, pickup_office — забор в офис")
    comment: str = ""
    minutes_spent: int | None = None


class DeliveryPostponeIn(BaseModel):
    days: int = 1
    comment: str = ""


def _engineer_id_of(user: dict, engineer_id: int | None) -> int:
    if user["role"] == "engineer":
        if not user.get("engineer_id"):
            raise HTTPException(400, "Пользователь не привязан к инженеру")
        return int(user["engineer_id"])
    if not engineer_id:
        raise HTTPException(400, "Укажите engineer_id (для админа/оператора)")
    return int(engineer_id)


@app.get("/api/engineer/route")
def api_engineer_route(day: str | None = None, engineer_id: int | None = None,
                       user: dict = Depends(auth.current_user)) -> dict:
    """Маршрут на день: срочность + география, ссылки для Яндекс.Навигатора."""
    eid = _engineer_id_of(user, engineer_id)
    return routing.plan_day(eid, zones.parse_day(day) if day else date.today())


@app.get("/api/engineer/tasks")
def api_engineer_tasks(day: str | None = None, engineer_id: int | None = None,
                       user: dict = Depends(auth.current_user)) -> dict:
    eid = _engineer_id_of(user, engineer_id)
    d = zones.parse_day(day) if day else date.today()
    tasks = db.rows2dicts(db.q(
        """SELECT r.id, r.number, r.priority, r.status, r.address, r.lat, r.lon, r.contact_person, r.phone,
                  r.comment, r.contractor, r.unp, r.bank_account, r.planned_date, r.equipment, r.serial,
                  w.name AS work_name, w.site_kind, z.name AS zone_name
           FROM requests r JOIN works w ON w.id=r.work_id LEFT JOIN zones z ON z.id=r.zone_id
           WHERE r.engineer_id=? AND r.status IN ('new','assigned','in_progress','postponed','pickup_office')
           ORDER BY CASE r.priority WHEN 'emergency' THEN 0 WHEN 'urgent' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, r.id""",
        (eid,)))
    for t in tasks:
        t["nav_intent"] = (f"yandexnavi://build_route_on_map?lat_to={t['lat']}&lon_to={t['lon']}"
                           if t["lat"] and t["lon"] else None)
        t["priority_label"] = routing.PRIORITY_LABEL.get(t["priority"], t["priority"])
        t["status_label"] = routing.STATUS_LABEL.get(t["status"], t["status"])
        t["phone_formatted"] = validation.format_phone(t["phone"])
    return {"day": d.isoformat(), "tasks": tasks, "deliveries": routing.deliveries_for(eid, d)}


@app.post("/api/engineer/task/{rid}/go")
def api_task_go(rid: int, inp: GoIn, user: dict = Depends(auth.current_user)) -> dict:
    """Кнопка «Поехали» — заявка переходит в работу, клиент открывает Яндекс.Навигатор."""
    res = rs.change_status(rid, "in_progress", user["username"], "Инженер нажал «Поехали»")
    res["nav_intent"] = f"yandexnavi://build_route_on_map?lat_to={res['lat']}&lon_to={res['lon']}" if res["lat"] else None
    res["nav_url"] = f"https://yandex.ru/maps/?rtext={res['lat']},{res['lon']}&rtt=auto" if res["lat"] else None
    if inp.lat and inp.lon:
        db.audit(user["username"], "engineer.position", {"request_id": rid, "lat": inp.lat, "lon": inp.lon})
    return res


@app.post("/api/engineer/task/{rid}/done")
def api_task_done(rid: int, inp: DoneIn, user: dict = Depends(auth.current_user)) -> dict:
    """Чекбокс готовности: на месте либо забор в офис (доставка на след. рабочий день)."""
    if inp.result == "onsite":
        status, visit = "done_onsite", "Выполнено на месте"
    elif inp.result == "pickup_office":
        status, visit = "pickup_office", "Забор в офис"
    else:
        raise HTTPException(422, "result должен быть onsite или pickup_office")
    res = rs.change_status(rid, status, user["username"], inp.comment, visit, inp.minutes_spent)
    if inp.result == "pickup_office" and res.get("deliveries"):
        res["delivery"] = res["deliveries"][-1]
    return res


@app.post("/api/engineer/delivery/{delivery_id}/postpone")
def api_delivery_postpone(delivery_id: int, inp: DeliveryPostponeIn,
                          user: dict = Depends(auth.current_user)) -> dict:
    """Перенос доставки заказчику, если оборудование/картриджи не готовы."""
    return routing.postpone_delivery(delivery_id, inp.days, inp.comment, user["username"])


@app.post("/api/engineer/delivery/{delivery_id}/done")
def api_delivery_done(delivery_id: int, user: dict = Depends(auth.current_user)) -> dict:
    d = db.row2dict(db.q1("SELECT * FROM deliveries WHERE id=?", (delivery_id,)))
    if not d:
        raise HTTPException(404, "Доставка не найдена")
    db.execute("UPDATE deliveries SET status='done', updated_at=? WHERE id=?", (db.now(), delivery_id))
    rs.change_status(d["request_id"], "delivered", user["username"], "Оборудование/картриджи доставлены заказчику")
    return {"ok": True, "request": rs.get_request(d["request_id"])}


@app.get("/api/engineer/feed")
def api_engineer_feed(since: int = 0, engineer_id: int | None = None, wait: int = 0,
                      user: dict = Depends(auth.current_user)) -> dict:
    """Лента push-событий для приложения инженера (long-poll: wait=25 секунд)."""
    eid = _engineer_id_of(user, engineer_id)
    deadline = time.time() + max(0, min(wait, 30))
    while True:
        rows = db.rows2dicts(db.q(
            """SELECT * FROM outbox WHERE id>? AND (engineer_id=? OR engineer_id IS NULL)
               ORDER BY id LIMIT 50""", (since, eid)))
        if rows or time.time() >= deadline:
            for r in rows:
                try:
                    r["payload"] = json.loads(r["payload"] or "{}")
                except json.JSONDecodeError:
                    r["payload"] = {}
            return {"events": rows, "last_id": rows[-1]["id"] if rows else since}
        time.sleep(1.5)


@app.get("/api/events/stream")
async def api_events_stream(request: Request, user: dict = Depends(auth.current_user)) -> StreamingResponse:
    """SSE-поток изменений для админки (живое обновление списка заявок)."""

    async def gen():
        last = int(request.query_params.get("since", 0))
        while True:
            rows = db.rows2dicts(db.q(
                "SELECT id, at, kind, request_id, engineer_id FROM outbox WHERE id>? ORDER BY id LIMIT 100", (last,)))
            for r in rows:
                last = max(last, r["id"])
                yield f"data: {json.dumps(r, ensure_ascii=False)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =====================================================================
#  Админка: инженеры, зоны, отпуска, справочники, отчёты
# =====================================================================

@app.get("/api/admin/engineers")
def api_engineers(active_only: bool = False, user: dict = Depends(auth.current_user)) -> list[dict]:
    sql = "SELECT * FROM engineers" + (" WHERE active=1" if active_only else "") + " ORDER BY full_name"
    rows = db.rows2dicts(db.q(sql))
    for e in rows:
        e["zones_count"] = db.q1("SELECT COUNT(*) AS c FROM zone_assignments WHERE engineer_id=? AND date_to IS NULL",
                                 (e["id"],))["c"]
        e["open_tasks"] = db.q1("""SELECT COUNT(*) AS c FROM requests WHERE engineer_id=?
                                   AND status IN ('new','assigned','in_progress','pickup_office')""", (e["id"],))["c"]
        e["absence"] = db.row2dict(db.q1(
            "SELECT * FROM absences WHERE engineer_id=? AND date_from<=date('now') AND date_to>=date('now') ORDER BY id DESC LIMIT 1",
            (e["id"],)))
    return rows


@app.post("/api/admin/engineers")
def api_engineer_create(inp: EngineerIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    eid = db.execute("INSERT INTO engineers(full_name,phone,base_lat,base_lon,active,notes) VALUES(?,?,?,?,?,?)",
                     (inp.full_name, validation.format_phone(validation.normalize_phone(inp.phone or "")) or None,
                      inp.base_lat, inp.base_lon, inp.active, inp.notes))
    db.audit(user["username"], "engineer.create", {"id": eid, "name": inp.full_name})
    return db.row2dict(db.q1("SELECT * FROM engineers WHERE id=?", (eid,)))


@app.put("/api/admin/engineers/{eid}")
def api_engineer_update(eid: int, inp: EngineerIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    db.execute("UPDATE engineers SET full_name=?, phone=?, base_lat=?, base_lon=?, active=?, notes=? WHERE id=?",
               (inp.full_name, inp.phone, inp.base_lat, inp.base_lon, inp.active, inp.notes, eid))
    db.audit(user["username"], "engineer.update", {"id": eid})
    return db.row2dict(db.q1("SELECT * FROM engineers WHERE id=?", (eid,)))


@app.get("/api/admin/users")
def api_users(user: dict = Depends(auth.require_roles("admin"))) -> list[dict]:
    return db.rows2dicts(db.q(
        "SELECT id,username,role,full_name,engineer_id,active,created_at FROM users ORDER BY id"))


@app.post("/api/admin/users")
def api_user_create(inp: UserIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    if db.q1("SELECT 1 FROM users WHERE username=?", (inp.username.lower(),)):
        raise HTTPException(409, "Такой логин уже существует")
    if inp.role not in ("admin", "operator", "engineer"):
        raise HTTPException(422, "Роль: admin | operator | engineer")
    uid = auth.create_user(inp.username.lower(), inp.password, inp.role, inp.full_name or inp.username, inp.engineer_id)
    db.audit(user["username"], "user.create", {"username": inp.username, "role": inp.role})
    return {"id": uid, "username": inp.username.lower(), "role": inp.role}


@app.get("/api/admin/assignments")
def api_assignments(day: str | None = None, user: dict = Depends(auth.current_user)) -> list[dict]:
    d = zones.parse_day(day).isoformat() if day else date.today().isoformat()
    return db.rows2dicts(db.q(
        """SELECT za.*, z.name AS zone_name, e.full_name AS engineer_name
           FROM zone_assignments za JOIN zones z ON z.id=za.zone_id JOIN engineers e ON e.id=za.engineer_id
           WHERE za.date_from <= ? AND (za.date_to IS NULL OR za.date_to >= ?)
           ORDER BY z.kind, z.name""", (d, d)))


@app.post("/api/admin/assignments")
def api_assignment_create(inp: ZoneAssignIn, user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    zid = zones.assign_zone(inp.zone_id, inp.engineer_id, inp.date_from, inp.date_to,
                            user["username"], inp.reason, inp.is_primary)
    moved = zones.reassign_requests_of_absent_engineers()
    return {"ok": True, "assignment_id": zid, "requests_reassigned": moved}


@app.get("/api/admin/absences")
def api_absences(user: dict = Depends(auth.current_user)) -> list[dict]:
    return db.rows2dicts(db.q(
        """SELECT a.*, e.full_name AS engineer_name, r.full_name AS replacement_name
           FROM absences a JOIN engineers e ON e.id=a.engineer_id
           LEFT JOIN engineers r ON r.id=a.replacement_engineer_id
           ORDER BY a.date_from DESC"""))


@app.post("/api/admin/absences")
def api_absence_create(inp: AbsenceIn, user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    aid = zones.add_absence(inp.engineer_id, inp.date_from, inp.date_to, inp.kind,
                            inp.replacement_engineer_id, inp.comment, user["username"])
    moved = zones.reassign_requests_of_absent_engineers()
    if inp.replacement_engineer_id:
        db.push_event(inp.replacement_engineer_id, "zone.replacement", None,
                      {"from": inp.date_from, "to": inp.date_to, "kind": inp.kind})
    return {"ok": True, "absence_id": aid, "requests_reassigned": moved}


@app.delete("/api/admin/absences/{aid}")
def api_absence_delete(aid: int, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    res = zones.remove_absence(aid, user["username"])
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "Запись не найдена"))
    return res


@app.post("/api/admin/reassign-absent")
def api_reassign_absent(day: str | None = None, user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    from datetime import date as _date
    moved = zones.reassign_requests_of_absent_engineers(zones.parse_day(day) if day else _date.today())
    return {"ok": True, "requests_reassigned": moved, "coverage": zones.coverage()}


@app.get("/api/admin/works")
def api_admin_works(user: dict = Depends(auth.current_user)) -> list[dict]:
    return db.rows2dicts(db.q("SELECT * FROM works ORDER BY category, name"))


@app.post("/api/admin/works")
def api_work_create(inp: WorkIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    wid = db.execute("""INSERT INTO works(code,name,category,site_kind,default_minutes,active)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,
                        category=excluded.category, site_kind=excluded.site_kind,
                        default_minutes=excluded.default_minutes, active=excluded.active""",
                     (inp.code.upper(), inp.name, inp.category, inp.site_kind, inp.default_minutes, inp.active))
    db.audit(user["username"], "work.upsert", {"code": inp.code})
    return db.row2dict(db.q1("SELECT * FROM works WHERE code=?", (inp.code.upper(),)))


class StreetIn(BaseModel):
    name: str
    lat: float
    lon: float
    district: str | None = None
    kind: str = "street"


@app.get("/api/admin/streets")
def api_streets(q: str = "", limit: int = 100, user: dict = Depends(auth.current_user)) -> dict:
    """Локальный справочник адресов: работает без доступа в интернет."""
    if q:
        rows = db.rows2dicts(db.q("SELECT * FROM street_index WHERE key LIKE ? OR name LIKE ? ORDER BY name LIMIT ?",
                                  (f"%{geocode.street_key(q)}%", f"%{q}%", min(limit, 500))))
    else:
        rows = db.rows2dicts(db.q("SELECT * FROM street_index ORDER BY kind, name LIMIT ?", (min(limit, 500),)))
    total = db.q1("SELECT COUNT(*) AS c FROM street_index")["c"]
    return {"total": total, "items": rows}


@app.post("/api/admin/streets")
def api_street_add(inp: StreetIn, user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    sid = db.execute(
        """INSERT INTO street_index(name,key,lat,lon,district,kind,source,created_at)
           VALUES(?,?,?,?,?,?,'admin',?)""",
        (inp.name, geocode.street_key(inp.name), inp.lat, inp.lon, inp.district, inp.kind, db.now()))
    db.audit(user["username"], "street.add", {"name": inp.name, "lat": inp.lat, "lon": inp.lon})
    return {"ok": True, "id": sid, "key": geocode.street_key(inp.name)}


@app.get("/api/admin/settings")
def api_settings(user: dict = Depends(auth.current_user)) -> dict:
    return db.rows2dicts(db.q("SELECT key, value FROM settings ORDER BY key"))


class SettingsIn(BaseModel):
    values: dict[str, str]


@app.post("/api/admin/settings")
def api_settings_set(inp: SettingsIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    for k, v in inp.values.items():
        db.set_setting(k, str(v), user["username"])
    return {"ok": True, "count": len(inp.values)}


@app.get("/api/admin/coverage")
def api_admin_coverage(day: str | None = None, user: dict = Depends(auth.current_user)) -> dict:
    d = zones.parse_day(day) if day else date.today()
    return {"day": d.isoformat(), "coverage": zones.coverage(d),
            "absent": zones.absent_engineers(d), "workload": routing.workload_forecast(d)}


@app.get("/api/admin/reports/summary")
def api_reports(day_from: str | None = None, day_to: str | None = None,
                user: dict = Depends(auth.current_user)) -> dict:
    return rs.stats(day_from, day_to)


@app.get("/api/admin/routes")
def api_admin_routes(day: str | None = None, user: dict = Depends(auth.current_user)) -> list[dict]:
    return routing.all_engineers_routes(zones.parse_day(day) if day else date.today())


@app.post("/api/admin/holidays")
def api_holiday_add(day: str, name: str = "Праздник", user: dict = Depends(auth.require_roles("admin"))) -> dict:
    db.execute("INSERT INTO holidays(day,name) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET name=excluded.name",
               (day, name))
    db.audit(user["username"], "holiday.add", {"day": day})
    return {"ok": True}


@app.delete("/api/admin/holidays/{day}")
def api_holiday_del(day: str, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    db.execute("DELETE FROM holidays WHERE day=?", (day,))
    return {"ok": True}


@app.get("/api/admin/audit")
def api_audit(limit: int = 200, user: dict = Depends(auth.require_roles("admin"))) -> list[dict]:
    return db.rows2dicts(db.q("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 2000),)))


# =====================================================================
#  Раздача файлов: APK инженера, exe для Windows, серверный дистрибутив
# =====================================================================

DOWNLOAD_FILES = {
    "win": ("CRM-Windows-Setup.exe", "Клиент для Windows (приём заявок)"),
    "apk": ("CRM-Engineer.apk", "Приложение инженера для Android"),
    "server": ("crm-server.zip", "Сервер (Python)"),
    "src": ("crm-sources.zip", "Исходники клиента Windows и Android"),
}


@app.get("/api/downloads")
def api_downloads() -> list[dict]:
    out = []
    for key, (fname, title) in DOWNLOAD_FILES.items():
        path = os.path.join(DOWNLOAD_DIR, fname)
        out.append({
            "key": key, "file": fname, "title": title, "available": os.path.exists(path),
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
            "url": f"/download/{fname}" if os.path.exists(path) else None,
        })
    return out


@app.get("/download/{fname}")
def api_download(fname: str):
    safe = os.path.basename(fname)
    path = os.path.join(DOWNLOAD_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, f"Файл {safe} ещё не собран на этом сервере")
    return FileResponse(path, filename=safe)


@app.get("/downloads")
def downloads_page() -> HTMLResponse:
    items = api_downloads()
    rows = "".join(
        f'<li><b>{i["title"]}</b> — <code>{i["file"]}</code> '
        + (f'({i["size"]//1024} КБ) <a href="{i["url"]}">скачать</a>' if i["available"] else '— не найден на сервере')
        + "</li>" for i in items)
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
    <title>Загрузки CRM</title><style>body{{font:15px system-ui;margin:40px;max-width:760px}}
    h1{{font-size:22px}} li{{margin:8px 0}}</style></head><body>
    <h1>Файлы приложений</h1><ul>{rows}</ul>
    <p><a href="/admin">→ Веб-админка диспетчера</a> · <a href="/m">→ Мобильное приложение инженера</a> · <a href="/docs">→ API</a></p>
    </body></html>""")


# =====================================================================
#  Веб-интерфейсы (админка и мобильный клиент инженера)
# =====================================================================

@app.get("/")
def root() -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
    <title>CRM — заявки на заправку картриджей и ремонт оргтехники</title>
    <style>body{{font:16px system-ui;margin:40px;max-width:820px;line-height:1.5}}
    a.b{{display:inline-block;margin:6px 12px 6px 0;padding:10px 16px;background:#1a56db;color:#fff;
    border-radius:8px;text-decoration:none}}</style></head><body>
    <h1>Сервер заявок работает</h1>
    <p>Время сервера: {db.now()}</p>
    <p><a class="b" href="/admin">Веб-админка диспетчера</a>
       <a class="b" href="/m">Приложение инженера (мобильное)</a>
       <a class="b" href="/downloads">Файлы (exe / apk)</a>
       <a class="b" href="/docs">API (docs)</a></p>
    <p>Windows-клиент подаёт заявки на <code>POST /api/requests</code>, инженер получает их
       в мобильном приложении с маршрутом на день и Яндекс.Навигатором.</p>
    </body></html>""")


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "admin", "index.html"))


@app.get("/m")
def mobile_page() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "mobile", "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail,
                                                              "status": exc.status_code})
