"""Начальное заполнение: справочник работ, инженеры, зоны, демо-данные."""
from __future__ import annotations

import os

from . import auth, db, geocode, zones

# code, name, category, site_kind, default_minutes
WORKS = [
    ("ZAP-KARTR", "Заправка картриджа (тонер)", "Картриджи", "office", 40),
    ("ZAP-KARTR-BLK", "Заправка картриджа чёрного", "Картриджи", "office", 35),
    ("ZAP-KARTR-CLR", "Заправка цветного картриджа", "Картриджи", "office", 55),
    ("VOSST-KARTR", "Восстановление картриджа (замена узлов)", "Картриджи", "office", 90),
    ("ZAM-KARTR", "Замена картриджа на месте", "Картриджи", "onsite", 20),
    ("CHIP", "Замена чипа / сброс счётчика", "Картриджи", "office", 45),
    ("REM-PRINT", "Ремонт принтера на месте", "Оргтехника", "onsite", 60),
    ("REM-MFP", "Ремонт МФУ на месте", "Оргтехника", "onsite", 90),
    ("REM-PLOTTER", "Ремонт плоттера / инженерной системы", "Оргтехника", "onsite", 120),
    ("DIAG", "Диагностика оборудования", "Оргтехника", "onsite", 30),
    ("SETUP-MFP", "Установка и подключение МФУ/принтера", "Оргтехника", "onsite", 60),
    ("SETUP-NET", "Настройка сетевой печати / сканирования", "Оргтехника", "onsite", 45),
    ("TO-PRINT", "Техническое обслуживание парка печати", "Обслуживание", "onsite", 120),
    ("TO-CLEAN", "Чистка и профилактика принтера", "Обслуживание", "onsite", 60),
    ("REM-PC", "Ремонт компьютера / моноблока", "Компьютеры", "onsite", 90),
    ("REM-LAPTOP", "Ремонт ноутбука", "Компьютеры", "onsite", 90),
    ("SETUP-OS", "Установка ОС и ПО", "Компьютеры", "onsite", 120),
    ("SETUP-1C", "Настройка 1С / бухгалтерского ПО", "Компьютеры", "remote", 60),
    ("SETUP-SERVER", "Настройка сервера / сети", "Компьютеры", "remote", 120),
    ("REM-UPS", "Ремонт / замена ИБП", "Оборудование", "office", 60),
    ("REM-SHCANNER", "Ремонт сканера / шреддера", "Оргтехника", "onsite", 60),
    ("SHIP-EQUIP", "Забор оборудования в офис (ремонт)", "Доставка", "office", 30),
    ("SHIP-KARTR", "Забор картриджей в офис (заправка)", "Доставка", "office", 20),
    ("DELIVER", "Доставка заказчику", "Доставка", "onsite", 30),
    ("CONSULT", "Консультация / выезд по рекламации", "Прочее", "onsite", 30),
]


def ensure_works() -> None:
    conn = db.get_conn()
    have = {r["code"] for r in db.q("SELECT code FROM works")}
    for code, name, category, site_kind, minutes in WORKS:
        if code not in have:
            conn.execute("INSERT INTO works(code,name,category,site_kind,default_minutes) VALUES(?,?,?,?,?)",
                         (code, name, category, site_kind, minutes))
    conn.commit()


DEFAULT_SETTINGS = {
    "office_lat": "53.9045",
    "office_lon": "27.5615",
    "office_address": "г. Минск, ул. Кальварийская, 17 (офис/склад)",
    "office_phone": "+375 17 200-00-00",
    "workday_start": "09:00",
    "workday_end": "18:00",
    "company_name": "ООО «Сервис-Картридж»",
    "yandex_geocoder_key": os.environ.get("YANDEX_GEOCODER_KEY", ""),
    "delivery_lead_days": "1",
    "route_radius_warn_km": "25",
}


