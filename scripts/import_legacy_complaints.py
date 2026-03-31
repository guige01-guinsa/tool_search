from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_RENDER_SERVICE_ID = "srv-d6g57jbuibrs739g5mvg"
DEFAULT_TARGET_DB = ROOT_DIR / "operations.db"


def _load_psycopg():
    try:
        import psycopg  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - user environment dependent
        raise SystemExit(
            "psycopg가 필요합니다. `pip install psycopg[binary]` 후 다시 실행해 주세요."
        ) from exc
    return psycopg


def _render_api_get(path: str, api_key: str) -> Any:
    request = urllib.request.Request(
        f"https://api.render.com{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "facility-ops-legacy-import/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _normalize_postgres_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def _ensure_external_render_url(raw_url: str, api_key: str) -> str:
    url = _normalize_postgres_url(raw_url)
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if not host:
        raise SystemExit("레거시 DATABASE_URL에서 호스트를 확인하지 못했습니다.")
    if "." in host:
        return _ensure_sslmode(url)

    connection_info = _render_api_get(f"/v1/postgres/{host}/connection-info", api_key)
    external_connection_string = str((connection_info or {}).get("externalConnectionString") or "").strip()
    if external_connection_string:
        return _ensure_sslmode(_normalize_postgres_url(external_connection_string))

    postgres_meta = _render_api_get(f"/v1/postgres/{host}", api_key)
    region = (postgres_meta or {}).get("region", "").strip()
    if not region:
        raise SystemExit("Render Postgres 메타데이터에서 region을 확인하지 못했습니다.")
    external_host = f"{host}.{region}-postgres.render.com"

    auth = ""
    if parsed.username is not None:
        auth = urllib.parse.quote(parsed.username, safe="")
        if parsed.password is not None:
            auth += f":{urllib.parse.quote(parsed.password, safe='')}"
        auth += "@"

    netloc = f"{auth}{external_host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    external_url = urllib.parse.urlunsplit(
        (parsed.scheme or "postgresql", netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return _ensure_sslmode(external_url)


def _ensure_sslmode(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    lower_keys = {key.lower() for key, _ in pairs}
    if "sslmode" not in lower_keys:
        pairs.append(("sslmode", "require"))
    query = urllib.parse.urlencode(pairs)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def resolve_legacy_database_url(explicit_url: str, render_service_id: str) -> str:
    if explicit_url.strip():
        api_key = os.getenv("RENDER_API_KEY", "").strip()
        return _ensure_external_render_url(explicit_url, api_key) if api_key else _normalize_postgres_url(explicit_url)

    api_key = os.getenv("RENDER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("RENDER_API_KEY가 없어서 Render 서비스에서 DATABASE_URL을 읽을 수 없습니다.")
    env_rows = _render_api_get(f"/v1/services/{render_service_id}/env-vars", api_key)
    raw_url = ""
    for row in env_rows:
        env_row = row.get("envVar", row)
        if str(env_row.get("key", "")).strip() == "DATABASE_URL":
            raw_url = str(env_row.get("value", "")).strip()
            break
    if not raw_url:
        raise SystemExit("Render 서비스 환경변수에서 DATABASE_URL을 찾지 못했습니다.")
    return _ensure_external_render_url(raw_url, api_key)


def _format_dt(value: Any, *, date_only: bool = False) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        if date_only and len(text) >= 10:
            return text[:10]
        if not date_only and len(text) == 10:
            return f"{text} 00:00:00"
        return text
    return parsed.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")


def _map_priority(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "critical": "긴급",
        "urgent": "긴급",
        "high": "높음",
        "medium": "보통",
        "normal": "보통",
        "low": "낮음",
    }.get(key, "보통")


def _map_complaint_status(status: Any, assignee: Any) -> str:
    key = str(status or "").strip().lower()
    if key == "completed":
        return "종결"
    if key == "canceled":
        return "취소"
    if key == "acked":
        return "처리중"
    if key == "open":
        return "배정완료" if str(assignee or "").strip() else "접수"
    return "접수"


def _map_work_status(status: Any) -> str:
    key = str(status or "").strip().lower()
    return {
        "open": "접수",
        "acked": "진행중",
        "completed": "완료",
        "canceled": "종결",
    }.get(key, "접수")


def _map_event_type(value: Any) -> str:
    key = str(value or "").strip().lower()
    if "comment" in key or "note" in key:
        return "내부메모"
    if "complete" in key or "resolve" in key:
        return "완료보고"
    if "cancel" in key:
        return "상태변경"
    if "reopen" in key:
        return "재오픈"
    if "ack" in key or "assign" in key:
        return "배정"
    if "create" in key or key == "open":
        return "접수"
    return "상태변경"


def _build_description(row: dict[str, Any]) -> str:
    chunks: list[str] = []
    description = str(row.get("description") or "").strip()
    resolution = str(row.get("resolution_notes") or "").strip()
    if description:
        chunks.append(description)
    if resolution:
        chunks.append(f"[레거시 해결 메모]\n{resolution}")
    if row.get("is_escalated"):
        chunks.append("[레거시 표시] 에스컬레이션 대상")
    return "\n\n".join(chunks).strip()


def _legacy_complaint_code(legacy_id: int) -> str:
    return f"LGCY-CM-{legacy_id:06d}"


def _legacy_work_code(legacy_id: int) -> str:
    return f"LGCY-WO-{legacy_id:06d}"


def _load_target_module(target_db: Path):
    os.environ["OPS_DB_PATH"] = str(target_db)
    from ops import db as ops_db  # pylint: disable=import-outside-toplevel

    ops_db.DB_PATH = target_db
    ops_db.init_db()
    return ops_db


def inspect_legacy_database(legacy_db_url: str) -> dict[str, Any]:
    psycopg = _load_psycopg()
    with psycopg.connect(legacy_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            db_name, db_user = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM work_orders")
            work_order_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM work_order_events")
            event_count = cur.fetchone()[0]
            cur.execute("SELECT status, COUNT(*) FROM work_orders GROUP BY status ORDER BY status")
            status_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT priority, COUNT(*) FROM work_orders GROUP BY priority ORDER BY priority")
            priority_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT event_type, COUNT(*) FROM work_order_events GROUP BY event_type ORDER BY event_type")
            event_type_counts = {row[0]: row[1] for row in cur.fetchall()}
    return {
        "database": db_name,
        "user": db_user,
        "work_orders": work_order_count,
        "work_order_events": event_count,
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "event_type_counts": event_type_counts,
    }


def _fetch_legacy_rows(legacy_db_url: str, limit: int | None) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    psycopg = _load_psycopg()
    with psycopg.connect(legacy_db_url) as conn:
        from psycopg.rows import dict_row  # type: ignore  # pylint: disable=import-outside-toplevel

        with conn.cursor(row_factory=dict_row) as cur:
            base_query = """
                SELECT id, title, description, site, location, priority, status, assignee, reporter,
                       inspection_id, due_at, acknowledged_at, completed_at, resolution_notes,
                       is_escalated, created_at, updated_at
                  FROM work_orders
                 ORDER BY id
            """
            if limit and limit > 0:
                cur.execute(f"{base_query} LIMIT %s", (limit,))
            else:
                cur.execute(base_query)
            work_orders = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT id, work_order_id, event_type, actor_username, from_status, to_status, note, detail_json, created_at
                  FROM work_order_events
                 ORDER BY work_order_id, created_at, id
                """
            )
            grouped_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in cur.fetchall():
                grouped_events[int(row["work_order_id"])].append(dict(row))
    return work_orders, grouped_events


def _user_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    lookup: dict[str, int] = {}
    rows = conn.execute("SELECT id, username, full_name FROM users").fetchall()
    for row in rows:
        if row["username"]:
            lookup[str(row["username"]).strip().lower()] = int(row["id"])
        if row["full_name"]:
            lookup[str(row["full_name"]).strip().lower()] = int(row["id"])
    return lookup


def _lookup_user_id(user_map: dict[str, int], value: Any) -> int | None:
    key = str(value or "").strip().lower()
    return user_map.get(key) if key else None


def _get_existing_id(conn: sqlite3.Connection, table: str, code_col: str, code_value: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {table} WHERE {code_col} = ?", (code_value,)).fetchone()
    return int(row["id"]) if row else None


def _update_exists(conn: sqlite3.Connection, complaint_id: int, payload: dict[str, Any]) -> bool:
    row = conn.execute(
        """
        SELECT id
          FROM complaint_updates
         WHERE complaint_id = ?
           AND update_type = ?
           AND status_from = ?
           AND status_to = ?
           AND message = ?
           AND created_at = ?
        """,
        (
            complaint_id,
            payload["update_type"],
            payload["status_from"],
            payload["status_to"],
            payload["message"],
            payload["created_at"],
        ),
    ).fetchone()
    return bool(row)


def import_legacy_data(
    legacy_db_url: str,
    target_db: Path,
    *,
    dry_run: bool,
    update_existing: bool,
    import_work_orders: bool,
    limit: int | None,
) -> dict[str, Any]:
    ops_db = _load_target_module(target_db)
    work_orders, grouped_events = _fetch_legacy_rows(legacy_db_url, limit)

    conn = ops_db.get_conn()
    user_map = _user_lookup(conn)
    counts = Counter()

    try:
        for legacy_row in work_orders:
            legacy_id = int(legacy_row["id"])
            complaint_code = _legacy_complaint_code(legacy_id)
            complaint_status = _map_complaint_status(legacy_row.get("status"), legacy_row.get("assignee"))
            complaint_payload = {
                "complaint_code": complaint_code,
                "channel": "레거시이관",
                "category_primary": "레거시 민원",
                "category_secondary": "기존 work_orders",
                "facility_id": None,
                "unit_label": str(legacy_row.get("site") or "").strip(),
                "location_detail": str(legacy_row.get("location") or "").strip(),
                "requester_name": str(legacy_row.get("reporter") or "").strip(),
                "requester_phone": "",
                "requester_email": "",
                "title": str(legacy_row.get("title") or f"레거시 민원 {legacy_id}").strip(),
                "description": _build_description(legacy_row),
                "priority": _map_priority(legacy_row.get("priority")),
                "status": complaint_status,
                "response_due_at": _format_dt(legacy_row.get("due_at"), date_only=True),
                "resolved_at": _format_dt(legacy_row.get("completed_at")),
                "closed_at": _format_dt(legacy_row.get("completed_at")) if complaint_status in {"종결", "취소"} else "",
                "assignee_user_id": _lookup_user_id(user_map, legacy_row.get("assignee")),
                "created_by": _lookup_user_id(user_map, legacy_row.get("reporter")),
                "updated_by": _lookup_user_id(user_map, legacy_row.get("assignee")) or _lookup_user_id(user_map, legacy_row.get("reporter")),
                "created_at": _format_dt(legacy_row.get("created_at")),
                "updated_at": _format_dt(legacy_row.get("updated_at")) or _format_dt(legacy_row.get("created_at")),
            }

            complaint_id = _get_existing_id(conn, "complaints", "complaint_code", complaint_code)
            is_new_complaint = complaint_id is None
            if complaint_id is None:
                counts["complaints_inserted"] += 1
                if not dry_run:
                    cursor = conn.execute(
                        """
                        INSERT INTO complaints(
                            complaint_code, channel, category_primary, category_secondary, facility_id,
                            unit_label, location_detail, requester_name, requester_phone, requester_email,
                            title, description, priority, status, response_due_at, resolved_at, closed_at,
                            assignee_user_id, created_by, updated_by, created_at, updated_at
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            complaint_payload["complaint_code"],
                            complaint_payload["channel"],
                            complaint_payload["category_primary"],
                            complaint_payload["category_secondary"],
                            complaint_payload["facility_id"],
                            complaint_payload["unit_label"],
                            complaint_payload["location_detail"],
                            complaint_payload["requester_name"],
                            complaint_payload["requester_phone"],
                            complaint_payload["requester_email"],
                            complaint_payload["title"],
                            complaint_payload["description"],
                            complaint_payload["priority"],
                            complaint_payload["status"],
                            complaint_payload["response_due_at"],
                            complaint_payload["resolved_at"],
                            complaint_payload["closed_at"],
                            complaint_payload["assignee_user_id"],
                            complaint_payload["created_by"],
                            complaint_payload["updated_by"],
                            complaint_payload["created_at"],
                            complaint_payload["updated_at"],
                        ),
                    )
                    complaint_id = int(cursor.lastrowid)
                else:
                    complaint_id = -legacy_id
            else:
                counts["complaints_existing"] += 1
                if update_existing:
                    counts["complaints_updated"] += 1
                    if not dry_run:
                        conn.execute(
                            """
                            UPDATE complaints
                               SET channel = ?, category_primary = ?, category_secondary = ?, facility_id = ?,
                                   unit_label = ?, location_detail = ?, requester_name = ?, requester_phone = ?,
                                   requester_email = ?, title = ?, description = ?, priority = ?, status = ?,
                                   response_due_at = ?, resolved_at = ?, closed_at = ?, assignee_user_id = ?,
                                   created_by = ?, updated_by = ?, created_at = ?, updated_at = ?
                             WHERE id = ?
                            """,
                            (
                                complaint_payload["channel"],
                                complaint_payload["category_primary"],
                                complaint_payload["category_secondary"],
                                complaint_payload["facility_id"],
                                complaint_payload["unit_label"],
                                complaint_payload["location_detail"],
                                complaint_payload["requester_name"],
                                complaint_payload["requester_phone"],
                                complaint_payload["requester_email"],
                                complaint_payload["title"],
                                complaint_payload["description"],
                                complaint_payload["priority"],
                                complaint_payload["status"],
                                complaint_payload["response_due_at"],
                                complaint_payload["resolved_at"],
                                complaint_payload["closed_at"],
                                complaint_payload["assignee_user_id"],
                                complaint_payload["created_by"],
                                complaint_payload["updated_by"],
                                complaint_payload["created_at"],
                                complaint_payload["updated_at"],
                                complaint_id,
                            ),
                        )

            if complaint_id is None:
                continue

            base_update = {
                "complaint_id": complaint_id,
                "update_type": "접수",
                "status_from": "",
                "status_to": complaint_status,
                "message": "레거시 시스템에서 이관되었습니다.",
                "is_public_note": 0,
                "created_by": complaint_payload["created_by"],
                "created_at": complaint_payload["created_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            if is_new_complaint or not _update_exists(conn, complaint_id, base_update):
                counts["updates_inserted"] += 1
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO complaint_updates(
                            complaint_id, update_type, status_from, status_to, message, is_public_note, created_by, created_at
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            base_update["complaint_id"],
                            base_update["update_type"],
                            base_update["status_from"],
                            base_update["status_to"],
                            base_update["message"],
                            base_update["is_public_note"],
                            base_update["created_by"],
                            base_update["created_at"],
                        ),
                    )

            for event_row in grouped_events.get(legacy_id, []):
                update_payload = {
                    "complaint_id": complaint_id,
                    "update_type": _map_event_type(event_row.get("event_type")),
                    "status_from": _map_complaint_status(event_row.get("from_status"), legacy_row.get("assignee")) if event_row.get("from_status") else "",
                    "status_to": _map_complaint_status(event_row.get("to_status"), legacy_row.get("assignee")) if event_row.get("to_status") else "",
                    "message": str(event_row.get("note") or f"레거시 이벤트: {event_row.get('event_type') or 'unknown'}").strip(),
                    "is_public_note": 0,
                    "created_by": _lookup_user_id(user_map, event_row.get("actor_username")),
                    "created_at": _format_dt(event_row.get("created_at")) or complaint_payload["created_at"],
                }
                if (not is_new_complaint) and _update_exists(conn, complaint_id, update_payload):
                    counts["updates_existing"] += 1
                    continue
                counts["updates_inserted"] += 1
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO complaint_updates(
                            complaint_id, update_type, status_from, status_to, message, is_public_note, created_by, created_at
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            update_payload["complaint_id"],
                            update_payload["update_type"],
                            update_payload["status_from"],
                            update_payload["status_to"],
                            update_payload["message"],
                            update_payload["is_public_note"],
                            update_payload["created_by"],
                            update_payload["created_at"],
                        ),
                    )

            if import_work_orders:
                work_code = _legacy_work_code(legacy_id)
                work_payload = {
                    "work_code": work_code,
                    "complaint_id": complaint_id,
                    "category": "민원",
                    "title": complaint_payload["title"],
                    "facility_id": None,
                    "requester_name": complaint_payload["requester_name"],
                    "priority": complaint_payload["priority"],
                    "status": _map_work_status(legacy_row.get("status")),
                    "description": complaint_payload["description"],
                    "assignee_user_id": complaint_payload["assignee_user_id"],
                    "due_date": _format_dt(legacy_row.get("due_at"), date_only=True),
                    "completed_at": _format_dt(legacy_row.get("completed_at")),
                    "created_by": complaint_payload["created_by"],
                    "updated_by": complaint_payload["updated_by"],
                    "created_at": complaint_payload["created_at"],
                    "updated_at": complaint_payload["updated_at"],
                }
                work_id = _get_existing_id(conn, "work_orders", "work_code", work_code)
                if work_id is None:
                    counts["work_orders_inserted"] += 1
                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO work_orders(
                                work_code, complaint_id, category, title, facility_id, requester_name,
                                priority, status, description, assignee_user_id, due_date, completed_at,
                                created_by, updated_by, created_at, updated_at
                            )
                            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                work_payload["work_code"],
                                work_payload["complaint_id"],
                                work_payload["category"],
                                work_payload["title"],
                                work_payload["facility_id"],
                                work_payload["requester_name"],
                                work_payload["priority"],
                                work_payload["status"],
                                work_payload["description"],
                                work_payload["assignee_user_id"],
                                work_payload["due_date"],
                                work_payload["completed_at"],
                                work_payload["created_by"],
                                work_payload["updated_by"],
                                work_payload["created_at"],
                                work_payload["updated_at"],
                            ),
                        )
                else:
                    counts["work_orders_existing"] += 1
                    if update_existing:
                        counts["work_orders_updated"] += 1
                        if not dry_run:
                            conn.execute(
                                """
                                UPDATE work_orders
                                   SET complaint_id = ?, category = ?, title = ?, facility_id = ?, requester_name = ?,
                                       priority = ?, status = ?, description = ?, assignee_user_id = ?, due_date = ?,
                                       completed_at = ?, created_by = ?, updated_by = ?, created_at = ?, updated_at = ?
                                 WHERE id = ?
                                """,
                                (
                                    work_payload["complaint_id"],
                                    work_payload["category"],
                                    work_payload["title"],
                                    work_payload["facility_id"],
                                    work_payload["requester_name"],
                                    work_payload["priority"],
                                    work_payload["status"],
                                    work_payload["description"],
                                    work_payload["assignee_user_id"],
                                    work_payload["due_date"],
                                    work_payload["completed_at"],
                                    work_payload["created_by"],
                                    work_payload["updated_by"],
                                    work_payload["created_at"],
                                    work_payload["updated_at"],
                                    work_id,
                                ),
                            )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    counts["legacy_rows"] = len(work_orders)
    return {
        "target_db": str(target_db),
        "dry_run": dry_run,
        "import_work_orders": import_work_orders,
        "update_existing": update_existing,
        "counts": dict(counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="이전 ka-facility-os DB를 민원/작업 데이터로 이관합니다.")
    parser.add_argument("--legacy-db-url", default=os.getenv("LEGACY_DATABASE_URL", ""), help="직접 지정한 레거시 PostgreSQL URL")
    parser.add_argument("--render-service-id", default=os.getenv("LEGACY_RENDER_SERVICE_ID", DEFAULT_RENDER_SERVICE_ID), help="DATABASE_URL을 읽어올 Render 서비스 ID")
    parser.add_argument("--target-db", default=str(DEFAULT_TARGET_DB), help="대상 SQLite DB 경로")
    parser.add_argument("--limit", type=int, default=0, help="상위 N건만 처리")
    parser.add_argument("--inspect", action="store_true", help="원본 DB 건수만 조회")
    parser.add_argument("--apply", action="store_true", help="실제 이관 수행")
    parser.add_argument("--update-existing", action="store_true", help="이미 이관된 레코드도 덮어씀")
    parser.add_argument("--skip-work-orders", action="store_true", help="연결 작업지시 생성/업데이트 생략")
    args = parser.parse_args()

    legacy_db_url = resolve_legacy_database_url(args.legacy_db_url, args.render_service_id)
    target_db = Path(args.target_db).resolve()

    if args.inspect:
        print(json.dumps(inspect_legacy_database(legacy_db_url), ensure_ascii=False, indent=2))
        return

    summary = import_legacy_data(
        legacy_db_url,
        target_db,
        dry_run=not args.apply,
        update_existing=args.update_existing,
        import_work_orders=not args.skip_work_orders,
        limit=args.limit or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
