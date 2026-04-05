from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_SOURCE_BASE_URL = os.getenv(
    "LEGACY_COMPLAINTS_API_BASE_URL",
    "https://ka-facility-os.onrender.com",
).strip()
DEFAULT_SOURCE_SITE = os.getenv("LEGACY_COMPLAINTS_SITE", "").strip()
DEFAULT_SOURCE_SERVICE_ID = os.getenv(
    "LEGACY_COMPLAINTS_RENDER_SERVICE_ID",
    os.getenv("LEGACY_RENDER_SERVICE_ID", "srv-d6g57jbuibrs739g5mvg").strip(),
).strip()
DEFAULT_TARGET_DB = ROOT_DIR / "operations.db"
TYPE_LABELS = {
    "screen_contamination": "방충망 오염",
    "screen_damage": "방충망 파손",
    "glass_contamination": "유리/창문 오염",
    "glass_damage": "유리/창문 파손",
    "railing_contamination": "난간 오염",
    "louver_issue": "루버창 불량",
    "silicone_issue": "실리콘/퍼티 불량",
    "wall_floor_contamination": "벽면/바닥 오염",
    "other_finish_issue": "기타 마감불량",
    "composite": "복합 민원",
}
CHANNEL_LABELS = {
    "legacy_excel": "레거시엑셀",
    "manual": "수기등록",
    "manual_entry": "수기등록",
    "phone": "전화",
    "visit": "방문",
    "mobile": "모바일",
    "kakao": "카카오톡",
    "email": "이메일",
}


def _render_api_get(path: str, api_key: str) -> Any:
    request = urllib.request.Request(
        f"https://api.render.com{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "facility-ops-complaints-import/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def resolve_source_admin_token(explicit_token: str, render_service_id: str) -> str:
    if explicit_token.strip():
        return explicit_token.strip()
    api_key = os.getenv("RENDER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "소스 ADMIN_TOKEN이 없고 RENDER_API_KEY도 없어 레거시 민원 API에 접속할 수 없습니다."
        )
    env_rows = _render_api_get(f"/v1/services/{render_service_id}/env-vars", api_key)
    for row in env_rows:
        env_row = row.get("envVar", row)
        if str(env_row.get("key", "")).strip() == "ADMIN_TOKEN":
            token = str(env_row.get("value", "")).strip()
            if token:
                return token
            break
    raise SystemExit("Render 서비스 환경변수에서 ADMIN_TOKEN을 찾지 못했습니다.")


def _normalize_base_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        raise SystemExit("소스 서비스 URL이 비어 있습니다.")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    return url


def _source_request_json(base_url: str, path: str, admin_token: str) -> Any:
    url = f"{_normalize_base_url(base_url)}{path}"
    headers = {
        "Accept": "application/json",
        "X-Admin-Token": admin_token,
        "User-Agent": "facility-ops-complaints-import/1.0",
    }
    last_error: Exception | None = None
    for attempt in range(6):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            retry_after = exc.headers.get("Retry-After", "").strip()
            wait_seconds = float(retry_after) if retry_after.isdigit() else min(45.0, 2.5 * (attempt + 1))
            time.sleep(wait_seconds)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(min(30.0, 2.0 * (attempt + 1)))
    raise SystemExit(f"소스 민원 API 호출이 반복 실패했습니다: {last_error}")


def _source_query(site: str, *, limit: int | None = None, record_type: str | None = None) -> str:
    params: dict[str, str] = {"site": site}
    if limit:
        params["limit"] = str(limit)
    if record_type:
        params["record_type"] = record_type
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def fetch_source_cases(base_url: str, admin_token: str, site: str) -> list[dict[str, Any]]:
    rows = _source_request_json(base_url, f"/api/complaints?{_source_query(site)}", admin_token)
    if not isinstance(rows, list):
        raise SystemExit("소스 민원 목록 응답 형식이 예상과 다릅니다.")
    return [dict(row) for row in rows]


def fetch_admin_record_rows(
    base_url: str,
    admin_token: str,
    site: str,
    record_type: str,
) -> tuple[list[dict[str, Any]], int]:
    payload = _source_request_json(
        base_url,
        f"/api/complaints/admin/records?{_source_query(site, limit=1000, record_type=record_type)}",
        admin_token,
    )
    if not isinstance(payload, dict):
        raise SystemExit(f"{record_type} 레코드 응답 형식이 예상과 다릅니다.")
    rows = payload.get("rows") or []
    total_count = int(payload.get("total_count") or len(rows))
    return [dict(row) for row in rows], total_count


def inspect_source_data(base_url: str, admin_token: str, site: str) -> dict[str, Any]:
    cases = fetch_source_cases(base_url, admin_token, site)
    events, events_total = fetch_admin_record_rows(base_url, admin_token, site, "events")
    attachments, attachments_total = fetch_admin_record_rows(base_url, admin_token, site, "attachments")
    messages, messages_total = fetch_admin_record_rows(base_url, admin_token, site, "messages")
    cost_items, cost_items_total = fetch_admin_record_rows(base_url, admin_token, site, "cost_items")
    return {
        "base_url": _normalize_base_url(base_url),
        "site": site,
        "cases": len(cases),
        "events": events_total,
        "attachments": attachments_total,
        "messages": messages_total,
        "cost_items": cost_items_total,
        "status_counts": dict(Counter(str(row.get("status") or "").strip() for row in cases)),
        "type_counts": dict(Counter(str(row.get("complaint_type") or "").strip() for row in cases)),
        "event_type_counts": dict(Counter(str(row.get("event_type") or "").strip() for row in events)),
        "message_status_counts": dict(
            Counter(str(row.get("delivery_status") or "").strip() for row in messages)
        ),
    }


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
        "urgent": "긴급",
        "high": "높음",
        "medium": "보통",
        "normal": "보통",
        "low": "낮음",
    }.get(key, "보통")


