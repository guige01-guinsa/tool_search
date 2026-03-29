from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from ops.db import get_conn

SESSION_COOKIE = "ops_session"

ROLE_LABELS = {
    "admin": "관리자",
    "manager": "운영관리",
    "technician": "작업자",
    "viewer": "조회전용",
}

ROLE_PERMISSIONS = {
    "admin": {
        "dashboard:view",
        "facilities:view",
        "facilities:edit",
        "inventory:view",
        "inventory:edit",
        "inventory:transact",
        "work_orders:view",
        "work_orders:edit",
        "work_orders:update",
        "reports:view",
        "users:manage",
    },
    "manager": {
        "dashboard:view",
        "facilities:view",
        "facilities:edit",
        "inventory:view",
        "inventory:edit",
        "inventory:transact",
        "work_orders:view",
        "work_orders:edit",
        "work_orders:update",
        "reports:view",
    },
    "technician": {
        "dashboard:view",
        "facilities:view",
        "inventory:view",
        "inventory:transact",
        "work_orders:view",
        "work_orders:edit",
        "work_orders:update",
        "reports:view",
    },
    "viewer": {
        "dashboard:view",
        "facilities:view",
        "inventory:view",
        "work_orders:view",
        "reports:view",
    },
}

FALLBACK_ADMIN_USERNAME = "admin"
FALLBACK_ADMIN_PASSWORD = "admin1234"
FALLBACK_ADMIN_NAME = "시스템 관리자"

_ENV_ADMIN_USERNAME = os.getenv("OPS_ADMIN_USERNAME", "").strip()
_ENV_ADMIN_PASSWORD = os.getenv("OPS_ADMIN_PASSWORD", "")
_ENV_ADMIN_NAME = os.getenv("OPS_ADMIN_NAME", "").strip()

DEFAULT_ADMIN_USERNAME = _ENV_ADMIN_USERNAME or FALLBACK_ADMIN_USERNAME
DEFAULT_ADMIN_PASSWORD = _ENV_ADMIN_PASSWORD or FALLBACK_ADMIN_PASSWORD
DEFAULT_ADMIN_NAME = _ENV_ADMIN_NAME or FALLBACK_ADMIN_NAME


def should_show_bootstrap_password() -> bool:
    return not bool(_ENV_ADMIN_PASSWORD)


def role_options() -> list[tuple[str, str]]:
    return [(key, label) for key, label in ROLE_LABELS.items()]


def valid_role(role: str) -> bool:
    return role in ROLE_LABELS


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    rounds = 390000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, rounds_s, salt_hex, digest_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            rounds,
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def ensure_admin_user() -> None:
    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,)).fetchone()
    if not existing:
        conn.execute(
            """
            INSERT INTO users(username, full_name, role, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, 'admin', ?, 1, ?, ?)
            """,
            (
                DEFAULT_ADMIN_USERNAME,
                DEFAULT_ADMIN_NAME,
                hash_password(DEFAULT_ADMIN_PASSWORD),
                _now_text(),
                _now_text(),
            ),
        )
        conn.commit()
    conn.close()


def create_session(user_id: int, days: int = 7) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO sessions(user_id, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, token_hash, expires_at, _now_text()),
    )
    conn.commit()
    conn.close()
    return raw_token


def invalidate_session(raw_token: str | None) -> None:
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()


def get_user_by_session(raw_token: str | None):
    if not raw_token:
        return None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now', 'localtime')")
    user = conn.execute(
        """
        SELECT u.*
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ?
          AND s.expires_at > datetime('now', 'localtime')
          AND u.is_active = 1
        """,
        (token_hash,),
    ).fetchone()
    conn.commit()
    conn.close()
    return user
