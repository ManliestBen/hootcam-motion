"""
Single-user authentication: username/password stored in SQLite (hashed).
Default user: admin / admin. One user only; password can be changed via API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import bcrypt

from . import database

AUTH_CONFIG_KEY = "auth_user"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except Exception:
        return False


def get_auth_user(db_path: Path) -> Optional[tuple[str, str]]:
    """Return (username, password_hash) or None if not set."""
    raw = database.config_get_json(db_path, AUTH_CONFIG_KEY)
    if not isinstance(raw, dict):
        return None
    username = raw.get("username")
    password_hash = raw.get("password_hash")
    if username and password_hash:
        return (username, password_hash)
    return None


def set_auth_user(db_path: Path, username: str, password_hash: str) -> None:
    """Store single user (replaces any existing)."""
    database.init_db(db_path)
    database.config_set_json(db_path, AUTH_CONFIG_KEY, {"username": username, "password_hash": password_hash})


def ensure_default_user(db_path: Path) -> None:
    """If no user exists, create default admin/admin."""
    if get_auth_user(db_path) is not None:
        return
    set_auth_user(db_path, DEFAULT_USERNAME, hash_password(DEFAULT_PASSWORD))


def check_credentials(db_path: Path, username: str, password: str) -> bool:
    """Return True if username and password match the stored user."""
    user = get_auth_user(db_path)
    if user is None:
        return False
    stored_username, stored_hash = user
    return stored_username == username and verify_password(password, stored_hash)


def update_password(db_path: Path, current_password: str, new_password: str) -> bool:
    """
    Update the user's password. Returns True if successful.
    Caller must have verified current_password matches the stored user.
    """
    user = get_auth_user(db_path)
    if user is None:
        return False
    stored_username, stored_hash = user
    if not verify_password(current_password, stored_hash):
        return False
    set_auth_user(db_path, stored_username, hash_password(new_password))
    return True