def _map_status(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "received": "접수",
        "assigned": "배정완료",
        "visit_scheduled": "처리중",
        "in_progress": "처리중",
        "resolved": "처리완료",
        "resident_confirmed": "회신완료",
        "reopened": "재오픈",
        "closed": "종결",
        "canceled": "취소",
        "cancelled": "취소",
    }.get(key, "접수")


def _map_work_status(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "received": "접수",
        "assigned": "진행중",
        "visit_scheduled": "진행중",
        "in_progress": "진행중",
        "resolved": "완료",
        "resident_confirmed": "완료",
        "reopened": "진행중",
        "closed": "종결",
        "canceled": "종결",
        "cancelled": "종결",
    }.get(key, "접수")


def _map_channel(value: Any) -> str:
    key = str(value or "").strip().lower()
    return CHANNEL_LABELS.get(key, str(value or "").strip() or "기타")


def _map_event_type(event_type: Any, status_to: Any) -> str:
    key = str(event_type or "").strip().lower()
    mapped_status = _map_status(status_to)
    if key == "created":
        return "접수"
    if key == "updated":
        return "내부메모"
    if key == "message":
        return "회신"
    if mapped_status == "배정완료":
        return "배정"
    if mapped_status == "재오픈":
        return "재오픈"
    if mapped_status in {"처리완료", "회신완료", "종결"}:
        return "완료보고"
    return "상태변경"


