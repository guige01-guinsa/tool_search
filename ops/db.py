from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("OPS_DB_PATH", BASE_DIR / "operations.db"))
LEGACY_DB_PATH_RAW = os.getenv("LEGACY_DB_PATH", "").strip()
LEGACY_DB_PATH = Path(LEGACY_DB_PATH_RAW) if LEGACY_DB_PATH_RAW else None


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_sql: str, column_name: str) -> None:
    if not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


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

    _ensure_column(conn, "users", "phone TEXT NOT NULL DEFAULT ''", "phone")
    _ensure_column(conn, "users", "recovery_question TEXT NOT NULL DEFAULT ''", "recovery_question")
    _ensure_column(conn, "users", "recovery_answer_hash TEXT NOT NULL DEFAULT ''", "recovery_answer_hash")

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
            source_type TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
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
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_code TEXT NOT NULL UNIQUE,
            batch_id INTEGER,
            site_name TEXT NOT NULL DEFAULT '',
            building_label TEXT NOT NULL DEFAULT '',
            unit_number TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT '전화',
            category_primary TEXT NOT NULL DEFAULT '',
            category_secondary TEXT NOT NULL DEFAULT '',
            facility_id INTEGER,
            unit_label TEXT NOT NULL DEFAULT '',
            location_detail TEXT NOT NULL DEFAULT '',
            requester_name TEXT NOT NULL DEFAULT '',
            requester_phone TEXT NOT NULL DEFAULT '',
            requester_email TEXT NOT NULL DEFAULT '',
            external_assignee_name TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '보통',
            status TEXT NOT NULL DEFAULT '접수',
            response_due_at TEXT NOT NULL DEFAULT '',
            resolved_at TEXT NOT NULL DEFAULT '',
            closed_at TEXT NOT NULL DEFAULT '',
            assignee_user_id INTEGER,
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
        CREATE TABLE IF NOT EXISTS complaint_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL,
            update_type TEXT NOT NULL,
            status_from TEXT NOT NULL DEFAULT '',
            status_to TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            is_public_note INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complaint_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL UNIQUE,
            rating INTEGER NOT NULL,
            comment TEXT NOT NULL DEFAULT '',
            follow_up_at TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            updated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complaint_response_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category_primary TEXT NOT NULL DEFAULT '',
            update_type TEXT NOT NULL DEFAULT '회신',
            status_to TEXT NOT NULL DEFAULT '',
            is_public_note INTEGER NOT NULL DEFAULT 1,
            body TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 100,
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
        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_code TEXT NOT NULL UNIQUE,
            batch_id INTEGER,
            complaint_id INTEGER,
            external_assignee_name TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_reference TEXT NOT NULL DEFAULT '',
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
            FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE SET NULL,
            FOREIGN KEY(facility_id) REFERENCES facilities(id) ON DELETE SET NULL,
            FOREIGN KEY(assignee_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS office_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_code TEXT NOT NULL UNIQUE,
            record_type TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            facility_id INTEGER,
            target_name TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '보통',
            status TEXT NOT NULL DEFAULT '작성전',
            description TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER,
            due_date TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            updated_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(facility_id) REFERENCES facilities(id) ON DELETE SET NULL,
            FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )

    _ensure_column(conn, "work_orders", "complaint_id INTEGER", "complaint_id")
    _ensure_column(conn, "facilities", "source_type TEXT NOT NULL DEFAULT ''", "source_type")
    _ensure_column(conn, "facilities", "source_reference TEXT NOT NULL DEFAULT ''", "source_reference")
    _ensure_column(conn, "complaints", "batch_id INTEGER", "batch_id")
    _ensure_column(conn, "complaints", "site_name TEXT NOT NULL DEFAULT ''", "site_name")
    _ensure_column(conn, "complaints", "building_label TEXT NOT NULL DEFAULT ''", "building_label")
    _ensure_column(conn, "complaints", "unit_number TEXT NOT NULL DEFAULT ''", "unit_number")
    _ensure_column(conn, "complaints", "external_assignee_name TEXT NOT NULL DEFAULT ''", "external_assignee_name")
    _ensure_column(conn, "complaints", "source_type TEXT NOT NULL DEFAULT ''", "source_type")
    _ensure_column(conn, "complaints", "source_reference TEXT NOT NULL DEFAULT ''", "source_reference")
    _ensure_column(conn, "work_orders", "batch_id INTEGER", "batch_id")
    _ensure_column(conn, "work_orders", "external_assignee_name TEXT NOT NULL DEFAULT ''", "external_assignee_name")
    _ensure_column(conn, "work_orders", "source_type TEXT NOT NULL DEFAULT ''", "source_type")
    _ensure_column(conn, "work_orders", "source_reference TEXT NOT NULL DEFAULT ''", "source_reference")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complaint_import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_code TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            source_fingerprint TEXT NOT NULL UNIQUE,
            site_name TEXT NOT NULL DEFAULT '',
            report_title TEXT NOT NULL DEFAULT '',
            document_type TEXT NOT NULL DEFAULT '',
            recipient_name TEXT NOT NULL DEFAULT '',
            submitter_name TEXT NOT NULL DEFAULT '',
            contractor_name TEXT NOT NULL DEFAULT '',
            project_name TEXT NOT NULL DEFAULT '',
            report_date TEXT NOT NULL DEFAULT '',
            report_generated_at TEXT NOT NULL DEFAULT '',
            latest_received_at TEXT NOT NULL DEFAULT '',
            total_complaints INTEGER NOT NULL DEFAULT 0,
            household_count INTEGER NOT NULL DEFAULT 0,
            open_count INTEGER NOT NULL DEFAULT 0,
            closed_count INTEGER NOT NULL DEFAULT 0,
            repeat_count INTEGER NOT NULL DEFAULT 0,
            status_summary_json TEXT NOT NULL DEFAULT '',
            building_summary_json TEXT NOT NULL DEFAULT '',
            raw_payload TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
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
        CREATE TABLE IF NOT EXISTS office_record_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            office_record_id INTEGER NOT NULL,
            update_type TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY(office_record_id) REFERENCES office_records(id) ON DELETE CASCADE,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_status ON facilities(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory_items(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_location ON inventory_items(location)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_low ON inventory_items(quantity, min_quantity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_tx_item ON inventory_transactions(item_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facilities_source_reference ON facilities(source_reference)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_due ON complaints(response_due_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_assignee ON complaints(assignee_user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_requester_phone ON complaints(requester_phone)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_batch ON complaints(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaints_site_building ON complaints(site_name, building_label, unit_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaint_updates_complaint ON complaint_updates(complaint_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaint_feedback_complaint ON complaint_feedback(complaint_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaint_feedback_rating ON complaint_feedback(rating, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_complaint_templates_active ON complaint_response_templates(is_active, category_primary, sort_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_due_date ON work_orders(due_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_priority ON work_orders(priority)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_complaint ON work_orders(complaint_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_orders_batch ON work_orders(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_updates_work_order ON work_order_updates(work_order_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_records_type ON office_records(record_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_records_status ON office_records(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_records_due_date ON office_records(due_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_records_owner ON office_records(owner_user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_updates_record ON office_record_updates(office_record_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_entity ON attachments(entity_type, entity_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_facilities_source_ref_unique ON facilities(source_reference) WHERE source_reference != ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_complaints_source_ref_unique ON complaints(source_reference) WHERE source_reference != ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_work_orders_source_ref_unique ON work_orders(source_reference) WHERE source_reference != ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_complaint_import_batches_fingerprint ON complaint_import_batches(source_fingerprint)"
    )

    _seed_default_complaint_templates(conn)
    conn.commit()
    conn.close()


def _set_entity_code(conn: sqlite3.Connection, table: str, code_field: str, prefix: str, row_id: int) -> str:
    code = f"{prefix}-{row_id:04d}"
    conn.execute(f"UPDATE {table} SET {code_field} = ? WHERE id = ?", (code, row_id))
    return code


def _seed_default_complaint_templates(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS count FROM complaint_response_templates").fetchone()
    if int(existing["count"] or 0):
        return

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    defaults = [
        ("접수 안내", "", "회신", "분류완료", 1, "민원이 정상 접수되었습니다. 담당자가 확인 후 처리 방향과 일정을 다시 안내드리겠습니다.", 10),
        ("현장 일정 안내", "", "회신", "처리중", 1, "현장 확인 일정을 배정했습니다. 방문 전 다시 연락드리고, 조치가 끝나면 결과를 공유드리겠습니다.", 20),
        ("조치 완료 회신", "", "회신", "회신완료", 1, "요청하신 민원 조치를 완료했습니다. 사용 중 불편이 계속되면 다시 접수해 주세요.", 30),
        ("추가 확인 요청", "", "회신", "", 1, "정확한 조치를 위해 추가 확인이 필요합니다. 방문 가능 시간이나 증상을 조금 더 알려주시면 빠르게 반영하겠습니다.", 40),
        ("전기 안전 안내", "전기", "회신", "처리중", 1, "전기 관련 민원으로 분류되어 안전 점검 후 조치하겠습니다. 위험 징후가 있으면 즉시 사용을 중지해 주세요.", 50),
        ("기계 설비 안내", "기계", "회신", "처리중", 1, "기계 설비 민원으로 분류되었습니다. 부품 상태와 설비 작동을 확인한 뒤 조치 결과를 안내드리겠습니다.", 60),
    ]
    for name, category_primary, update_type, status_to, is_public_note, body, sort_order in defaults:
        cursor = conn.execute(
            """
            INSERT INTO complaint_response_templates(
                template_code, name, category_primary, update_type, status_to, is_public_note,
                body, is_active, sort_order, created_at, updated_at
            )
            VALUES ('', ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                name,
                category_primary,
                update_type,
                status_to,
                is_public_note,
                body,
                sort_order,
                now_text,
                now_text,
            ),
        )
        _set_entity_code(conn, "complaint_response_templates", "template_code", "CT", cursor.lastrowid)


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
