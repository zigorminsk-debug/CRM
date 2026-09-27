"""Слой доступа к данным (SQLite).

Один файл базы, схема создаётся автоматически при первом запуске.
Используется чистый sqlite3 из стандартной библиотеки — никаких внешних ORM.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import _runtime

if os.environ.get("CRM_DB"):
    DB_PATH = os.environ["CRM_DB"]
elif os.environ.get("CRM_DATA_DIR"):
    # вся база в указанной папке (переносимой): CRM_DATA_DIR=/D/crm-data
    DB_PATH = os.path.join(os.environ["CRM_DATA_DIR"], "crm.sqlite3")
elif _runtime.is_frozen():
    # exe: база живёт в папке data рядом с CRM-Server.exe, чтобы переживать
    # перезапуски и обновления: новый exe подхватывает ту же папку data
    DB_PATH = os.path.join(_runtime.writable_dir("data"), "crm.sqlite3")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crm.sqlite3")

DATA_DIR = os.path.dirname(DB_PATH)
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

_README = """Эта папка — ВСЯ база данных Cartridge Engineer.
crm.sqlite3          — база (заявки, пользователи, контрагенты, зоны, настройки)
backups/             — резервные копии (кнопки «Выгрузить/Загрузить базу» в админке)

Как перенести базу на новый/обновлённый сервер:
  1. Остановите сервер (закройте окно CRM-Server.exe).
  2. Скопируйте эту папку data в папку с новым CRM-Server.exe (или укажите
     путь к ней переменной окружения CRM_DATA_DIR).
  3. Запустите сервер — он подхватит базу со всеми данными.