def _parse_json_text(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _detail_suffix(value: Any) -> str:
    detail = _parse_json_text(value)
    if isinstance(detail, dict) and detail:
        changed = detail.get("changed_fields")
        if isinstance(changed, list) and changed:
            joined = ", ".join(str(item) for item in changed)
            return f" (변경: {joined})"
        return f" ({json.dumps(detail, ensure_ascii=False)})"
    if isinstance(detail, list) and detail:
        return f" ({json.dumps(detail, ensure_ascii=False)})"
    return ""


def _build_unit_label(case_row: dict[str, Any]) -> str:
    building = str(case_row.get("building") or "").strip()
    unit = str(case_row.get("unit_number") or "").strip()
    return " ".join(part for part in [building, unit] if part).strip()


def _build_description(case_row: dict[str, Any]) -> str:
    body = str(case_row.get("description") or "").strip()
    notes: list[str] = []
    if case_row.get("recurrence_flag"):
        count = int(case_row.get("recurrence_count") or 0)
        notes.append(f"[원본 정보] 재민원 {'(' + str(count) + '회)' if count else ''}".strip())
    if str(case_row.get("source_channel") or "").strip():
        notes.append(f"[원본 채널] {case_row.get('source_channel')}")
    if str(case_row.get("assignee") or "").strip():
        notes.append(f"[원본 담당자] {case_row.get('assignee')}")
    if case_row.get("linked_work_order_id"):
        notes.append(f"[원본 연결 작업지시] {case_row.get('linked_work_order_id')}")
    if str(case_row.get("case_key") or "").strip():
        notes.append(f"[원본 case_key] {case_row.get('case_key')}")
    chunks = [body] if body else []
    if notes:
        chunks.append("\n".join(notes))
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def _complaint_code(source_id: int) -> str:
    return f"API-CM-{source_id:06d}"


def _work_code(source_id: int) -> str:
    return f"API-WO-{source_id:06d}"


def _load_target_module(target_db: Path):
    os.environ["OPS_DB_PATH"] = str(target_db)
    from ops import db as ops_db  # pylint: disable=import-outside-toplevel

    ops_db.DB_PATH = target_db
    ops_db.init_db()
    return ops_db


def _user_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    lookup: dict[str, int] = {}
    rows = conn.execute("SELECT id, username, full_name FROM users").fetchall()
    for row in rows:
        if row["username"]:
            lookup[str(row["username"]).strip().lower()] = int(row["id"])
        if row["full_name"]:
            lookup[str(row["full_name"]).strip().lower()] = int(row["id"])
    return lookup


def _lookup_user_id(user_map: dict[str, int], value: Any, fallback: int | None = None) -> int | None:
    key = str(value or "").strip().lower()
    if key and key in user_map:
        return user_map[key]
    return fallback


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


def import_api_data(
    base_url: str,
    admin_token: str,
    site: str,
    target_db: Path,
    *,
    dry_run: bool,
    update_existing: bool,
    import_work_orders: bool,
    default_user_id: int | None = None,
) -> dict[str, Any]:
    base_url = _normalize_base_url(base_url)
    ops_db = _load_target_module(target_db)
    cases = fetch_source_cases(base_url, admin_token, site)
    event_rows, events_total = fetch_admin_record_rows(base_url, admin_token, site, "events")
    attachment_rows, attachments_total = fetch_admin_record_rows(base_url, admin_token, site, "attachments")
    message_rows, messages_total = fetch_admin_record_rows(base_url, admin_token, site, "messages")
    cost_rows, cost_total = fetch_admin_record_rows(base_url, admin_token, site, "cost_items")

    events_by_case: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        events_by_case[int(row["complaint_id"])].append(dict(row))
    messages_by_case: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in message_rows:
        messages_by_case[int(row["complaint_id"])].append(dict(row))
    attachments_by_case: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in attachment_rows:
        attachments_by_case[int(row["complaint_id"])].append(dict(row))
    costs_by_case: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in cost_rows:
        costs_by_case[int(row["complaint_id"])].append(dict(row))

    conn = ops_db.get_conn()
    user_map = _user_lookup(conn)
    counts = Counter()

    try:
        for case_row in cases:
            source_id = int(case_row["id"])
            complaint_code = _complaint_code(source_id)
            mapped_status = _map_status(case_row.get("status"))
            created_at = _format_dt(case_row.get("reported_at")) or _format_dt(case_row.get("created_at"))
            updated_at = _format_dt(case_row.get("updated_at")) or created_at
            resolved_at = _format_dt(case_row.get("resolved_at")) or _format_dt(
                case_row.get("resident_confirmed_at")
            )
            closed_at = _format_dt(case_row.get("closed_at")) if mapped_status in {"종결", "취소"} else ""
            assignee_user_id = _lookup_user_id(user_map, case_row.get("assignee"))
            actor_user_id = _lookup_user_id(user_map, case_row.get("created_by"), fallback=default_user_id)
            complaint_payload = {
                "complaint_code": complaint_code,
                "channel": _map_channel(case_row.get("source_channel")),
                "category_primary": str(
                    case_row.get("complaint_type_label")
                    or TYPE_LABELS.get(str(case_row.get("complaint_type") or ""), "기타")
                ).strip(),
                "category_secondary": str(case_row.get("complaint_type") or "").strip(),
                "facility_id": None,
                "unit_label": _build_unit_label(case_row),
                "location_detail": str(case_row.get("site") or "").strip(),
                "requester_name": str(case_row.get("resident_name") or "").strip(),
                "requester_phone": str(case_row.get("contact_phone") or "").strip(),
                "requester_email": "",
                "title": str(case_row.get("title") or _build_unit_label(case_row) or f"민원 {source_id}").strip(),
                "description": _build_description(case_row),
                "priority": _map_priority(case_row.get("priority")),
                "status": mapped_status,
                "response_due_at": _format_dt(case_row.get("scheduled_visit_at"), date_only=True),
                "resolved_at": resolved_at,
                "closed_at": closed_at,
                "assignee_user_id": assignee_user_id,
                "created_by": actor_user_id,
                "updated_by": _lookup_user_id(user_map, case_row.get("assignee"), fallback=actor_user_id),
                "created_at": created_at,
                "updated_at": updated_at,
            }

            complaint_id = _get_existing_id(conn, "complaints", "complaint_code", complaint_code)
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
                    complaint_id = -source_id
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

            source_events = events_by_case.get(source_id, [])
            if not source_events:
                base_update = {
                    "complaint_id": complaint_id,
                    "update_type": "접수",
                    "status_from": "",
                    "status_to": mapped_status,
                    "message": f"세대 민원 시스템에서 이관되었습니다. ({case_row.get('site')}/{case_row.get('id')})",
                    "is_public_note": 0,
                    "created_by": actor_user_id,
                    "created_at": created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if not _update_exists(conn, complaint_id, base_update):
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

            for event_row in source_events:
                status_from = _map_status(event_row.get("from_status")) if event_row.get("from_status") else ""
                status_to = _map_status(event_row.get("to_status")) if event_row.get("to_status") else ""
                message = f"{str(event_row.get('note') or '').strip()}{_detail_suffix(event_row.get('detail_json'))}".strip()
                if not message:
                    message = f"소스 이벤트 {event_row.get('event_type') or 'unknown'}"
                update_payload = {
                    "complaint_id": complaint_id,
                    "update_type": _map_event_type(event_row.get("event_type"), event_row.get("to_status")),
                    "status_from": status_from,
                    "status_to": status_to,
                    "message": message,
                    "is_public_note": 0,
                    "created_by": _lookup_user_id(
                        user_map,
                        event_row.get("actor_username"),
                        fallback=default_user_id,
                    ),
                    "created_at": _format_dt(event_row.get("created_at")) or created_at,
                }
                if _update_exists(conn, complaint_id, update_payload):
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

            for message_row in messages_by_case.get(source_id, []):
                body = str(message_row.get("body") or "").strip()
                delivery_status = str(message_row.get("delivery_status") or "").strip()
                provider = str(message_row.get("provider_name") or "").strip()
                note_parts = [body] if body else ["레거시 문자 이력"]
                meta_parts = []
                if delivery_status:
                    meta_parts.append(f"상태 {delivery_status}")
                if provider:
                    meta_parts.append(f"발송수단 {provider}")
                if str(message_row.get("recipient") or "").strip():
                    meta_parts.append(f"수신 {message_row.get('recipient')}")
                if meta_parts:
                    note_parts.append(f"[문자 메타] {' / '.join(meta_parts)}")
                update_payload = {
                    "complaint_id": complaint_id,
                    "update_type": "회신",
                    "status_from": "",
                    "status_to": "",
                    "message": "\n".join(note_parts).strip(),
                    "is_public_note": 1,
                    "created_by": _lookup_user_id(
                        user_map,
                        message_row.get("sent_by"),
                        fallback=default_user_id,
                    ),
                    "created_at": _format_dt(message_row.get("sent_at"))
                    or _format_dt(message_row.get("created_at"))
                    or updated_at,
                }
                if _update_exists(conn, complaint_id, update_payload):
                    counts["message_updates_existing"] += 1
                    continue
                counts["message_updates_inserted"] += 1
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

            for attachment_row in attachments_by_case.get(source_id, []):
                filename = str(
                    attachment_row.get("original_name")
                    or attachment_row.get("stored_name")
                    or "첨부"
                ).strip()
                update_payload = {
                    "complaint_id": complaint_id,
                    "update_type": "내부메모",
                    "status_from": "",
                    "status_to": "",
                    "message": f"[원본 첨부] {filename}",
                    "is_public_note": 0,
                    "created_by": default_user_id,
                    "created_at": _format_dt(attachment_row.get("created_at")) or updated_at,
                }
                if _update_exists(conn, complaint_id, update_payload):
                    counts["attachment_notes_existing"] += 1
                    continue
                counts["attachment_notes_inserted"] += 1
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

            for cost_row in costs_by_case.get(source_id, []):
                amount = str(cost_row.get("amount") or cost_row.get("unit_cost") or "").strip()
                category = str(cost_row.get("cost_category") or "").strip()
                note = str(cost_row.get("note") or "").strip()
                pieces = ["[원본 비용]"]
                if category:
                    pieces.append(category)
                if amount:
                    pieces.append(amount)
                if note:
                    pieces.append(note)
                update_payload = {
                    "complaint_id": complaint_id,
                    "update_type": "내부메모",
                    "status_from": "",
                    "status_to": "",
                    "message": " / ".join(pieces),
                    "is_public_note": 0,
                    "created_by": default_user_id,
                    "created_at": _format_dt(cost_row.get("created_at")) or updated_at,
                }
                if _update_exists(conn, complaint_id, update_payload):
                    counts["cost_notes_existing"] += 1
                    continue
                counts["cost_notes_inserted"] += 1
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
                work_payload = {
                    "work_code": _work_code(source_id),
                    "complaint_id": complaint_id,
                    "category": "민원",
                    "title": complaint_payload["title"],
                    "facility_id": None,
                    "requester_name": complaint_payload["requester_name"],
                    "priority": complaint_payload["priority"],
                    "status": _map_work_status(case_row.get("status")),
                    "description": complaint_payload["description"],
                    "assignee_user_id": complaint_payload["assignee_user_id"],
                    "due_date": _format_dt(case_row.get("scheduled_visit_at"), date_only=True),
                    "completed_at": complaint_payload["resolved_at"] or complaint_payload["closed_at"],
                    "created_by": complaint_payload["created_by"],
                    "updated_by": complaint_payload["updated_by"],
                    "created_at": complaint_payload["created_at"],
                    "updated_at": complaint_payload["updated_at"],
                }
                work_id = _get_existing_id(conn, "work_orders", "work_code", work_payload["work_code"])
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

    counts["source_cases_total"] = len(cases)
    counts["source_events_total"] = events_total
    counts["source_messages_total"] = messages_total
    counts["source_attachments_total"] = attachments_total
    counts["source_cost_items_total"] = cost_total
    return {
        "source": {
            "base_url": base_url,
            "site": site,
        },
        "target_db": str(target_db),
        "dry_run": dry_run,
        "update_existing": update_existing,
        "import_work_orders": import_work_orders,
        "counts": dict(counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ka-facility-os 세대 민원 API 데이터를 현재 시스템으로 이관합니다."
    )
    parser.add_argument("--base-url", default=DEFAULT_SOURCE_BASE_URL, help="소스 서비스 기본 URL")
    parser.add_argument("--site", default=DEFAULT_SOURCE_SITE, help="이관할 site 이름")
    parser.add_argument(
        "--admin-token",
        default=os.getenv("LEGACY_ADMIN_TOKEN", ""),
        help="소스 X-Admin-Token",
    )
    parser.add_argument(
        "--render-service-id",
        default=DEFAULT_SOURCE_SERVICE_ID,
        help="ADMIN_TOKEN을 읽어올 Render 서비스 ID",
    )
    parser.add_argument("--target-db", default=str(DEFAULT_TARGET_DB), help="대상 SQLite DB 경로")
    parser.add_argument("--inspect", action="store_true", help="소스 건수와 분포만 확인")
    parser.add_argument("--apply", action="store_true", help="실제 이관 수행")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="이미 들어간 API-* 레코드도 덮어씀",
    )
    parser.add_argument(
        "--import-work-orders",
        action="store_true",
        help="민원과 함께 작업지시도 생성",
    )
    parser.add_argument(
        "--default-user-id",
        type=int,
        default=0,
        help="매핑 실패 시 사용할 기본 사용자 id",
    )
    args = parser.parse_args()

    site = str(args.site or "").strip()
    if not site:
        raise SystemExit("--site 값이 필요합니다.")

    admin_token = resolve_source_admin_token(args.admin_token, args.render_service_id)
    target_db = Path(args.target_db).resolve()

    if args.inspect:
        summary = inspect_source_data(args.base_url, admin_token, site)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    summary = import_api_data(
        args.base_url,
        admin_token,
        site,
        target_db,
        dry_run=not args.apply,
        update_existing=args.update_existing,
        import_work_orders=args.import_work_orders,
        default_user_id=args.default_user_id or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
