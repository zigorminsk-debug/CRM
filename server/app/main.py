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
import sqlite3
import time
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, db, geocode, requests_service as rs, routing, seed, updates, validation, zones
from . import _runtime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _runtime.is_frozen():
    # exe: ресурсы вшиты в бандл (sys._MEIPASS), static лежит в app/static — см. crm-server.spec
    STATIC_DIR = os.path.join(_runtime.bundle_root(), "app", "static")
else:
    STATIC_DIR = os.path.join(BASE_DIR, "static")
if os.environ.get("CRM_DOWNLOAD_DIR"):
    DOWNLOAD_DIR = os.environ["CRM_DOWNLOAD_DIR"]
elif _runtime.is_frozen():
    # exe: папка раздачи рядом с CRM-Server.exe (туда же кладут CRM-Windows.exe и APK)
    DOWNLOAD_DIR = _runtime.writable_dir("downloads")
else:
    DOWNLOAD_DIR = os.path.join(os.path.dirname(BASE_DIR), "..", "downloads")

app = FastAPI(
    title="Cartridge Engineer: заявки на заправку картриджей и ремонт оргтехники",
    version=updates.version_info()["version"],
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
    unp: str = Field("", description="УНП (необязательно — могут быть частные лица)")
    bank_account: str = Field("", description="Расчётный счёт (необязательно)")
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
    time_from: str | None = Field(None, description="Окно визита «с», ЧЧ:ММ")
    time_to: str | None = Field(None, description="Окно визита «по», ЧЧ:ММ")


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
    company: str | None = None
    unp: str | None = None


class PasswordIn(BaseModel):
    password: str


class ActiveIn(BaseModel):
    active: int


class ClientRequestIn(BaseModel):
    """Заявка из кабинета клиента: реквизиты организации берутся из профиля."""
    contact_person: str = Field(..., description="Контактное лицо")
    phone: str = Field(..., description="Телефон, приводится к +375 XX XXX-XX-XX")
    work_id: int | None = None
    work_code: str | None = None
    priority: str = "normal"
    address: str = Field(..., description="Адрес: Минск / Минский район")
    comment: str = ""
    equipment: str | None = None
    serial: str | None = None


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
    return {"ok": True, "time": db.now(), "version": updates.version_info()["version"],
            "db": os.path.basename(db.DB_PATH), "data_dir": db.DATA_DIR,
            "zone_count": len(zones.all_zones())}


@app.get("/api/version")
def api_version() -> dict:
    """Версия сервера и адрес манифеста автообновления (используется клиентами)."""
    info = updates.version_info()
    info["manifest_url"] = updates.manifest_url()
    return info


@app.get("/api/updates")
def api_updates(force: bool = False) -> dict:
    """Сведения об обновлениях: текущая версия + последний релиз на GitHub.

    Windows-клиент и приложение инженера сравнивают номер сборки (`build`) со своим
    и при наличии новой версии скачивают файл из релиза (подпись — постоянным ключом).
    """
    return updates.updates_payload(force=force)


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
    """Справочник контрагентов + поиск (регистронезависимо, кириллица — в Python)."""
    if not q:
        return db.rows2dicts(db.q("SELECT * FROM contractors ORDER BY id DESC LIMIT ?", (limit,)))
    t = q.strip().lower()
    rows = db.rows2dicts(db.q("SELECT * FROM contractors ORDER BY id DESC LIMIT 2000"))
    def _hit(d: dict) -> bool:
        return (t in (d.get("name") or "").lower()
                or t in (d.get("unp") or "")
                or t in (d.get("bank_account") or "").lower()
                or t in (d.get("contact_person") or "").lower()
                or t in (d.get("phone") or ""))
    starts = [d for d in rows if _hit(d) and (d.get("name") or "").lower().startswith(t)]
    rest = [d for d in rows if _hit(d) and d not in starts]
    return (starts + rest)[:limit]


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


@app.get("/api/geo/streets")
def api_geo_streets(q: str = Query(..., min_length=2, max_length=80), settlement: str = "",
                    user: dict = Depends(auth.current_user)) -> list[dict]:
    """Подсказки улиц/проспектов/переулков по началу ввода."""
    return geocode.search_streets(q, settlement)


@app.get("/api/geo/settlements")
def api_geo_settlements(q: str = Query(..., min_length=2, max_length=80),
                        user: dict = Depends(auth.current_user)) -> list[dict]:
    """Подсказки населённых пунктов (Минск и Минская область)."""
    return geocode.search_settlements(q)


@app.get("/api/admin/backup/info")
def api_backup_info(user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    """Где лежит база, её размер и имеющиеся копии (data/backups)."""
    def _dir_items():
        items = []
        try:
            for name in os.listdir(db.BACKUP_DIR):
                p = os.path.join(db.BACKUP_DIR, name)
                if os.path.isfile(p):
                    items.append({"name": name, "size": os.path.getsize(p),
                                  "modified": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")})
        except OSError:
            pass
        return sorted(items, key=lambda x: x["name"], reverse=True)

    size = os.path.getsize(db.DB_PATH) if os.path.exists(db.DB_PATH) else 0
    return {"ok": True, "data_dir": db.DATA_DIR, "db_file": os.path.basename(db.DB_PATH),
            "db_size": size, "backups": _dir_items()}


@app.get("/api/admin/backup")
def api_backup_download(user: dict = Depends(auth.require_roles("admin", "operator"))) -> FileResponse:
    """Выгрузить базу в бэкап: консистентная копия + скачивание файла."""
    os.makedirs(db.BACKUP_DIR, exist_ok=True)
    stamp = "".join(ch for ch in db.now() if ch.isdigit())[:14]
    path = os.path.join(db.BACKUP_DIR, f"crm-backup-{stamp}.sqlite")
    db.backup_to(path)
    db.audit(user.get("username"), "backup.export", {"file": os.path.basename(path)})
    return FileResponse(path, filename=os.path.basename(path),
                        media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(path)}"'})


@app.post("/api/admin/backup/restore")
async def api_backup_restore(request: Request,
                             user: dict = Depends(auth.require_roles("admin"))) -> dict:
    """Загрузить базу из бэкапа (только администратор).

    Тело запроса — файл .sqlite. Текущая база перед заменой сохраняется
    в data/backups/auto-before-restore-*.sqlite.
    """
    data = await request.body()
    if len(data) < 100 or not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(422, "Это не файл базы SQLite — загрузите файл, выгруженный кнопкой «Выгрузить базу в бэкап»")
    os.makedirs(db.BACKUP_DIR, exist_ok=True)
    tmp = os.path.join(db.BACKUP_DIR, f"upload-{''.join(ch for ch in db.now() if ch.isdigit())[:14]}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)

    # проверка копии: целостность и наличие ключевых таблиц
    try:
        chk = sqlite3.connect(tmp)
        try:
            ic = chk.execute("PRAGMA integrity_check").fetchone()[0]
            if ic != "ok":
                raise HTTPException(422, f"Файл повреждён (integrity_check: {ic})")
            tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for need in ("users", "requests", "works", "zones"):
                if need not in tables:
                    raise HTTPException(422, f"В файле нет таблицы {need} — это не база CRM")
            counts = {t: chk.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("users", "requests", "engineers", "contractors")}
        finally:
            chk.close()
    except HTTPException:
        os.remove(tmp)
        raise
    except sqlite3.Error as exc:
        os.remove(tmp)
        raise HTTPException(422, f"Файл не читается как база SQLite: {exc}")

    # страховка: текущая база -> auto-before-restore-*.sqlite
    stamp = "".join(ch for ch in db.now() if ch.isdigit())[:14]
    safety = os.path.join(db.BACKUP_DIR, f"auto-before-restore-{stamp}.sqlite")
    try:
        db.backup_to(safety)
    except sqlite3.Error:
        safety = ""
    db.restore_from(tmp)
    _keep_last_backups(20)
    db.audit(user.get("username"), "backup.restore",
             {"users": counts["users"], "requests": counts["requests"], "safety_copy": os.path.basename(safety) or "-"})
    return {"ok": True, "message": f"База восстановлена: пользователей {counts['users']}, заявок {counts['requests']}",
            "safety_copy": os.path.basename(safety) if safety else None, "counts": counts}


def _keep_last_backups(limit: int = 20) -> None:
    """В data/backups храним не больше limit последних файлов."""
    try:
        files = sorted((os.path.join(db.BACKUP_DIR, n) for n in os.listdir(db.BACKUP_DIR)
                        if n.endswith(".sqlite")), key=os.path.getmtime, reverse=True)
        for extra in files[limit:]:
            os.remove(extra)
    except OSError:
        pass


@app.get("/api/engineer/history")
def api_engineer_history(engineer_id: int | None = None, limit: int = 100,
                         user: dict = Depends(auth.current_user)) -> dict:
    """Исполненные заявки инженера (для кнопки «История» в Android)."""
    eid = _engineer_id_of(user, engineer_id)
    rows = db.q("""SELECT r.id, r.number, r.status, r.address, r.contractor, r.phone,
                          r.planned_date, r.time_from, r.time_to, r.done_at, r.created_at, w.name AS work_name
                   FROM requests r JOIN works w ON w.id=r.work_id
                   WHERE r.engineer_id=? AND r.status IN ('done_onsite','delivered','closed')
                   ORDER BY COALESCE(r.done_at, r.planned_date) DESC LIMIT ?""", (eid, limit))
    items = db.rows2dicts(rows)
    for t in items:
        t["status_label"] = routing.STATUS_LABEL.get(t["status"], t["status"])
        t["phone_formatted"] = validation.format_phone(t["phone"])
    return {"ok": True, "items": items}


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
                  w.name AS work_name, w.site_kind, z.name AS zone_name,
                  r.time_from, r.time_to
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

# =====================================================================
#  Кабинет клиента (role=client): свои заявки и доставки по УНП профиля
# =====================================================================

def _client_profile(user: dict) -> dict:
    return {"username": user["username"], "full_name": user["full_name"],
            "company": user.get("company"), "unp": user.get("unp")}


def _require_client_unp(user: dict) -> str:
    unp = (user.get("unp") or "").strip()
    if not unp:
        raise HTTPException(422, "В профиле клиента не указан УНП организации — "
                                 "попросите администратора заполнить его (админка → Пользователи → Клиенты)")
    return unp


@app.get("/api/client/me")
def api_client_me(user: dict = Depends(auth.require_roles("client"))) -> dict:
    return _client_profile(user)


@app.get("/api/client/requests")
def api_client_requests(user: dict = Depends(auth.require_roles("client")),
                        limit: int = 200, offset: int = 0) -> dict:
    return rs.search({"unp": _require_client_unp(user)}, limit=min(limit, 500), offset=offset)


@app.post("/api/client/requests", status_code=201)
def api_client_create(inp: ClientRequestIn, user: dict = Depends(auth.require_roles("client"))) -> dict:
    unp = _require_client_unp(user)
    data = {
        "contractor": user.get("company") or user.get("full_name") or user["username"],
        "unp": unp,
        "bank_account": "",   # кабинет клиента: счёт не обязателен, укажет диспетчер
        "contact_person": inp.contact_person,
        "phone": inp.phone,
        "work_id": inp.work_id,
        "work_code": inp.work_code,
        "priority": inp.priority,
        "address": inp.address,
        "comment": inp.comment,
        "equipment": inp.equipment,
        "serial": inp.serial,
        "account_optional": True,   # р/с у клиента не спрашиваем — дополнит диспетчер
    }
    return rs.create_request(data, actor=user["username"], source="client")


@app.get("/api/client/requests/{rid}")
def api_client_request(rid: int, user: dict = Depends(auth.require_roles("client"))) -> dict:
    r = rs.get_request(rid)
    if (r.get("unp") or "") != _require_client_unp(user):
        raise HTTPException(403, "Это заявка другой организации")
    return r


@app.get("/api/client/deliveries")
def api_client_deliveries(user: dict = Depends(auth.require_roles("client"))) -> list[dict]:
    return db.rows2dicts(db.q(
        """SELECT d.id, d.scheduled_date, d.status, d.postpone_count, d.comment,
                  r.number, r.contractor, r.address
           FROM deliveries d JOIN requests r ON r.id = d.request_id
           WHERE r.unp=? ORDER BY d.scheduled_date DESC, d.id DESC LIMIT 100""",
        (_require_client_unp(user),)))


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


@app.delete("/api/admin/engineers/{eid}")
def api_engineer_delete(eid: int, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    """Удаление инженера. Заявки остаются в истории, но открепляются от инженера
    (открытые становятся нераспределёнными); зоны и отпуска удаляются."""
    eng = db.q1("SELECT * FROM engineers WHERE id=?", (eid,))
    if not eng:
        raise HTTPException(404, "Инженер не найден")
    counts = {
        "requests_unlinked": db.q1("SELECT COUNT(*) AS c FROM requests WHERE engineer_id=?", (eid,))["c"],
        "open_requests_unassigned": db.q1(
            "SELECT COUNT(*) AS c FROM requests WHERE engineer_id=? "
            "AND status IN ('new','assigned','in_progress','pickup_office')", (eid,))["c"],
        "zones_removed": db.q1("SELECT COUNT(*) AS c FROM zone_assignments WHERE engineer_id=?", (eid,))["c"],
        "absences_removed": db.q1("SELECT COUNT(*) AS c FROM absences WHERE engineer_id=?", (eid,))["c"],
    }
    db.execute("UPDATE absences SET replacement_engineer_id=NULL WHERE replacement_engineer_id=?", (eid,))
    db.execute("UPDATE requests SET engineer_id=NULL WHERE engineer_id=?", (eid,))
    db.execute("UPDATE deliveries SET engineer_id=NULL WHERE engineer_id=?", (eid,))
    db.execute("UPDATE users SET engineer_id=NULL WHERE engineer_id=?", (eid,))
    db.execute("DELETE FROM zone_assignments WHERE engineer_id=?", (eid,))
    db.execute("DELETE FROM absences WHERE engineer_id=?", (eid,))
    db.execute("DELETE FROM route_plans WHERE engineer_id=?", (eid,))
    db.execute("DELETE FROM outbox WHERE engineer_id=?", (eid,))
    db.execute("DELETE FROM engineers WHERE id=?", (eid,))
    db.audit(user["username"], "engineer.delete",
             {"id": eid, "name": eng["full_name"], **counts})
    return {"ok": True, "name": eng["full_name"], **counts}


ROLES = ("admin", "operator", "engineer", "client")
ROLE_LABELS = {"admin": "Администратор", "operator": "Диспетчер",
               "engineer": "Инженер", "client": "Клиент"}


def _validate_unp(unp: str) -> str:
    u = validation.normalize_unp(unp or "")
    err = validation.unp_error(u)
    if err:
        raise HTTPException(422, err)
    return u


@app.get("/api/admin/users")
def api_users(user: dict = Depends(auth.require_roles("admin"))) -> list[dict]:
    return db.rows2dicts(db.q(
        "SELECT id,username,role,full_name,engineer_id,company,unp,active,created_at FROM users ORDER BY id"))


@app.post("/api/admin/users")
def api_user_create(inp: UserIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    if db.q1("SELECT 1 FROM users WHERE username=?", (inp.username.lower(),)):
        raise HTTPException(409, "Такой логин уже существует")
    if inp.role not in ROLES:
        raise HTTPException(422, "Роль: admin | operator (диспетчер) | engineer | client")
    unp = None
    if inp.role == "client":
        if not (inp.company or "").strip():
            raise HTTPException(422, "Для клиента укажите наименование организации")
        unp = _validate_unp(inp.unp or "")
        if db.q1("SELECT 1 FROM users WHERE role='client' AND unp=?", (unp,)):
            raise HTTPException(409, f"Клиент с УНП {unp} уже заведён")
    uid = auth.create_user(inp.username.lower(), inp.password, inp.role, inp.full_name or inp.username,
                           inp.engineer_id, (inp.company or "").strip() or None, unp)
    db.audit(user["username"], "user.create", {"username": inp.username, "role": inp.role})
    return {"id": uid, "username": inp.username.lower(), "role": inp.role}


@app.delete("/api/admin/users/{uid}")
def api_user_delete(uid: int, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    target = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if target["id"] == user["id"]:
        raise HTTPException(422, "Нельзя удалить свою учётную запись")
    if target["role"] == "admin" and not db.q1("SELECT 1 FROM users WHERE role='admin' AND id!=? AND active=1", (uid,)):
        raise HTTPException(422, "Это последний администратор — сначала создайте другого")
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit(user["username"], "user.delete", {"username": target["username"]})
    return {"ok": True}


@app.post("/api/admin/users/{uid}/password")
def api_user_password(uid: int, inp: PasswordIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    if len(inp.password or "") < 6:
        raise HTTPException(422, "Пароль — минимум 6 символов")
    if not db.q1("SELECT 1 FROM users WHERE id=?", (uid,)):
        raise HTTPException(404, "Пользователь не найден")
    auth.set_password(uid, inp.password)
    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.audit(user["username"], "user.password", {"user_id": uid})
    return {"ok": True}


@app.post("/api/admin/users/{uid}/active")
def api_user_active(uid: int, inp: ActiveIn, user: dict = Depends(auth.require_roles("admin"))) -> dict:
    target = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    active = 1 if inp.active else 0
    if not active:
        if target["id"] == user["id"]:
            raise HTTPException(422, "Нельзя блокировать свою учётную запись")
        if target["role"] == "admin" and not db.q1(
                "SELECT 1 FROM users WHERE role='admin' AND id!=? AND active=1", (uid,)):
            raise HTTPException(422, "Это последний администратор — нельзя блокировать")
    db.execute("UPDATE users SET active=? WHERE id=?", (active, uid))
    if not active:
        db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    db.audit(user["username"], "user.active", {"user_id": uid, "active": active})
    return {"ok": True, "active": active}


@app.post("/api/admin/geocode/refresh")
def api_geocode_refresh(user: dict = Depends(auth.require_roles("admin", "operator"))) -> dict:
    """Перегеокодировать активные заявки: стираем устаревший кэш адресов и
    заново определяем координаты (онлайн-геокодер → локальный справочник).
    Возвращает по каждой заявке: новый провайдер, точность, координаты."""
    rows = db.rows2dicts(db.q(
        """SELECT id, number, address FROM requests
           WHERE status IN ('new','assigned','in_progress','postponed','pickup_office')
           ORDER BY id"""))
    results, fixed = [], 0
    for r in rows:
        db.execute("DELETE FROM geo_cache WHERE query=?", (r["address"].strip().lower(),))
        g = geocode.resolve(r["address"], use_cache=False)
        if g.get("ok") and g.get("lat"):
            z = zones.resolve_zone(g["lat"], g["lon"], g.get("district", ""),
                                   (g.get("text") or "") + " " + r["address"])
            db.execute("UPDATE requests SET lat=?, lon=?, zone_id=? WHERE id=?",
                       (g["lat"], g["lon"], z["id"] if z else None, r["id"]))
            fixed += 1
        results.append({"number": r["number"], "address": r["address"],
                        "lat": g.get("lat"), "lon": g.get("lon"),
                        "provider": g.get("provider"), "precision": g.get("precision"),
                        "message": g.get("message")})
    db.audit(user["username"], "geocode.refresh", {"total": len(rows), "fixed": fixed})
    return {"ok": True, "total": len(rows), "fixed": fixed, "results": results}


@app.post("/api/admin/purge-demo")
def api_purge_demo(user: dict = Depends(auth.require_roles("admin"))) -> dict:
    """Удаление всех демо-записей: заявки, доставки, контрагенты, инженеры,
    закрепления зон, отпуска, лента событий. Остаётся структура: зоны,
    справочник работ, настройки, пользователи."""
    counts = {}
    for table in ("route_plans", "deliveries", "requests", "request_events", "contractors",
                  "absences", "zone_assignments", "outbox"):
        counts[table] = db.q1(f"SELECT COUNT(*) AS c FROM {table}")["c"]  # noqa: S608 (фиксированный список)
        db.execute(f"DELETE FROM {table}")  # noqa: S608
    # отвязываем пользователей от инженеров (ссылки на удаляемые записи)
    counts["users_unlinked"] = db.q1("SELECT COUNT(*) AS c FROM users WHERE engineer_id IS NOT NULL")["c"]
    db.execute("UPDATE users SET engineer_id=NULL")
    counts["engineers"] = db.q1("SELECT COUNT(*) AS c FROM engineers")["c"]
    db.execute("DELETE FROM engineers")
    db.set_setting("demo_loaded", "1", actor=user["username"])
    db.audit(user["username"], "demo.purge", counts)
    return {"ok": True, "deleted": counts,
            "message": "Демо-данные удалены. Добавьте инженеров и закрепите зоны — система готова к работе."}


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
    "win": (("CRM-Windows.exe", "CRM-Windows-Setup.exe"), "Клиент для Windows (приём заявок)"),
    "apk": (("CRM-Engineer.apk",), "Приложение инженера для Android"),
    "server": (("crm-server.zip",), "Сервер (Python)"),
    "server-exe": (("CRM-Server.exe",), "Сервер одним файлом (exe, Python не нужен)"),
    "src": (("crm-sources.zip",), "Исходники клиента Windows и Android"),
}


def _download_file(candidates: tuple) -> str:
    """Имя первого существующего файла из списка (клиент может называться по-разному)."""
    for name in candidates:
        if os.path.isfile(os.path.join(DOWNLOAD_DIR, name)):
            return name
    return candidates[0]


@app.get("/api/downloads")
def api_downloads() -> list[dict]:
    out = []
    for key, (candidates, title) in DOWNLOAD_FILES.items():
        fname = _download_file(candidates)
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
    <p>Автообновление: клиенты проверяют <a href="/api/updates">/api/updates</a> и берут сборки из
    последнего релиза на GitHub (<a href="{updates.manifest_url()}">update.json</a>).</p>
    <p><a href="/admin">→ Веб-админка диспетчера</a> · <a href="/m">→ Мобильное приложение инженера</a> · <a href="/docs">→ API</a></p>
    </body></html>""")


# =====================================================================
#  Веб-интерфейсы (админка и мобильный клиент инженера)
# =====================================================================

@app.get("/")
def root() -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
    <title>Cartridge Engineer — приём заявок</title>
    <link rel="icon" href="/static/mobile/icon-32.png">
    <style>body{{font:16px system-ui;margin:40px;max-width:820px;line-height:1.5}}
    a.b{{display:inline-block;margin:6px 12px 6px 0;padding:10px 16px;background:#1a56db;color:#fff;
    border-radius:8px;text-decoration:none}}</style></head><body>
    <h1>Cartridge Engineer — сервер работает</h1>
    <p>Время сервера: {db.now()}</p>
    <p><a class="b" href="/admin">Веб-админка диспетчера</a>
       <a class="b" href="/m">Приложение инженера (мобильное)</a>
       <a class="b" href="/downloads">Файлы (exe / apk)</a>
       <a class="b" href="/docs">API (docs)</a></p>
    <p>Windows-клиент подаёт заявки на <code>POST /api/requests</code>, инженер получает их
       в мобильном приложении с маршрутом на день и Яндекс.Навигатором.</p>
    <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0">
    <p style="color:#64748b">Разработчик: Zakharevich Igor ·
       <a href="tel:+375293371412">+375 29 337-14-12</a> ·
       <a href="mailto:ziv@csl.by">ziv@csl.by</a></p>
    </body></html>""")


def _page_or_error(path: str, title: str) -> HTMLResponse | FileResponse:
    """Страница интерфейса; если файл не найден (неполное обновление) —
    понятная страница вместо голого «Internal Server Error»."""
    if not os.path.exists(path):
        return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
        <title>{title} — файлы не найдены</title></head>
        <body style="font:16px system-ui;margin:40px;line-height:1.5">
        <h2>Файл интерфейса не найден</h2>
        <p>Сервер запущен, но в папке программы нет файла:<br><code>{path}</code></p>
        <p>Обычно это значит, что при обновлении скопировались не все файлы.
        Установите сервер заново из CRM-Server-Setup.exe (или распакуйте свежий
        crm-server.zip целиком) — база данных в папке data не пострадает.</p>
        </body></html>""", status_code=503)
    return FileResponse(path)


@app.get("/admin")
def admin_page():
    return _page_or_error(os.path.join(STATIC_DIR, "admin", "index.html"), "Админка")


@app.get("/m")
def mobile_page():
    return _page_or_error(os.path.join(STATIC_DIR, "mobile", "index.html"), "Приложение инженера")


@app.get("/dispatcher")
def dispatcher_page():
    return _page_or_error(os.path.join(STATIC_DIR, "dispatcher", "index.html"), "Диспетчер")


@app.get("/client")
def client_page():
    return _page_or_error(os.path.join(STATIC_DIR, "client", "index.html"), "Кабинет клиента")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception) -> JSONResponse:
    """Любая непредвиденная ошибка: трассировка в data/error.log (последние записи),
    ответ — читаемый текст с краткой причиной (видно и в браузере, и в логах)."""
    import traceback as _tb
    trace = _tb.format_exc()
    try:
        log = os.path.join(db.DATA_DIR, "error.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n=== " + db.now() + " " + str(request.url) + " ===\n" + trace)
        # держим файл компактным: не более ~400 КБ
        if os.path.getsize(log) > 400_000:
            with open(log, "r", encoding="utf-8") as f:
                tail = f.read()[-200_000:]
            with open(log, "w", encoding="utf-8") as f:
                f.write(tail)
    except OSError:
        pass
    return JSONResponse(status_code=500, content={
        "ok": False,
        "error": f"Внутренняя ошибка сервера: {exc.__class__.__name__}: {exc}. "
                 f"Полная трассировка: data/error.log (или /api/diag)"})


@app.get("/api/diag")
def api_diag() -> dict:
    """Диагностика сервера: версии, файлы интерфейсов, последние ошибки (без секретов)."""
    import sys as _sys
    pages = {}
    for name, rel in (("admin", "admin/index.html"), ("mobile", "mobile/index.html"),
                      ("dispatcher", "dispatcher/index.html"), ("client", "client/index.html")):
        p = os.path.join(STATIC_DIR, rel)
        pages[name] = {"ok": os.path.exists(p), "path": p}
    last_error = ""
    try:
        log = os.path.join(db.DATA_DIR, "error.log")
        if os.path.exists(log):
            with open(log, "r", encoding="utf-8") as f:
                last_error = f.read()[-4000:]
    except OSError:
        pass
    geo = {}
    try:
        geo["cache_by_provider"] = {r["provider"]: r["n"] for r in db.rows2dicts(
            db.q("SELECT provider, COUNT(*) AS n FROM geo_cache GROUP BY provider"))}
        geo["errors"] = [{"at": r["at"], "action": r["action"], "detail": (r["detail"] or "")[:200]}
                          for r in db.rows2dicts(db.q(
            """SELECT at, action, detail FROM audit_log
               WHERE action LIKE 'geocode.%error%' ORDER BY id DESC LIMIT 3"""))]
    except Exception:
        pass
    return {"ok": True, "geo": geo, "version": updates.version_info()["version"],
            "python": _sys.version.split()[0], "data_dir": db.DATA_DIR,
            "db": {"path": db.DB_PATH, "exists": os.path.exists(db.DB_PATH),
                    "size": os.path.getsize(db.DB_PATH) if os.path.exists(db.DB_PATH) else 0},
            "pages": pages, "last_error": last_error}


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Ошибки схемы запроса по-русски и строкой — иначе клиенты показывают [object Object]."""
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
        parts.append(f"{loc or 'поле'}: {e.get('msg', 'некорректное значение')}")
    return JSONResponse(status_code=422, content={
        "ok": False, "error": "Некорректный запрос — " + "; ".join(parts), "status": 422})


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail,
                                                              "status": exc.status_code})