Резервную копию можно также скачать кнопкой «Выгрузить базу в бэкап»
(Админка → Настройки → Обслуживание базы) и восстановить ею же.
"""
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    _rm = os.path.join(DATA_DIR, "README.txt")
    if not os.path.exists(_rm):
        with open(_rm, "w", encoding="utf-8") as _f:
            _f.write(_README)
except OSError:
    pass

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engineers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    phone     TEXT,
    base_lat  REAL,
    base_lon  REAL,
    active    INTEGER NOT NULL DEFAULT 1,
    notes     TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','operator','engineer','client')),
    full_name     TEXT,
    engineer_id   INTEGER REFERENCES engineers(id),
    company       TEXT,
    unp           TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    device     TEXT
);

-- Зоны обслуживания: районы Минска и районы Минской области
CREATE TABLE IF NOT EXISTS zones (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    code   TEXT NOT NULL UNIQUE,
    name   TEXT NOT NULL,
    kind   TEXT NOT NULL CHECK (kind IN ('city','region')),
    lat    REAL NOT NULL,
    lon    REAL NOT NULL
);

-- Закрепление зоны за инженером на период (date_to IS NULL = бессрочно)
CREATE TABLE IF NOT EXISTS zone_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_id     INTEGER NOT NULL REFERENCES zones(id),
    engineer_id INTEGER NOT NULL REFERENCES engineers(id),
    date_from   TEXT NOT NULL,
    date_to     TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 1,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_za_zone ON zone_assignments(zone_id, date_from, date_to);

-- Отпуска / больничные / выходные инженеров
CREATE TABLE IF NOT EXISTS absences (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer_id            INTEGER NOT NULL REFERENCES engineers(id),
    date_from              TEXT NOT NULL,
    date_to                TEXT NOT NULL,
    kind                   TEXT NOT NULL CHECK (kind IN ('vacation','sick','dayoff','other')),
    replacement_engineer_id INTEGER REFERENCES engineers(id),
    comment                TEXT,
    created_at             TEXT NOT NULL,
    created_by             TEXT
);

CREATE TABLE IF NOT EXISTS contractors (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    unp            TEXT,
    bank_account   TEXT,
    bank_name      TEXT,
    address        TEXT,
    contact_person TEXT,
    phone          TEXT,
    email          TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contractors_unp ON contractors(unp);
CREATE INDEX IF NOT EXISTS idx_contractors_acc ON contractors(bank_account);

CREATE TABLE IF NOT EXISTS works (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    site_kind       TEXT NOT NULL CHECK (site_kind IN ('onsite','office','remote')),
    default_minutes INTEGER NOT NULL DEFAULT 60,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    number         TEXT NOT NULL UNIQUE,
    created_at     TEXT NOT NULL,
    created_by     TEXT,
    contractor_id  INTEGER REFERENCES contractors(id),
    contractor     TEXT NOT NULL,
    unp            TEXT NOT NULL,
    bank_account   TEXT NOT NULL,
    contact_person TEXT NOT NULL,
    phone          TEXT NOT NULL,
    work_id        INTEGER NOT NULL REFERENCES works(id),
    priority       TEXT NOT NULL CHECK (priority IN ('emergency','urgent','normal','planned')),
    address        TEXT NOT NULL,
    lat            REAL,
    lon            REAL,
    zone_id        INTEGER REFERENCES zones(id),
    comment        TEXT,
    equipment      TEXT,
    serial         TEXT,
    status         TEXT NOT NULL DEFAULT 'new',
    engineer_id    INTEGER REFERENCES engineers(id),
    assigned_by    TEXT,
    assigned_at    TEXT,
    planned_date   TEXT,
    time_from      TEXT,
    time_to        TEXT,
    done_at        TEXT,
    visit_result   TEXT,
    minutes_planned INTEGER,
    source         TEXT DEFAULT 'win'
);
CREATE INDEX IF NOT EXISTS idx_req_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_req_engineer ON requests(engineer_id, planned_date);
CREATE INDEX IF NOT EXISTS idx_req_unp ON requests(unp);
CREATE INDEX IF NOT EXISTS idx_req_account ON requests(bank_account);
CREATE INDEX IF NOT EXISTS idx_req_contractor ON requests(contractor);

CREATE TABLE IF NOT EXISTS request_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    actor      TEXT,
    from_status TEXT,
    to_status  TEXT,
    comment    TEXT,
    meta       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_req ON request_events(request_id);

-- Доставка оборудования/картриджей заказчику (забор в офис -> выдача на след. рабочий день)
CREATE TABLE IF NOT EXISTS deliveries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id     INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    engineer_id    INTEGER REFERENCES engineers(id),
    scheduled_date TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'scheduled',
    postpone_count INTEGER NOT NULL DEFAULT 0,
    comment        TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliv_date ON deliveries(scheduled_date, status);

-- Журнал изменений зон (перераспределение на время отпуска и т.п.)
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT,
    action  TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS geo_cache (
    query      TEXT PRIMARY KEY,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    zone_id    INTEGER REFERENCES zones(id),
    provider   TEXT,
    raw        TEXT,
    created_at TEXT NOT NULL
);

-- Очередь push-событий для мобильных клиентов инженеров
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    engineer_id INTEGER,
    kind        TEXT NOT NULL,
    request_id  INTEGER,
    payload     TEXT,
    sent        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_eng ON outbox(engineer_id, id);

-- Локальный индекс адресов (работает без интернета): улицы Минска, нас. пункты области
CREATE TABLE IF NOT EXISTS street_index (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    key        TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    district   TEXT,
    kind       TEXT NOT NULL DEFAULT 'street',
    source     TEXT DEFAULT 'bundled',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_street_key ON street_index(key);

-- Рабочие дни (праздники РБ можно выключить)
CREATE TABLE IF NOT EXISTS holidays (
    day  TEXT PRIMARY KEY,
    name TEXT
);

CREATE TABLE IF NOT EXISTS route_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer_id INTEGER NOT NULL REFERENCES engineers(id),
    day         TEXT NOT NULL,
    built_at    TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate_requests(_conn)
            _migrate(_conn)
            _conn.commit()
        return _conn


def _migrate_requests(conn: sqlite3.Connection) -> None:
    """Окно визита «с … по …» — колонки для баз, созданных раньше этой фичи."""
    info = conn.execute("PRAGMA table_info(requests)").fetchall()
    if not info:
        return
    cols = {r[1] for r in info}
    for col in ("time_from", "time_to"):
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")

def _migrate(conn: sqlite3.Connection) -> None:
    """Обновление структуры старых баз (созданных до добавления роли client).

    SQLite не умеет менять CHECK у существующей таблицы — пересоздаём users:
    + роль 'client' (кабинет заказчика), + колонки company/unp для привязки
    клиента к организации. Данные пользователей сохраняются.
    """
    info = conn.execute("PRAGMA table_info(users)").fetchall()
    if not info:
        return
    cols = {r[1] for r in info}
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
    need_rebuild = "'client'" not in ddl
    need_company = "company" not in cols
    need_unp = "unp" not in cols
    if not (need_rebuild or need_company or need_unp):
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    if need_rebuild:
        conn.execute("""CREATE TABLE users_migrated (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('admin','operator','engineer','client')),
            full_name     TEXT,
            engineer_id   INTEGER REFERENCES engineers(id),
            company       TEXT,
            unp           TEXT,
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL)""")
        conn.execute("""INSERT INTO users_migrated
            (id,username,password_hash,salt,role,full_name,engineer_id,company,unp,active,created_at)
            SELECT id,username,password_hash,salt,role,full_name,engineer_id,NULL,NULL,active,created_at
            FROM users""")
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_migrated RENAME TO users")
    else:
        if need_company:
            conn.execute("ALTER TABLE users ADD COLUMN company TEXT")
        if need_unp:
            conn.execute("ALTER TABLE users ADD COLUMN unp TEXT")
    conn.execute("PRAGMA foreign_keys=ON")
    print("CRM: база обновлена — добавлена роль «клиент» и привязка пользователя к организации")


@contextmanager
def tx():
    """Транзакция: commit / rollback + блокировка записи."""
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def reopen() -> None:
    """Закрыть соединение (после подмены файла базы откроется заново)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            _conn.close()
            _conn = None


