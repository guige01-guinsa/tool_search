from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO

SOURCE_TYPE = "pdf_report"
STATUS_PATTERN = re.compile(r"^(접수|분류완료|배정완료|처리중|처리완료|회신완료|종결|보류|취소|재오픈)$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
CONTACT_PATTERN = re.compile(r"^(?=.*\d)[0-9()+ \-]{2,}$")


@dataclass(slots=True)
class ParsedComplaintRow:
    source_ticket_id: str
    building_label: str
    unit_number: str
    source_category: str
    status: str
    assignee_name: str
    received_at: str
    requester_phone: str
    description: str
    page_number: int


@dataclass(slots=True)
class ParsedComplaintReport:
    source_name: str
    source_fingerprint: str
    site_name: str
    report_title: str
    report_summary: str
    document_type: str
    recipient_name: str
    submitter_name: str
    contractor_name: str
    project_name: str
    report_date: str
    report_generated_at: str
    latest_received_at: str
    total_complaints: int
    household_count: int
    open_count: int
    closed_count: int
    repeat_count: int
    progress_rate: str
    completion_rate: str
    status_counts: dict[str, int] = field(default_factory=dict)
    building_counts: dict[str, int] = field(default_factory=dict)
    complaints: list[ParsedComplaintRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_complaints_pdf_bytes(data: bytes, source_name: str = "") -> ParsedComplaintReport:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf dependency not installed") from exc

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        pages.append(lines)
    return parse_complaints_pdf_pages(
        pages,
        source_name=source_name,
        source_fingerprint=hashlib.sha1(data).hexdigest(),
    )


def parse_complaints_pdf_pages(
    pages: list[list[str]],
    *,
    source_name: str = "",
    source_fingerprint: str = "",
) -> ParsedComplaintReport:
    if not pages:
        raise ValueError("PDF에서 읽은 페이지가 없습니다.")

    first_page = pages[0]
    warnings: list[str] = []
    status_counts = _parse_status_counts(first_page)
    latest_received = _first_match(first_page, re.compile(r"최근 접수\s+(.+)$"))
    generated_line = _first_line(first_page, "보고기준 ")
    generated_at = ""
    if generated_line:
        generated_at = generated_line.replace("보고기준", "", 1).split("·", 1)[0].strip()
    complaints = _parse_complaint_pages(
        pages,
        warnings,
        expected_building_counts=_parse_building_totals(pages),
        fallback_received_at=_normalize_datetime_text(latest_received) or _normalize_datetime_text(generated_at),
        fallback_status=max(status_counts.items(), key=lambda item: item[1])[0] if status_counts else "배정완료",
    )
    building_counts = dict(Counter(row.building_label or "미상" for row in complaints))

    return ParsedComplaintReport(
        source_name=source_name or "uploaded-report.pdf",
        source_fingerprint=source_fingerprint
        or hashlib.sha1("".join("".join(page) for page in pages).encode("utf-8")).hexdigest(),
        site_name=_value_after(first_page, "단지명"),
        report_title=_first_line(first_page, "세대 민원 처리 현황 보고서") or "세대 민원 처리 현황 보고서",
        report_summary=_compose_summary(first_page),
        document_type=_value_after(first_page, "문서구분"),
        recipient_name=_value_after(first_page, "제출처"),
        submitter_name=_value_after(first_page, "제출사"),
        contractor_name=_value_after(first_page, "시공사"),
        project_name=_value_after(first_page, "공사명"),
        report_date=_normalize_korean_date(_value_after(first_page, "보고일")),
        report_generated_at=_normalize_datetime_text(generated_at),
        latest_received_at=_normalize_datetime_text(latest_received),
        total_complaints=_safe_int(_value_after(first_page, "민원건수"), default=len(complaints)),
        household_count=_safe_int(_value_after(first_page, "세대수")),
        open_count=_safe_int(_value_after(first_page, "미처리")),
        closed_count=_safe_int(_value_after(first_page, "종결")),
        repeat_count=_safe_int(_value_after(first_page, "재민원")),
        progress_rate=_value_after(first_page, "진행률"),
        completion_rate=_value_after(first_page, "완성률"),
        status_counts=status_counts or dict(Counter(row.status for row in complaints)),
        building_counts=building_counts,
        complaints=complaints,
        warnings=warnings,
    )


def _parse_complaint_pages(
    pages: list[list[str]],
    warnings: list[str],
    *,
    expected_building_counts: dict[str, int],
    fallback_received_at: str,
    fallback_status: str,
) -> list[ParsedComplaintRow]:
    complaints: list[ParsedComplaintRow] = []
    current_building = ""

    for page_number, lines in enumerate(pages, start=1):
        body_lines = _body_lines(lines)
        if not body_lines:
            continue
        idx = 0
        while idx < len(body_lines):
            line = body_lines[idx]
            if line.startswith("동: "):
                current_building = _normalize_building(line.split(":", 1)[1].strip())
                idx += 1
                continue
            if line.startswith("동 소계"):
                idx += 1
                continue
            if _looks_like_record_start(body_lines, idx):
                end = idx + 1
                while end < len(body_lines):
                    if body_lines[end].startswith("동: ") or body_lines[end].startswith("동 소계"):
                        break
                    if _looks_like_record_start(body_lines, end):
                        break
                    end += 1
                record_lines = body_lines[idx:end]
                parsed = _parse_record_lines(record_lines, current_building, page_number, warnings)
                if parsed:
                    complaints.append(parsed)
                idx = end
                continue
            idx += 1
    actual_counts = Counter(row.building_label or "미상" for row in complaints)
    for building_label, expected_count in expected_building_counts.items():
        normalized_building = _normalize_building(building_label)
        counter_key = normalized_building or "미상"
        deficit = int(expected_count or 0) - int(actual_counts.get(counter_key, 0))
        for index in range(max(deficit, 0)):
            warnings.append(
                f"{building_label} 동의 민원 {deficit}건은 PDF 텍스트 추출 누락으로 placeholder 레코드로 보정했습니다."
            )
            complaints.append(
                ParsedComplaintRow(
                    source_ticket_id=f"MISSING-{building_label or '미상'}-{index + 1}",
                    building_label=normalized_building,
                    unit_number="",
                    source_category="미상",
                    status=fallback_status or "배정완료",
                    assignee_name="",
                    received_at=fallback_received_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    requester_phone="",
                    description="PDF 텍스트 추출 누락 민원",
                    page_number=0,
                )
            )
    return complaints


def _parse_record_lines(
    lines: list[str],
    current_building: str,
    page_number: int,
    warnings: list[str],
) -> ParsedComplaintRow | None:
    if len(lines) < 8:
        warnings.append(f"{page_number}페이지에서 길이가 짧은 민원 레코드를 건너뛰었습니다: {lines!r}")
        return None

    source_ticket_id = lines[0].strip()
    date_idx = next((index for index, value in enumerate(lines) if DATE_PATTERN.match(value)), -1)
    if date_idx < 5 or date_idx + 1 >= len(lines) or not TIME_PATTERN.match(lines[date_idx + 1]):
        warnings.append(f"{page_number}페이지 민원 {source_ticket_id}의 접수일시를 해석하지 못했습니다.")
        return None

    building_label = _normalize_building(lines[1]) or current_building
    unit_number = _normalize_unit(lines[2])
    source_category = " ".join(part.strip() for part in lines[3 : date_idx - 2] if part.strip()).strip() or "기타"
    status = _normalize_status(lines[date_idx - 2])
    assignee_name = _normalize_assignee(lines[date_idx - 1])
    received_at = _normalize_datetime_text(f"{lines[date_idx]} {lines[date_idx + 1]}")
    requester_phone, description = _split_contact_and_description(
        [item.strip() for item in lines[date_idx + 2 :] if item.strip()]
    )

    if not building_label:
        warnings.append(f"{page_number}페이지 민원 {source_ticket_id}는 동 정보가 비어 있어 미상으로 처리합니다.")
    if not unit_number:
        warnings.append(f"{page_number}페이지 민원 {source_ticket_id}는 호수 정보가 비어 있습니다.")

    return ParsedComplaintRow(
        source_ticket_id=source_ticket_id,
        building_label=building_label,
        unit_number=unit_number,
        source_category=source_category,
        status=status,
        assignee_name=assignee_name,
        received_at=received_at,
        requester_phone=requester_phone,
        description=description or source_category,
        page_number=page_number,
    )


def _body_lines(lines: list[str]) -> list[str]:
    try:
        header_idx = lines.index("민원내용")
    except ValueError:
        return []
    body = []
    for line in lines[header_idx + 1 :]:
        if line.startswith("KA Facility OS"):
            break
        if re.fullmatch(r"\d+\s*page", line):
            break
        body.append(line)
    return body


def _looks_like_record_start(lines: list[str], idx: int) -> bool:
    if idx + 2 >= len(lines):
        return False
    if not re.fullmatch(r"\d+", lines[idx].strip()):
        return False
    building = lines[idx + 1].strip()
    unit_number = lines[idx + 2].strip()
    return (building.endswith("동") or building == "동") and (unit_number.endswith("호") or unit_number == "호수")


def _split_contact_and_description(remaining: list[str]) -> tuple[str, str]:
    if not remaining:
        return "", ""
    if len(remaining) == 1:
        return "", remaining[0].strip()
    first = remaining[0].strip()
    if CONTACT_PATTERN.match(first):
        return first, " ".join(item.strip() for item in remaining[1:] if item.strip()).strip()
    return "", " ".join(item.strip() for item in remaining if item.strip()).strip()


def _parse_status_counts(lines: list[str]) -> dict[str, int]:
    try:
        start = lines.index("상태 분포") + 1
        end = lines.index("상세 목록")
    except ValueError:
        return {}
    counts: dict[str, int] = {}
    idx = start
    while idx + 1 < end:
        key = lines[idx].strip()
        value = lines[idx + 1].strip()
        if not key or not re.fullmatch(r"\d+", value):
            idx += 1
            continue
        counts[key] = int(value)
        idx += 2
    return counts


def _parse_building_totals(pages: list[list[str]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    pattern = re.compile(r"^동 소계 ·\s*(.+):\s*(\d+)건$")
    for lines in pages:
        for line in lines:
            matched = pattern.match(line.strip())
            if not matched:
                continue
            building_label = matched.group(1).strip()
            totals[building_label] = int(matched.group(2))
    return totals


def import_complaints_pdf_bytes(
    conn: sqlite3.Connection,
    data: bytes,
    *,
    source_name: str = "",
    dry_run: bool = False,
    update_existing: bool = False,
    create_work_orders: bool = True,
    default_user_id: int | None = None,
) -> dict[str, object]:
    report = parse_complaints_pdf_bytes(data, source_name=source_name)
    return import_parsed_complaint_report(
        conn,
        report,
        dry_run=dry_run,
        update_existing=update_existing,
        create_work_orders=create_work_orders,
        default_user_id=default_user_id,
    )


def import_parsed_complaint_report(
    conn: sqlite3.Connection,
    report: ParsedComplaintReport,
    *,
    dry_run: bool = False,
    update_existing: bool = False,
    create_work_orders: bool = True,
    default_user_id: int | None = None,
) -> dict[str, object]:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch_row = conn.execute(
        "SELECT * FROM complaint_import_batches WHERE source_fingerprint = ?",
        (report.source_fingerprint,),
    ).fetchone()
    batch_id = int(batch_row["id"]) if batch_row else None
    batch_code = str(batch_row["batch_code"]) if batch_row else ""
    batch_created = False
    facilities_created = 0
    facilities_matched = 0
    complaints_inserted = 0
    complaints_updated = 0
    complaints_skipped = 0
    work_orders_inserted = 0
    work_orders_updated = 0
    existing_matches = 0
    planned_facility_state: dict[str, bool] = {}
    planned_work_state: dict[str, bool] = {}

    if not dry_run:
        batch_id, batch_code, batch_created = _ensure_batch_row(conn, report, default_user_id, now_text)

    for row in report.complaints:
        complaint_source_ref = _complaint_source_reference(report.site_name, row.source_ticket_id)
        existing_complaint = conn.execute(
            "SELECT * FROM complaints WHERE source_reference = ?",
            (complaint_source_ref,),
        ).fetchone()
        if existing_complaint:
            existing_matches += 1

        facility_id = None
        if row.building_label:
            facility_source_ref = _facility_source_reference(report.site_name, row.building_label)
            if dry_run:
                if facility_source_ref not in planned_facility_state:
                    matched = conn.execute(
                        """
                        SELECT id FROM facilities
                        WHERE source_reference = ?
                           OR (building = ? AND name = ?)
                        LIMIT 1
                        """,
                        (
                            facility_source_ref,
                            row.building_label,
                            _facility_name(report.site_name, row.building_label),
                        ),
                    ).fetchone()
                    planned_facility_state[facility_source_ref] = bool(matched)
                    if matched:
                        facilities_matched += 1
                    else:
                        facilities_created += 1
            else:
                facility_id, created = _ensure_facility_row(conn, report, row, default_user_id, now_text)
                if created:
                    facilities_created += 1
                else:
                    facilities_matched += 1

        complaint_payload = _complaint_payload(report, row, batch_id, facility_id, default_user_id)
        if existing_complaint and not update_existing:
            complaints_skipped += 1
            complaint_id = int(existing_complaint["id"])
        elif dry_run and existing_complaint:
            complaints_updated += 1
            complaint_id = int(existing_complaint["id"])
        elif dry_run:
            complaints_inserted += 1
            complaint_id = 0
        elif existing_complaint:
            complaint_id = int(existing_complaint["id"])
            conn.execute(
                """
                UPDATE complaints
                SET batch_id = ?, site_name = ?, building_label = ?, unit_number = ?, channel = ?, category_primary = ?,
                    category_secondary = ?, facility_id = ?, unit_label = ?, location_detail = ?, requester_name = ?,
                    requester_phone = ?, requester_email = ?, external_assignee_name = ?, source_type = ?, source_reference = ?,
                    title = ?, description = ?, priority = ?, status = ?, response_due_at = ?, resolved_at = ?, closed_at = ?,
                    assignee_user_id = NULL, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    complaint_payload["batch_id"],
                    complaint_payload["site_name"],
                    complaint_payload["building_label"],
                    complaint_payload["unit_number"],
                    complaint_payload["channel"],
                    complaint_payload["category_primary"],
                    complaint_payload["category_secondary"],
                    complaint_payload["facility_id"],
                    complaint_payload["unit_label"],
                    complaint_payload["location_detail"],
                    complaint_payload["requester_name"],
                    complaint_payload["requester_phone"],
                    complaint_payload["requester_email"],
                    complaint_payload["external_assignee_name"],
                    complaint_payload["source_type"],
                    complaint_payload["source_reference"],
                    complaint_payload["title"],
                    complaint_payload["description"],
                    complaint_payload["priority"],
                    complaint_payload["status"],
                    complaint_payload["response_due_at"],
                    complaint_payload["resolved_at"],
                    complaint_payload["closed_at"],
                    complaint_payload["updated_by"],
                    complaint_payload["updated_at"],
                    complaint_id,
                ),
            )
            complaints_updated += 1
            _record_import_update(
                conn,
                complaint_id,
                row,
                default_user_id,
                complaint_payload["updated_at"],
                message_prefix="PDF 재이관",
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO complaints(
                    complaint_code, batch_id, site_name, building_label, unit_number, channel, category_primary,
                    category_secondary, facility_id, unit_label, location_detail, requester_name, requester_phone,
                    requester_email, external_assignee_name, source_type, source_reference, title, description,
                    priority, status, response_due_at, resolved_at, closed_at, assignee_user_id, created_by,
                    updated_by, created_at, updated_at
                )
                VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    complaint_payload["batch_id"],
                    complaint_payload["site_name"],
                    complaint_payload["building_label"],
                    complaint_payload["unit_number"],
                    complaint_payload["channel"],
                    complaint_payload["category_primary"],
                    complaint_payload["category_secondary"],
                    complaint_payload["facility_id"],
                    complaint_payload["unit_label"],
                    complaint_payload["location_detail"],
                    complaint_payload["requester_name"],
                    complaint_payload["requester_phone"],
                    complaint_payload["requester_email"],
                    complaint_payload["external_assignee_name"],
                    complaint_payload["source_type"],
                    complaint_payload["source_reference"],
                    complaint_payload["title"],
                    complaint_payload["description"],
                    complaint_payload["priority"],
                    complaint_payload["status"],
                    complaint_payload["response_due_at"],
                    complaint_payload["resolved_at"],
                    complaint_payload["closed_at"],
                    complaint_payload["created_by"],
                    complaint_payload["updated_by"],
                    complaint_payload["created_at"],
                    complaint_payload["updated_at"],
                ),
            )
            complaint_id = int(cursor.lastrowid)
            _assign_code(conn, "complaints", "complaint_code", "CP", complaint_id)
            complaints_inserted += 1
            _record_import_update(conn, complaint_id, row, default_user_id, complaint_payload["updated_at"])

        if not create_work_orders or (not complaint_id and not dry_run):
            continue

        work_source_ref = _work_order_source_reference(report.site_name, row.source_ticket_id)
        existing_work = conn.execute(
            "SELECT * FROM work_orders WHERE source_reference = ?",
            (work_source_ref,),
        ).fetchone()
        work_payload = _work_order_payload(report, row, batch_id, facility_id, complaint_id, default_user_id)
        if existing_work and not update_existing:
            continue
        if dry_run:
            if work_source_ref not in planned_work_state:
                planned_work_state[work_source_ref] = bool(existing_work)
                if existing_work:
                    work_orders_updated += 1
                else:
                    work_orders_inserted += 1
            continue
        if existing_work:
            work_id = int(existing_work["id"])
            conn.execute(
                """
                UPDATE work_orders
                SET batch_id = ?, complaint_id = ?, external_assignee_name = ?, source_type = ?, source_reference = ?,
                    category = ?, title = ?, facility_id = ?, requester_name = ?, priority = ?, status = ?,
                    description = ?, assignee_user_id = NULL, due_date = ?, completed_at = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    work_payload["batch_id"],
                    work_payload["complaint_id"],
                    work_payload["external_assignee_name"],
                    work_payload["source_type"],
                    work_payload["source_reference"],
                    work_payload["category"],
                    work_payload["title"],
                    work_payload["facility_id"],
                    work_payload["requester_name"],
                    work_payload["priority"],
                    work_payload["status"],
                    work_payload["description"],
                    work_payload["due_date"],
                    work_payload["completed_at"],
                    work_payload["updated_by"],
                    work_payload["updated_at"],
                    work_id,
                ),
            )
            work_orders_updated += 1
            _record_work_order_update(conn, work_id, row, default_user_id, work_payload["updated_at"], "PDF 재이관")
        else:
            cursor = conn.execute(
                """
                INSERT INTO work_orders(
                    work_code, batch_id, complaint_id, external_assignee_name, source_type, source_reference, category,
                    title, facility_id, requester_name, priority, status, description, assignee_user_id, due_date,
                    completed_at, created_by, updated_by, created_at, updated_at
                )
                VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_payload["batch_id"],
                    work_payload["complaint_id"],
                    work_payload["external_assignee_name"],
                    work_payload["source_type"],
                    work_payload["source_reference"],
                    work_payload["category"],
                    work_payload["title"],
                    work_payload["facility_id"],
                    work_payload["requester_name"],
                    work_payload["priority"],
                    work_payload["status"],
                    work_payload["description"],
                    work_payload["due_date"],
                    work_payload["completed_at"],
                    work_payload["created_by"],
                    work_payload["updated_by"],
                    work_payload["created_at"],
                    work_payload["updated_at"],
                ),
            )
            work_id = int(cursor.lastrowid)
            _assign_code(conn, "work_orders", "work_code", "WO", work_id)
            work_orders_inserted += 1
            _record_work_order_update(conn, work_id, row, default_user_id, work_payload["updated_at"], "PDF 이관")

    return {
        "report": {
            "source_name": report.source_name,
            "site_name": report.site_name,
            "project_name": report.project_name,
            "contractor_name": report.contractor_name,
            "report_date": report.report_date,
            "report_generated_at": report.report_generated_at,
            "latest_received_at": report.latest_received_at,
            "total_complaints": report.total_complaints,
            "status_counts": report.status_counts,
            "building_counts": report.building_counts,
            "warnings": report.warnings,
        },
        "batch": {
            "id": batch_id,
            "batch_code": batch_code,
            "created": batch_created,
            "existing": bool(batch_row),
        },
        "counts": {
            "parsed_complaints": len(report.complaints),
            "existing_matches": existing_matches,
            "facilities_created": facilities_created,
            "facilities_matched": facilities_matched,
            "complaints_inserted": complaints_inserted,
            "complaints_updated": complaints_updated,
            "complaints_skipped": complaints_skipped,
            "work_orders_inserted": work_orders_inserted,
            "work_orders_updated": work_orders_updated,
        },
        "dry_run": dry_run,
        "create_work_orders": create_work_orders,
        "update_existing": update_existing,
    }


def _value_after(lines: list[str], label: str) -> str:
    try:
        idx = lines.index(label)
    except ValueError:
        return ""
    if idx + 1 >= len(lines):
        return ""
    return lines[idx + 1].strip()


def _first_line(lines: list[str], prefix: str) -> str:
    for line in lines:
        if line.startswith(prefix):
            return line.strip()
    return ""


def _first_match(lines: list[str], pattern: re.Pattern[str]) -> str:
    for line in lines:
        matched = pattern.search(line)
        if matched:
            return matched.group(1).strip()
    return ""


def _compose_summary(lines: list[str]) -> str:
    summary_lines = []
    capture = False
    for line in lines:
        if line == "세대 민원 처리 현황 보고서":
            capture = True
            continue
        if capture and line.startswith("최근 접수"):
            break
        if capture:
            summary_lines.append(line.strip())
    return " ".join(item for item in summary_lines if item)


def _normalize_korean_date(value: str) -> str:
    if not value:
        return ""
    matched = re.search(r"(\d{4})년\s*(\d{2})월\s*(\d{2})일", value)
    if matched:
        return f"{matched.group(1)}-{matched.group(2)}-{matched.group(3)}"
    return value.strip()[:10]


def _normalize_datetime_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ").replace(".", "-")
    if DATE_PATTERN.match(text):
        return f"{text} 00:00:00"
    matched = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::(\d{2}))?$", text)
    if matched:
        seconds = matched.group(3) or "00"
        return f"{matched.group(1)} {matched.group(2)}:{seconds}"
    return text


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return default


def _normalize_building(value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"", "동"}:
        return ""
    return raw


def _normalize_unit(value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"", "호수"}:
        return ""
    return raw


def _normalize_status(value: str) -> str:
    raw = str(value or "").strip()
    return raw if STATUS_PATTERN.match(raw) else "접수"


def _normalize_assignee(value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"", "미배정"}:
        return ""
    return raw


def _facility_name(site_name: str, building_label: str) -> str:
    site = str(site_name or "").strip()
    building = str(building_label or "").strip()
    return f"{site} {building}".strip() or building or site or "PDF 이관 시설"


def _facility_source_reference(site_name: str, building_label: str) -> str:
    return f"{SOURCE_TYPE}:facility:{site_name.strip()}:{building_label.strip()}"


def _complaint_source_reference(site_name: str, source_ticket_id: str) -> str:
    return f"{SOURCE_TYPE}:complaint:{site_name.strip()}:{source_ticket_id.strip()}"


def _work_order_source_reference(site_name: str, source_ticket_id: str) -> str:
    return f"{SOURCE_TYPE}:work:{site_name.strip()}:{source_ticket_id.strip()}"


def _ensure_batch_row(
    conn: sqlite3.Connection,
    report: ParsedComplaintReport,
    default_user_id: int | None,
    now_text: str,
) -> tuple[int, str, bool]:
    existing = conn.execute(
        "SELECT id, batch_code FROM complaint_import_batches WHERE source_fingerprint = ?",
        (report.source_fingerprint,),
    ).fetchone()
    payload = json.dumps(
        {
            "report": {
                "source_name": report.source_name,
                "site_name": report.site_name,
                "report_title": report.report_title,
                "report_summary": report.report_summary,
                "document_type": report.document_type,
                "recipient_name": report.recipient_name,
                "submitter_name": report.submitter_name,
                "contractor_name": report.contractor_name,
                "project_name": report.project_name,
                "report_date": report.report_date,
                "report_generated_at": report.report_generated_at,
                "latest_received_at": report.latest_received_at,
                "total_complaints": report.total_complaints,
                "household_count": report.household_count,
                "open_count": report.open_count,
                "closed_count": report.closed_count,
                "repeat_count": report.repeat_count,
                "progress_rate": report.progress_rate,
                "completion_rate": report.completion_rate,
                "warnings": report.warnings,
            },
        },
        ensure_ascii=False,
    )
    if existing:
        batch_id = int(existing["id"])
        batch_code = str(existing["batch_code"])
        conn.execute(
            """
            UPDATE complaint_import_batches
            SET source_type = ?, source_name = ?, site_name = ?, report_title = ?, document_type = ?, recipient_name = ?,
                submitter_name = ?, contractor_name = ?, project_name = ?, report_date = ?, report_generated_at = ?,
                latest_received_at = ?, total_complaints = ?, household_count = ?, open_count = ?, closed_count = ?,
                repeat_count = ?, status_summary_json = ?, building_summary_json = ?, raw_payload = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                SOURCE_TYPE,
                report.source_name,
                report.site_name,
                report.report_title,
                report.document_type,
                report.recipient_name,
                report.submitter_name,
                report.contractor_name,
                report.project_name,
                report.report_date,
                report.report_generated_at,
                report.latest_received_at,
                report.total_complaints,
                report.household_count,
                report.open_count,
                report.closed_count,
                report.repeat_count,
                json.dumps(report.status_counts, ensure_ascii=False),
                json.dumps(report.building_counts, ensure_ascii=False),
                payload,
                now_text,
                batch_id,
            ),
        )
        return batch_id, batch_code, False

    cursor = conn.execute(
        """
        INSERT INTO complaint_import_batches(
            batch_code, source_type, source_name, source_fingerprint, site_name, report_title, document_type,
            recipient_name, submitter_name, contractor_name, project_name, report_date, report_generated_at,
            latest_received_at, total_complaints, household_count, open_count, closed_count, repeat_count,
            status_summary_json, building_summary_json, raw_payload, created_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_TYPE,
            report.source_name,
            report.source_fingerprint,
            report.site_name,
            report.report_title,
            report.document_type,
            report.recipient_name,
            report.submitter_name,
            report.contractor_name,
            report.project_name,
            report.report_date,
            report.report_generated_at,
            report.latest_received_at,
            report.total_complaints,
            report.household_count,
            report.open_count,
            report.closed_count,
            report.repeat_count,
            json.dumps(report.status_counts, ensure_ascii=False),
            json.dumps(report.building_counts, ensure_ascii=False),
            payload,
            default_user_id,
            now_text,
            now_text,
        ),
    )
    batch_id = int(cursor.lastrowid)
    batch_code = _assign_code(conn, "complaint_import_batches", "batch_code", "BATCH", batch_id)
    return batch_id, batch_code, True


def _ensure_facility_row(
    conn: sqlite3.Connection,
    report: ParsedComplaintReport,
    row: ParsedComplaintRow,
    default_user_id: int | None,
    now_text: str,
) -> tuple[int | None, bool]:
    if not row.building_label:
        return None, False

    source_reference = _facility_source_reference(report.site_name, row.building_label)
    existing = conn.execute(
        """
        SELECT * FROM facilities
        WHERE source_reference = ?
           OR (building = ? AND name = ?)
        ORDER BY id ASC
        LIMIT 1
        """,
        (source_reference, row.building_label, _facility_name(report.site_name, row.building_label)),
    ).fetchone()
    if existing:
        facility_id = int(existing["id"])
        if not str(existing["source_reference"] or "").strip():
            conn.execute(
                """
                UPDATE facilities
                SET source_type = ?, source_reference = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (SOURCE_TYPE, source_reference, default_user_id, now_text, facility_id),
            )
        return facility_id, False

    cursor = conn.execute(
        """
        INSERT INTO facilities(
            facility_code, source_type, source_reference, category, name, building, floor, zone, status,
            manager_user_id, note, created_by, updated_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, '', '', '운영중', NULL, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_TYPE,
            source_reference,
            _map_primary_category(row.source_category, row.description),
            _facility_name(report.site_name, row.building_label),
            row.building_label,
            f"{report.site_name} PDF 이관 배치 {report.report_date or report.report_generated_at[:10]}",
            default_user_id,
            default_user_id,
            now_text,
            now_text,
        ),
    )
    facility_id = int(cursor.lastrowid)
    _assign_code(conn, "facilities", "facility_code", "FAC", facility_id)
    return facility_id, True


def _complaint_payload(
    report: ParsedComplaintReport,
    row: ParsedComplaintRow,
    batch_id: int | None,
    facility_id: int | None,
    default_user_id: int | None,
) -> dict[str, object]:
    priority = _infer_priority(row.source_category, row.description)
    created_at = row.received_at or report.latest_received_at or report.report_generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated_at = report.report_generated_at or created_at
    due_date = _derive_due_date(created_at, priority)
    resolved_at, closed_at = _resolved_dates(row.status, updated_at)
    unit_label = " ".join(part for part in [row.building_label, row.unit_number] if part).strip()
    return {
        "batch_id": batch_id,
        "site_name": report.site_name,
        "building_label": row.building_label,
        "unit_number": row.unit_number,
        "channel": "기타",
        "category_primary": _map_primary_category(row.source_category, row.description),
        "category_secondary": row.source_category,
        "facility_id": facility_id,
        "unit_label": unit_label,
        "location_detail": "",
        "requester_name": "",
        "requester_phone": row.requester_phone,
        "requester_email": "",
        "external_assignee_name": row.assignee_name,
        "source_type": SOURCE_TYPE,
        "source_reference": _complaint_source_reference(report.site_name, row.source_ticket_id),
        "title": " ".join(part for part in [row.building_label, row.unit_number, row.source_category] if part).strip()
        or row.source_category,
        "description": row.description,
        "priority": priority,
        "status": row.status,
        "response_due_at": due_date,
        "resolved_at": resolved_at,
        "closed_at": closed_at,
        "created_by": default_user_id,
        "updated_by": default_user_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _work_order_payload(
    report: ParsedComplaintReport,
    row: ParsedComplaintRow,
    batch_id: int | None,
    facility_id: int | None,
    complaint_id: int,
    default_user_id: int | None,
) -> dict[str, object]:
    priority = _infer_priority(row.source_category, row.description)
    created_at = row.received_at or report.latest_received_at or report.report_generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated_at = report.report_generated_at or created_at
    work_status = _map_work_order_status(row.status)
    return {
        "batch_id": batch_id,
        "complaint_id": complaint_id,
        "external_assignee_name": row.assignee_name,
        "source_type": SOURCE_TYPE,
        "source_reference": _work_order_source_reference(report.site_name, row.source_ticket_id),
        "category": row.source_category,
        "title": " ".join(part for part in [row.building_label, row.unit_number, row.source_category] if part).strip()
        or row.source_category,
        "facility_id": facility_id,
        "requester_name": "",
        "priority": priority,
        "status": work_status,
        "description": row.description,
        "due_date": _derive_due_date(created_at, priority),
        "completed_at": updated_at if work_status in {"완료", "종결"} else "",
        "created_by": default_user_id,
        "updated_by": default_user_id,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _record_import_update(
    conn: sqlite3.Connection,
    complaint_id: int,
    row: ParsedComplaintRow,
    actor_user_id: int | None,
    created_at: str,
    message_prefix: str = "PDF 이관",
) -> None:
    conn.execute(
        """
        INSERT INTO complaint_updates(
            complaint_id, update_type, status_from, status_to, message, is_public_note, created_by, created_at
        )
        VALUES (?, '분류', '', ?, ?, 0, ?, ?)
        """,
        (
            complaint_id,
            row.status,
            f"{message_prefix}: 원본 민원ID {row.source_ticket_id} / {row.source_category} / {row.description}",
            actor_user_id,
            created_at,
        ),
    )


def _record_work_order_update(
    conn: sqlite3.Connection,
    work_order_id: int,
    row: ParsedComplaintRow,
    actor_user_id: int | None,
    created_at: str,
    prefix: str,
) -> None:
    conn.execute(
        """
        INSERT INTO work_order_updates(work_order_id, update_type, body, actor_user_id, created_at)
        VALUES (?, '이관', ?, ?, ?)
        """,
        (
            work_order_id,
            f"{prefix}: 원본 민원ID {row.source_ticket_id} / {row.source_category} / {row.description}",
            actor_user_id,
            created_at,
        ),
    )


def _assign_code(conn: sqlite3.Connection, table: str, field: str, prefix: str, row_id: int) -> str:
    code = f"{prefix}-{row_id:04d}"
    conn.execute(f"UPDATE {table} SET {field} = ? WHERE id = ?", (code, row_id))
    return code


def _map_primary_category(source_category: str, description: str) -> str:
    joined = f"{source_category} {description}".strip()
    if any(keyword in joined for keyword in ("누수", "루버", "기계", "보일러", "연통")):
        return "기계"
    if any(keyword in joined for keyword in ("소방", "감지기", "경보")):
        return "소방"
    if any(keyword in joined for keyword in ("전기", "조명", "차단기")):
        return "전기"
    if any(keyword in joined for keyword in ("주차",)):
        return "주차"
    if any(
        keyword in joined
        for keyword in ("청소", "오염", "페인트", "방충망", "난간", "유리", "창문", "마감", "벽면", "바닥", "외벽", "파손", "하자")
    ):
        return "건축"
    return "민원"


def _infer_priority(source_category: str, description: str) -> str:
    joined = f"{source_category} {description}".strip()
    urgent_keywords = ("파손", "누수", "교체", "고장", "크랙", "빗물유입", "외부밧줄", "소실", "갈라짐")
    high_keywords = ("복합", "불량", "훼손", "찢어짐", "구멍", "하자", "미완료", "누락", "연락", "방문")
    if any(keyword in joined for keyword in urgent_keywords):
        return "긴급"
    if any(keyword in joined for keyword in high_keywords):
        return "높음"
    return "보통"


def _derive_due_date(created_at: str, priority: str) -> str:
    base_days = {"긴급": 0, "높음": 1, "보통": 3, "낮음": 5}
    base_dt = _parse_dt(created_at) or datetime.now()
    return (base_dt.date() + timedelta(days=base_days.get(priority, 3))).strftime("%Y-%m-%d")


def _resolved_dates(status: str, updated_at: str) -> tuple[str, str]:
    if status in {"종결", "취소"}:
        return updated_at, updated_at
    if status in {"처리완료", "회신완료"}:
        return updated_at, ""
    return "", ""


def _map_work_order_status(complaint_status: str) -> str:
    if complaint_status in {"종결", "취소"}:
        return "종결"
    if complaint_status in {"처리완료", "회신완료"}:
        return "완료"
    if complaint_status in {"배정완료", "처리중"}:
        return "진행중"
    if complaint_status == "보류":
        return "보류"
    return "접수"


def _parse_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
