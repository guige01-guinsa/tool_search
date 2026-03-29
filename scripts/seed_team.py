from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ops import auth
from ops.db import get_conn, init_db

TEMP_PASSWORD = "ChangeMe!2026"

TEAM_USERS = [
    ("admin", "시스템 관리자", "admin"),
    ("ops_lead", "운영 총괄", "manager"),
    ("facility_mgr", "시설 관리자", "manager"),
    ("electric_1", "전기 담당 1", "technician"),
    ("electric_2", "전기 담당 2", "technician"),
    ("mechanical_1", "기계 담당 1", "technician"),
    ("mechanical_2", "기계 담당 2", "technician"),
    ("fire_safety", "소방 담당", "technician"),
    ("viewer_audit", "보고서 조회", "viewer"),
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    init_db()
    conn = get_conn()

    created = []
    updated = []
    for username, full_name, role in TEAM_USERS:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE users
                SET full_name = ?, role = ?, is_active = 1, updated_at = ?
                WHERE id = ?
                """,
                (full_name, role, now_text(), existing["id"]),
            )
            updated.append(username)
        else:
            conn.execute(
                """
                INSERT INTO users(username, full_name, role, password_hash, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    username,
                    full_name,
                    role,
                    auth.hash_password(TEMP_PASSWORD),
                    now_text(),
                    now_text(),
                ),
            )
            created.append(username)

    conn.commit()
    conn.close()

    print("Temporary password:", TEMP_PASSWORD)
    print("Created:", ", ".join(created) if created else "-")
    print("Updated:", ", ".join(updated) if updated else "-")
    print("Team users:")
    for username, full_name, role in TEAM_USERS:
        print(f" - {username:12s} | {full_name:12s} | {role}")


if __name__ == "__main__":
    main()