def backup_to(path: str) -> None:
    """Консистентная копия базы через sqlite backup API (можно при живом сервере)."""
    with _lock:
        dst = sqlite3.connect(path)
        try:
            get_conn().backup(dst)
            dst.commit()
        finally:
            dst.close()


def restore_from(path: str) -> None:
    """Подменить файл базы проверенной копией и переоткрыть соединение."""
    global _conn
    with _lock:
        reopen()
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + suffix)
            except OSError:
                pass
        os.replace(path, DB_PATH)
        _conn = None
        get_conn()          # схема + миграции применятся к восстановленной базе


def q(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, args).fetchall()


def q1(sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, args).fetchone()


def execute(sql: str, args: tuple | list = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, args)
        return cur.lastrowid


def row2dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows2dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def setting(key: str, default: str | None = None) -> str | None:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key: str, value: str, actor: str = "system") -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    audit(actor, "setting.change", {"key": key, "value": value})


def audit(actor: str | None, action: str, payload: dict | None = None) -> None:
    import json
    execute(
        "INSERT INTO audit_log(at,actor,action,payload) VALUES(?,?,?,?)",
        (now(), actor, action, json.dumps(payload or {}, ensure_ascii=False)),
    )


def push_event(engineer_id: int | None, kind: str, request_id: int | None = None,
               payload: dict | None = None) -> int:
    """Событие для push-канала мобильного приложения инженера."""
    import json
    return execute("INSERT INTO outbox(at,engineer_id,kind,request_id,payload) VALUES(?,?,?,?,?)",
                   (now(), engineer_id, kind, request_id, json.dumps(payload or {}, ensure_ascii=False)))
