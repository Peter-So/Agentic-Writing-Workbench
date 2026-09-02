from __future__ import annotations

import contextvars
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import ROOT


AUTH_DB = Path(os.getenv("WRITING_AUTH_DB") or (ROOT / "data" / "auth.db"))
COOKIE_NAME = "writing_session"
SESSION_HOURS = 12
_lock = threading.RLock()
_initialized = False
_current_user: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("writing_user", default=None)

DEFAULT_MENUS = [
    ("workspace", "创作工作台", "/", None, 10),
    ("admin", "后台管理", "/admin", None, 100),
    ("admin.users", "用户管理", "/admin#users", "admin", 110),
    ("admin.roles", "角色管理", "/admin#roles", "admin", 120),
    ("admin.menus", "菜单管理", "/admin#menus", "admin", 130),
    ("admin.permissions", "角色权限", "/admin#permissions", "admin", 140),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_auth_db() -> None:
    global _initialized
    if _initialized:
        return
    with _lock, _connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
          is_active INTEGER NOT NULL DEFAULT 1, is_superuser INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS roles (
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS menus (
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL, path TEXT NOT NULL DEFAULT '', parent_id INTEGER,
          sort_order INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(parent_id) REFERENCES menus(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS user_roles (
          user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
          PRIMARY KEY(user_id, role_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
          FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS role_menus (
          role_id INTEGER NOT NULL, menu_id INTEGER NOT NULL,
          PRIMARY KEY(role_id, menu_id),
          FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE,
          FOREIGN KEY(menu_id) REFERENCES menus(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS project_owners (
          novel_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
        now = _now()
        db.execute("INSERT OR IGNORE INTO roles(code,name,description,created_at) VALUES('super_admin','超级管理员','全部权限',?)", (now,))
        db.execute("INSERT OR IGNORE INTO roles(code,name,description,created_at) VALUES('admin','管理员','后台管理权限',?)", (now,))
        db.execute("INSERT OR IGNORE INTO roles(code,name,description,created_at) VALUES('user','普通用户','创作工作台访问',?)", (now,))
        for code, name, path, parent_code, order in DEFAULT_MENUS:
            parent_id = None
            if parent_code:
                row = db.execute("SELECT id FROM menus WHERE code=?", (parent_code,)).fetchone()
                parent_id = row["id"] if row else None
            db.execute(
                "INSERT OR IGNORE INTO menus(code,name,path,parent_id,sort_order,is_active) VALUES(?,?,?,?,?,1)",
                (code, name, path, parent_id, order),
            )
        user_role = db.execute("SELECT id FROM roles WHERE code='user'").fetchone()["id"]
        workspace = db.execute("SELECT id FROM menus WHERE code='workspace'").fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO role_menus(role_id,menu_id) VALUES(?,?)", (user_role, workspace))
        admin_role = db.execute("SELECT id FROM roles WHERE code='admin'").fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO role_menus(role_id,menu_id) SELECT ?,id FROM menus WHERE code='admin' OR code LIKE 'admin.%'", (admin_role,))
        db.commit()
    _initialized = True


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少 10 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _kind, rounds, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except Exception:
        return False


def bootstrap_admin(username: str, password: str, display_name: str = "超级管理员") -> dict[str, Any]:
    init_auth_db()
    username = username.strip().lower()
    with _lock, _connect() as db:
        now = _now()
        row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            uid = row["id"]
            db.execute("UPDATE users SET password_hash=?,display_name=?,is_active=1,is_superuser=1,updated_at=? WHERE id=?", (hash_password(password), display_name, now, uid))
        else:
            cur = db.execute("INSERT INTO users(username,password_hash,display_name,is_active,is_superuser,created_at,updated_at) VALUES(?,?,?,1,1,?,?)", (username, hash_password(password), display_name, now, now))
            uid = cur.lastrowid
        rid = db.execute("SELECT id FROM roles WHERE code='super_admin'").fetchone()["id"]
        db.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)", (uid, rid))
        db.execute("INSERT OR IGNORE INTO role_menus(role_id,menu_id) SELECT ?,id FROM menus", (rid,))
        novels = ROOT / "projects" / "writing" / "novels"
        if novels.is_dir():
            for item in novels.iterdir():
                if item.is_dir():
                    db.execute("INSERT OR IGNORE INTO project_owners(novel_id,user_id,created_at) VALUES(?,?,?)", (item.name, uid, now))
        db.commit()
    return {"ok": True, "username": username, "user_id": uid}


def _user_payload(db: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = db.execute("SELECT id,username,display_name,is_active,is_superuser,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["is_active"]:
        return None
    roles = [dict(x) for x in db.execute("SELECT r.id,r.code,r.name FROM roles r JOIN user_roles ur ON ur.role_id=r.id WHERE ur.user_id=? ORDER BY r.id", (user_id,))]
    menus = [dict(x) for x in db.execute("SELECT DISTINCT m.id,m.code,m.name,m.path,m.parent_id,m.sort_order FROM menus m JOIN role_menus rm ON rm.menu_id=m.id JOIN user_roles ur ON ur.role_id=rm.role_id WHERE ur.user_id=? AND m.is_active=1 ORDER BY m.sort_order,m.id", (user_id,))]
    data = dict(row)
    data["is_superuser"] = bool(data["is_superuser"])
    data["roles"] = roles
    data["menus"] = menus
    data["permissions"] = [m["code"] for m in menus]
    return data


def login(username: str, password: str) -> tuple[str, dict[str, Any]] | None:
    init_auth_db()
    with _lock, _connect() as db:
        row = db.execute("SELECT id,password_hash,is_active FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
        db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], expires.isoformat(timespec="seconds"), _now()))
        db.commit()
        return token, _user_payload(db, row["id"])


def session_user(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    init_auth_db()
    with _connect() as db:
        row = db.execute("SELECT user_id,expires_at FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row["expires_at"] < _now():
            return None
        return _user_payload(db, row["user_id"])


def logout(token: str | None) -> None:
    if token:
        with _connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            db.commit()


def current_user() -> dict[str, Any] | None:
    return _current_user.get()


def require_user() -> dict[str, Any]:
    user = current_user()
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def require_permission(code: str) -> dict[str, Any]:
    user = require_user()
    if not user["is_superuser"] and code not in user["permissions"]:
        raise HTTPException(403, "权限不足")
    return user


def has_admin_access(user: dict[str, Any] | None = None) -> bool:
    user = user or current_user()
    if not user:
        return False
    if user.get("is_superuser"):
        return True
    return any(role.get("code") in {"admin", "super_admin"} for role in user.get("roles") or [])


def can_access_project(novel_id: str, user: dict[str, Any] | None = None) -> bool:
    user = user or current_user()
    if not user or user["is_superuser"]:
        return True
    with _connect() as db:
        row = db.execute("SELECT 1 FROM project_owners WHERE novel_id=? AND user_id=?", (novel_id, user["id"])).fetchone()
        return bool(row)


def assert_project_access(novel_id: str, *, allow_missing: bool = False) -> None:
    user = current_user()
    if not user or user["is_superuser"]:
        return
    project_path = ROOT / "projects" / "writing" / "novels" / novel_id
    if allow_missing and not project_path.exists():
        return
    if not can_access_project(novel_id):
        raise HTTPException(403, "无权访问该项目")


def assign_project_owner(novel_id: str, user_id: int | None = None) -> None:
    user = require_user()
    owner = user_id or user["id"]
    with _connect() as db:
        db.execute("INSERT OR REPLACE INTO project_owners(novel_id,user_id,created_at) VALUES(?,?,?)", (novel_id, owner, _now()))
        db.commit()


def list_project_owners() -> list[dict[str, Any]]:
    require_permission("admin.users")
    novels = ROOT / "projects" / "writing" / "novels"
    with _connect() as db:
        owners = {row["novel_id"]: dict(row) for row in db.execute("SELECT p.novel_id,p.user_id,u.username,u.display_name FROM project_owners p JOIN users u ON u.id=p.user_id")}
    return [{"novel_id": item.name, **owners.get(item.name, {"user_id": None, "username": "", "display_name": ""})} for item in sorted(novels.iterdir()) if item.is_dir()]


def set_project_owner(novel_id: str, user_id: int) -> dict[str, Any]:
    require_permission("admin.users")
    if not (ROOT / "projects" / "writing" / "novels" / novel_id).is_dir():
        raise KeyError(novel_id)
    with _connect() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone():
            raise ValueError("目标用户不存在或已停用")
        db.execute("INSERT OR REPLACE INTO project_owners(novel_id,user_id,created_at) VALUES(?,?,?)", (novel_id, user_id, _now()))
        db.commit()
    return {"ok": True, "novel_id": novel_id, "user_id": user_id}


def remove_project_owner(novel_id: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM project_owners WHERE novel_id=?", (novel_id,))
        db.commit()


def allowed_project_ids() -> set[str] | None:
    user = current_user()
    if not user or user["is_superuser"]:
        return None
    with _connect() as db:
        return {row["novel_id"] for row in db.execute("SELECT novel_id FROM project_owners WHERE user_id=?", (user["id"],))}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        public = path in {"/", "/admin", "/api/auth/login", "/api/auth/session"} or path.startswith("/static/")
        user = session_user(request.cookies.get(COOKIE_NAME))
        if path.startswith("/api/") and not public and not user:
            return JSONResponse({"detail": "请先登录", "code": "authentication_required"}, status_code=401)
        query_novel_id = request.query_params.get("novel_id")
        if user and query_novel_id and not can_access_project(query_novel_id, user):
            return JSONResponse({"detail": "无权访问该项目"}, status_code=403)
        if user and (path.startswith("/api/writing/") or path.startswith("/api/chat/")):
            if not user["is_superuser"] and "workspace" not in user["permissions"]:
                return JSONResponse({"detail": "无工作台访问权限"}, status_code=403)
        if user and path.startswith("/api/app-upgrade") and not user["is_superuser"]:
            return JSONResponse({"detail": "仅超管可执行升级"}, status_code=403)
        if user and path.startswith("/api/admin/") and not has_admin_access(user):
            return JSONResponse({"detail": "仅管理员或超级管理员可访问后台"}, status_code=403)
        if user and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            expected = request.headers.get("x-forwarded-host") or request.headers.get("host")
            if origin and expected and origin.split("//", 1)[-1].rstrip("/") != expected:
                return JSONResponse({"detail": "请求来源校验失败"}, status_code=403)
        token = _current_user.set(user)
        try:
            return await call_next(request)
        finally:
            _current_user.reset(token)


def list_users() -> list[dict[str, Any]]:
    require_permission("admin.users")
    with _connect() as db:
        rows = db.execute("SELECT id,username,display_name,is_active,is_superuser,created_at,updated_at FROM users ORDER BY id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_active"] = bool(item["is_active"]); item["is_superuser"] = bool(item["is_superuser"])
            item["roles"] = [x["id"] for x in db.execute("SELECT role_id AS id FROM user_roles WHERE user_id=?", (row["id"],))]
            result.append(item)
        return result


def create_user(data: dict[str, Any]) -> dict[str, Any]:
    require_permission("admin.users")
    username = str(data.get("username") or "").strip().lower()
    if not username or not username.replace("_", "").isalnum(): raise ValueError("用户名只允许字母、数字和下划线")
    with _connect() as db:
        now = _now()
        cur = db.execute("INSERT INTO users(username,password_hash,display_name,is_active,is_superuser,created_at,updated_at) VALUES(?,?,?,1,0,?,?)", (username, hash_password(str(data.get("password") or "")), str(data.get("display_name") or username), now, now))
        role = db.execute("SELECT id FROM roles WHERE code='user'").fetchone()
        if role: db.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (cur.lastrowid, role["id"]))
        db.commit()
        return {"ok": True, "id": cur.lastrowid}


def update_user(user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    actor = require_permission("admin.users")
    with _connect() as db:
        row = db.execute("SELECT is_superuser FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: raise KeyError(user_id)
        if row["is_superuser"] and user_id != actor["id"]: raise ValueError("不能修改其他超管")
        if user_id == actor["id"] and not data.get("is_active", True): raise ValueError("不能停用当前登录账号")
        db.execute("UPDATE users SET display_name=?,is_active=?,updated_at=? WHERE id=?", (str(data.get("display_name") or ""), 1 if data.get("is_active", True) else 0, _now(), user_id))
        if data.get("password"): db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(str(data["password"])), user_id))
        if "role_ids" in data:
            db.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            db.executemany("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", [(user_id, int(x)) for x in data.get("role_ids") or []])
        db.commit()
    return {"ok": True}


def list_roles() -> list[dict[str, Any]]:
    require_permission("admin.roles")
    with _connect() as db:
        return [dict(x) for x in db.execute("SELECT id,code,name,description,created_at FROM roles ORDER BY id")]


def save_role(data: dict[str, Any], role_id: int | None = None) -> dict[str, Any]:
    require_permission("admin.roles")
    with _connect() as db:
        if role_id:
            db.execute("UPDATE roles SET name=?,description=? WHERE id=? AND code NOT IN ('super_admin')", (str(data.get("name") or ""), str(data.get("description") or ""), role_id))
            rid = role_id
        else:
            cur = db.execute("INSERT INTO roles(code,name,description,created_at) VALUES(?,?,?,?)", (str(data.get("code") or "").strip(), str(data.get("name") or "").strip(), str(data.get("description") or ""), _now()))
            rid = cur.lastrowid
        db.commit(); return {"ok": True, "id": rid}


def list_menus() -> list[dict[str, Any]]:
    require_permission("admin.menus")
    with _connect() as db:
        return [dict(x) for x in db.execute("SELECT id,code,name,path,parent_id,sort_order,is_active FROM menus ORDER BY sort_order,id")]


def save_menu(data: dict[str, Any], menu_id: int | None = None) -> dict[str, Any]:
    require_permission("admin.menus")
    values = (str(data.get("code") or "").strip(), str(data.get("name") or "").strip(), str(data.get("path") or ""), data.get("parent_id"), int(data.get("sort_order") or 0), 1 if data.get("is_active", True) else 0)
    with _connect() as db:
        if menu_id:
            db.execute("UPDATE menus SET code=?,name=?,path=?,parent_id=?,sort_order=?,is_active=? WHERE id=?", (*values, menu_id)); mid = menu_id
        else:
            cur = db.execute("INSERT INTO menus(code,name,path,parent_id,sort_order,is_active) VALUES(?,?,?,?,?,?)", values); mid = cur.lastrowid
        db.commit(); return {"ok": True, "id": mid}


def role_permissions(role_id: int, menu_ids: list[int] | None = None) -> dict[str, Any]:
    require_permission("admin.permissions")
    with _connect() as db:
        if menu_ids is not None:
            code = db.execute("SELECT code FROM roles WHERE id=?", (role_id,)).fetchone()
            if code and code["code"] == "super_admin": raise ValueError("超管权限不可缩减")
            db.execute("DELETE FROM role_menus WHERE role_id=?", (role_id,))
            db.executemany("INSERT INTO role_menus(role_id,menu_id) VALUES(?,?)", [(role_id, int(x)) for x in menu_ids])
            db.commit()
        ids = [x["menu_id"] for x in db.execute("SELECT menu_id FROM role_menus WHERE role_id=?", (role_id,))]
        return {"ok": True, "role_id": role_id, "menu_ids": ids}
