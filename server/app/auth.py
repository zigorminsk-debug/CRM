"""Аутентификация: логин/пароль, токены-сессии, роли."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request

from . import db

TOKEN_TTL_DAYS = 30


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 60_000).hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected)


def create_user(username: str, password: str, role: str, full_name: str,
                engineer_id: int | None = None) -> int:
    salt = secrets.token_hex(16)
    return db.execute(
        """INSERT INTO users(username,password_hash,salt,role,full_name,engineer_id,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (username, hash_password(password, salt), salt, role, full_name, engineer_id, db.now()))


def login(username: str, password: str, device: str = "") -> dict:
    u = db.q1("SELECT * FROM users WHERE username=? AND active=1", ((username or "").strip().lower(),))
    if not u or not verify_password(password or "", u["salt"], u["password_hash"]):
        db.audit(username, "auth.fail", {"device": device})
        raise HTTPException(401, "Неверный логин или пароль")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=TOKEN_TTL_DAYS)).isoformat()
    db.execute("INSERT INTO sessions(token,user_id,created_at,expires_at,device) VALUES(?,?,?,?,?)",
               (token, u["id"], db.now(), expires, device))
    db.audit(u["username"], "auth.ok", {"device": device})
    return {"token": token, "role": u["role"], "full_name": u["full_name"],
            "username": u["username"], "engineer_id": u["engineer_id"], "expires_at": expires}


def current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-Token", "")
    token = token or request.query_params.get("token", "")
    if not token:
        raise HTTPException(401, "Требуется авторизация (заголовок Authorization: Bearer <token>)")
    s = db.q1("SELECT * FROM sessions WHERE token=?", (token,))
    if not s:
        raise HTTPException(401, "Сессия не найдена — войдите заново")
    if s["expires_at"] < datetime.now().isoformat():
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        raise HTTPException(401, "Сессия истекла — войдите заново")
    u = db.row2dict(db.q1("SELECT * FROM users WHERE id=? AND active=1", (s["user_id"],)))
    if not u:
        raise HTTPException(401, "Пользователь заблокирован")
    u.pop("password_hash", None)
    u.pop("salt", None)
    u["token"] = token
    return u


def require_roles(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if roles and user["role"] not in roles:
            raise HTTPException(403, f"Недостаточно прав (нужна роль: {', '.join(roles)})")
        return user
    return dep


def logout(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token=?", (token,))


def ensure_default_admin() -> None:
    """Создаёт администратора, если в базе ещё нет ни одного пользователя с ролью admin."""
    if db.q1("SELECT 1 FROM users WHERE role='admin' LIMIT 1"):
        return
    admin_pass = os.environ.get("CRM_ADMIN_PASSWORD", "admin123")
    create_user("admin", admin_pass, "admin", "Администратор")
    db.audit("system", "bootstrap", {"admin_password_from_env": bool(os.environ.get("CRM_ADMIN_PASSWORD"))})
    print("CRM: создан администратор по умолчанию — логин admin, пароль "
          + ("из переменной CRM_ADMIN_PASSWORD" if os.environ.get("CRM_ADMIN_PASSWORD") else "admin123 (смените!)"))