def seed(create_demo: bool | None = None) -> None:
    zones.ensure_zones()
    ensure_works()
    geocode.load_street_index()   # локальный справочник адресов (работает без интернета)
    nh = geocode.load_house_index()   # ДОМА с координатами (точный адрес -> точка)
    if nh:
        db.audit("system", "house_index.bundled", {"rows": nh})
    nh = geocode.load_house_index()   # ДОМА с координатами (точный адрес -> точка)
    if nh:
        db.audit("system", "house_index.bundled", {"rows": nh})
    for k, v in DEFAULT_SETTINGS.items():
        if db.setting(k) is None:
            db.set_setting(k, v, actor="bootstrap")

    if not db.q1("SELECT 1 FROM engineers LIMIT 1"):
        demo = create_demo if create_demo is not None else os.environ.get("CRM_DEMO", "1") != "0"
        # демо-инженеры создаются один раз: после «Очистить базу от демо» при
        # перезапуске появится только нейтральный «Инженер 1», без демо-имён
        demo = demo and db.setting("demo_loaded") != "1"
        eng_ids = []
        if demo:
            data = [
                ("Иванов Сергей Петрович", "+375 29 111-22-33", 1),
                ("Ковалёв Дмитрий Александрович", "+375 29 222-33-44", 2),
                ("Петрова Ольга Николаевна", "+375 33 333-44-55", 3),
                ("Сидоренко Артём Владимирович", "+375 44 444-55-66", 4),
            ]
            for name, phone, _ in data:
                eng_ids.append(db.execute(
                    "INSERT INTO engineers(full_name,phone,base_lat,base_lon,active,notes) VALUES(?,?,?,?,1,?)",
                    (name, phone, 53.9045, 27.5615, "База: офис")))
            db.set_setting("demo_loaded", "1", actor="bootstrap")
        else:
            eng_ids.append(db.execute(
                "INSERT INTO engineers(full_name,phone,base_lat,base_lon,active,notes) VALUES(?,?,?,?,1,?)",
                ("Инженер 1", "+375 29 000-00-00", 53.9045, 27.5615, "База: офис")))
        eng_name = db.q1("SELECT full_name FROM engineers WHERE id=?", (eng_ids[0],))["full_name"]
        if not db.q1("SELECT 1 FROM users WHERE username='engineer'"):
            auth.create_user("engineer", "engineer123", "engineer", eng_name + " (демо-доступ)", eng_ids[0])

        city = db.rows2dicts(db.q("SELECT id FROM zones WHERE kind='city' ORDER BY id"))
        region = db.rows2dicts(db.q("SELECT id FROM zones WHERE kind='region' ORDER BY id"))
        if len(eng_ids) >= 4:
            # 8 районов города: по 2 на первых четырёх инженеров
            for i, z in enumerate(city):
                zones.assign_zone(z["id"], eng_ids[i % len(eng_ids)], "2020-01-01", None,
                                  actor="bootstrap", reason="Первичное закрепление районов Минска")
            for i, z in enumerate(region):
                zones.assign_zone(z["id"], eng_ids[i % len(eng_ids)], "2020-01-01", None,
                                  actor="bootstrap", reason="Первичное закрепление районов области")
            # демонстрация отпуска: инженер 2 в отпуске, зоны уходят инженеру 4
            db.execute("""INSERT INTO absences(engineer_id,date_from,date_to,kind,replacement_engineer_id,comment,created_at,created_by)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (eng_ids[1], "2026-09-01", "2026-10-15", "vacation", eng_ids[3],
                        "Ежегодный отпуск (демо-запись — удалить в админке)", db.now(), "bootstrap"))
        else:
            for z in city + region:
                zones.assign_zone(z["id"], eng_ids[0], "2020-01-01", None, actor="bootstrap",
                                  reason="Первичное закрепление зон")

    db.audit("system", "seed.done", {})


def demo_requests() -> None:
    """Несколько демонстрационных заявок (только если включён CRM_DEMO)."""
    if os.environ.get("CRM_DEMO", "1") == "0":
        return
    if db.setting("demo_loaded") == "1":   # после «Очистить базу от демо» заявки не возвращаются
        return
    if db.q1("SELECT 1 FROM requests LIMIT 1"):
        return
    import json
    from . import requests_service  # noqa: WPS433 (локальный импорт во избежание цикла)

    demo = [
        dict(contractor='ООО "Ромашка"', unp="190123455", bank_account="BY96BLBB30120000000000000001",
             contact_person="Главный бухгалтер Т.И. Смирнова", phone="+375291112233",
             work_code="ZAP-KARTR", priority="normal", address="г. Минск, ул. Притыцкого, 62",
             comment="Заправить 3 картриджа HP CF218A, пропуски на листе.", equipment="HP LaserJet Pro MFP M132nw", serial="VNC1234567"),
        dict(contractor='ЗАО "СтройТрест-М"', unp="191234563", bank_account="BY69BLBB30120000000000000002",
             contact_person="Начальник АХО Иванов А.А.", phone="80172030405",
             work_code="REM-MFP", priority="urgent", address="г. Минск, пр-т Дзержинского, 119",
             comment="МФУ не захватывает бумагу из лотка 2.", equipment="Kyocera TASKalfa 2553ci", serial="KJ7X12345"),
        dict(contractor='УП "БелТоргСервис"', unp="100234568", bank_account="BY42BLBB30120000000000000003",
             contact_person="Инженер-программист Кузнецов Д.", phone="+375447778899",
             work_code="SETUP-NET", priority="planned", address="Минский район, аг. Колодищи, ул. Минская, 5",
             comment="Настроить печать по сети и сканирование в папку.", equipment="Canon iR-ADV C3725", serial="CNC000777"),
    ]
    for d in demo:
        try:
            requests_service.create_request(d, actor="demo")
        except Exception as exc:  # noqa: BLE001
            db.audit("system", "demo.request_error", {"address": d["address"], "error": str(exc)})
