#!/usr/bin/env python3
"""Сквозной тест системы: приём заявки → распределение по зоне → маршрут инженера →
«Поехали» → чекбоксы готовности → забор в офис и доставка на следующий рабочий день →
перенос доставки → отпуск и перераспределение зон → поиск в истории.

Запуск:  python3 server/tests/e2e_test.py [http://127.0.0.1:8000]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TOKEN = {"admin": None, "engineer": None}
FAILS: list[str] = []
CHECKS = [0, 0]


def call(method: str, path: str, body=None, token: str | None = None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8-sig")
            try:
                return r.status, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return r.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def check(cond: bool, name: str, detail: str = "") -> bool:
    CHECKS[0] += 1
    if cond:
        CHECKS[1] += 1
        print(f"[ OK ] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def next_working_day(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main() -> int:
    print(f"=== Сквозной тест CRM, сервер {BASE} ===\n")

    st, health = call("GET", "/api/health")
    check(st == 200 and health.get("ok"), "Сервер доступен", f"зон: {health.get('zone_count')}")

    st, res = call("POST", "/api/auth/login", {"username": "admin", "password": "admin123", "device": "test"})
    check(st == 200 and res.get("role") == "admin", "Вход администратора", str(res.get("error", "")))
    TOKEN["admin"] = res.get("token")

    st, res = call("POST", "/api/auth/login", {"username": "engineer", "password": "engineer123", "device": "test"})
    check(st == 200 and res.get("engineer_id"), "Вход инженера", f"инженер #{res.get('engineer_id')} {res.get('full_name')}")
    TOKEN["engineer"] = res.get("token")
    engineer_id = res.get("engineer_id")

    # прибираем возможные следы предыдущих прогонов теста
    st, abs_list = call("GET", "/api/admin/absences", token=TOKEN["admin"])
    for a in abs_list if isinstance(abs_list, list) else []:
        if str(a.get("comment", "")).startswith("Тест: отпуск"):
            call("DELETE", f"/api/admin/absences/{a['id']}", token=TOKEN["admin"])
            print(f"       (удалена тестовая запись об отпуске #{a['id']} от прошлого прогона)")

    st, works = call("GET", "/api/works")
    check(st == 200 and isinstance(works, list) and len(works) > 10, "Справочник работ (выпадающий список)",
          f"{len(works)} работ")
    work = next((w for w in works if w["code"] == "ZAP-KARTR"), works[0])

    # ---------------------------------------------------- адрес → координаты
    st, geo = call("GET", "/api/geo/resolve?" + urllib.parse.urlencode({"address": "г. Минск, ул. Кальварийская, 17"}))
    check(st == 200 and geo.get("ok"), "Адрес Минска → координаты", f"{geo.get('lat')},{geo.get('lon')} · {geo.get('zone_name')}")
    check(bool(geo.get("engineer_name")), "Для зоны определён ответственный инженер", geo.get("engineer_name", ""))
    zone_id = geo.get("zone_id")

    st, geo2 = call("GET", "/api/geo/resolve?" + urllib.parse.urlencode({"address": "Минский район, аг. Колодищи, ул. Минская, 5"}))
    check(st == 200 and geo2.get("ok") and "Минский" in (geo2.get("zone_name") or ""),
          "Адрес Минского района → координаты и зона", geo2.get("zone_name", ""))

    # ------------------------------------------------------- создание заявки
    payload = {
        "contractor": "ООО «Тест-Сервис»",
        "unp": "100345679",
        "bank_account": "BY15BLBB30120000000000000004",
        "contact_person": "Проверкин Пётр Петрович",
        "phone": "8 029 777-88-99",
        "work_id": work["id"],
        "priority": "urgent",
        "address": "г. Минск, ул. Кальварийская, 17",
        "comment": "Сквозной тест: заправить картридж, проверить протяжку.",
        "equipment": "HP LaserJet Pro M428",
        "serial": "E2E-0001",
    }
    st, req = call("POST", "/api/requests", payload)
    check(st == 201, "Заявка принята сервером", f"{req.get('number')} · {req.get('status_label')} · {req.get('engineer_name')}")
    check(req.get("phone") == "+375297778899", "Телефон приведён к международному формату", req.get("phone", ""))
    check(req.get("zone_id") is not None, "Определена зона (район) заявки", req.get("zone_name", ""))
    check(bool(req.get("engineer_id")), "Заявка назначена инженеру зоны", str(req.get("engineer_name")))
    request_id = req.get("id")
    assigned_engineer = req.get("engineer_id")

    st, bad = call("POST", "/api/requests", {**payload, "unp": "111111111"})
    check(st == 422 and "УНП" in json.dumps(bad, ensure_ascii=False), "Неверный УНП отклонён сервером")

    st, bad2 = call("POST", "/api/requests", {**payload, "bank_account": "BY00BLBB30120000000000000099"})
    check(st == 422, "Неверный р/с отклонён сервером", str(bad2.get("error", ""))[:60])

    # ------------------------------------------------------------ маршрут дня
    st, route = call("GET", f"/api/engineer/route?engineer_id={assigned_engineer}", token=TOKEN["admin"])
    check(st == 200 and route.get("ok"), "Маршрут на день получен", f"{len(route.get('route', []))} заявок, {route.get('total_km')} км")
    items = route.get("route", [])
    check(any(i["id"] == request_id for i in items), "Новая заявка попала в маршрут")
    check(all("nav_intent" in i for i in items), "У заявок есть ссылка на Яндекс.Навигатор")
    if items:
        check(items[0].get("eta", "").count(":") == 1, "Рассчитано время прибытия", items[0].get("eta", ""))
        a = [i for i in items if i["priority"] in ("emergency", "urgent")]
        b = [i for i in items if i["priority"] not in ("emergency", "urgent")]
        if a and b:
            check(items.index(a[-1]) < items.index(b[0]) or items.index(b[0]) > items.index(a[0]),
                  "Срочные заявки стоят выше обычных в маршруте")

    # ------------------------------------------------ «Поехали» и готовность
    st, go = call("POST", f"/api/engineer/task/{request_id}/go", {}, token=TOKEN["admin"])
    check(st == 200 and go.get("status") == "in_progress", "Кнопка «Поехали» переводит заявку в работу",
          go.get("status_label", ""))
    check("yandexnavi://" in (go.get("nav_intent") or ""), "Сформирован deep-link Яндекс.Навигатора",
          (go.get("nav_intent") or "")[:60])

    st, done = call("POST", f"/api/engineer/task/{request_id}/done",
                    {"result": "onsite", "comment": "Картридж заправлен, тест-страница чистая"}, token=TOKEN["admin"])
    check(st == 200 and done.get("status") == "done_onsite", "Чекбокс «готово на месте»",
          done.get("status_label", "") + " · " + str(done.get("visit_result")))

    # -------------------------- забор в офис → доставка на следующий рабочий день
    st, req2 = call("POST", "/api/requests", {**payload, "comment": "Тест забора: забрать картридж в офис"})
    rid2 = req2.get("id")
    call("POST", f"/api/engineer/task/{rid2}/go", {}, token=TOKEN["admin"])
    st, pick = call("POST", f"/api/engineer/task/{rid2}/done",
                    {"result": "pickup_office", "comment": "Картридж забрал, везу в офис"}, token=TOKEN["admin"])
    check(st == 200 and pick.get("status") == "pickup_office", "Чекбокс «забор в офис»", pick.get("status_label", ""))
    deliveries = pick.get("deliveries", [])
    check(bool(deliveries), "Создана доставка заказчику")
    if deliveries:
        d0 = deliveries[-1]
        expected = next_working_day(date.today()).isoformat()
        check(d0["scheduled_date"] == expected, "Доставка назначена на следующий рабочий день",
              f"{d0['scheduled_date']} (ожидалось {expected})")

        st, post = call("POST", f"/api/engineer/delivery/{d0['id']}/postpone",
                        {"days": 1, "comment": "Оборудование не готово"}, token=TOKEN["admin"])
        check(st == 200, "Перенос доставки при неготовности", post.get("scheduled_date", ""))
        expected2 = next_working_day(date.fromisoformat(expected)).isoformat()
        check(post.get("scheduled_date") == expected2, "Доставка перенесена ещё на один рабочий день",
              post.get("scheduled_date", ""))

    # ------------------------------------------- история: поиск по УНП и р/с
    st, hist = call("GET", "/api/history/contractor?" + urllib.parse.urlencode({"value": "100345679", "kind": "unp"}))
    check(st == 200 and hist.get("items"), "История по УНП", f"найдено заявок: {hist.get('summary', {}).get('total')}")
    st, hist2 = call("GET", "/api/history/contractor?" + urllib.parse.urlencode(
        {"value": "BY15BLBB30120000000000000004", "kind": "account"}))
    check(st == 200 and hist2.get("items"), "История по расчётному счёту",
          f"найдено заявок: {hist2.get('summary', {}).get('total')}")
    st, hist3 = call("GET", "/api/requests?" + urllib.parse.urlencode({"contractor": "Тест-Сервис"}))
    check(st == 200 and hist3.get("total", 0) >= 2, "Поиск по контрагенту", f"заявок: {hist3.get('total')}")

    # ------------------------------- отпуск инженера и перераспределение зон
    st, engs = call("GET", "/api/admin/engineers", token=TOKEN["admin"])
    if not isinstance(engs, list):
        check(False, "Список инженеров доступен администратору", str(engs)[:120])
        engs = []
    replacement = next((e for e in engs if isinstance(e, dict) and e.get("id") != assigned_engineer), None)
    check(bool(replacement), "Есть инженер для подмены", replacement["full_name"] if replacement else "")

    st, cov_before = call("GET", "/api/admin/coverage", token=TOKEN["admin"])
    absent_ids = {int(k) for k in (cov_before.get("absent") or {}).keys()}
    if replacement and replacement["id"] in absent_ids:
        replacement = next((e for e in engs if isinstance(e, dict) and e.get("id") not in absent_ids
                            and e.get("id") != assigned_engineer), replacement)
    zone_before = next((c for c in cov_before["coverage"] if c["id"] == zone_id), None)

    start = date.today().isoformat()
    end = (date.today() + timedelta(days=14)).isoformat()
    st, abs_res = call("POST", "/api/admin/absences", {
        "engineer_id": assigned_engineer, "date_from": start, "date_to": end, "kind": "vacation",
        "replacement_engineer_id": replacement["id"], "comment": "Тест: отпуск и перераспределение"
    }, token=TOKEN["admin"])
    check(st == 200, "Оформлен отпуск с заменой", f"перераспределено заявок: {abs_res.get('requests_reassigned')}")

    st, cov_after = call("GET", "/api/admin/coverage", token=TOKEN["admin"])
    zone_after = next((c for c in cov_after["coverage"] if c["id"] == zone_id), None)
    took = (zone_after or {}).get("engineer") or {}
    check(took.get("id") == replacement["id"], "Зона на время отпуска передана замене",
          f"{zone_before['engineer']['full_name'] if zone_before and zone_before['engineer'] else '—'} → {took.get('full_name', '—')}")

    # отмена отпуска: зона должна вернуться прежнему инженеру
    st, del_res = call("DELETE", f"/api/admin/absences/{abs_res.get('absence_id')}", token=TOKEN["admin"])
    st2, cov_back = call("GET", "/api/admin/coverage", token=TOKEN["admin"])
    zone_back = next((c for c in cov_back["coverage"] if c["id"] == zone_id), None)
    back_eng = (zone_back or {}).get("engineer") or {}
    check(st == 200 and back_eng.get("id") == assigned_engineer, "Отмена отпуска возвращает зону прежнему инженеру",
          f"{took.get('full_name', '—')} → {back_eng.get('full_name', '—')}, зон возвращено: {len(del_res.get('zones_returned') or [])}")

    st, feed = call("GET", "/api/engineer/feed?since=0&wait=0", token=TOKEN["engineer"])
    check(st == 200 and feed.get("events"), "Лента событий инженера (push)", f"событий: {len(feed.get('events', []))}")

    st, rep = call("GET", "/api/admin/reports/summary", token=TOKEN["admin"])
    check(st == 200 and rep.get("by_status"), "Отчёты по статусам/инженерам",
          f"статусов: {len(rep.get('by_status', []))}, инженеров: {len(rep.get('by_engineer', []))}")

    st, exp = call("GET", "/api/requests/export.csv?" + urllib.parse.urlencode({"contractor": "Тест-Сервис"}))
    check(st == 200, "Экспорт истории в CSV")

    # страницы
    for path, name in (("/", "главная"), ("/admin", "админка"), ("/m", "мобильное приложение"), ("/downloads", "загрузки")):
        st, _ = call("GET", path)
        check(st == 200, f"Страница {name} ({path}) открывается")

    # прибираем тестовую запись об отпуске, чтобы демо-данные остались как были
    st, absences = call("GET", "/api/admin/absences", token=TOKEN["admin"])
    for a in absences if isinstance(absences, list) else []:
        if a.get("comment", "").startswith("Тест: отпуск"):
            call("DELETE", f"/api/admin/absences/{a['id']}", token=TOKEN["admin"])

    print(f"\n=== Итог: пройдено {CHECKS[1]} из {CHECKS[0]} проверок ===")
    if FAILS:
        print("Не пройдены: " + "; ".join(FAILS))
        return 1
    print("Все проверки пройдены успешно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
