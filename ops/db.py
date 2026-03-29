from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("OPS_DB_PATH", BASE_DIR / "operations.db"))
LEGACY_DB_PATH_RAW = os.getenv("LEGACY_DB_PATH", "").strip()
LEGACY_DB_PATH = Path(LEGACY_DB_PATH_RAW) if LEGACY_DB_PATH_RAW else None


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def init_db() -> None:
    conn = get_conn()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facility_code TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            building TEXT NOT NULL DEFAULT '',
            floor TEXT NOT NULL DEFAULT '',
            zone TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '운영중',
            manager_user_id INTEGER,
            note TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            updated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(manager_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            specification TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '개',
            location TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '정상',
            min_quantity INTEGER NOT NULL DEFAULT 0,
            purchase_date TEXT NOT NULL DEFAULT '',
            purchase_amount INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            legacy_tool_id INTEGER UNIQUE,
            created_by INTEGER,
            updated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            tx_type TEXT NOT NULL,
            quantity_delta INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER,
            work_order_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(item_id) REFERENCES inventory_items(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_code TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            facility_id INTEGER,
            requester_name TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '보통',
            status TEXT NOT NULL DEFAULT '접수',
            description TEXT NOT NULL DEFAULT '',
            assignee_user_id INTEGER,
            due_date TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            updated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(facility_id) REFERENCES facilities(id) ON DELETE SET NULL,
            FOREIGN KEY(assignee_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_order_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_order_id INTEGER NOT NULL,
            update_type TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(work_order_id) REFERENCES work_orders(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            original_name TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_status ON facilities(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory_items(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_location ON inventory_items(location)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_low ON inventory_items(quantity, min_quantity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_tx_item ON inventory_transactions(item_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_due_date ON work_orders(due_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_priority ON work_orders(priority)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_updates_work_order ON work_order_updates(work_order_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id)")

    conn.commit()
    conn.close()


def _set_entity_code(conn: sqlite3.Connection, table: str, code_field: str, prefix: str, row_id: int) -> str:
    code = f"{prefix}-{row_id:04d}"
    conn.execute(f"UPDATE {table} SET {code_field} = ? WHERE id = ?", (code, row_id))
    return code


def _note_from_legacy_row(row: sqlite3.Row) -> str:
    parts = []
    purpose = (row["purpose"] or "").strip()
    if purpose:
        parts.append(f"용도: {purpose}")
    cat_parts = [value.strip() for value in [row["cat_l"], row["cat_m"], row["cat_s"]] if (value or "").strip()]
    if cat_parts:
        parts.append(f"레거시 분류: {' / '.join(cat_parts)}")
    return " | ".join(parts)


def migrate_legacy_tools(default_user_id: int | None = None) -> int:
    if not LEGACY_DB_PATH or not LEGACY_DB_PATH.exists():
        return 0

    conn = get_conn()
    current_count = conn.execute("SELECT COUNT(*) AS count FROM inventory_items").fetchone()["count"]
    if current_count:
        conn.close()
        return 0

    legacy = sqlite3.connect(str(LEGACY_DB_PATH))
    legacy.row_factory = sqlite3.Row

    if not _table_exists(legacy, "tools"):
        legacy.close()
        conn.close()
        return 0

    location_map: dict[str, int] = {}
    imported = 0

    legacy_tools = legacy.execute("SELECT * FROM tools ORDER BY id ASC").fetchall()
    for row in legacy_tools:
        location = (row["location"] or "").strip()
        if location and location not in location_map:
            cur = conn.execute(
                """
                INSERT INTO facilities(
                    facility_code, category, name, building, floor, zone, status, note,
                    created_by, updated_by, created_at, updated_at
                )
                VALUES ('', '레거시 위치', ?, '', '', '', '운영중', ?, ?, ?, ?, ?)
                """,
                (
                    location,
                    "tool_search 위치 정보에서 자동 이관",
                    default_user_id,
                    default_user_id,
                    row["created_at"],
                    row["created_at"],
                ),
            )
            facility_id = cur.lastrowid
            _set_entity_code(conn, "facilities", "facility_code", "FAC", facility_id)
            location_map[location] = facility_id

        cur = conn.execute(
            """
            INSERT INTO inventory_items(
                item_code, category, name, specification, quantity, unit, location, status,
                min_quantity, purchase_date, purchase_amount, note, legacy_tool_id,
                created_by, updated_by, created_at, updated_at
            )
            VALUES ('', ?, ?, ?, ?, '개', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                " / ".join(
                    [value.strip() for value in [row["cat_l"], row["cat_m"], row["cat_s"]] if (value or "").strip()]
                ),
                row["name"],
                row["purpose"],
                int(row["qty"] or 0),
                location,
                row["status"] or "정상",
                (row["created_at"] or "")[:10],
                int(row["purchase_amount"] or 0),
                _note_from_legacy_row(row),
                row["id"],
                default_user_id,
                default_user_id,
                row["created_at"],
                row["created_at"],
            ),
        )
        item_id = cur.lastrowid
        _set_entity_code(conn, "inventory_items", "item_code", "INV", item_id)
        imported += 1

        if _table_exists(legacy, "tool_images"):
            images = legacy.execute(
                "SELECT image_path, created_at FROM tool_images WHERE tool_id = ? ORDER BY id ASC",
                (row["id"],),
            ).fetchall()
            for image in images:
                file_name = Path(image["image_path"]).name
                if not file_name:
                    continue
                conn.execute(
                    """
                    INSERT INTO attachments(entity_type, entity_id, file_path, original_name, created_by, created_at)
                    VALUES ('inventory', ?, ?, ?, ?, ?)
                    """,
                    (item_id, file_name, file_name, default_user_id, image["created_at"]),
                )

        if _table_exists(legacy, "tool_events"):
            events = legacy.execute(
                "SELECT event_type, person, note, created_at FROM tool_events WHERE tool_id = ? ORDER BY id ASC",
                (row["id"],),
            ).fetchall()
            for event in events:
                reason_parts = [(event["person"] or "").strip(), (event["note"] or "").strip()]
                reason = " | ".join([part for part in reason_parts if part])
                conn.execute(
                    """
                    INSERT INTO inventory_transactions(item_id, tx_type, quantity_delta, reason, actor_user_id, created_at)
                    VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (item_id, event["event_type"], reason, default_user_id, event["created_at"]),
                )

    conn.commit()
    legacy.close()
    conn.close()
    return imported
