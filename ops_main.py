from __future__ import annotations

import os
import json
import sqlite3
import uuid
from io import BytesIO
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ops import auth, db as ops_db
from ops.db import get_conn, init_db, migrate_legacy_tools
from ops.ui import (
    attachment_gallery,
    empty_state,
    esc,
    fmt_date,
    fmt_datetime,
    info_box,
    layout,
    metric_card,
    page_header,
    render_options,
    status_badge,
)

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = Path(os.getenv("OPS_UPLOAD_DIR", str(BASE_DIR / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_SECURE = str(os.getenv("OPS_COOKIE_SECURE", "")).strip().lower() in {"1", "true", "on", "yes"}
PWA_CACHE_VERSION = "facility-ops-v1"

app = FastAPI(title="시설 운영 시스템")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


def _bootstrap() -> None:
    init_db()
    auth.ensure_admin_user()
    conn = get_conn()
    admin_user = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (auth.DEFAULT_ADMIN_USERNAME,),
    ).fetchone()
    conn.close()
    migrate_legacy_tools(admin_user["id"] if admin_user else None)


_bootstrap()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_text() -> str:
    return date.today().strftime("%Y-%m-%d")


def _month_start_text() -> str:
    today = date.today()
    return today.replace(day=1).strftime("%Y-%m-%d")


COMPLAINT_STATUS_OPTIONS = ["접수", "분류완료", "배정완료", "처리중", "처리완료", "회신완료", "종결", "보류", "취소", "재오픈"]
COMPLAINT_PRIORITY_OPTIONS = ["낮음", "보통", "높음", "긴급"]
COMPLAINT_CHANNEL_OPTIONS = ["전화", "방문", "모바일", "카카오톡", "이메일", "기타"]
COMPLAINT_CATEGORY_OPTIONS = ["전기", "기계", "소방", "건축", "청소", "소음", "주차", "안전", "민원", "기타"]
COMPLAINT_UPDATE_TYPE_OPTIONS = ["내부메모", "상태변경", "회신", "재오픈", "분류", "배정", "완료보고", "만족도"]
COMPLAINT_RESOLVED_STATUSES = {"처리완료", "회신완료", "종결"}
COMPLAINT_CLOSED_STATUSES = {"종결", "취소"}
COMPLAINT_SLA_DAYS = {"긴급": 0, "높음": 1, "보통": 3, "낮음": 5}
COMPLAINT_REPEAT_WINDOW_DAYS = 90
COMPLAINTS_API_IMPORT_DEFAULT_BASE_URL = os.getenv(
    "LEGACY_COMPLAINTS_API_BASE_URL",
    "https://ka-facility-os.onrender.com",
).strip()
COMPLAINTS_API_IMPORT_DEFAULT_SITE = os.getenv("LEGACY_COMPLAINTS_SITE", "").strip()
COMPLAINTS_API_IMPORT_DEFAULT_SERVICE_ID = os.getenv(
    "LEGACY_COMPLAINTS_RENDER_SERVICE_ID",
    os.getenv("LEGACY_RENDER_SERVICE_ID", "srv-d6g57jbuibrs739g5mvg").strip(),
).strip()
COMPLAINTS_API_IMPORT_DEFAULT_TOKEN = os.getenv("LEGACY_ADMIN_TOKEN", "").strip()


def _parse_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _bool_from_form(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _with_flash(path: str, message: str = "", level: str = "info") -> RedirectResponse:
    if message:
        delimiter = "&" if "?" in path else "?"
        path = f"{path}{delimiter}{urlencode({'msg': message, 'level': level})}"
    return RedirectResponse(url=path, status_code=303)


def _flash_from_request(request: Request) -> tuple[str, str]:
    return request.query_params.get("msg", ""), request.query_params.get("level", "info")


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return JSONResponse(
        {
            "id": "/",
            "name": "시설 운영 시스템",
            "short_name": "시설운영",
            "description": "시설, 재고, 작업지시, 보고서를 한 화면에서 관리합니다.",
            "lang": "ko",
            "start_url": "/login?source=pwa",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#f2f4ef",
            "theme_color": "#1f5a55",
            "icons": [
                {
                    "src": "/assets/pwa/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/assets/pwa/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/assets/pwa/apple-touch-icon.png",
                    "sizes": "180x180",
                    "type": "image/png",
                    "purpose": "any",
                },
            ],
        },
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def pwa_service_worker():
    script = f"""
const CACHE_NAME = "{PWA_CACHE_VERSION}";
const APP_SHELL = [
  "/login",
  "/manifest.webmanifest",
  "/assets/pwa/icon-192.png",
  "/assets/pwa/icon-512.png",
  "/assets/pwa/apple-touch-icon.png"
];

self.addEventListener("install", (event) => {{
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {{
          if (key !== CACHE_NAME) {{
            return caches.delete(key);
          }}
          return Promise.resolve();
        }})
      )
    ).then(() => self.clients.claim())
  );
}});

self.addEventListener("fetch", (event) => {{
  const request = event.request;
  if (request.method !== "GET") {{
    return;
  }}

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {{
    return;
  }}

  if (request.mode === "navigate") {{
    event.respondWith(
      fetch(request)
        .then((response) => {{
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        }})
        .catch(async () => (await caches.match(request)) || (await caches.match("/login")))
    );
    return;
  }}

  if (url.pathname.startsWith("/assets/") || url.pathname === "/manifest.webmanifest") {{
    event.respondWith(
      caches.match(request).then((cached) => {{
        if (cached) {{
          return cached;
        }}
        return fetch(request).then((response) => {{
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        }});
      }})
    );
    return;
  }}

  event.respondWith(
    fetch(request)
      .then((response) => {{
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        return response;
      }})
      .catch(() => caches.match(request))
  );
}});
""".strip()
    return Response(content=script, media_type="application/javascript")


def _admin_bootstrap_message() -> str:
    if auth.should_show_bootstrap_password():
        return (
            f"최초 관리자 계정은 {auth.DEFAULT_ADMIN_USERNAME} / {auth.DEFAULT_ADMIN_PASSWORD} 입니다. "
            "접속 후 즉시 비밀번호를 변경해 주세요."
        )
    return (
        f"최초 관리자 계정은 {auth.DEFAULT_ADMIN_USERNAME} 입니다. "
        "초기 비밀번호는 배포 환경변수 `OPS_ADMIN_PASSWORD` 값으로 설정됩니다."
    )


def _auth_entry_links(active: str) -> str:
    links = [
        ("/login", "로그인", "login"),
        ("/register", "회원가입", "register"),
        ("/account/username", "아이디 찾기", "username"),
        ("/account/password", "비밀번호 재설정", "password"),
    ]
    items = []
    for href, label, key in links:
        tone = "primary" if key == active else "secondary"
        items.append(f"<a class='btn {tone}' href='{href}'>{esc(label)}</a>")
    return "<div class='row-actions' style='margin:16px 0; flex-wrap:wrap;'>" + "".join(items) + "</div>"


def _redirect_if_logged_in(request: Request):
    user = auth.get_user_by_session(request.cookies.get(auth.SESSION_COOKIE))
    if user:
        return RedirectResponse(url="/", status_code=303)
    return None


def _normalize_recovery_question(question: str) -> str:
    return str(question or "").strip()


def _password_error(password: str) -> str:
    if len(password.strip()) < 8:
        return "비밀번호는 8자 이상으로 입력해 주세요."
    return ""


def _mask_username(username: str) -> str:
    value = username.strip()
    if len(value) <= 2:
        return value[:1] + "*"
    return value[:2] + ("*" * (len(value) - 3)) + value[-1]


def _find_recovery_user(
    conn,
    *,
    full_name: str,
    phone: str,
    recovery_question: str,
    username: str | None = None,
):
    params = [full_name.strip(), auth.normalize_phone(phone), _normalize_recovery_question(recovery_question)]
    sql = """
        SELECT *
        FROM users
        WHERE full_name = ?
          AND phone = ?
          AND recovery_question = ?
    """
    if username is not None:
        sql += " AND username = ?"
        params.append(username.strip())
    return conn.execute(sql, params).fetchone()


def _guest_page(
    *,
    title: str,
    eyebrow: str,
    heading: str,
    description: str,
    active_tab: str,
    main_panel: str,
    side_content: str,
    flash_message: str = "",
    flash_level: str = "info",
):
    body = (
        page_header(eyebrow, heading, description)
        + _auth_entry_links(active_tab)
        + "<div class='layout-2'>"
        + main_panel
        + "<div class='stack'>"
        + side_content
        + "</div></div>"
    )
    return HTMLResponse(layout(title=title, body=body, flash_message=flash_message, flash_level=flash_level))


def _register_panel() -> str:
    return (
        "<section class='panel'><h2>회원가입</h2><p class='muted'>신규 계정은 기본적으로 조회전용으로 즉시 생성되며, 가입 직후 바로 로그인할 수 있습니다.</p>"
        "<form action='/register' method='post' class='stack' style='margin-top:16px;'>"
        "<div><label>아이디</label><input name='username' autocomplete='username' required></div>"
        "<div><label>이름</label><input name='full_name' required></div>"
        "<div><label>연락처</label><input name='phone' inputmode='tel' placeholder='숫자만 또는 010-0000-0000' required></div>"
        "<div><label>비밀번호</label><input name='password' type='password' autocomplete='new-password' required></div>"
        "<div><label>비밀번호 확인</label><input name='password_confirm' type='password' autocomplete='new-password' required></div>"
        "<div><label>복구 질문</label><input name='recovery_question' placeholder='예: 가장 기억에 남는 근무지는?' required></div>"
        "<div><label>복구 답변</label><input name='recovery_answer' type='password' autocomplete='off' required></div>"
        "<div class='row-actions'><button class='btn primary' type='submit'>회원가입</button></div>"
        "</form></section>"
    )


def _username_recovery_panel(result_html: str = "") -> str:
    return (
        "<section class='panel'><h2>아이디 찾기</h2><p class='muted'>이름, 연락처, 복구 질문과 답변이 일치하면 아이디를 확인할 수 있습니다.</p>"
        "<form action='/account/username' method='post' class='stack' style='margin-top:16px;'>"
        "<div><label>이름</label><input name='full_name' required></div>"
        "<div><label>연락처</label><input name='phone' inputmode='tel' required></div>"
        "<div><label>복구 질문</label><input name='recovery_question' required></div>"
        "<div><label>복구 답변</label><input name='recovery_answer' type='password' autocomplete='off' required></div>"
        "<div class='row-actions'><button class='btn primary' type='submit'>아이디 확인</button></div>"
        "</form>"
        + result_html
        + "</section>"
    )


def _password_reset_panel(result_html: str = "") -> str:
    return (
        "<section class='panel'><h2>비밀번호 재설정</h2><p class='muted'>아이디와 등록된 복구 정보를 확인한 뒤 새 비밀번호로 바로 바꿉니다.</p>"
        "<form action='/account/password' method='post' class='stack' style='margin-top:16px;'>"
        "<div><label>아이디</label><input name='username' autocomplete='username' required></div>"
        "<div><label>이름</label><input name='full_name' required></div>"
        "<div><label>연락처</label><input name='phone' inputmode='tel' required></div>"
        "<div><label>복구 질문</label><input name='recovery_question' required></div>"
        "<div><label>복구 답변</label><input name='recovery_answer' type='password' autocomplete='off' required></div>"
        "<div><label>새 비밀번호</label><input name='new_password' type='password' autocomplete='new-password' required></div>"
        "<div><label>새 비밀번호 확인</label><input name='new_password_confirm' type='password' autocomplete='new-password' required></div>"
        "<div class='row-actions'><button class='btn primary' type='submit'>비밀번호 변경</button></div>"
        "</form>"
        + result_html
        + "</section>"
    )


def _authorize(request: Request, permission: str | None = None):
    user = auth.get_user_by_session(request.cookies.get(auth.SESSION_COOKIE))
    if not user:
        return None, RedirectResponse(url="/login", status_code=303)
    if permission and not auth.has_permission(user["role"], permission):
        body = (
            page_header(
                "Access Control",
                "권한이 없습니다",
                "현재 계정의 역할로는 이 화면이나 작업에 접근할 수 없습니다.",
            )
            + info_box("필요 조치", "관리자 계정으로 로그인하거나 현재 사용자 역할을 조정해 주세요.")
        )
        return (
            None,
            HTMLResponse(
                layout(title="권한 없음", body=body, user=user, flash_message="접근 권한이 부족합니다.", flash_level="error"),
                status_code=403,
            ),
        )
    return user, None


def _upload_file(file: UploadFile) -> str | None:
    filename = (file.filename or "").strip()
    if not filename:
        return None
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / saved_name
    with open(target, "wb") as handle:
        handle.write(file.file.read())
    return saved_name


def _save_attachments(conn, entity_type: str, entity_id: int, files: Iterable[UploadFile], user_id: int | None) -> None:
    for file in files:
        saved_name = _upload_file(file)
        if not saved_name:
            continue
        conn.execute(
            """
            INSERT INTO attachments(entity_type, entity_id, file_path, original_name, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, saved_name, file.filename or saved_name, user_id, _now_text()),
        )


def _attachment_map(conn, entity_type: str, entity_ids: list[int]) -> dict[int, list]:
    if not entity_ids:
        return {}
    placeholders = ",".join(["?"] * len(entity_ids))
    rows = conn.execute(
        f"""
        SELECT *
        FROM attachments
        WHERE entity_type = ?
          AND entity_id IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        """,
        [entity_type, *entity_ids],
    ).fetchall()
    result: dict[int, list] = {entity_id: [] for entity_id in entity_ids}
    for row in rows:
        result.setdefault(row["entity_id"], []).append(row)
    return result


def _delete_attachments(conn, entity_type: str, entity_id: int) -> None:
    rows = conn.execute(
        "SELECT file_path FROM attachments WHERE entity_type = ? AND entity_id = ?",
        (entity_type, entity_id),
    ).fetchall()
    conn.execute(
        "DELETE FROM attachments WHERE entity_type = ? AND entity_id = ?",
        (entity_type, entity_id),
    )
    for row in rows:
        file_path = UPLOAD_DIR / row["file_path"]
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError:
            pass


def _compose_facility_location(row) -> str:
    parts = [part.strip() for part in [row["building"], row["floor"], row["zone"]] if (part or "").strip()]
    return " / ".join(parts) if parts else "-"


def _post_action_button(path: str, label: str, confirm_message: str | None = None, tone: str = "danger") -> str:
    confirm_attr = f" onclick=\"return confirm('{esc(confirm_message)}');\"" if confirm_message else ""
    return (
        f"<button class='btn {tone}' type='submit' formaction='{esc(path)}' formmethod='post' formnovalidate"
        f"{confirm_attr}>{esc(label)}</button>"
    )


def _facility_options(conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT id, facility_code, name FROM facilities ORDER BY name ASC, id ASC"
    ).fetchall()
    return [(str(row["id"]), f"{row['facility_code']} · {row['name']}") for row in rows]


def _user_options(conn, *, include_viewers: bool = True) -> list[tuple[str, str]]:
    sql = "SELECT id, full_name, role FROM users WHERE is_active = 1"
    params: list = []
    if not include_viewers:
        sql += " AND role != ?"
        params.append("viewer")
    sql += " ORDER BY role ASC, full_name ASC"
    rows = conn.execute(sql, params).fetchall()
    return [(str(row["id"]), f"{row['full_name']} ({auth.ROLE_LABELS.get(row['role'], row['role'])})") for row in rows]


def _complaint_options(conn) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT id, complaint_code, title, status
        FROM complaints
        ORDER BY CASE WHEN status IN ('종결', '취소') THEN 2 WHEN status = '회신완료' THEN 1 ELSE 0 END,
                 updated_at DESC, id DESC
        """
    ).fetchall()
    return [(str(row["id"]), f"{row['complaint_code']} · {row['title']} ({row['status']})") for row in rows]


def _badge(value: str, tone: str = "neutral") -> str:
    return f"<span class='badge {esc(tone)}'>{esc(value)}</span>"


def _date_value(value: str | None) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _complaint_due_default(priority: str, *, base_text: str = "") -> str:
    base_date = _date_value(base_text) or date.today()
    delta_days = COMPLAINT_SLA_DAYS.get(priority.strip(), COMPLAINT_SLA_DAYS["보통"])
    return (base_date + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def _normalize_complaint_due_date(raw_value: str, priority: str, existing_row=None) -> str:
    due_text = str(raw_value or "").strip()
    if due_text:
        return due_text
    if existing_row and str(existing_row["response_due_at"] or "").strip():
        return str(existing_row["response_due_at"]).strip()
    base_text = existing_row["created_at"] if existing_row else _today_text()
    return _complaint_due_default(priority, base_text=base_text)


def _complaint_sla_meta(row) -> tuple[str, str, str]:
    due_date = _date_value(row["response_due_at"] if row else "")
    if not due_date:
        return "미설정", "warn", "회신 목표일이 아직 없습니다."
    if row["status"] in {"회신완료", "종결", "취소"}:
        return "완료", "good", f"회신 목표일 {due_date.strftime('%Y-%m-%d')}"

    today = date.today()
    remaining_days = (due_date - today).days
    if remaining_days < 0:
        return "지연", "danger", f"{abs(remaining_days)}일 초과"
    if remaining_days == 0:
        return "오늘 마감", "warn", "오늘 중 회신이 필요합니다."
    if remaining_days == 1:
        return "임박", "warn", "1일 남았습니다."
    return "정상", "good", f"{remaining_days}일 남았습니다."


def _complaint_sla_badge(row) -> str:
    label, tone, _ = _complaint_sla_meta(row)
    return _badge(label, tone)


def _complaint_feedback_badge(rating: int | None) -> str:
    if not rating:
        return _badge("미평가", "neutral")
    tone = "good" if rating >= 4 else "warn" if rating == 3 else "danger"
    return _badge(f"{rating}점", tone)


def _complaint_repeat_candidates(conn, complaint_row, limit: int = 5):
    if not complaint_row:
        return []

    where = [
        "c.id != ?",
        "c.requester_phone != ''",
        "c.requester_phone = ?",
        f"c.created_at >= datetime('now', '-{COMPLAINT_REPEAT_WINDOW_DAYS} days')",
    ]
    params: list = [complaint_row["id"], complaint_row["requester_phone"]]

    match_terms = []
    if complaint_row["facility_id"]:
        match_terms.append("c.facility_id = ?")
        params.append(complaint_row["facility_id"])
    if str(complaint_row["unit_label"] or "").strip():
        match_terms.append("c.unit_label = ?")
        params.append(str(complaint_row["unit_label"]).strip())
    if str(complaint_row["location_detail"] or "").strip():
        match_terms.append("c.location_detail = ?")
        params.append(str(complaint_row["location_detail"]).strip())
    if str(complaint_row["category_primary"] or "").strip():
        match_terms.append("c.category_primary = ?")
        params.append(str(complaint_row["category_primary"]).strip())

    if not match_terms:
        return []

    where.append("(" + " OR ".join(match_terms) + ")")
    sql = f"""
        SELECT c.*, f.name AS facility_name, u.full_name AS assignee_name
        FROM complaints c
        LEFT JOIN facilities f ON f.id = c.facility_id
        LEFT JOIN users u ON u.id = c.assignee_user_id
        WHERE {' AND '.join(where)}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _complaint_template_rows(conn, category_primary: str):
    category = str(category_primary or "").strip()
    return conn.execute(
        """
        SELECT *
        FROM complaint_response_templates
        WHERE is_active = 1
          AND (category_primary = '' OR category_primary = ?)
        ORDER BY sort_order ASC, name ASC, id ASC
        """,
        (category,),
    ).fetchall()


def _complaint_filter_sql(q: str = "", status: str = "", channel: str = "", priority: str = "") -> tuple[str, list]:
    where = []
    params: list = []
    if q:
        where.append(
            "(c.complaint_code LIKE ? OR c.title LIKE ? OR c.description LIKE ? OR c.requester_name LIKE ? OR c.requester_phone LIKE ? OR c.unit_label LIKE ? OR c.location_detail LIKE ?)"
        )
        params.extend([f"%{q}%"] * 7)
    if status:
        where.append("c.status = ?")
        params.append(status)
    if channel:
        where.append("c.channel = ?")
        params.append(channel)
    if priority:
        where.append("c.priority = ?")
        params.append(priority)
    return ("WHERE " + " AND ".join(where) if where else ""), params


def _fetch_complaint_rows(conn, q: str = "", status: str = "", channel: str = "", priority: str = ""):
    where_sql, params = _complaint_filter_sql(q, status, channel, priority)
    return conn.execute(
        f"""
        SELECT c.*, f.name AS facility_name, u.full_name AS assignee_name, COUNT(DISTINCT w.id) AS work_count,
               cf.rating AS feedback_rating, cf.follow_up_at AS feedback_follow_up_at,
               (
                 SELECT COUNT(*)
                 FROM complaints c2
                 WHERE c2.id != c.id
                   AND c.requester_phone != ''
                   AND c2.requester_phone = c.requester_phone
                   AND c2.created_at >= datetime('now', '-{COMPLAINT_REPEAT_WINDOW_DAYS} days')
                   AND (
                     (c.facility_id IS NOT NULL AND c2.facility_id = c.facility_id)
                     OR (c.unit_label != '' AND c2.unit_label = c.unit_label)
                     OR (c.location_detail != '' AND c2.location_detail = c.location_detail)
                     OR (c.category_primary != '' AND c2.category_primary = c.category_primary)
                   )
               ) AS repeat_count
        FROM complaints c
        LEFT JOIN facilities f ON f.id = c.facility_id
        LEFT JOIN users u ON u.id = c.assignee_user_id
        LEFT JOIN work_orders w ON w.complaint_id = c.id
        LEFT JOIN complaint_feedback cf ON cf.complaint_id = c.id
        {where_sql}
        GROUP BY c.id
        ORDER BY CASE c.priority WHEN '긴급' THEN 1 WHEN '높음' THEN 2 WHEN '보통' THEN 3 ELSE 4 END,
                 c.updated_at DESC, c.id DESC
        """,
        params,
    ).fetchall()


def _build_complaints_pdf(rows, *, q: str = "", status: str = "", channel: str = "", priority: str = "") -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("PDF generator dependency not installed") from exc

    font_name = "Helvetica"
    for candidate in ("HYGothic-Medium", "HYSMyeongJo-Medium"):
        try:
            if candidate not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(candidate))
            font_name = candidate
            break
        except Exception:
            continue

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ComplaintPdfTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=22,
        leading=28,
        textColor=colors.HexColor("#1f5a55"),
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    subtitle_style = ParagraphStyle(
        "ComplaintPdfSubtitle",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#3c4a57"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "ComplaintPdfBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#17212b"),
    )
    small_style = ParagraphStyle(
        "ComplaintPdfSmall",
        parent=body_style,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#60707d"),
    )
    cell_style = ParagraphStyle(
        "ComplaintPdfCell",
        parent=body_style,
        fontSize=8.5,
        leading=10,
    )
    header_style = ParagraphStyle(
        "ComplaintPdfHeader",
        parent=body_style,
        fontSize=8.5,
        leading=10,
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    def para(text: str, style=body_style) -> Paragraph:
        return Paragraph(esc(text or "-").replace("\n", "<br/>"), style)

    result_count = len(rows)
    location_values = sorted({str(row["location_detail"] or "").strip() for row in rows if str(row["location_detail"] or "").strip()})
    site_label = location_values[0] if len(location_values) == 1 else ("복수 현장" if location_values else "전체")
    status_counts = Counter(str(row["status"] or "미분류") for row in rows)
    priority_counts = Counter(str(row["priority"] or "보통") for row in rows)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    filter_summary = " / ".join(
        [
            f"검색어 {q}" if q else "검색어 전체",
            f"상태 {status}" if status else "상태 전체",
            f"채널 {channel}" if channel else "채널 전체",
            f"우선도 {priority}" if priority else "우선도 전체",
        ]
    )
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="민원 처리 현황 보고",
        author="시설 운영 시스템",
    )
    story = [
        Paragraph("시설 운영 시스템", small_style),
        Paragraph(f"{site_label} 민원 처리 현황 보고", title_style),
        Paragraph(
            f"생성 시각 {generated_at} · 출력 대상 {result_count}건 · {filter_summary}",
            subtitle_style,
        ),
    ]

    summary_table = Table(
        [
            [para("현장", body_style), para(site_label, body_style), para("현재 결과", body_style), para(f"{result_count}건", body_style)],
            [para("상태 요약", body_style), para(", ".join(f"{key} {value}건" for key, value in status_counts.items()) or "-", small_style), para("우선도 요약", body_style), para(", ".join(f"{key} {value}건" for key, value in priority_counts.items()) or "-", small_style)],
        ],
        colWidths=[22 * mm, 86 * mm, 22 * mm, 124 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f3ea")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#d8dfd5")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8dfd5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([summary_table, Spacer(1, 6 * mm)])

    table_rows = [
        [
            Paragraph("번호", header_style),
            Paragraph("민원", header_style),
            Paragraph("위치", header_style),
            Paragraph("채널/분류", header_style),
            Paragraph("우선/상태", header_style),
            Paragraph("담당/기한", header_style),
            Paragraph("연결작업", header_style),
        ]
    ]
    for row in rows:
        title_block = f"{row['title']}\n{row['requester_name'] or '-'} / {row['requester_phone'] or '-'}"
        location_block = row["unit_label"] or row["location_detail"] or "-"
        channel_block = f"{row['channel']}\n{row['category_primary'] or '-'}"
        status_block = f"{row['priority']} / {row['status']}\nSLA {_complaint_sla_meta(row)[0]}"
        assignee_block = f"{row['assignee_name'] or '미배정'}\n회신 목표 {fmt_date(row['response_due_at'])}"
        table_rows.append(
            [
                para(str(row["complaint_code"]), cell_style),
                para(title_block, cell_style),
                para(location_block, cell_style),
                para(channel_block, cell_style),
                para(status_block, cell_style),
                para(assignee_block, cell_style),
                para(f"{row['work_count']}건", cell_style),
            ]
        )

    if result_count == 0:
        table_rows.append(
            [
                para("데이터 없음", cell_style),
                para("조건에 맞는 민원이 없습니다.", cell_style),
                para("-", cell_style),
                para("-", cell_style),
                para("-", cell_style),
                para("-", cell_style),
                para("-", cell_style),
            ]
        )

    complaints_table = Table(
        table_rows,
        repeatRows=1,
        colWidths=[28 * mm, 68 * mm, 38 * mm, 33 * mm, 31 * mm, 38 * mm, 18 * mm],
    )
    complaints_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f5a55")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#d8dfd5")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8faf7")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(complaints_table)

    def draw_page(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#60707d"))
        canvas.drawString(document.leftMargin, 8 * mm, "시설 운영 시스템 민원 PDF")
        canvas.drawRightString(document.pagesize[0] - document.rightMargin, 8 * mm, f"{canvas.getPageNumber()}p")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buf.getvalue()


DB_TABLE_LABELS = {
    "users": "사용자",
    "sessions": "세션",
    "facilities": "시설",
    "inventory_items": "재고 항목",
    "inventory_transactions": "재고 수불 이력",
    "complaints": "민원",
    "complaint_updates": "민원 업데이트",
    "complaint_feedback": "민원 만족도",
    "complaint_response_templates": "민원 회신 템플릿",
    "work_orders": "작업지시",
    "work_order_updates": "작업 업데이트",
    "attachments": "첨부파일",
}
DB_MANAGED_TABLES = list(DB_TABLE_LABELS.keys())
DB_TEXTAREA_COLUMNS = {"note", "body", "description", "reason", "specification", "message"}
DB_CODE_FIELDS = {
    "facilities": ("facility_code", "FAC"),
    "inventory_items": ("item_code", "INV"),
    "complaints": ("complaint_code", "CP"),
    "complaint_response_templates": ("template_code", "CT"),
    "work_orders": ("work_code", "WO"),
}
_DB_OMIT = object()


def _db_safe_table(table: str) -> str:
    return table if table in DB_TABLE_LABELS else DB_MANAGED_TABLES[0]


def _db_columns(conn, table: str):
    return conn.execute(f"PRAGMA table_info({_db_safe_table(table)})").fetchall()


def _db_default_text(default_value) -> str:
    if default_value is None:
        return ""
    text = str(default_value)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1]
    return text


def _db_convert_value(column, raw_value, *, for_create: bool):
    value = "" if raw_value is None else str(raw_value)
    col_type = (column["type"] or "").upper()
    default_value = column["dflt_value"]

    if value == "":
        if for_create and default_value is not None:
            return _DB_OMIT
        if "INT" in col_type:
            if column["notnull"]:
                raise ValueError(f"{column['name']} 값이 필요합니다.")
            return None
        if column["notnull"] and default_value is None:
            raise ValueError(f"{column['name']} 값이 필요합니다.")
        if not column["notnull"]:
            return None
        return ""

    if "INT" in col_type:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{column['name']} 는 숫자여야 합니다.") from exc
    return value


def _db_form_field(column, value) -> str:
    name = column["name"]
    col_type = (column["type"] or "").upper()
    default_note = ""
    if column["dflt_value"] is not None:
        default_note = f"<div class='muted'>빈값이면 기본값: {esc(_db_default_text(column['dflt_value']))}</div>"
    elif any(name == code_field for code_field, _ in DB_CODE_FIELDS.values()):
        default_note = "<div class='muted'>빈값이면 저장 시 자동 채번됩니다.</div>"

    label = f"{esc(name)} <span class='muted'>{esc(column['type'] or 'TEXT')}</span>"
    value_text = "" if value is None else str(value)
    if name in DB_TEXTAREA_COLUMNS or len(value_text) > 120:
        control = f"<textarea name='col_{esc(name)}' rows='4'>{esc(value_text)}</textarea>"
    else:
        input_type = "number" if "INT" in col_type else "text"
        control = f"<input name='col_{esc(name)}' type='{input_type}' value='{esc(value_text)}'>"
    return f"<div><label>{label}</label>{control}{default_note}</div>"


def _db_render_form(table: str, columns, edit_row) -> str:
    fields = []
    if edit_row:
        fields.append(f"<div><label>id</label><input value='{esc(edit_row['id'])}' disabled></div>")
    for column in columns:
        if column["pk"]:
            continue
        current_value = edit_row[column["name"]] if edit_row else ""
        fields.append(_db_form_field(column, current_value))
    submit_label = "행 수정" if edit_row else "행 등록"
    helper = "raw DB 수준에서 직접 수정합니다. 제약조건과 외래키를 함께 고려해야 합니다."
    return (
        "<section class='panel'><h2>행 등록 / 수정</h2>"
        f"<p class='muted'>{esc(helper)}</p>"
        "<form action='/admin/database/save' method='post' class='stack'>"
        f"<input type='hidden' name='table' value='{esc(table)}'>"
        f"<input type='hidden' name='row_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
        + "".join(fields)
        + "<div class='row-actions'>"
        + f"<button class='btn primary' type='submit'>{esc(submit_label)}</button>"
        + f"<a class='btn secondary' href='/admin/database?table={esc(table)}'>새 행</a>"
        + "</div></form></section>"
    )


def _db_cell_preview(value) -> str:
    text = "" if value is None else str(value)
    if len(text) > 48:
        text = text[:45] + "..."
    return esc(text or "-")


def _db_render_rows(table: str, columns, rows) -> str:
    headers = "".join(f"<th>{esc(column['name'])}</th>" for column in columns)
    if not rows:
        return "<section class='panel'><h2>행 목록</h2>" + empty_state("표시할 행이 없습니다.") + "</section>"

    form_id = f"db-bulk-form-{table}"
    row_html = []
    for row in rows:
        checkbox = (
            f"<input type='checkbox' name='row_ids' value='{esc(row['id'])}' "
            f"data-db-row='1' style='width:18px;height:18px;'>"
        )
        cells = "".join(
            f"<td data-label='{esc(column['name'])}'>{_db_cell_preview(row[column['name']])}</td>"
            for column in columns
        )
        actions = (
            f"<a class='btn secondary' href='/admin/database?table={esc(table)}&edit={row['id']}'>수정</a>"
            + f"<button class='btn warn' type='submit' name='row_id' value='{esc(row['id'])}' "
            + "formaction='/admin/database/delete' formmethod='post' formnovalidate "
            + "onclick=\"return confirm('이 행을 삭제하시겠습니까?');\">삭제</button>"
        )
        row_html.append(
            f"<tr><td data-label='선택' class='db-check-cell'>{checkbox}</td>{cells}<td data-label='관리' class='db-action-cell'>{actions}</td></tr>"
        )

    return (
        "<section class='panel'><h2>행 목록</h2><div style='overflow:auto;'>"
        f"<form id='{esc(form_id)}' method='post' action='/admin/database/delete-selected' class='stack'>"
        f"<input type='hidden' name='table' value='{esc(table)}'>"
        + "<div class='row-actions' style='margin-bottom:12px;'>"
        + "<button class='btn warn' type='submit' onclick=\"return confirm('선택한 행을 한꺼번에 삭제하시겠습니까?');\">선택 삭제</button>"
        + "<span class='muted'>체크한 행만 삭제합니다. 현재 로그인한 사용자 행은 제외됩니다.</span>"
        + "</div>"
        + "<table class='responsive-table db-table'><thead><tr>"
        + "<th><input type='checkbox' data-db-select-all='1' style='width:18px;height:18px;'></th>"
        + headers
        + "<th>관리</th></tr></thead><tbody>"
        + "".join(row_html)
        + "</tbody></table>"
        + "<div class='row-actions' style='margin-top:12px;'>"
        + "<button class='btn warn' type='submit' onclick=\"return confirm('선택한 행을 한꺼번에 삭제하시겠습니까?');\">선택 삭제</button>"
        + "</div>"
        + "</form>"
        + "<script>(function(){const form=document.getElementById('"
        + esc(form_id)
        + "');if(!form)return;const master=form.querySelector('[data-db-select-all]');if(!master)return;master.addEventListener('change',()=>{form.querySelectorAll('[data-db-row]').forEach((box)=>{box.checked=master.checked;});});form.querySelectorAll('[data-db-row]').forEach((box)=>{box.addEventListener('change',()=>{const items=[...form.querySelectorAll('[data-db-row]')];master.checked=items.length>0&&items.every((item)=>item.checked);});});})();</script>"
        + "</div></section>"
    )


def _db_delete_rows(conn, table: str, row_ids: list[int], current_user_id: int) -> tuple[int, int, int, list[str]]:
    unique_ids = sorted({row_id for row_id in row_ids if row_id > 0})
    if not unique_ids:
        return 0, 0, 0, []

    placeholders = ",".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders})",
        unique_ids,
    ).fetchall()
    found_ids = {int(row["id"]) for row in rows}
    missing_count = len(unique_ids) - len(found_ids)

    delete_ids: list[int] = []
    file_paths: list[str] = []
    blocked_count = 0
    for row in rows:
        row_id = int(row["id"])
        if table == "users" and row_id == current_user_id:
            blocked_count += 1
            continue
        delete_ids.append(row_id)
        if table == "attachments" and row["file_path"]:
            file_paths.append(str(row["file_path"]))

    if delete_ids:
        delete_placeholders = ",".join("?" for _ in delete_ids)
        conn.execute(
            f"DELETE FROM {table} WHERE id IN ({delete_placeholders})",
            delete_ids,
        )
    return len(delete_ids), blocked_count, missing_count, file_paths


def _db_table_cards(conn, selected_table: str) -> str:
    cards = []
    for table in DB_MANAGED_TABLES:
        count = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
        tone = "primary" if table == selected_table else "secondary"
        cards.append(
            "<div class='panel'>"
            f"<div class='split'><strong>{esc(DB_TABLE_LABELS[table])}</strong><span class='muted'>{esc(table)}</span></div>"
            f"<div class='metric-value' style='font-size:28px; margin-top:10px;'>{count}</div>"
            f"<div class='row-actions' style='margin-top:12px;'><a class='btn {tone}' href='/admin/database?table={esc(table)}'>열기</a></div>"
            "</div>"
        )
    return "".join(cards)


def _db_backup_snapshot(prefix: str) -> Path:
    backup_dir = ops_db.DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    with sqlite3.connect(str(ops_db.DB_PATH)) as source_conn, sqlite3.connect(str(backup_path)) as backup_conn:
        source_conn.backup(backup_conn)
    return backup_path


def _complaints_api_import_panel() -> str:
    source_hint = (
        "소스 관리자 토큰을 비워 두면 Render 서비스에서 ADMIN_TOKEN을 읽어옵니다."
        if not COMPLAINTS_API_IMPORT_DEFAULT_TOKEN
        else "환경변수 LEGACY_ADMIN_TOKEN을 기본 사용합니다."
    )
    return (
        "<section class='panel'><h2>세대 민원 API 이관</h2>"
        "<p class='muted'>ka-facility-os의 세대 민원 API(cases/events/messages)를 현재 민원 구조로 변환합니다.</p>"
        f"{info_box('기본 소스', source_hint)}"
        f"{info_box('기본 동작', '민원 본체와 처리 이력을 우선 이관합니다. 작업지시는 필요할 때만 생성하는 편이 안전합니다.')}"
        "<form action='/admin/complaints-api-import' method='post' class='stack'>"
        f"<div><label>소스 서비스 URL</label><input name='base_url' value='{esc(COMPLAINTS_API_IMPORT_DEFAULT_BASE_URL)}' placeholder='https://ka-facility-os.onrender.com'></div>"
        f"<div><label>site</label><input name='site' value='{esc(COMPLAINTS_API_IMPORT_DEFAULT_SITE)}' placeholder='예: 연산더샵'></div>"
        "<div><label>소스 X-Admin-Token (선택)</label><input name='admin_token' type='password' value='' autocomplete='off' placeholder='비워 두면 기본 소스를 사용합니다.'></div>"
        f"<div><label>Render 서비스 ID</label><input name='render_service_id' value='{esc(COMPLAINTS_API_IMPORT_DEFAULT_SERVICE_ID)}' placeholder='srv-...'></div>"
        "<label style='display:flex;align-items:center;gap:8px;'><input name='update_existing' type='checkbox' value='1' style='width:auto;'>이미 이관된 API-* 레코드도 덮어쓰기</label>"
        "<label style='display:flex;align-items:center;gap:8px;'><input name='import_work_orders' type='checkbox' value='1' style='width:auto;'>민원과 함께 작업지시도 생성</label>"
        "<div class='row-actions'>"
        + "<button class='btn secondary' type='submit' name='action' value='inspect'>원본 확인</button>"
        + "<button class='btn secondary' type='submit' name='action' value='dry_run'>드라이런</button>"
        + "<button class='btn warn' type='submit' name='action' value='apply' onclick=\"return confirm('현재 운영 DB에 세대 민원 데이터를 실제 반영합니다. 계속하시겠습니까?');\">실제 이관</button>"
        + "</div></form></section>"
    )


def _complaints_api_import_message(action: str, summary: dict) -> str:
    counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
    if action == "inspect":
        return (
            f"원본 확인: 민원 {summary.get('cases', 0)}건, 이력 {summary.get('events', 0)}건, "
            f"문자 {summary.get('messages', 0)}건, 첨부 {summary.get('attachments', 0)}건"
        )
    if action == "apply":
        return (
            f"이관 완료: 민원 {counts.get('complaints_inserted', 0)}건, "
            f"이력 {counts.get('updates_inserted', 0)}건, "
            f"문자이력 {counts.get('message_updates_inserted', 0)}건, "
            f"작업지시 {counts.get('work_orders_inserted', 0)}건"
        )
    return (
        f"드라이런: 민원 {counts.get('complaints_inserted', 0)}건, "
        f"이력 {counts.get('updates_inserted', 0)}건, "
        f"문자이력 {counts.get('message_updates_inserted', 0)}건, "
        f"작업지시 {counts.get('work_orders_inserted', 0)}건 예정"
    )


def _can_manage_work_order(user, row) -> bool:
    if auth.has_permission(user["role"], "work_orders:edit"):
        return True
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"])


def _can_update_work_order(user, row) -> bool:
    if auth.has_permission(user["role"], "work_orders:edit"):
        return True
    if not auth.has_permission(user["role"], "work_orders:update"):
        return False
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"]) or int(row["assignee_user_id"] or 0) == int(user["id"])


def _can_delete_work_order(user, row) -> bool:
    if auth.has_permission(user["role"], "work_orders:edit"):
        return True
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"])


def _can_manage_complaint(user, row) -> bool:
    if auth.has_permission(user["role"], "complaints:edit"):
        return True
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"])


def _can_update_complaint(user, row) -> bool:
    if auth.has_permission(user["role"], "complaints:edit"):
        return True
    if not auth.has_permission(user["role"], "complaints:update"):
        return False
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"]) or int(row["assignee_user_id"] or 0) == int(user["id"])


def _can_delete_complaint(user, row) -> bool:
    if auth.has_permission(user["role"], "complaints:edit"):
        return True
    if not row:
        return False
    return int(row["created_by"] or 0) == int(user["id"])


def _complaint_timestamps(status: str, existing_row=None) -> tuple[str, str]:
    resolved_at = existing_row["resolved_at"] if existing_row else ""
    closed_at = existing_row["closed_at"] if existing_row else ""

    if status in COMPLAINT_RESOLVED_STATUSES:
        if not resolved_at:
            resolved_at = _now_text()
    else:
        resolved_at = ""

    if status in COMPLAINT_CLOSED_STATUSES:
        if not closed_at:
            closed_at = _now_text()
    else:
        closed_at = ""

    return resolved_at, closed_at


def _record_complaint_update(
    conn,
    complaint_id: int,
    update_type: str,
    message: str,
    actor_user_id: int | None,
    *,
    status_from: str = "",
    status_to: str = "",
    is_public_note: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO complaint_updates(complaint_id, update_type, status_from, status_to, message, is_public_note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (complaint_id, update_type.strip(), status_from.strip(), status_to.strip(), message.strip(), is_public_note, actor_user_id, _now_text()),
    )


def _sync_complaint_for_work_order(conn, complaint_id: int | None, work_order_id: int, work_code: str, actor_user_id: int | None, assignee_user_id: int | None) -> None:
    if not complaint_id:
        return
    complaint = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if not complaint:
        return

    next_status = complaint["status"]
    if complaint["status"] in {"접수", "분류완료", "재오픈"}:
        next_status = "배정완료"
    resolved_at, closed_at = _complaint_timestamps(next_status, complaint)
    next_assignee = assignee_user_id or complaint["assignee_user_id"]
    conn.execute(
        """
        UPDATE complaints
        SET status = ?, assignee_user_id = ?, resolved_at = ?, closed_at = ?, updated_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (next_status, next_assignee, resolved_at, closed_at, actor_user_id, _now_text(), complaint_id),
    )
    status_from = complaint["status"] if complaint["status"] != next_status else ""
    status_to = next_status if status_from else ""
    _record_complaint_update(
        conn,
        complaint_id,
        "작업연결",
        f"{work_code} 작업지시와 연결되었습니다.",
        actor_user_id,
        status_from=status_from,
        status_to=status_to,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    flash_message, flash_level = _flash_from_request(request)
    conn = get_conn()
    user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    conn.close()
    hint = ""
    if user_count == 1:
        if auth.should_show_bootstrap_password():
            hint = (
                "<div class='pill-row'>"
                f"<span class='pill'>초기 관리자: {esc(auth.DEFAULT_ADMIN_USERNAME)}</span>"
                f"<span class='pill'>초기 비밀번호: {esc(auth.DEFAULT_ADMIN_PASSWORD)}</span>"
                "</div>"
            )
        else:
            hint = (
                "<div class='pill-row'>"
                f"<span class='pill'>초기 관리자: {esc(auth.DEFAULT_ADMIN_USERNAME)}</span>"
                "<span class='pill'>초기 비밀번호: Render 환경변수 사용</span>"
                "</div>"
            )

    body = (
        page_header(
            "Secure Entry",
            "시설 운영 시스템 로그인",
            "공유 운영 환경을 위해 계정과 역할 기반으로 접근을 제어합니다.",
        )
        + _auth_entry_links("login")
        + "<div class='layout-2'>"
        + "<section class='panel'>"
        + "<h2>로그인</h2><p class='muted'>현장 팀원별 계정으로 접속하세요.</p>"
        + hint
        + """
          <form action="/login" method="post" class="stack" style="margin-top:16px;">
            <div><label>아이디</label><input name="username" autocomplete="username" required></div>
            <div><label>비밀번호</label><input name="password" type="password" autocomplete="current-password" required></div>
            <div class="row-actions">
              <button class="btn primary" type="submit">로그인</button>
              <a class="btn secondary" href="/register">회원가입</a>
            </div>
            <div class="muted" style="margin-top:6px;">
              <a href="/account/username">아이디 찾기</a>
              &nbsp;·&nbsp;
              <a href="/account/password">비밀번호 재설정</a>
            </div>
          </form>
        """
        + "</section>"
        + "<div class='stack'>"
        + info_box("계정 지원", "신규 사용자는 회원가입 직후 바로 로그인할 수 있고, 아이디 찾기와 비밀번호 재설정은 등록된 연락처와 복구질문을 기준으로 처리됩니다.")
        + info_box("권한 모델", "관리자, 운영관리, 작업자, 조회전용 역할에 따라 화면 접근과 수정 범위가 나뉩니다.")
        + info_box("공유 운영", "시설 상태, 재고 변동, 작업 진행 내역은 사용자 계정 기준으로 남아 이후 감사와 보고서 자동화에 활용됩니다.")
        + "</div></div>"
    )
    return HTMLResponse(layout(title="로그인", body=body, flash_message=flash_message, flash_level=flash_level))


@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username.strip(),),
    ).fetchone()
    conn.close()
    if not user or not user["is_active"] or not auth.verify_password(password, user["password_hash"]):
        return _with_flash("/login", "아이디 또는 비밀번호가 올바르지 않습니다.", "error")

    token = auth.create_session(user["id"])
    response = _with_flash("/", "로그인되었습니다.", "ok")
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
    )
    return response


@app.post("/logout")
def logout(request: Request):
    auth.invalidate_session(request.cookies.get(auth.SESSION_COOKIE))
    response = _with_flash("/login", "로그아웃되었습니다.", "info")
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    flash_message, flash_level = _flash_from_request(request)
    return _guest_page(
        title="회원가입",
        eyebrow="Account Onboarding",
        heading="회원가입",
        description="현장 팀원 계정은 조회전용으로 즉시 생성하고, 필요 시 관리자가 역할을 상향합니다.",
        active_tab="register",
        main_panel=_register_panel(),
        side_content=(
            info_box("기본 권한", "회원가입 계정은 기본적으로 조회전용으로 활성화됩니다. 추가 권한이 필요하면 관리자가 역할을 조정합니다.")
            + info_box("복구 정보", "아이디 찾기와 비밀번호 재설정은 가입 시 등록한 연락처와 복구 질문/답변을 기준으로 처리됩니다.")
        ),
        flash_message=flash_message,
        flash_level=flash_level,
    )


@app.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    recovery_question: str = Form(...),
    recovery_answer: str = Form(...),
):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    username_v = username.strip()
    full_name_v = full_name.strip()
    phone_v = auth.normalize_phone(phone)
    recovery_question_v = _normalize_recovery_question(recovery_question)
    recovery_answer_v = recovery_answer.strip()
    password_rule_error = _password_error(password)

    if not username_v or not full_name_v or not phone_v or not recovery_question_v or not recovery_answer_v:
        return _with_flash("/register", "모든 항목을 입력해 주세요.", "error")
    if password_rule_error:
        return _with_flash("/register", password_rule_error, "error")
    if password != password_confirm:
        return _with_flash("/register", "비밀번호 확인이 일치하지 않습니다.", "error")

    conn = get_conn()
    try:
        username_exists = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username_v,),
        ).fetchone()
        duplicate_person = conn.execute(
            "SELECT id FROM users WHERE full_name = ? AND phone = ?",
            (full_name_v, phone_v),
        ).fetchone()
        if username_exists:
            conn.close()
            return _with_flash("/register", "이미 사용 중인 아이디입니다.", "error")
        if duplicate_person:
            conn.close()
            return _with_flash("/register", "같은 이름과 연락처로 등록된 계정이 이미 있습니다.", "error")

        conn.execute(
            """
            INSERT INTO users(
                username, full_name, role, password_hash, is_active, phone,
                recovery_question, recovery_answer_hash, created_at, updated_at
            )
            VALUES (?, ?, 'viewer', ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                username_v,
                full_name_v,
                auth.hash_password(password.strip()),
                phone_v,
                recovery_question_v,
                auth.hash_recovery_answer(recovery_answer_v),
                _now_text(),
                _now_text(),
            ),
        )
        conn.commit()
        conn.close()
        return _with_flash("/login", "회원가입이 완료되었습니다. 바로 로그인할 수 있습니다.", "ok")
    except Exception as exc:
        conn.close()
        return _with_flash("/register", f"회원가입 요청에 실패했습니다: {exc}", "error")


@app.get("/account/username", response_class=HTMLResponse)
def account_username_page(request: Request):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    flash_message, flash_level = _flash_from_request(request)
    return _guest_page(
        title="아이디 찾기",
        eyebrow="Account Recovery",
        heading="아이디 찾기",
        description="등록된 본인확인 정보가 일치하면 아이디를 확인할 수 있습니다.",
        active_tab="username",
        main_panel=_username_recovery_panel(),
        side_content=(
            info_box("입력 기준", "회원가입 또는 관리자 등록 시 저장한 연락처와 복구 질문/답변을 그대로 입력해야 합니다.")
            + info_box("표시 방식", "보안을 위해 찾은 아이디는 일부만 마스킹해 보여주고, 상태도 함께 안내합니다.")
        ),
        flash_message=flash_message,
        flash_level=flash_level,
    )


@app.post("/account/username", response_class=HTMLResponse)
def account_username_submit(
    request: Request,
    full_name: str = Form(...),
    phone: str = Form(...),
    recovery_question: str = Form(...),
    recovery_answer: str = Form(...),
):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    conn = get_conn()
    user = _find_recovery_user(
        conn,
        full_name=full_name,
        phone=phone,
        recovery_question=recovery_question,
    )
    conn.close()

    if not user or not user["recovery_answer_hash"] or not auth.verify_recovery_answer(recovery_answer, user["recovery_answer_hash"]):
        return _guest_page(
            title="아이디 찾기",
            eyebrow="Account Recovery",
            heading="아이디 찾기",
            description="등록된 본인확인 정보가 일치하면 아이디를 확인할 수 있습니다.",
            active_tab="username",
            main_panel=_username_recovery_panel(),
            side_content=(
                info_box("입력 기준", "회원가입 또는 관리자 등록 시 저장한 연락처와 복구 질문/답변을 그대로 입력해야 합니다.")
                + info_box("도움말", "복구 정보가 등록되지 않은 계정은 관리자가 연락처와 복구 질문을 먼저 저장해야 합니다.")
            ),
            flash_message="입력한 복구 정보와 일치하는 계정을 찾지 못했습니다.",
            flash_level="error",
        )

    status_text = "활성" if user["is_active"] else "비활성"
    result_html = (
        "<section class='panel' style='margin-top:16px;'>"
        "<h2>조회 결과</h2>"
        f"<div class='muted'>아이디: <strong>{esc(_mask_username(user['username']))}</strong></div>"
        f"<div class='muted' style='margin-top:8px;'>계정 상태: {esc(status_text)}</div>"
        "</section>"
    )
    return _guest_page(
        title="아이디 찾기",
        eyebrow="Account Recovery",
        heading="아이디 찾기",
        description="등록된 본인확인 정보가 일치하면 아이디를 확인할 수 있습니다.",
        active_tab="username",
        main_panel=_username_recovery_panel(result_html),
        side_content=(
            info_box("다음 단계", "비활성 계정은 관리자가 다시 활성화해야 로그인할 수 있습니다.")
            + info_box("비밀번호가 기억나지 않으면", "비밀번호 재설정 화면에서 같은 복구 정보와 새 비밀번호를 입력하면 바로 변경할 수 있습니다.")
        ),
        flash_message="아이디를 확인했습니다.",
        flash_level="ok",
    )


@app.get("/account/password", response_class=HTMLResponse)
def account_password_page(request: Request):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    flash_message, flash_level = _flash_from_request(request)
    return _guest_page(
        title="비밀번호 재설정",
        eyebrow="Account Recovery",
        heading="비밀번호 재설정",
        description="메일 없이도 복구 정보가 맞으면 새 비밀번호로 직접 재설정할 수 있습니다.",
        active_tab="password",
        main_panel=_password_reset_panel(),
        side_content=(
            info_box("재설정 방식", "아이디, 이름, 연락처, 복구 질문과 답변이 모두 일치해야 비밀번호를 바꿀 수 있습니다.")
            + info_box("보안 기준", "새 비밀번호는 8자 이상으로 설정해 주세요.")
        ),
        flash_message=flash_message,
        flash_level=flash_level,
    )


@app.post("/account/password")
def account_password_submit(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    recovery_question: str = Form(...),
    recovery_answer: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    redirect = _redirect_if_logged_in(request)
    if redirect:
        return redirect

    password_rule_error = _password_error(new_password)
    if password_rule_error:
        return _with_flash("/account/password", password_rule_error, "error")
    if new_password != new_password_confirm:
        return _with_flash("/account/password", "새 비밀번호 확인이 일치하지 않습니다.", "error")

    conn = get_conn()
    user = _find_recovery_user(
        conn,
        username=username,
        full_name=full_name,
        phone=phone,
        recovery_question=recovery_question,
    )
    if not user or not user["recovery_answer_hash"] or not auth.verify_recovery_answer(recovery_answer, user["recovery_answer_hash"]):
        conn.close()
        return _with_flash("/account/password", "입력한 복구 정보와 일치하는 계정을 찾지 못했습니다.", "error")

    conn.execute(
        """
        UPDATE users
        SET password_hash = ?, updated_at = ?
        WHERE id = ?
        """,
        (auth.hash_password(new_password.strip()), _now_text(), user["id"]),
    )
    conn.commit()
    conn.close()
    auth.invalidate_user_sessions(user["id"])
    return _with_flash("/login", "비밀번호가 재설정되었습니다. 새 비밀번호로 로그인해 주세요.", "ok")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user, error = _authorize(request, "dashboard:view")
    if error:
        return error

    conn = get_conn()
    total_facilities = conn.execute("SELECT COUNT(*) AS count FROM facilities").fetchone()["count"]
    active_facilities = conn.execute(
        "SELECT COUNT(*) AS count FROM facilities WHERE status = '운영중'"
    ).fetchone()["count"]
    total_items = conn.execute("SELECT COUNT(*) AS count FROM inventory_items").fetchone()["count"]
    low_stock = conn.execute(
        "SELECT COUNT(*) AS count FROM inventory_items WHERE quantity <= min_quantity"
    ).fetchone()["count"]
    total_complaints = conn.execute("SELECT COUNT(*) AS count FROM complaints").fetchone()["count"]
    open_complaints = conn.execute(
        "SELECT COUNT(*) AS count FROM complaints WHERE status NOT IN ('종결', '취소')"
    ).fetchone()["count"]
    overdue_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE response_due_at != ''
          AND response_due_at < ?
          AND status NOT IN ('회신완료', '종결', '취소')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    today_received = conn.execute(
        "SELECT COUNT(*) AS count FROM complaints WHERE created_at LIKE ?",
        (f"{_today_text()}%",),
    ).fetchone()["count"]
    due_today_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE response_due_at = ?
          AND status NOT IN ('회신완료', '종결', '취소')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    repeat_open_complaints = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM complaints c
        WHERE c.status NOT IN ('종결', '취소')
          AND EXISTS (
            SELECT 1
            FROM complaints c2
            WHERE c2.id != c.id
              AND c.requester_phone != ''
              AND c2.requester_phone = c.requester_phone
              AND c2.created_at >= datetime('now', '-{COMPLAINT_REPEAT_WINDOW_DAYS} days')
              AND (
                (c.facility_id IS NOT NULL AND c2.facility_id = c.facility_id)
                OR (c.unit_label != '' AND c2.unit_label = c.unit_label)
                OR (c.location_detail != '' AND c2.location_detail = c.location_detail)
                OR (c.category_primary != '' AND c2.category_primary = c.category_primary)
              )
          )
        """
    ).fetchone()["count"]
    feedback_stats = conn.execute(
        """
        SELECT COUNT(*) AS count,
               ROUND(AVG(rating), 1) AS avg_rating,
               SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS low_count
        FROM complaint_feedback
        """
    ).fetchone()
    open_work = conn.execute(
        "SELECT COUNT(*) AS count FROM work_orders WHERE status NOT IN ('완료', '종결')"
    ).fetchone()["count"]
    overdue_work = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM work_orders
        WHERE due_date != ''
          AND due_date < ?
          AND status NOT IN ('완료', '종결')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    active_users = conn.execute(
        "SELECT COUNT(*) AS count FROM users WHERE is_active = 1"
    ).fetchone()["count"]
    today_completed = conn.execute(
        "SELECT COUNT(*) AS count FROM work_orders WHERE completed_at LIKE ?",
        (f"{_today_text()}%",),
    ).fetchone()["count"]

    recent_work_orders = conn.execute(
        """
        SELECT w.*, f.name AS facility_name, u.full_name AS assignee_name
        FROM work_orders w
        LEFT JOIN facilities f ON f.id = w.facility_id
        LEFT JOIN users u ON u.id = w.assignee_user_id
        ORDER BY w.updated_at DESC, w.id DESC
        LIMIT 8
        """
    ).fetchall()
    recent_complaints = conn.execute(
        """
        SELECT c.*, f.name AS facility_name, u.full_name AS assignee_name, COUNT(DISTINCT w.id) AS work_count,
               cf.rating AS feedback_rating,
               (
                 SELECT COUNT(*)
                 FROM complaints c2
                 WHERE c2.id != c.id
                   AND c.requester_phone != ''
                   AND c2.requester_phone = c.requester_phone
                   AND c2.created_at >= datetime('now', '-90 days')
                   AND (
                     (c.facility_id IS NOT NULL AND c2.facility_id = c.facility_id)
                     OR (c.unit_label != '' AND c2.unit_label = c.unit_label)
                     OR (c.location_detail != '' AND c2.location_detail = c.location_detail)
                     OR (c.category_primary != '' AND c2.category_primary = c.category_primary)
                   )
               ) AS repeat_count
        FROM complaints c
        LEFT JOIN facilities f ON f.id = c.facility_id
        LEFT JOIN users u ON u.id = c.assignee_user_id
        LEFT JOIN work_orders w ON w.complaint_id = c.id
        LEFT JOIN complaint_feedback cf ON cf.complaint_id = c.id
        GROUP BY c.id
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT 8
        """
    ).fetchall()

    recent_transactions = conn.execute(
        """
        SELECT t.*, i.item_code, i.name AS item_name, u.full_name AS actor_name
        FROM inventory_transactions t
        JOIN inventory_items i ON i.id = t.item_id
        LEFT JOIN users u ON u.id = t.actor_user_id
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 8
        """
    ).fetchall()
    conn.close()

    metrics = (
        "<section class='metrics'>"
        + metric_card("시설", total_facilities, f"운영중 {active_facilities}개")
        + metric_card("재고 항목", total_items, f"부족 경고 {low_stock}건")
        + metric_card("진행 민원", open_complaints, f"회신 지연 {overdue_complaints}건")
        + metric_card("반복 민원", repeat_open_complaints, f"최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 기준")
        + metric_card("미완료 작업", open_work, f"지연 {overdue_work}건")
        + metric_card("활성 사용자", active_users, f"오늘 완료 {today_completed}건")
        + metric_card("오늘 접수", today_received, f"SLA 오늘 마감 {due_today_complaints}건")
        + metric_card("만족도", feedback_stats["avg_rating"] or "-", f"피드백 {feedback_stats['count']}건 / 저평가 {int(feedback_stats['low_count'] or 0)}건")
        + "</section>"
    )

    if recent_complaints:
        complaint_rows = "".join(
            """
            <tr>
              <td>{code}</td>
              <td><strong>{title}</strong><div class='muted'>{requester}</div><div style='margin-top:6px'>{feedback}</div></td>
              <td>{priority}<div style='margin-top:6px'>{status}</div></td>
              <td>{assignee}<div style='margin-top:6px'>{repeat_badge}</div></td>
              <td>{due}<div style='margin-top:6px'>{sla}</div></td>
            </tr>
            """.format(
                code=esc(row["complaint_code"]),
                title=esc(row["title"]),
                requester=esc(f"{row['requester_name'] or '-'} / {row['unit_label'] or row['location_detail'] or '-'}"),
                feedback=_complaint_feedback_badge(row["feedback_rating"]) if row["feedback_rating"] else _badge("미평가", "neutral"),
                priority=status_badge(row["priority"]),
                status=status_badge(row["status"]),
                assignee=esc(row["assignee_name"] or "미배정"),
                due=esc(fmt_date(row["response_due_at"])),
                repeat_badge=_badge(f"반복 {row['repeat_count']}건", "danger") if int(row["repeat_count"] or 0) else _badge("반복 없음", "good"),
                sla=_complaint_sla_badge(row),
            )
            for row in recent_complaints
        )
        complaint_table = (
            "<section class='panel'><div class='split'><h2>최근 민원</h2>"
            "<a class='btn secondary' href='/complaints'>전체 보기</a></div>"
            "<table><thead><tr><th>번호</th><th>민원</th><th>우선도/상태</th><th>담당</th><th>회신 목표일</th></tr></thead>"
            f"<tbody>{complaint_rows}</tbody></table></section>"
        )
    else:
        complaint_table = "<section class='panel'><h2>최근 민원</h2>" + empty_state("등록된 민원이 없습니다.") + "</section>"

    if recent_work_orders:
        work_rows = "".join(
            """
            <tr>
              <td>{code}</td>
              <td><strong>{title}</strong><div class='muted'>{facility}</div></td>
              <td>{priority}<div style='margin-top:6px'>{status}</div></td>
              <td>{assignee}</td>
              <td>{due}</td>
            </tr>
            """.format(
                code=esc(row["work_code"]),
                title=esc(row["title"]),
                facility=esc(row["facility_name"] or "시설 미지정"),
                priority=status_badge(row["priority"]),
                status=status_badge(row["status"]),
                assignee=esc(row["assignee_name"] or "미배정"),
                due=esc(fmt_date(row["due_date"])),
            )
            for row in recent_work_orders
        )
        work_table = (
            "<section class='panel'><div class='split'><h2>최근 작업지시</h2>"
            "<a class='btn secondary' href='/work-orders'>전체 보기</a></div>"
            "<table><thead><tr><th>번호</th><th>작업</th><th>우선도/상태</th><th>담당</th><th>기한</th></tr></thead>"
            f"<tbody>{work_rows}</tbody></table></section>"
        )
    else:
        work_table = "<section class='panel'><h2>최근 작업지시</h2>" + empty_state("등록된 작업지시가 없습니다.") + "</section>"

    if recent_transactions:
        tx_rows = "".join(
            """
            <tr>
              <td>{when}</td>
              <td><strong>{item}</strong><div class='muted'>{code}</div></td>
              <td>{tx_type}</td>
              <td>{delta}</td>
              <td>{actor}</td>
              <td>{reason}</td>
            </tr>
            """.format(
                when=esc(fmt_datetime(row["created_at"])),
                item=esc(row["item_name"]),
                code=esc(row["item_code"]),
                tx_type=status_badge(row["tx_type"]),
                delta=esc(f"{row['quantity_delta']:+d}"),
                actor=esc(row["actor_name"] or "system"),
                reason=esc(row["reason"] or "-"),
            )
            for row in recent_transactions
        )
        tx_table = (
            "<section class='panel'><div class='split'><h2>최근 재고 변동</h2>"
            "<a class='btn secondary' href='/inventory'>재고 관리</a></div>"
            "<table><thead><tr><th>시각</th><th>품목</th><th>유형</th><th>변동</th><th>처리자</th><th>사유</th></tr></thead>"
            f"<tbody>{tx_rows}</tbody></table></section>"
        )
    else:
        tx_table = "<section class='panel'><h2>최근 재고 변동</h2>" + empty_state("재고 변동 이력이 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Operations Overview",
            "공동 운영용 시설 관리 대시보드",
            "9명까지 동시에 쓰는 환경을 기준으로 시설, 재고, 작업지시, 보고서를 한 흐름으로 묶었습니다.",
            actions=(
                "<a class='btn primary' href='/work-orders'>작업지시 바로가기</a>"
                "<a class='btn secondary' href='/reports'>운영 보고서</a>"
            ),
        )
        + metrics
        + "<div class='layout-2'>"
        + "<div class='stack'>"
        + info_box(
            "운영 원칙",
            "시설 상태는 위치 중심으로, 민원은 접수와 회신 기준으로, 작업지시는 조치 내역 중심으로 관리합니다.",
        )
        + info_box(
            "초기 계정",
            _admin_bootstrap_message(),
        )
        + "</div>"
        + "<div class='stack'>"
        + complaint_table
        + work_table
        + tx_table
        + "</div></div>"
    )
    return HTMLResponse(layout(title="대시보드", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.get("/facilities", response_class=HTMLResponse)
def facilities_page(request: Request):
    user, error = _authorize(request, "facilities:view")
    if error:
        return error

    can_edit = auth.has_permission(user["role"], "facilities:edit")
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    edit_id = _parse_int(request.query_params.get("edit", ""), 0)

    conn = get_conn()
    where = []
    params: list = []
    if q:
        where.append(
            "(f.facility_code LIKE ? OR f.name LIKE ? OR f.building LIKE ? OR f.floor LIKE ? OR f.zone LIKE ? OR f.note LIKE ?)"
        )
        params.extend([f"%{q}%"] * 6)
    if status:
        where.append("f.status = ?")
        params.append(status)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = conn.execute(
        f"""
        SELECT f.*, u.full_name AS manager_name
        FROM facilities f
        LEFT JOIN users u ON u.id = f.manager_user_id
        {where_sql}
        ORDER BY f.updated_at DESC, f.id DESC
        """,
        params,
    ).fetchall()
    attachments = _attachment_map(conn, "facility", [row["id"] for row in rows])
    edit_row = None
    if edit_id:
        edit_row = conn.execute("SELECT * FROM facilities WHERE id = ?", (edit_id,)).fetchone()
    manager_options = _user_options(conn)
    conn.close()

    status_options = ["운영중", "점검필요", "보수중", "사용중지"]
    category_options = ["전기", "기계", "소방", "건축", "공용", "기타", "레거시 위치"]
    form_html = ""
    if can_edit:
        form_html = (
            "<section class='panel'><h2>시설 등록 / 수정</h2><p class='muted'>시설 단위로 위치와 상태, 담당자를 관리합니다.</p>"
            "<form action='/facilities/save' method='post' enctype='multipart/form-data' class='stack'>"
            f"<input type='hidden' name='facility_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
            + (
                f"<div><label>시설 코드</label><input value='{esc(edit_row['facility_code'])}' disabled></div>"
                if edit_row
                else ""
            )
            + f"<div><label>분류</label><select name='category'>{render_options(category_options, edit_row['category'] if edit_row else '', blank_label='선택')}</select></div>"
            + f"<div><label>시설명</label><input name='name' value='{esc(edit_row['name'] if edit_row else '')}' required></div>"
            + "<div class='grid two'>"
            + f"<div><label>동/건물</label><input name='building' value='{esc(edit_row['building'] if edit_row else '')}'></div>"
            + f"<div><label>층/영역</label><input name='floor' value='{esc(edit_row['floor'] if edit_row else '')}'></div>"
            + "</div>"
            + f"<div><label>세부 위치</label><input name='zone' value='{esc(edit_row['zone'] if edit_row else '')}' placeholder='예: 기계실 A구역 / 분전반 전면'></div>"
            + f"<div><label>상태</label><select name='status'>{render_options(status_options, edit_row['status'] if edit_row else '운영중')}</select></div>"
            + f"<div><label>담당자</label><select name='manager_user_id'>{render_options(manager_options, str(edit_row['manager_user_id'] or '') if edit_row else '', blank_label='미지정')}</select></div>"
            + f"<div><label>비고</label><textarea name='note'>{esc(edit_row['note'] if edit_row else '')}</textarea></div>"
            + "<div><label>첨부 이미지</label><input name='files' type='file' accept='image/*' multiple></div>"
            + "<div class='row-actions'>"
            + "<button class='btn primary' type='submit'>저장</button>"
            + "<a class='btn secondary' href='/facilities'>새로 입력</a>"
            + (
                _post_action_button(f"/facilities/delete/{edit_row['id']}", "삭제", "시설을 삭제하시겠습니까?")
                if edit_row
                else ""
            )
            + "</div></form>"
        )
        if edit_row:
            form_html += "<div style='margin-top:14px'><label>현재 첨부</label>" + attachment_gallery(attachments.get(edit_row["id"], [])) + "</div>"
        form_html += "</section>"
    else:
        form_html = info_box("읽기 전용", "현재 계정은 시설 조회 권한만 있습니다. 수정은 운영관리 이상 역할이 필요합니다.")

    if rows:
        body_rows = []
        for row in rows:
            actions = f"<a class='btn secondary' href='/facilities?edit={row['id']}'>상세/수정</a>" if can_edit else "<span class='muted'>조회</span>"
            body_rows.append(
                """
                <tr>
                  <td>{code}</td>
                  <td><strong>{name}</strong><div class='muted'>{category}</div></td>
                  <td>{location}</td>
                  <td>{status}</td>
                  <td>{manager}</td>
                  <td>{updated}</td>
                  <td>{attachments}</td>
                  <td>{actions}</td>
                </tr>
                """.format(
                    code=esc(row["facility_code"]),
                    name=esc(row["name"]),
                    category=esc(row["category"] or "-"),
                    location=esc(_compose_facility_location(row)),
                    status=status_badge(row["status"]),
                    manager=esc(row["manager_name"] or "미지정"),
                    updated=esc(fmt_datetime(row["updated_at"])),
                    attachments=attachment_gallery(attachments.get(row["id"], [])),
                    actions=actions,
                )
            )
        list_html = (
            "<section class='panel'><div class='split'><div><h2>시설 목록</h2><p class='muted'>상태와 담당자 기준으로 관리합니다.</p></div>"
            "<form class='inline-form' method='get' action='/facilities'>"
            f"<input name='q' value='{esc(q)}' placeholder='코드, 시설명, 위치 검색'>"
            f"<select name='status'>{render_options(status_options, status, blank_label='전체 상태')}</select>"
            "<button class='btn secondary' type='submit'>검색</button>"
            "</form></div>"
            "<table><thead><tr><th>코드</th><th>시설</th><th>위치</th><th>상태</th><th>담당</th><th>수정일</th><th>첨부</th><th>관리</th></tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table></section>"
        )
    else:
        list_html = "<section class='panel'><h2>시설 목록</h2>" + empty_state("검색 조건에 맞는 시설이 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Facility Registry",
            "시설 관리",
            "설비 위치를 표준화해 작업지시와 보고서의 기준 데이터로 사용합니다.",
        )
        + "<div class='layout-2'>"
        + form_html
        + list_html
        + "</div>"
    )
    return HTMLResponse(layout(title="시설 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.post("/facilities/save")
def facilities_save(
    request: Request,
    facility_id: str = Form(""),
    category: str = Form(""),
    name: str = Form(...),
    building: str = Form(""),
    floor: str = Form(""),
    zone: str = Form(""),
    status: str = Form("운영중"),
    manager_user_id: str = Form(""),
    note: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "facilities:edit")
    if error:
        return error

    facility_id_i = _parse_int(facility_id, 0)
    manager_id_i = _parse_int(manager_user_id, 0) or None
    conn = get_conn()
    if facility_id_i:
        conn.execute(
            """
            UPDATE facilities
            SET category = ?, name = ?, building = ?, floor = ?, zone = ?, status = ?, manager_user_id = ?, note = ?,
                updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                category.strip(),
                name.strip(),
                building.strip(),
                floor.strip(),
                zone.strip(),
                status.strip(),
                manager_id_i,
                note.strip(),
                user["id"],
                _now_text(),
                facility_id_i,
            ),
        )
        _save_attachments(conn, "facility", facility_id_i, files, user["id"])
        conn.commit()
        conn.close()
        return _with_flash(f"/facilities?edit={facility_id_i}", "시설 정보가 수정되었습니다.", "ok")

    cursor = conn.execute(
        """
        INSERT INTO facilities(
            facility_code, category, name, building, floor, zone, status, manager_user_id, note,
            created_by, updated_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category.strip(),
            name.strip(),
            building.strip(),
            floor.strip(),
            zone.strip(),
            status.strip(),
            manager_id_i,
            note.strip(),
            user["id"],
            user["id"],
            _now_text(),
            _now_text(),
        ),
    )
    facility_id_i = cursor.lastrowid
    conn.execute("UPDATE facilities SET facility_code = ? WHERE id = ?", (f"FAC-{facility_id_i:04d}", facility_id_i))
    _save_attachments(conn, "facility", facility_id_i, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/facilities?edit={facility_id_i}", "시설이 등록되었습니다.", "ok")


@app.post("/facilities/delete/{facility_id}")
def facilities_delete(request: Request, facility_id: int):
    user, error = _authorize(request, "facilities:edit")
    if error:
        return error
    conn = get_conn()
    _delete_attachments(conn, "facility", facility_id)
    conn.execute("UPDATE work_orders SET facility_id = NULL WHERE facility_id = ?", (facility_id,))
    conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
    conn.commit()
    conn.close()
    return _with_flash("/facilities", "시설이 삭제되었습니다.", "ok")


@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request):
    user, error = _authorize(request, "inventory:view")
    if error:
        return error

    can_edit = auth.has_permission(user["role"], "inventory:edit")
    can_tx = auth.has_permission(user["role"], "inventory:transact")
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    category = request.query_params.get("category", "").strip()
    low_only = request.query_params.get("low_only", "").strip() == "1"
    edit_id = _parse_int(request.query_params.get("edit", ""), 0)

    conn = get_conn()
    where = []
    params: list = []
    if q:
        where.append("(item_code LIKE ? OR name LIKE ? OR specification LIKE ? OR location LIKE ? OR note LIKE ?)")
        params.extend([f"%{q}%"] * 5)
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    if low_only:
        where.append("quantity <= min_quantity")
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    items = conn.execute(
        f"""
        SELECT *
        FROM inventory_items
        {where_sql}
        ORDER BY (quantity <= min_quantity) DESC, updated_at DESC, id DESC
        """,
        params,
    ).fetchall()
    attachments = _attachment_map(conn, "inventory", [row["id"] for row in items])
    edit_row = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    tx_rows = (
        conn.execute(
            """
            SELECT t.*, u.full_name AS actor_name
            FROM inventory_transactions t
            LEFT JOIN users u ON u.id = t.actor_user_id
            WHERE t.item_id = ?
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT 10
            """,
            (edit_id,),
        ).fetchall()
        if edit_id
        else []
    )
    conn.close()

    status_options = ["정상", "부족", "점검필요", "폐기대기"]
    category_options = ["전기", "기계", "소방", "건축", "사무", "안전", "레거시 이관", "기타"]
    form_html = ""
    if can_edit:
        form_html = (
            "<section class='panel'><h2>재고 품목 등록 / 수정</h2><p class='muted'>품목 기준정보를 관리하고 수불 이력을 누적합니다.</p>"
            "<form action='/inventory/save' method='post' enctype='multipart/form-data' class='stack'>"
            f"<input type='hidden' name='item_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
            + (
                f"<div><label>품목 코드</label><input value='{esc(edit_row['item_code'])}' disabled></div>"
                if edit_row
                else ""
            )
            + f"<div><label>분류</label><select name='category'>{render_options(category_options, edit_row['category'] if edit_row else '', blank_label='선택')}</select></div>"
            + f"<div><label>품목명</label><input name='name' value='{esc(edit_row['name'] if edit_row else '')}' required></div>"
            + f"<div><label>규격 / 사양</label><input name='specification' value='{esc(edit_row['specification'] if edit_row else '')}'></div>"
            + "<div class='grid two'>"
            + f"<div><label>현재 수량</label><input name='quantity' type='number' min='0' value='{esc(edit_row['quantity'] if edit_row else 0)}'></div>"
            + f"<div><label>최소 수량</label><input name='min_quantity' type='number' min='0' value='{esc(edit_row['min_quantity'] if edit_row else 0)}'></div>"
            + "</div>"
            + "<div class='grid two'>"
            + f"<div><label>단위</label><input name='unit' value='{esc(edit_row['unit'] if edit_row else '개')}'></div>"
            + f"<div><label>상태</label><select name='status'>{render_options(status_options, edit_row['status'] if edit_row else '정상')}</select></div>"
            + "</div>"
            + "<div class='grid two'>"
            + f"<div><label>보관 위치</label><input name='location' value='{esc(edit_row['location'] if edit_row else '')}'></div>"
            + f"<div><label>구매일자</label><input name='purchase_date' type='date' value='{esc(fmt_date(edit_row['purchase_date']) if edit_row else '')}'></div>"
            + "</div>"
            + f"<div><label>구매금액(원)</label><input name='purchase_amount' type='number' min='0' value='{esc(edit_row['purchase_amount'] if edit_row else 0)}'></div>"
            + f"<div><label>비고</label><textarea name='note'>{esc(edit_row['note'] if edit_row else '')}</textarea></div>"
            + "<div><label>첨부 이미지</label><input name='files' type='file' accept='image/*' multiple></div>"
            + "<div class='row-actions'>"
            + "<button class='btn primary' type='submit'>저장</button>"
            + "<a class='btn secondary' href='/inventory'>새로 입력</a>"
            + (
                _post_action_button(f"/inventory/delete/{edit_row['id']}", "삭제", "재고 품목을 삭제하시겠습니까?")
                if edit_row and can_edit
                else ""
            )
            + "</div></form>"
        )
        if edit_row:
            form_html += "<div style='margin-top:14px'><label>현재 첨부</label>" + attachment_gallery(attachments.get(edit_row["id"], [])) + "</div>"
        if edit_row and can_tx:
            tx_history = (
                "".join(
                    """
                    <tr>
                      <td>{when}</td>
                      <td>{type}</td>
                      <td>{delta}</td>
                      <td>{actor}</td>
                      <td>{reason}</td>
                    </tr>
                    """.format(
                        when=esc(fmt_datetime(tx["created_at"])),
                        type=status_badge(tx["tx_type"]),
                        delta=esc(f"{tx['quantity_delta']:+d}"),
                        actor=esc(tx["actor_name"] or "system"),
                        reason=esc(tx["reason"] or "-"),
                    )
                    for tx in tx_rows
                )
                if tx_rows
                else "<tr><td colspan='5' class='muted'>수불 이력이 없습니다.</td></tr>"
            )
            form_html += (
                "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                "<h3>수불 처리</h3><p class='muted'>입고/반출/조정은 이력으로 남고 수량이 자동 갱신됩니다.</p>"
                f"<form action='/inventory/tx/{edit_row['id']}' method='post' class='stack'>"
                "<div class='grid two'>"
                f"<div><label>처리 유형</label><select name='tx_type'>{render_options(['입고', '반출', '반납', '사용', '조정'], '입고')}</select></div>"
                "<div><label>수량</label><input name='quantity' type='number' required></div>"
                "</div>"
                "<div><label>사유</label><input name='reason' placeholder='예: 작업지시 WO-0003 사용'></div>"
                "<div class='row-actions'><button class='btn warn' type='submit'>수불 반영</button></div>"
                "</form>"
                "<div style='margin-top:14px'><h3>최근 수불 이력</h3>"
                "<table><thead><tr><th>시각</th><th>유형</th><th>수량</th><th>처리자</th><th>사유</th></tr></thead>"
                f"<tbody>{tx_history}</tbody></table></div></div>"
            )
        form_html += "</section>"
    else:
        form_html = info_box("읽기 전용", "현재 계정은 재고 조회만 가능합니다. 수불 반영은 작업자 이상 권한이 필요합니다.")

    if items:
        rows_html = []
        for row in items:
            actions = []
            if can_edit:
                actions.append(f"<a class='btn secondary' href='/inventory?edit={row['id']}'>상세/수정</a>")
            elif can_tx:
                actions.append(f"<a class='btn secondary' href='/inventory?edit={row['id']}'>수불 처리</a>")
            else:
                actions.append("<span class='muted'>조회</span>")
            rows_html.append(
                """
                <tr>
                  <td>{code}</td>
                  <td><strong>{name}</strong><div class='muted'>{spec}</div></td>
                  <td>{category}</td>
                  <td>{qty}<div class='muted'>최소 {min_qty}</div></td>
                  <td>{status}</td>
                  <td>{location}</td>
                  <td>{attachments}</td>
                  <td>{actions}</td>
                </tr>
                """.format(
                    code=esc(row["item_code"]),
                    name=esc(row["name"]),
                    spec=esc(row["specification"] or "-"),
                    category=esc(row["category"] or "-"),
                    qty=esc(f"{row['quantity']} {row['unit']}"),
                    min_qty=esc(f"{row['min_quantity']} {row['unit']}"),
                    status=status_badge("부족" if row["quantity"] <= row["min_quantity"] else row["status"]),
                    location=esc(row["location"] or "-"),
                    attachments=attachment_gallery(attachments.get(row["id"], [])),
                    actions="".join(actions),
                )
            )
        list_html = (
            "<section class='panel'><div class='split'><div><h2>재고 목록</h2><p class='muted'>부족 경고 기준과 실제 수량을 함께 확인합니다.</p></div>"
            "<form class='inline-form' method='get' action='/inventory'>"
            f"<input name='q' value='{esc(q)}' placeholder='코드, 품목명, 위치 검색'>"
            f"<select name='status'>{render_options(status_options, status, blank_label='전체 상태')}</select>"
            f"<select name='category'>{render_options(category_options, category, blank_label='전체 분류')}</select>"
            f"<label style='display:flex;gap:8px;align-items:center;margin:0;'><input name='low_only' type='checkbox' value='1' {'checked' if low_only else ''} style='width:auto;'>부족만</label>"
            "<button class='btn secondary' type='submit'>검색</button>"
            "</form></div>"
            "<table><thead><tr><th>코드</th><th>품목</th><th>분류</th><th>수량</th><th>상태</th><th>위치</th><th>첨부</th><th>관리</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></section>"
        )
    else:
        list_html = "<section class='panel'><h2>재고 목록</h2>" + empty_state("조건에 맞는 재고 품목이 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Inventory Control",
            "재고 관리",
            "재고 품목과 수불 이력을 분리해, 단순 숫자 수정이 아니라 변화 내역이 남는 구조로 전환했습니다.",
        )
        + "<div class='layout-2'>"
        + form_html
        + list_html
        + "</div>"
    )
    return HTMLResponse(layout(title="재고 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.post("/inventory/save")
def inventory_save(
    request: Request,
    item_id: str = Form(""),
    category: str = Form(""),
    name: str = Form(...),
    specification: str = Form(""),
    quantity: str = Form("0"),
    unit: str = Form("개"),
    location: str = Form(""),
    status: str = Form("정상"),
    min_quantity: str = Form("0"),
    purchase_date: str = Form(""),
    purchase_amount: str = Form("0"),
    note: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "inventory:edit")
    if error:
        return error

    item_id_i = _parse_int(item_id, 0)
    quantity_i = max(0, _parse_int(quantity, 0))
    min_quantity_i = max(0, _parse_int(min_quantity, 0))
    purchase_amount_i = max(0, _parse_int(purchase_amount, 0))
    conn = get_conn()
    derived_status = status.strip() or "정상"
    if derived_status in {"정상", "부족"}:
        derived_status = "부족" if quantity_i <= min_quantity_i else "정상"

    if item_id_i:
        conn.execute(
            """
            UPDATE inventory_items
            SET category = ?, name = ?, specification = ?, quantity = ?, unit = ?, location = ?, status = ?,
                min_quantity = ?, purchase_date = ?, purchase_amount = ?, note = ?, updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                category.strip(),
                name.strip(),
                specification.strip(),
                quantity_i,
                unit.strip() or "개",
                location.strip(),
                derived_status,
                min_quantity_i,
                purchase_date.strip(),
                purchase_amount_i,
                note.strip(),
                user["id"],
                _now_text(),
                item_id_i,
            ),
        )
        _save_attachments(conn, "inventory", item_id_i, files, user["id"])
        conn.commit()
        conn.close()
        return _with_flash(f"/inventory?edit={item_id_i}", "재고 품목이 수정되었습니다.", "ok")

    cursor = conn.execute(
        """
        INSERT INTO inventory_items(
            item_code, category, name, specification, quantity, unit, location, status, min_quantity,
            purchase_date, purchase_amount, note, created_by, updated_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category.strip(),
            name.strip(),
            specification.strip(),
            quantity_i,
            unit.strip() or "개",
            location.strip(),
            derived_status,
            min_quantity_i,
            purchase_date.strip(),
            purchase_amount_i,
            note.strip(),
            user["id"],
            user["id"],
            _now_text(),
            _now_text(),
        ),
    )
    item_id_i = cursor.lastrowid
    conn.execute("UPDATE inventory_items SET item_code = ? WHERE id = ?", (f"INV-{item_id_i:04d}", item_id_i))
    if quantity_i:
        conn.execute(
            """
            INSERT INTO inventory_transactions(item_id, tx_type, quantity_delta, reason, actor_user_id, created_at)
            VALUES (?, '초기등록', ?, ?, ?, ?)
            """,
            (item_id_i, quantity_i, "초기 재고 등록", user["id"], _now_text()),
        )
    _save_attachments(conn, "inventory", item_id_i, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/inventory?edit={item_id_i}", "재고 품목이 등록되었습니다.", "ok")


@app.post("/inventory/tx/{item_id}")
def inventory_transaction(
    request: Request,
    item_id: int,
    tx_type: str = Form(...),
    quantity: str = Form(...),
    reason: str = Form(""),
):
    user, error = _authorize(request, "inventory:transact")
    if error:
        return error

    raw_qty = _parse_int(quantity, 0)
    if raw_qty == 0 and tx_type != "조정":
        return _with_flash(f"/inventory?edit={item_id}", "수량은 0이 될 수 없습니다.", "error")

    if tx_type in {"반출", "사용"}:
        delta = -abs(raw_qty)
    elif tx_type in {"입고", "반납"}:
        delta = abs(raw_qty)
    else:
        delta = raw_qty

    conn = get_conn()
    item = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return _with_flash("/inventory", "재고 품목을 찾을 수 없습니다.", "error")

    new_qty = int(item["quantity"] or 0) + delta
    if new_qty < 0:
        conn.close()
        return _with_flash(f"/inventory?edit={item_id}", "재고가 음수가 될 수 없습니다.", "error")

    derived_status = item["status"]
    if derived_status in {"정상", "부족"}:
        derived_status = "부족" if new_qty <= int(item["min_quantity"] or 0) else "정상"

    conn.execute(
        """
        UPDATE inventory_items
        SET quantity = ?, status = ?, updated_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_qty, derived_status, user["id"], _now_text(), item_id),
    )
    conn.execute(
        """
        INSERT INTO inventory_transactions(item_id, tx_type, quantity_delta, reason, actor_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, tx_type.strip(), delta, reason.strip(), user["id"], _now_text()),
    )
    conn.commit()
    conn.close()
    return _with_flash(f"/inventory?edit={item_id}", "수불 이력이 반영되었습니다.", "ok")


@app.post("/inventory/delete/{item_id}")
def inventory_delete(request: Request, item_id: int):
    user, error = _authorize(request, "inventory:edit")
    if error:
        return error
    conn = get_conn()
    _delete_attachments(conn, "inventory", item_id)
    conn.execute("DELETE FROM inventory_transactions WHERE item_id = ?", (item_id,))
    conn.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return _with_flash("/inventory", "재고 품목이 삭제되었습니다.", "ok")


@app.get("/work-orders", response_class=HTMLResponse)
def work_orders_page(request: Request):
    user, error = _authorize(request, "work_orders:view")
    if error:
        return error

    can_create = auth.has_permission(user["role"], "work_orders:create")
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    priority = request.query_params.get("priority", "").strip()
    edit_id = _parse_int(request.query_params.get("edit", ""), 0)
    complaint_prefill_id = _parse_int(request.query_params.get("complaint_id", ""), 0) if not edit_id else 0

    conn = get_conn()
    where = []
    params: list = []
    if q:
        where.append("(w.work_code LIKE ? OR w.title LIKE ? OR w.description LIKE ? OR w.requester_name LIKE ? OR c.complaint_code LIKE ? OR c.title LIKE ?)")
        params.extend([f"%{q}%"] * 6)
    if status:
        where.append("w.status = ?")
        params.append(status)
    if priority:
        where.append("w.priority = ?")
        params.append(priority)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    orders = conn.execute(
        f"""
        SELECT w.*, f.name AS facility_name, u.full_name AS assignee_name, c.complaint_code, c.title AS complaint_title
        FROM work_orders w
        LEFT JOIN facilities f ON f.id = w.facility_id
        LEFT JOIN users u ON u.id = w.assignee_user_id
        LEFT JOIN complaints c ON c.id = w.complaint_id
        {where_sql}
        ORDER BY CASE w.priority WHEN '긴급' THEN 1 WHEN '높음' THEN 2 WHEN '보통' THEN 3 ELSE 4 END,
                 w.updated_at DESC, w.id DESC
        """,
        params,
    ).fetchall()
    attachments = _attachment_map(conn, "work_order", [row["id"] for row in orders])
    edit_row = conn.execute("SELECT * FROM work_orders WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    complaint_prefill = (
        conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_prefill_id,)).fetchone()
        if complaint_prefill_id
        else None
    )
    updates = (
        conn.execute(
            """
            SELECT u.*, us.full_name AS actor_name
            FROM work_order_updates u
            LEFT JOIN users us ON us.id = u.actor_user_id
            WHERE u.work_order_id = ?
            ORDER BY u.created_at DESC, u.id DESC
            LIMIT 12
            """,
            (edit_id,),
        ).fetchall()
        if edit_id
        else []
    )
    facility_options = _facility_options(conn)
    assignee_options = _user_options(conn, include_viewers=False)
    complaint_options = _complaint_options(conn)
    conn.close()

    can_manage_edit_row = _can_manage_work_order(user, edit_row)
    can_update_edit_row = _can_update_work_order(user, edit_row)
    can_delete_edit_row = _can_delete_work_order(user, edit_row)

    status_options = ["접수", "진행중", "대기", "보류", "완료", "종결"]
    priority_options = ["낮음", "보통", "높음", "긴급"]
    category_options = ["전기", "기계", "소방", "건축", "민원", "안전", "기타"]

    form_html = ""
    if can_create or edit_row:
        if edit_row and not (can_manage_edit_row or can_update_edit_row):
            form_html = info_box("권한 제한", "이 작업지시는 본인 생성 건이나 본인 배정 건일 때만 수정 또는 업데이트할 수 있습니다.")
        elif edit_row and not can_manage_edit_row:
            form_html = (
                "<section class='panel'><h2>작업지시 상세</h2><p class='muted'>기본정보 수정 권한은 없고, 진행 업데이트만 가능합니다.</p>"
                + "<div class='stack'>"
                + info_box("작업 번호", esc(edit_row["work_code"]))
                + info_box("작업 제목", esc(edit_row["title"]))
                + info_box("작업 내용", esc(edit_row["description"] or "-"))
                + "</div>"
            )
        else:
            form_html = (
                "<section class='panel'><h2>작업지시 등록 / 수정</h2><p class='muted'>시설과 담당자를 기준으로 작업을 배정하고 추적합니다.</p>"
                "<form action='/work-orders/save' method='post' enctype='multipart/form-data' class='stack'>"
                f"<input type='hidden' name='work_order_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
                + (
                    f"<div><label>작업 번호</label><input value='{esc(edit_row['work_code'])}' disabled></div>"
                    if edit_row
                    else ""
                )
                + f"<div><label>연결 민원</label><select name='complaint_id'>{render_options(complaint_options, str(edit_row['complaint_id'] or '') if edit_row else str(complaint_prefill['id']) if complaint_prefill else '', blank_label='미연결')}</select></div>"
                + (
                    f"<div class='muted' style='padding:8px 0 2px;'>민원 프리필: {esc(complaint_prefill['complaint_code'])} 민원에서 제목, 시설, 요청자, 우선도가 기본값으로 채워졌습니다.</div>"
                    if complaint_prefill and not edit_row
                    else ""
                )
                + f"<div><label>분류</label><select name='category'>{render_options(category_options, edit_row['category'] if edit_row else '', blank_label='선택')}</select></div>"
                + f"<div><label>작업 제목</label><input name='title' value='{esc(edit_row['title'] if edit_row else complaint_prefill['title'] if complaint_prefill else '')}' required></div>"
                + f"<div><label>대상 시설</label><select name='facility_id'>{render_options(facility_options, str(edit_row['facility_id'] or '') if edit_row else str(complaint_prefill['facility_id'] or '') if complaint_prefill else '', blank_label='미지정')}</select></div>"
                + "<div class='grid two'>"
                + f"<div><label>요청자</label><input name='requester_name' value='{esc(edit_row['requester_name'] if edit_row else complaint_prefill['requester_name'] if complaint_prefill else '')}'></div>"
                + f"<div><label>담당자</label><select name='assignee_user_id'>{render_options(assignee_options, str(edit_row['assignee_user_id'] or '') if edit_row else str(complaint_prefill['assignee_user_id'] or '') if complaint_prefill else '', blank_label='미지정')}</select></div>"
                + "</div>"
                + "<div class='grid two'>"
                + f"<div><label>우선도</label><select name='priority'>{render_options(priority_options, edit_row['priority'] if edit_row else complaint_prefill['priority'] if complaint_prefill else '보통')}</select></div>"
                + f"<div><label>상태</label><select name='status'>{render_options(status_options, edit_row['status'] if edit_row else '접수')}</select></div>"
                + "</div>"
                + f"<div><label>기한</label><input name='due_date' type='date' value='{esc(fmt_date(edit_row['due_date']) if edit_row else '')}'></div>"
                + f"<div><label>작업 내용</label><textarea name='description'>{esc(edit_row['description'] if edit_row else complaint_prefill['description'] if complaint_prefill else '')}</textarea></div>"
                + "<div><label>현장 사진 / 첨부</label><input name='files' type='file' accept='image/*' multiple></div>"
                + "<div class='row-actions'>"
                + "<button class='btn primary' type='submit'>저장</button>"
                + "<a class='btn secondary' href='/work-orders'>새로 입력</a>"
                + (
                    _post_action_button(f"/work-orders/delete/{edit_row['id']}", "삭제", "작업지시를 삭제하시겠습니까?")
                    if edit_row and can_delete_edit_row
                    else ""
                )
                + "</div></form>"
            )
        if edit_row and (can_manage_edit_row or can_update_edit_row):
            form_html += "<div style='margin-top:14px'><label>현재 첨부</label>" + attachment_gallery(attachments.get(edit_row["id"], [])) + "</div>"
        if edit_row and can_update_edit_row:
            updates_html = (
                "".join(
                    """
                    <tr>
                      <td>{when}</td>
                      <td>{type}</td>
                      <td>{body}</td>
                      <td>{actor}</td>
                    </tr>
                    """.format(
                        when=esc(fmt_datetime(row["created_at"])),
                        type=status_badge(row["update_type"]),
                        body=esc(row["body"]),
                        actor=esc(row["actor_name"] or "system"),
                    )
                    for row in updates
                )
                if updates
                else "<tr><td colspan='4' class='muted'>업데이트 이력이 없습니다.</td></tr>"
            )
            form_html += (
                "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                "<h3>진행 업데이트</h3><p class='muted'>상태 변경과 현장 코멘트를 남겨 보고서와 추적 이력에 바로 반영합니다.</p>"
                f"<form action='/work-orders/update/{edit_row['id']}' method='post' enctype='multipart/form-data' class='stack'>"
                "<div class='grid two'>"
                f"<div><label>업데이트 유형</label><select name='update_type'>{render_options(['현장조치', '상태변경', '부품요청', '메모', '완료보고'], '현장조치')}</select></div>"
                f"<div><label>새 상태</label><select name='status'>{render_options(status_options, edit_row['status'], blank_label='상태 유지')}</select></div>"
                "</div>"
                "<div><label>업데이트 내용</label><textarea name='body' required></textarea></div>"
                "<div><label>추가 첨부</label><input name='files' type='file' accept='image/*' multiple></div>"
                "<div class='row-actions'><button class='btn warn' type='submit'>업데이트 저장</button></div>"
                "</form>"
                "<div style='margin-top:14px'><h3>최근 업데이트</h3>"
                "<table><thead><tr><th>시각</th><th>유형</th><th>내용</th><th>작성자</th></tr></thead>"
                f"<tbody>{updates_html}</tbody></table></div></div>"
            )
        form_html += "</section>"
    else:
        form_html = info_box("읽기 전용", "현재 계정은 작업지시 조회만 가능합니다. 등록과 수정은 작업자 이상 권한이 필요합니다.")

    if orders:
        rows_html = []
        for row in orders:
            due_text = fmt_date(row["due_date"])
            overdue = row["due_date"] and row["due_date"] < _today_text() and row["status"] not in {"완료", "종결"}
            due_badge = status_badge("지연" if overdue else ("완료" if row["status"] in {"완료", "종결"} else "정상"))
            row_actions = []
            if _can_manage_work_order(user, row):
                row_actions.append(f"<a class='btn secondary' href='/work-orders?edit={row['id']}'>수정</a>")
            elif _can_update_work_order(user, row):
                row_actions.append(f"<a class='btn secondary' href='/work-orders?edit={row['id']}'>업데이트</a>")
            else:
                row_actions.append("<span class='muted'>조회</span>")
            rows_html.append(
                """
                <tr>
                  <td>{code}</td>
                  <td><strong>{title}</strong><div class='muted'>{category}</div><div class='muted'>{complaint}</div><div class='muted'>{description}</div></td>
                  <td>{facility}</td>
                  <td>{priority}<div style='margin-top:6px'>{status}</div></td>
                  <td>{assignee}<div class='muted'>기한 {due}</div><div style='margin-top:6px'>{due_badge}</div></td>
                  <td>{attachments}</td>
                  <td>{actions}</td>
                </tr>
                """.format(
                    code=esc(row["work_code"]),
                    title=esc(row["title"]),
                    category=esc(row["category"] or "-"),
                    complaint=esc(f"민원 {row['complaint_code']} · {row['complaint_title']}" if row["complaint_code"] else "민원 미연결"),
                    description=esc((row["description"] or "")[:80] + ("..." if len(row["description"] or "") > 80 else "")),
                    facility=esc(row["facility_name"] or "시설 미지정"),
                    priority=status_badge(row["priority"]),
                    status=status_badge(row["status"]),
                    assignee=esc(row["assignee_name"] or "미배정"),
                    due=esc(due_text),
                    due_badge=due_badge,
                    attachments=attachment_gallery(attachments.get(row["id"], [])),
                    actions="".join(row_actions),
                )
            )
        list_html = (
            "<section class='panel'><div class='split'><div><h2>작업지시 목록</h2><p class='muted'>우선도와 상태를 기준으로 지연 작업을 우선 확인합니다.</p></div>"
            "<form class='inline-form' method='get' action='/work-orders'>"
            f"<input name='q' value='{esc(q)}' placeholder='번호, 제목, 내용, 요청자 검색'>"
            f"<select name='status'>{render_options(status_options, status, blank_label='전체 상태')}</select>"
            f"<select name='priority'>{render_options(priority_options, priority, blank_label='전체 우선도')}</select>"
            "<button class='btn secondary' type='submit'>검색</button>"
            "</form></div>"
            "<table><thead><tr><th>번호</th><th>작업</th><th>시설</th><th>우선도/상태</th><th>담당/기한</th><th>첨부</th><th>관리</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></section>"
        )
    else:
        list_html = "<section class='panel'><h2>작업지시 목록</h2>" + empty_state("조건에 맞는 작업지시가 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Work Orders",
            "작업지시 관리",
            "작업 요청, 배정, 진행 업데이트, 완료 기록을 한 엔티티로 관리합니다.",
        )
        + "<div class='layout-2'>"
        + form_html
        + list_html
        + "</div>"
    )
    return HTMLResponse(layout(title="작업지시 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.post("/work-orders/save")
def work_orders_save(
    request: Request,
    work_order_id: str = Form(""),
    complaint_id: str = Form(""),
    category: str = Form(""),
    title: str = Form(...),
    facility_id: str = Form(""),
    requester_name: str = Form(""),
    priority: str = Form("보통"),
    status: str = Form("접수"),
    description: str = Form(""),
    assignee_user_id: str = Form(""),
    due_date: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "work_orders:view")
    if error:
        return error

    work_order_id_i = _parse_int(work_order_id, 0)
    complaint_id_i = _parse_int(complaint_id, 0) or None
    facility_id_i = _parse_int(facility_id, 0) or None
    assignee_id_i = _parse_int(assignee_user_id, 0) or None
    completed_at = _now_text() if status in {"완료", "종결"} else ""

    conn = get_conn()
    if work_order_id_i:
        existing = conn.execute("SELECT * FROM work_orders WHERE id = ?", (work_order_id_i,)).fetchone()
        if not existing:
            conn.close()
            return _with_flash("/work-orders", "작업지시를 찾을 수 없습니다.", "error")
        if not _can_manage_work_order(user, existing):
            conn.close()
            return _with_flash(f"/work-orders?edit={work_order_id_i}", "이 작업지시의 기본정보를 수정할 권한이 없습니다.", "error")
        conn.execute(
            """
            UPDATE work_orders
            SET complaint_id = ?, category = ?, title = ?, facility_id = ?, requester_name = ?, priority = ?, status = ?, description = ?,
                assignee_user_id = ?, due_date = ?, completed_at = ?, updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                complaint_id_i,
                category.strip(),
                title.strip(),
                facility_id_i,
                requester_name.strip(),
                priority.strip(),
                status.strip(),
                description.strip(),
                assignee_id_i,
                due_date.strip(),
                completed_at,
                user["id"],
                _now_text(),
                work_order_id_i,
            ),
        )
        conn.execute(
            """
            INSERT INTO work_order_updates(work_order_id, update_type, body, actor_user_id, created_at)
            VALUES (?, '기본정보수정', ?, ?, ?)
            """,
            (work_order_id_i, "작업지시 기본정보 수정", user["id"], _now_text()),
        )
        _sync_complaint_for_work_order(conn, complaint_id_i, work_order_id_i, existing["work_code"], user["id"], assignee_id_i)
        _save_attachments(conn, "work_order", work_order_id_i, files, user["id"])
        conn.commit()
        conn.close()
        return _with_flash(f"/work-orders?edit={work_order_id_i}", "작업지시가 수정되었습니다.", "ok")

    if not auth.has_permission(user["role"], "work_orders:create"):
        conn.close()
        return _with_flash("/work-orders", "작업지시를 등록할 권한이 없습니다.", "error")

    cursor = conn.execute(
        """
        INSERT INTO work_orders(
            work_code, complaint_id, category, title, facility_id, requester_name, priority, status, description,
            assignee_user_id, due_date, completed_at, created_by, updated_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            complaint_id_i,
            category.strip(),
            title.strip(),
            facility_id_i,
            requester_name.strip(),
            priority.strip(),
            status.strip(),
            description.strip(),
            assignee_id_i,
            due_date.strip(),
            completed_at,
            user["id"],
            user["id"],
            _now_text(),
            _now_text(),
        ),
    )
    work_order_id_i = cursor.lastrowid
    work_code = f"WO-{work_order_id_i:04d}"
    conn.execute("UPDATE work_orders SET work_code = ? WHERE id = ?", (work_code, work_order_id_i))
    conn.execute(
        """
        INSERT INTO work_order_updates(work_order_id, update_type, body, actor_user_id, created_at)
        VALUES (?, '생성', ?, ?, ?)
        """,
        (work_order_id_i, "작업지시가 생성되었습니다.", user["id"], _now_text()),
    )
    _sync_complaint_for_work_order(conn, complaint_id_i, work_order_id_i, work_code, user["id"], assignee_id_i)
    _save_attachments(conn, "work_order", work_order_id_i, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/work-orders?edit={work_order_id_i}", "작업지시가 등록되었습니다.", "ok")


@app.post("/work-orders/update/{work_order_id}")
def work_orders_update(
    request: Request,
    work_order_id: int,
    update_type: str = Form(...),
    body: str = Form(...),
    status: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "work_orders:update")
    if error:
        return error

    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id = ?", (work_order_id,)).fetchone()
    if not order:
        conn.close()
        return _with_flash("/work-orders", "작업지시를 찾을 수 없습니다.", "error")
    if not _can_update_work_order(user, order):
        conn.close()
        return _with_flash(f"/work-orders?edit={work_order_id}", "이 작업지시를 업데이트할 권한이 없습니다.", "error")

    new_status = status.strip() or order["status"]
    completed_at = order["completed_at"]
    if new_status in {"완료", "종결"} and not completed_at:
        completed_at = _now_text()
    elif new_status not in {"완료", "종결"}:
        completed_at = ""

    conn.execute(
        """
        UPDATE work_orders
        SET status = ?, completed_at = ?, updated_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_status, completed_at, user["id"], _now_text(), work_order_id),
    )
    conn.execute(
        """
        INSERT INTO work_order_updates(work_order_id, update_type, body, actor_user_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (work_order_id, update_type.strip(), body.strip(), user["id"], _now_text()),
    )
    _save_attachments(conn, "work_order", work_order_id, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/work-orders?edit={work_order_id}", "작업 업데이트가 저장되었습니다.", "ok")


@app.post("/work-orders/delete/{work_order_id}")
def work_orders_delete(request: Request, work_order_id: int):
    user, error = _authorize(request, "work_orders:view")
    if error:
        return error
    conn = get_conn()
    order = conn.execute("SELECT * FROM work_orders WHERE id = ?", (work_order_id,)).fetchone()
    if not order:
        conn.close()
        return _with_flash("/work-orders", "작업지시를 찾을 수 없습니다.", "error")
    if not _can_delete_work_order(user, order):
        conn.close()
        return _with_flash(f"/work-orders?edit={work_order_id}", "이 작업지시를 삭제할 권한이 없습니다.", "error")
    _delete_attachments(conn, "work_order", work_order_id)
    conn.execute("DELETE FROM work_order_updates WHERE work_order_id = ?", (work_order_id,))
    conn.execute("DELETE FROM work_orders WHERE id = ?", (work_order_id,))
    conn.commit()
    conn.close()
    return _with_flash("/work-orders", "작업지시가 삭제되었습니다.", "ok")


@app.get("/complaints", response_class=HTMLResponse)
def complaints_page(request: Request):
    user, error = _authorize(request, "complaints:view")
    if error:
        return error

    can_create = auth.has_permission(user["role"], "complaints:create")
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    channel = request.query_params.get("channel", "").strip()
    priority = request.query_params.get("priority", "").strip()
    edit_id = _parse_int(request.query_params.get("edit", ""), 0)

    conn = get_conn()
    complaints = _fetch_complaint_rows(conn, q, status, channel, priority)
    attachments = _attachment_map(conn, "complaint", [row["id"] for row in complaints])
    edit_row = (
        conn.execute(
            """
            SELECT c.*, f.name AS facility_name, u.full_name AS assignee_name,
                   cf.id AS feedback_id, cf.rating AS feedback_rating, cf.comment AS feedback_comment, cf.follow_up_at
            FROM complaints c
            LEFT JOIN facilities f ON f.id = c.facility_id
            LEFT JOIN users u ON u.id = c.assignee_user_id
            LEFT JOIN complaint_feedback cf ON cf.complaint_id = c.id
            WHERE c.id = ?
            """,
            (edit_id,),
        ).fetchone()
        if edit_id
        else None
    )
    updates = (
        conn.execute(
            """
            SELECT cu.*, us.full_name AS actor_name
            FROM complaint_updates cu
            LEFT JOIN users us ON us.id = cu.created_by
            WHERE cu.complaint_id = ?
            ORDER BY cu.created_at DESC, cu.id DESC
            LIMIT 20
            """,
            (edit_id,),
        ).fetchall()
        if edit_id
        else []
    )
    feedback_row = (
        conn.execute(
            """
            SELECT cf.*, cu.full_name AS created_by_name, uu.full_name AS updated_by_name
            FROM complaint_feedback cf
            LEFT JOIN users cu ON cu.id = cf.created_by
            LEFT JOIN users uu ON uu.id = cf.updated_by
            WHERE cf.complaint_id = ?
            """,
            (edit_id,),
        ).fetchone()
        if edit_id
        else None
    )
    linked_work_orders = (
        conn.execute(
            """
            SELECT w.*, f.name AS facility_name, u.full_name AS assignee_name
            FROM work_orders w
            LEFT JOIN facilities f ON f.id = w.facility_id
            LEFT JOIN users u ON u.id = w.assignee_user_id
            WHERE w.complaint_id = ?
            ORDER BY w.updated_at DESC, w.id DESC
            """,
            (edit_id,),
        ).fetchall()
        if edit_id
        else []
    )
    repeat_rows = _complaint_repeat_candidates(conn, edit_row, limit=6) if edit_row else []
    template_rows = _complaint_template_rows(conn, edit_row["category_primary"] if edit_row else "")
    facility_options = _facility_options(conn)
    assignee_options = _user_options(conn, include_viewers=False)
    conn.close()

    can_manage_edit_row = _can_manage_complaint(user, edit_row)
    can_update_edit_row = _can_update_complaint(user, edit_row)
    can_delete_edit_row = _can_delete_complaint(user, edit_row)

    signal_html = ""
    if edit_row:
        sla_label, sla_tone, sla_note = _complaint_sla_meta(edit_row)
        repeat_note = (
            f"최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 내 유사 민원 {len(repeat_rows)}건"
            if repeat_rows
            else f"최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 내 반복 민원이 없습니다."
        )
        feedback_note = (
            f"{feedback_row['rating']}점 / {fmt_date(feedback_row['follow_up_at'])} 확인"
            if feedback_row
            else "만족도 기록이 아직 없습니다."
        )
        signal_html = (
            "<div class='grid two' style='margin:14px 0;'>"
            + "<div style='border:1px solid rgba(23,33,43,0.08); border-radius:18px; padding:14px; background:#fff;'>"
            + "<label>SLA 신호</label>"
            + f"<div>{_badge(sla_label, sla_tone)}</div><div class='muted' style='margin-top:8px'>{esc(sla_note)}</div>"
            + "</div>"
            + "<div style='border:1px solid rgba(23,33,43,0.08); border-radius:18px; padding:14px; background:#fff;'>"
            + "<label>반복 / 만족도</label>"
            + f"<div>{_badge(f'반복 {len(repeat_rows)}건' if repeat_rows else '반복 없음', 'danger' if repeat_rows else 'good')} {_complaint_feedback_badge(feedback_row['rating'] if feedback_row else None)}</div>"
            + f"<div class='muted' style='margin-top:8px'>{esc(repeat_note)} / {esc(feedback_note)}</div>"
            + "</div></div>"
        )

    form_html = ""
    if can_create or edit_row:
        if edit_row and not (can_manage_edit_row or can_update_edit_row):
            form_html = signal_html + info_box("권한 제한", "이 민원은 본인 접수 건이나 본인 배정 건일 때만 수정 또는 업데이트할 수 있습니다.")
        elif edit_row and not can_manage_edit_row:
            form_html = (
                "<section class='panel'><h2>민원 상세</h2><p class='muted'>기본정보 수정 권한은 없고, 진행 업데이트만 가능합니다.</p>"
                + signal_html
                + "<div class='stack'>"
                + info_box("민원 번호", esc(edit_row["complaint_code"]))
                + info_box("민원 제목", esc(edit_row["title"]))
                + info_box("민원 내용", esc(edit_row["description"] or "-"))
                + info_box("민원인", esc(edit_row["requester_name"] or "-"))
                + "</div>"
            )
        else:
            form_html = (
                "<section class='panel'><h2>민원 등록 / 수정</h2><p class='muted'>민원 접수, 분류, 배정, 회신 이력을 접수 기준으로 관리합니다.</p>"
                + signal_html
                + "<form action='/complaints/save' method='post' enctype='multipart/form-data' class='stack'>"
                + f"<input type='hidden' name='complaint_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
                + (
                    f"<div><label>민원 번호</label><input value='{esc(edit_row['complaint_code'])}' disabled></div>"
                    if edit_row
                    else ""
                )
                + "<div class='grid two'>"
                + f"<div><label>접수 채널</label><select name='channel'>{render_options(COMPLAINT_CHANNEL_OPTIONS, edit_row['channel'] if edit_row else '전화')}</select></div>"
                + f"<div><label>대상 시설</label><select name='facility_id'>{render_options(facility_options, str(edit_row['facility_id'] or '') if edit_row else '', blank_label='미지정')}</select></div>"
                + "</div>"
                + "<div class='grid two'>"
                + f"<div><label>1차 분류</label><select name='category_primary'>{render_options(COMPLAINT_CATEGORY_OPTIONS, edit_row['category_primary'] if edit_row else '', blank_label='선택')}</select></div>"
                + f"<div><label>2차 분류</label><input name='category_secondary' value='{esc(edit_row['category_secondary'] if edit_row else '')}' placeholder='세부 유형'></div>"
                + "</div>"
                + f"<div><label>민원 제목</label><input name='title' value='{esc(edit_row['title'] if edit_row else '')}' required></div>"
                + "<div class='grid two'>"
                + f"<div><label>동/호 또는 위치</label><input name='unit_label' value='{esc(edit_row['unit_label'] if edit_row else '')}' placeholder='예: 101동 1203호'></div>"
                + f"<div><label>세부 위치</label><input name='location_detail' value='{esc(edit_row['location_detail'] if edit_row else '')}' placeholder='예: 주방 천장'></div>"
                + "</div>"
                + "<div class='grid two'>"
                + f"<div><label>민원인</label><input name='requester_name' value='{esc(edit_row['requester_name'] if edit_row else '')}'></div>"
                + f"<div><label>연락처</label><input name='requester_phone' value='{esc(edit_row['requester_phone'] if edit_row else '')}' inputmode='tel' placeholder='01012345678'></div>"
                + "</div>"
                + "<div class='grid two'>"
                + f"<div><label>이메일</label><input name='requester_email' type='email' value='{esc(edit_row['requester_email'] if edit_row else '')}'></div>"
                + f"<div><label>담당자</label><select name='assignee_user_id'>{render_options(assignee_options, str(edit_row['assignee_user_id'] or '') if edit_row else '', blank_label='미지정')}</select></div>"
                + "</div>"
                + "<div class='grid two'>"
                + f"<div><label>우선도</label><select name='priority'>{render_options(COMPLAINT_PRIORITY_OPTIONS, edit_row['priority'] if edit_row else '보통')}</select></div>"
                + f"<div><label>상태</label><select name='status'>{render_options(COMPLAINT_STATUS_OPTIONS, edit_row['status'] if edit_row else '접수')}</select></div>"
                + "</div>"
                + f"<div><label>회신 목표일</label><input name='response_due_at' type='date' value='{esc(fmt_date(edit_row['response_due_at']) if edit_row else _complaint_due_default('보통'))}'><div class='muted'>비워 두면 우선도 기준 SLA({', '.join(f'{k} {v}일' for k, v in COMPLAINT_SLA_DAYS.items())})로 자동 계산됩니다.</div></div>"
                + f"<div><label>민원 내용</label><textarea name='description'>{esc(edit_row['description'] if edit_row else '')}</textarea></div>"
                + "<div><label>사진 / 첨부</label><input name='files' type='file' accept='image/*' multiple></div>"
                + "<div class='row-actions'>"
                + "<button class='btn primary' type='submit'>저장</button>"
                + "<a class='btn secondary' href='/complaints'>새로 입력</a>"
                + (
                    f"<a class='btn secondary' href='/work-orders?complaint_id={edit_row['id']}'>작업지시 생성</a>"
                    if edit_row and auth.has_permission(user["role"], "work_orders:create")
                    else ""
                )
                + (
                    _post_action_button(f"/complaints/delete/{edit_row['id']}", "삭제", "민원을 삭제하시겠습니까?")
                    if edit_row and can_delete_edit_row
                    else ""
                )
                + "</div></form>"
            )

        if edit_row and (can_manage_edit_row or can_update_edit_row):
            form_html += "<div style='margin-top:14px'><label>현재 첨부</label>" + attachment_gallery(attachments.get(edit_row["id"], [])) + "</div>"

        if edit_row:
            linked_work_html = (
                "".join(
                    """
                    <tr>
                      <td>{code}</td>
                      <td><strong>{title}</strong><div class='muted'>{facility}</div></td>
                      <td>{status}</td>
                      <td>{assignee}</td>
                      <td><a class='btn secondary' href='/work-orders?edit={work_id}'>열기</a></td>
                    </tr>
                    """.format(
                        code=esc(row["work_code"]),
                        title=esc(row["title"]),
                        facility=esc(row["facility_name"] or "시설 미지정"),
                        status=status_badge(row["status"]),
                        assignee=esc(row["assignee_name"] or "미배정"),
                        work_id=row["id"],
                    )
                    for row in linked_work_orders
                )
                if linked_work_orders
                else "<tr><td colspan='5' class='muted'>연결된 작업지시가 없습니다.</td></tr>"
            )
            form_html += (
                "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                "<div class='split'><h3>연결된 작업지시</h3>"
                + (
                    f"<a class='btn secondary' href='/work-orders?complaint_id={edit_row['id']}'>작업지시 생성</a>"
                    if auth.has_permission(user["role"], "work_orders:create")
                    else ""
                )
                + "</div>"
                + "<table><thead><tr><th>번호</th><th>작업</th><th>상태</th><th>담당</th><th>관리</th></tr></thead>"
                + f"<tbody>{linked_work_html}</tbody></table></div>"
            )

            repeat_rows_html = (
                "".join(
                    """
                    <tr>
                      <td>{code}</td>
                      <td><strong>{title}</strong><div class='muted'>{requester}</div></td>
                      <td>{location}<div class='muted'>{facility}</div></td>
                      <td>{status}</td>
                      <td><a class='btn secondary' href='/complaints?edit={complaint_id}'>열기</a></td>
                    </tr>
                    """.format(
                        code=esc(row["complaint_code"]),
                        title=esc(row["title"]),
                        requester=esc(f"{row['requester_name'] or '-'} / {row['requester_phone'] or '-'}"),
                        location=esc(row["unit_label"] or row["location_detail"] or "-"),
                        facility=esc(row["facility_name"] or "시설 미지정"),
                        status=status_badge(row["status"]),
                        complaint_id=row["id"],
                    )
                    for row in repeat_rows
                )
                if repeat_rows
                else f"<tr><td colspan='5' class='muted'>최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 내 반복 민원이 감지되지 않았습니다.</td></tr>"
            )
            form_html += (
                "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                f"<h3>반복 민원 감지</h3><p class='muted'>같은 연락처 기준 최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 내 유사 민원을 보여 줍니다.</p>"
                + "<table><thead><tr><th>번호</th><th>민원</th><th>위치</th><th>상태</th><th>관리</th></tr></thead>"
                + f"<tbody>{repeat_rows_html}</tbody></table></div>"
            )

        if edit_row and can_update_edit_row:
            template_options = "".join(
                f"<option value='{row['id']}'>{esc(row['name'])} ({esc(row['category_primary'] or '공통')})</option>"
                for row in template_rows
            )
            template_payload = json.dumps(
                {
                    str(row["id"]): {
                        "body": row["body"],
                        "update_type": row["update_type"],
                        "status_to": row["status_to"],
                        "is_public_note": int(row["is_public_note"] or 0),
                    }
                    for row in template_rows
                },
                ensure_ascii=False,
            ).replace("</", "<\\/")
            updates_html = (
                "".join(
                    """
                    <tr>
                      <td>{when}</td>
                      <td>{type}</td>
                      <td>{status_move}</td>
                      <td>{public_note}</td>
                      <td>{body}</td>
                      <td>{actor}</td>
                    </tr>
                    """.format(
                        when=esc(fmt_datetime(row["created_at"])),
                        type=status_badge(row["update_type"]),
                        status_move=esc(f"{row['status_from'] or '-'} -> {row['status_to'] or '-'}"),
                        public_note=status_badge("회신" if row["is_public_note"] else "내부"),
                        body=esc(row["message"]),
                        actor=esc(row["actor_name"] or "system"),
                    )
                    for row in updates
                )
                if updates
                else "<tr><td colspan='6' class='muted'>민원 이력이 없습니다.</td></tr>"
            )
            feedback_summary_html = (
                "<div class='muted'>조치 후 확인된 만족도를 기록합니다. 4점 이상은 만족, 2점 이하는 불만족 신호로 집계합니다.</div>"
                + (
                    f"<div style='margin-top:10px'>{_complaint_feedback_badge(feedback_row['rating'])} <span class='muted'>후속 연락 {esc(fmt_date(feedback_row['follow_up_at']))} / 최종 수정 {esc(feedback_row['updated_by_name'] or feedback_row['created_by_name'] or 'system')}</span></div>"
                    if feedback_row
                    else "<div style='margin-top:10px' class='muted'>기록된 만족도가 없습니다.</div>"
                )
            )
            form_html += (
                "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                "<h3>민원 업데이트</h3><p class='muted'>회신, 상태변경, 재오픈, 내부 메모를 민원 기준으로 남깁니다.</p>"
                f"<form action='/complaints/update/{edit_row['id']}' method='post' enctype='multipart/form-data' class='stack'>"
                + (
                    "<div class='grid two'>"
                    + f"<div><label>회신 템플릿</label><select id='complaint-template-select'><option value=''>선택</option>{template_options}</select><div class='muted'>공통 템플릿과 현재 분류용 템플릿만 보입니다.</div></div>"
                    + "<div style='display:flex; align-items:flex-end;'><button class='btn secondary' id='complaint-template-apply' type='button'>템플릿 적용</button></div>"
                    + "</div>"
                    if template_rows
                    else ""
                )
                + "<div class='grid two'>"
                + f"<div><label>업데이트 유형</label><select id='complaint-update-type' name='update_type'>{render_options(COMPLAINT_UPDATE_TYPE_OPTIONS, '내부메모')}</select></div>"
                + f"<div><label>새 상태</label><select id='complaint-update-status' name='status'>{render_options(COMPLAINT_STATUS_OPTIONS, edit_row['status'], blank_label='상태 유지')}</select></div>"
                + "</div>"
                + "<label style='display:flex;align-items:center;gap:8px;'><input id='complaint-update-public' name='is_public_note' type='checkbox' value='1' style='width:auto;'>민원인 회신용 메모로 표시</label>"
                + "<div><label>업데이트 내용</label><textarea id='complaint-update-message' name='message' required></textarea></div>"
                + "<div><label>추가 첨부</label><input name='files' type='file' accept='image/*' multiple></div>"
                + "<div class='row-actions'><button class='btn warn' type='submit'>업데이트 저장</button></div>"
                + "</form>"
                + "<div style='margin-top:14px'><h3>최근 민원 이력</h3>"
                + "<table><thead><tr><th>시각</th><th>유형</th><th>상태변경</th><th>구분</th><th>내용</th><th>작성자</th></tr></thead>"
                + f"<tbody>{updates_html}</tbody></table></div></div>"
                + (
                    "<script>"
                    "(function(){"
                    "const templates = " + template_payload + ";"
                    "const select = document.getElementById('complaint-template-select');"
                    "const applyButton = document.getElementById('complaint-template-apply');"
                    "if(!select || !applyButton){return;}"
                    "applyButton.addEventListener('click', function(){"
                    "const selected = templates[select.value];"
                    "if(!selected){return;}"
                    "const message = document.getElementById('complaint-update-message');"
                    "const updateType = document.getElementById('complaint-update-type');"
                    "const statusField = document.getElementById('complaint-update-status');"
                    "const publicField = document.getElementById('complaint-update-public');"
                    "if(message){message.value = message.value.trim() ? message.value.trim() + '\\n\\n' + selected.body : selected.body;}"
                    "if(updateType && selected.update_type){updateType.value = selected.update_type;}"
                    "if(statusField && selected.status_to){statusField.value = selected.status_to;}"
                    "if(publicField){publicField.checked = !!selected.is_public_note;}"
                    "});"
                    "})();"
                    "</script>"
                    if template_rows
                    else ""
                )
                + "<div class='panel' style='margin-top:16px; padding:0; border:none; box-shadow:none;'>"
                + "<h3>만족도 기록</h3>"
                + feedback_summary_html
                + f"<form action='/complaints/feedback/{edit_row['id']}' method='post' class='stack' style='margin-top:12px;'>"
                + "<div class='grid two'>"
                + f"<div><label>만족도</label><select name='rating'>{render_options([('5', '5점 매우 만족'), ('4', '4점 만족'), ('3', '3점 보통'), ('2', '2점 불만'), ('1', '1점 매우 불만')], str(feedback_row['rating']) if feedback_row else '4')}</select></div>"
                + f"<div><label>후속 연락일</label><input name='follow_up_at' type='date' value='{esc(fmt_date(feedback_row['follow_up_at']) if feedback_row else _today_text())}'></div>"
                + "</div>"
                + f"<div><label>코멘트</label><textarea name='comment'>{esc(feedback_row['comment'] if feedback_row else '')}</textarea></div>"
                + "<div class='row-actions'><button class='btn secondary' type='submit'>만족도 저장</button></div>"
                + "</form></div>"
            )
        form_html += "</section>"
    else:
        form_html = info_box("읽기 전용", "현재 계정은 민원 조회만 가능합니다. 등록과 수정은 작업자 이상 권한이 필요합니다.")

    if complaints:
        rows_html = []
        for row in complaints:
            due_text = fmt_date(row["response_due_at"])
            row_actions = []
            if _can_manage_complaint(user, row):
                row_actions.append(f"<a class='btn secondary' href='/complaints?edit={row['id']}'>수정</a>")
            elif _can_update_complaint(user, row):
                row_actions.append(f"<a class='btn secondary' href='/complaints?edit={row['id']}'>업데이트</a>")
            else:
                row_actions.append("<span class='muted'>조회</span>")
            repeat_badge = _badge(f"반복 {row['repeat_count']}건", "danger") if int(row["repeat_count"] or 0) else ""
            feedback_badge = _complaint_feedback_badge(row["feedback_rating"]) if row["feedback_rating"] else ""
            rows_html.append(
                """
                <tr>
                  <td>{code}</td>
                  <td><strong>{title}</strong><div class='muted'>{channel} · {category}</div><div class='muted'>{requester}</div></td>
                  <td>{location}<div class='muted'>{facility}</div></td>
                  <td>{priority}<div style='margin-top:6px'>{status}</div><div style='margin-top:6px'>{sla}</div></td>
                  <td>{assignee}<div class='muted'>회신 목표 {due}</div><div style='margin-top:6px'>{repeat_badge} {feedback_badge}</div></td>
                  <td>{work_count}건</td>
                  <td>{actions}</td>
                </tr>
                """.format(
                    code=esc(row["complaint_code"]),
                    title=esc(row["title"]),
                    channel=esc(row["channel"]),
                    category=esc(row["category_primary"] or "-"),
                    requester=esc(f"{row['requester_name'] or '-'} / {row['requester_phone'] or '-'}"),
                    location=esc(row["unit_label"] or row["location_detail"] or "-"),
                    facility=esc(row["facility_name"] or "시설 미지정"),
                    priority=status_badge(row["priority"]),
                    status=status_badge(row["status"]),
                    sla=_complaint_sla_badge(row),
                    assignee=esc(row["assignee_name"] or "미배정"),
                    due=esc(due_text),
                    repeat_badge=repeat_badge,
                    feedback_badge=feedback_badge,
                    work_count=esc(row["work_count"]),
                    actions="".join(row_actions),
                )
            )
        list_html = (
            "<section class='panel'><div class='split'><div><h2>민원 목록</h2><p class='muted'>접수부터 회신, 종결까지 민원 기준으로 추적합니다.</p></div>"
            "<form class='inline-form' method='get' action='/complaints'>"
            f"<input name='q' value='{esc(q)}' placeholder='번호, 제목, 민원인, 연락처, 위치 검색'>"
            f"<select name='status'>{render_options(COMPLAINT_STATUS_OPTIONS, status, blank_label='전체 상태')}</select>"
            f"<select name='channel'>{render_options(COMPLAINT_CHANNEL_OPTIONS, channel, blank_label='전체 채널')}</select>"
            f"<select name='priority'>{render_options(COMPLAINT_PRIORITY_OPTIONS, priority, blank_label='전체 우선도')}</select>"
            "<button class='btn secondary' type='submit'>검색</button>"
            "</form></div>"
            "<table><thead><tr><th>번호</th><th>민원</th><th>위치</th><th>우선도/상태</th><th>담당/기한</th><th>연결 작업</th><th>관리</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></section>"
        )
    else:
        list_html = "<section class='panel'><h2>민원 목록</h2>" + empty_state("조건에 맞는 민원이 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    pdf_params = [(key, value) for key, value in [("q", q), ("status", status), ("channel", channel), ("priority", priority)] if value]
    pdf_href = "/complaints/pdf" + (f"?{urlencode(pdf_params)}" if pdf_params else "")
    body = (
        page_header(
            "Complaints",
            "민원 관리",
            "민원 접수, 분류, 배정, 회신, 작업지시 연결을 하나의 흐름으로 관리합니다.",
            actions=f"<a class='btn secondary' href='{esc(pdf_href)}' target='_blank' rel='noopener'>PDF 출력</a>",
        )
        + "<div class='layout-2'>"
        + form_html
        + list_html
        + "</div>"
    )
    return HTMLResponse(layout(title="민원 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.get("/complaints/pdf")
def complaints_pdf(request: Request):
    user, error = _authorize(request, "complaints:view")
    if error:
        return error

    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    channel = request.query_params.get("channel", "").strip()
    priority = request.query_params.get("priority", "").strip()

    conn = get_conn()
    complaint_rows = _fetch_complaint_rows(conn, q, status, channel, priority)
    conn.close()
    pdf_bytes = _build_complaints_pdf(
        complaint_rows,
        q=q,
        status=status,
        channel=channel,
        priority=priority,
    )
    filename = f"complaints-report-{date.today().strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/complaints/save")
def complaints_save(
    request: Request,
    complaint_id: str = Form(""),
    channel: str = Form("전화"),
    category_primary: str = Form(""),
    category_secondary: str = Form(""),
    facility_id: str = Form(""),
    unit_label: str = Form(""),
    location_detail: str = Form(""),
    requester_name: str = Form(""),
    requester_phone: str = Form(""),
    requester_email: str = Form(""),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("보통"),
    status: str = Form("접수"),
    response_due_at: str = Form(""),
    assignee_user_id: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "complaints:view")
    if error:
        return error

    complaint_id_i = _parse_int(complaint_id, 0)
    facility_id_i = _parse_int(facility_id, 0) or None
    assignee_id_i = _parse_int(assignee_user_id, 0) or None
    requester_phone_v = auth.normalize_phone(requester_phone)
    requester_email_v = requester_email.strip()

    conn = get_conn()
    if complaint_id_i:
        existing = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id_i,)).fetchone()
        if not existing:
            conn.close()
            return _with_flash("/complaints", "민원을 찾을 수 없습니다.", "error")
        if not _can_manage_complaint(user, existing):
            conn.close()
            return _with_flash(f"/complaints?edit={complaint_id_i}", "이 민원의 기본정보를 수정할 권한이 없습니다.", "error")

        response_due_at_v = _normalize_complaint_due_date(response_due_at, priority.strip(), existing)
        resolved_at, closed_at = _complaint_timestamps(status.strip(), existing)
        conn.execute(
            """
            UPDATE complaints
            SET channel = ?, category_primary = ?, category_secondary = ?, facility_id = ?, unit_label = ?, location_detail = ?,
                requester_name = ?, requester_phone = ?, requester_email = ?, title = ?, description = ?, priority = ?, status = ?,
                response_due_at = ?, resolved_at = ?, closed_at = ?, assignee_user_id = ?, updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                channel.strip(),
                category_primary.strip(),
                category_secondary.strip(),
                facility_id_i,
                unit_label.strip(),
                location_detail.strip(),
                requester_name.strip(),
                requester_phone_v,
                requester_email_v,
                title.strip(),
                description.strip(),
                priority.strip(),
                status.strip(),
                response_due_at_v,
                resolved_at,
                closed_at,
                assignee_id_i,
                user["id"],
                _now_text(),
                complaint_id_i,
            ),
        )
        _record_complaint_update(
            conn,
            complaint_id_i,
            "기본정보수정",
            "민원 기본정보가 수정되었습니다.",
            user["id"],
            status_from=existing["status"] if existing["status"] != status.strip() else "",
            status_to=status.strip() if existing["status"] != status.strip() else "",
        )
        _save_attachments(conn, "complaint", complaint_id_i, files, user["id"])
        conn.commit()
        conn.close()
        return _with_flash(f"/complaints?edit={complaint_id_i}", "민원이 수정되었습니다.", "ok")

    if not auth.has_permission(user["role"], "complaints:create"):
        conn.close()
        return _with_flash("/complaints", "민원을 등록할 권한이 없습니다.", "error")

    response_due_at_v = _normalize_complaint_due_date(response_due_at, priority.strip())
    resolved_at, closed_at = _complaint_timestamps(status.strip())
    cursor = conn.execute(
        """
        INSERT INTO complaints(
            complaint_code, channel, category_primary, category_secondary, facility_id, unit_label, location_detail,
            requester_name, requester_phone, requester_email, title, description, priority, status, response_due_at,
            resolved_at, closed_at, assignee_user_id, created_by, updated_by, created_at, updated_at
        )
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel.strip(),
            category_primary.strip(),
            category_secondary.strip(),
            facility_id_i,
            unit_label.strip(),
            location_detail.strip(),
            requester_name.strip(),
            requester_phone_v,
            requester_email_v,
            title.strip(),
            description.strip(),
            priority.strip(),
            status.strip(),
            response_due_at_v,
            resolved_at,
            closed_at,
            assignee_id_i,
            user["id"],
            user["id"],
            _now_text(),
            _now_text(),
        ),
    )
    complaint_id_i = cursor.lastrowid
    conn.execute("UPDATE complaints SET complaint_code = ? WHERE id = ?", (f"CP-{complaint_id_i:04d}", complaint_id_i))
    _record_complaint_update(conn, complaint_id_i, "접수", "민원이 접수되었습니다.", user["id"], status_to=status.strip())
    _save_attachments(conn, "complaint", complaint_id_i, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/complaints?edit={complaint_id_i}", "민원이 등록되었습니다.", "ok")


@app.post("/complaints/update/{complaint_id}")
def complaints_update(
    request: Request,
    complaint_id: int,
    update_type: str = Form(...),
    message: str = Form(...),
    status: str = Form(""),
    is_public_note: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    user, error = _authorize(request, "complaints:update")
    if error:
        return error

    conn = get_conn()
    complaint = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if not complaint:
        conn.close()
        return _with_flash("/complaints", "민원을 찾을 수 없습니다.", "error")
    if not _can_update_complaint(user, complaint):
        conn.close()
        return _with_flash(f"/complaints?edit={complaint_id}", "이 민원을 업데이트할 권한이 없습니다.", "error")

    new_status = status.strip() or complaint["status"]
    resolved_at, closed_at = _complaint_timestamps(new_status, complaint)
    conn.execute(
        """
        UPDATE complaints
        SET status = ?, resolved_at = ?, closed_at = ?, updated_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_status, resolved_at, closed_at, user["id"], _now_text(), complaint_id),
    )
    _record_complaint_update(
        conn,
        complaint_id,
        update_type.strip(),
        message.strip(),
        user["id"],
        status_from=complaint["status"] if complaint["status"] != new_status else "",
        status_to=new_status if complaint["status"] != new_status else "",
        is_public_note=1 if _bool_from_form(is_public_note) else 0,
    )
    _save_attachments(conn, "complaint", complaint_id, files, user["id"])
    conn.commit()
    conn.close()
    return _with_flash(f"/complaints?edit={complaint_id}", "민원 업데이트가 저장되었습니다.", "ok")


@app.post("/complaints/feedback/{complaint_id}")
def complaints_feedback(
    request: Request,
    complaint_id: int,
    rating: str = Form(...),
    comment: str = Form(""),
    follow_up_at: str = Form(""),
):
    user, error = _authorize(request, "complaints:update")
    if error:
        return error

    rating_i = _parse_int(rating, 0)
    if rating_i < 1 or rating_i > 5:
        return _with_flash(f"/complaints?edit={complaint_id}", "만족도는 1점부터 5점 사이여야 합니다.", "error")

    conn = get_conn()
    complaint = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if not complaint:
        conn.close()
        return _with_flash("/complaints", "민원을 찾을 수 없습니다.", "error")
    if not _can_update_complaint(user, complaint):
        conn.close()
        return _with_flash(f"/complaints?edit={complaint_id}", "이 민원의 만족도를 기록할 권한이 없습니다.", "error")

    existing = conn.execute("SELECT * FROM complaint_feedback WHERE complaint_id = ?", (complaint_id,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE complaint_feedback
            SET rating = ?, comment = ?, follow_up_at = ?, updated_by = ?, updated_at = ?
            WHERE complaint_id = ?
            """,
            (rating_i, comment.strip(), follow_up_at.strip(), user["id"], _now_text(), complaint_id),
        )
        action_message = "만족도 정보가 수정되었습니다."
    else:
        conn.execute(
            """
            INSERT INTO complaint_feedback(
                complaint_id, rating, comment, follow_up_at, created_by, updated_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (complaint_id, rating_i, comment.strip(), follow_up_at.strip(), user["id"], user["id"], _now_text(), _now_text()),
        )
        action_message = "만족도 정보가 기록되었습니다."

    conn.execute(
        "UPDATE complaints SET updated_by = ?, updated_at = ? WHERE id = ?",
        (user["id"], _now_text(), complaint_id),
    )
    _record_complaint_update(
        conn,
        complaint_id,
        "만족도",
        f"만족도 {rating_i}점이 기록되었습니다." + (f" 메모: {comment.strip()}" if comment.strip() else ""),
        user["id"],
    )
    conn.commit()
    conn.close()
    return _with_flash(f"/complaints?edit={complaint_id}", action_message, "ok")


@app.post("/complaints/delete/{complaint_id}")
def complaints_delete(request: Request, complaint_id: int):
    user, error = _authorize(request, "complaints:view")
    if error:
        return error
    conn = get_conn()
    complaint = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
    if not complaint:
        conn.close()
        return _with_flash("/complaints", "민원을 찾을 수 없습니다.", "error")
    if not _can_delete_complaint(user, complaint):
        conn.close()
        return _with_flash(f"/complaints?edit={complaint_id}", "이 민원을 삭제할 권한이 없습니다.", "error")
    _delete_attachments(conn, "complaint", complaint_id)
    conn.execute("UPDATE work_orders SET complaint_id = NULL, updated_by = ?, updated_at = ? WHERE complaint_id = ?", (user["id"], _now_text(), complaint_id))
    conn.execute("DELETE FROM complaint_updates WHERE complaint_id = ?", (complaint_id,))
    conn.execute("DELETE FROM complaints WHERE id = ?", (complaint_id,))
    conn.commit()
    conn.close()
    return _with_flash("/complaints", "민원이 삭제되었습니다.", "ok")


@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    user, error = _authorize(request, "reports:view")
    if error:
        return error

    start = request.query_params.get("start", _month_start_text())
    end = request.query_params.get("end", _today_text())
    conn = get_conn()
    created_work = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM work_orders
        WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()["count"]
    completed_work = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM work_orders
        WHERE completed_at != ''
          AND substr(completed_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()["count"]
    open_work = conn.execute(
        "SELECT COUNT(*) AS count FROM work_orders WHERE status NOT IN ('완료', '종결')"
    ).fetchone()["count"]
    overdue_work = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM work_orders
        WHERE due_date != ''
          AND due_date < ?
          AND status NOT IN ('완료', '종결')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    created_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()["count"]
    closed_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE closed_at != ''
          AND substr(closed_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()["count"]
    open_complaints = conn.execute(
        "SELECT COUNT(*) AS count FROM complaints WHERE status NOT IN ('종결', '취소')"
    ).fetchone()["count"]
    overdue_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE response_due_at != ''
          AND response_due_at < ?
          AND status NOT IN ('회신완료', '종결', '취소')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    due_today_complaints = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM complaints
        WHERE response_due_at = ?
          AND status NOT IN ('회신완료', '종결', '취소')
        """,
        (_today_text(),),
    ).fetchone()["count"]
    repeat_complaints = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM complaints c
        WHERE substr(c.created_at, 1, 10) BETWEEN ? AND ?
          AND EXISTS (
            SELECT 1
            FROM complaints c2
            WHERE c2.id != c.id
              AND c.requester_phone != ''
              AND c2.requester_phone = c.requester_phone
              AND c2.created_at >= datetime(c.created_at, '-{COMPLAINT_REPEAT_WINDOW_DAYS} days')
              AND (
                (c.facility_id IS NOT NULL AND c2.facility_id = c.facility_id)
                OR (c.unit_label != '' AND c2.unit_label = c.unit_label)
                OR (c.location_detail != '' AND c2.location_detail = c.location_detail)
                OR (c.category_primary != '' AND c2.category_primary = c.category_primary)
              )
          )
        """,
        (start, end),
    ).fetchone()["count"]
    feedback_stats = conn.execute(
        """
        SELECT COUNT(*) AS count,
               ROUND(AVG(rating), 1) AS avg_rating,
               SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS satisfied_count,
               SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS dissatisfied_count
        FROM complaint_feedback
        WHERE substr(updated_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    low_stock_rows = conn.execute(
        """
        SELECT item_code, name, quantity, min_quantity, unit, location
        FROM inventory_items
        WHERE quantity <= min_quantity
        ORDER BY quantity ASC, min_quantity DESC, name ASC
        LIMIT 10
        """
    ).fetchall()
    facility_status_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM facilities
        GROUP BY status
        ORDER BY count DESC, status ASC
        """
    ).fetchall()
    high_priority_rows = conn.execute(
        """
        SELECT work_code, title, priority, status, due_date
        FROM work_orders
        WHERE priority IN ('긴급', '높음')
          AND status NOT IN ('완료', '종결')
        ORDER BY CASE priority WHEN '긴급' THEN 1 ELSE 2 END, due_date ASC, updated_at DESC
        LIMIT 10
        """
    ).fetchall()
    recent_updates = conn.execute(
        """
        SELECT u.created_at, u.update_type, u.body, w.work_code, w.title
        FROM work_order_updates u
        JOIN work_orders w ON w.id = u.work_order_id
        WHERE substr(u.created_at, 1, 10) BETWEEN ? AND ?
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT 8
        """,
        (start, end),
    ).fetchall()
    complaint_updates = conn.execute(
        """
        SELECT cu.created_at, cu.update_type, cu.message, cu.status_from, cu.status_to, c.complaint_code, c.title
        FROM complaint_updates cu
        JOIN complaints c ON c.id = cu.complaint_id
        WHERE substr(cu.created_at, 1, 10) BETWEEN ? AND ?
        ORDER BY cu.created_at DESC, cu.id DESC
        LIMIT 8
        """,
        (start, end),
    ).fetchall()
    overdue_complaint_rows = conn.execute(
        """
        SELECT complaint_code, title, requester_name, priority, status, response_due_at
        FROM complaints
        WHERE response_due_at != ''
          AND response_due_at < ?
          AND status NOT IN ('회신완료', '종결', '취소')
        ORDER BY CASE priority WHEN '긴급' THEN 1 WHEN '높음' THEN 2 ELSE 3 END, response_due_at ASC, updated_at DESC
        LIMIT 10
        """,
        (_today_text(),),
    ).fetchall()
    repeat_complaint_rows = conn.execute(
        f"""
        SELECT c.complaint_code, c.title, c.requester_name, c.requester_phone, c.category_primary, c.status, c.created_at
        FROM complaints c
        WHERE substr(c.created_at, 1, 10) BETWEEN ? AND ?
          AND EXISTS (
            SELECT 1
            FROM complaints c2
            WHERE c2.id != c.id
              AND c.requester_phone != ''
              AND c2.requester_phone = c.requester_phone
              AND c2.created_at >= datetime(c.created_at, '-{COMPLAINT_REPEAT_WINDOW_DAYS} days')
              AND (
                (c.facility_id IS NOT NULL AND c2.facility_id = c.facility_id)
                OR (c.unit_label != '' AND c2.unit_label = c.unit_label)
                OR (c.location_detail != '' AND c2.location_detail = c.location_detail)
                OR (c.category_primary != '' AND c2.category_primary = c.category_primary)
              )
          )
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 10
        """,
        (start, end),
    ).fetchall()
    feedback_rows = conn.execute(
        """
        SELECT c.complaint_code, c.title, cf.rating, cf.comment, cf.follow_up_at, cf.updated_at
        FROM complaint_feedback cf
        JOIN complaints c ON c.id = cf.complaint_id
        WHERE substr(cf.updated_at, 1, 10) BETWEEN ? AND ?
        ORDER BY cf.rating ASC, cf.updated_at DESC, cf.id DESC
        LIMIT 10
        """,
        (start, end),
    ).fetchall()
    tx_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM inventory_transactions
        WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()["count"]
    conn.close()

    report_lines = [
        "[시설 운영 요약 보고서]",
        f"기간: {start} ~ {end}",
        "",
        "1. 민원 현황",
        f"- 신규 접수: {created_complaints}건",
        f"- 종결 처리: {closed_complaints}건",
        f"- 현재 진행: {open_complaints}건",
        f"- 회신 지연: {overdue_complaints}건",
        f"- SLA 오늘 마감: {due_today_complaints}건",
        f"- 반복 민원: {repeat_complaints}건",
        "",
        "2. 만족도",
        f"- 만족도 기록: {feedback_stats['count']}건",
        f"- 평균 점수: {feedback_stats['avg_rating'] or '-'}",
        f"- 만족(4점 이상): {int(feedback_stats['satisfied_count'] or 0)}건",
        f"- 불만(2점 이하): {int(feedback_stats['dissatisfied_count'] or 0)}건",
        "",
        "3. 작업지시 현황",
        f"- 신규 등록: {created_work}건",
        f"- 완료 처리: {completed_work}건",
        f"- 현재 미완료: {open_work}건",
        f"- 지연 작업: {overdue_work}건",
        "",
        "4. 재고 운영",
        f"- 기간 중 수불 처리: {tx_count}건",
        f"- 부족 재고 경고: {len(low_stock_rows)}건",
    ]
    if low_stock_rows:
        for row in low_stock_rows[:5]:
            report_lines.append(
                f"  · {row['item_code']} {row['name']} / {row['quantity']}{row['unit']} / 최소 {row['min_quantity']}{row['unit']} / {row['location']}"
            )
    else:
        report_lines.append("  · 부족 재고 없음")

    report_lines.extend(["", "5. 우선 조치 대상"])
    if high_priority_rows:
        for row in high_priority_rows[:5]:
            report_lines.append(
                f"  · {row['work_code']} {row['title']} / {row['priority']} / {row['status']} / 기한 {fmt_date(row['due_date'])}"
            )
    else:
        report_lines.append("  · 긴급/높음 미완료 작업 없음")

    report_lines.extend(["", "6. 민원 지연 현황"])
    if overdue_complaint_rows:
        for row in overdue_complaint_rows[:5]:
            report_lines.append(
                f"  · {row['complaint_code']} {row['title']} / {row['priority']} / {row['status']} / 회신 목표 {fmt_date(row['response_due_at'])}"
            )
    else:
        report_lines.append("  · 지연 민원 없음")

    report_lines.extend(["", "7. 시설 상태"])
    if facility_status_rows:
        for row in facility_status_rows:
            report_lines.append(f"  · {row['status']}: {row['count']}개")
    else:
        report_lines.append("  · 시설 데이터 없음")

    metrics = (
        "<section class='metrics'>"
        + metric_card("신규 민원", created_complaints, f"종결 {closed_complaints}건")
        + metric_card("진행 민원", open_complaints, f"회신 지연 {overdue_complaints}건 / 오늘 마감 {due_today_complaints}건")
        + metric_card("반복 민원", repeat_complaints, f"최근 {COMPLAINT_REPEAT_WINDOW_DAYS}일 기준")
        + metric_card("만족도 평균", feedback_stats["avg_rating"] or "-", f"피드백 {feedback_stats['count']}건")
        + metric_card("신규 작업", created_work, f"{start}~{end}")
        + metric_card("완료 작업", completed_work, f"현재 미완료 {open_work}건")
        + metric_card("지연 작업", overdue_work, f"저평가 {int(feedback_stats['dissatisfied_count'] or 0)}건")
        + metric_card("재고 변동", tx_count, f"부족 경고 {len(low_stock_rows)}건")
        + "</section>"
    )

    overdue_complaints_html = (
        "<table><thead><tr><th>번호</th><th>민원</th><th>요청자</th><th>우선도</th><th>상태</th><th>회신 목표일</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{code}</td>
                  <td>{title}</td>
                  <td>{requester}</td>
                  <td>{priority}</td>
                  <td>{status}</td>
                  <td>{due}</td>
                </tr>
                """.format(
                    code=esc(row["complaint_code"]),
                    title=esc(row["title"]),
                    requester=esc(row["requester_name"] or "-"),
                    priority=status_badge(row["priority"]),
                    status=status_badge(row["status"]),
                    due=esc(fmt_date(row["response_due_at"])),
                )
                for row in overdue_complaint_rows
            )
            if overdue_complaint_rows
            else "<tr><td colspan='6' class='muted'>지연 민원이 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    low_stock_html = (
        "<table><thead><tr><th>품목</th><th>수량</th><th>최소 수량</th><th>위치</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td><strong>{code}</strong><div class='muted'>{name}</div></td>
                  <td>{qty}</td>
                  <td>{min_qty}</td>
                  <td>{location}</td>
                </tr>
                """.format(
                    code=esc(row["item_code"]),
                    name=esc(row["name"]),
                    qty=esc(f"{row['quantity']}{row['unit']}"),
                    min_qty=esc(f"{row['min_quantity']}{row['unit']}"),
                    location=esc(row["location"] or "-"),
                )
                for row in low_stock_rows
            )
            if low_stock_rows
            else "<tr><td colspan='4' class='muted'>부족 재고가 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    high_priority_html = (
        "<table><thead><tr><th>번호</th><th>작업</th><th>우선도</th><th>상태</th><th>기한</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{code}</td>
                  <td>{title}</td>
                  <td>{priority}</td>
                  <td>{status}</td>
                  <td>{due}</td>
                </tr>
                """.format(
                    code=esc(row["work_code"]),
                    title=esc(row["title"]),
                    priority=status_badge(row["priority"]),
                    status=status_badge(row["status"]),
                    due=esc(fmt_date(row["due_date"])),
                )
                for row in high_priority_rows
            )
            if high_priority_rows
            else "<tr><td colspan='5' class='muted'>우선 조치 대상이 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    updates_html = (
        "<table><thead><tr><th>시각</th><th>작업</th><th>유형</th><th>내용</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{when}</td>
                  <td><strong>{code}</strong><div class='muted'>{title}</div></td>
                  <td>{type}</td>
                  <td>{body}</td>
                </tr>
                """.format(
                    when=esc(fmt_datetime(row["created_at"])),
                    code=esc(row["work_code"]),
                    title=esc(row["title"]),
                    type=status_badge(row["update_type"]),
                    body=esc(row["body"]),
                )
                for row in recent_updates
            )
            if recent_updates
            else "<tr><td colspan='4' class='muted'>기간 내 업데이트가 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    complaint_updates_html = (
        "<table><thead><tr><th>시각</th><th>민원</th><th>유형</th><th>상태변경</th><th>내용</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{when}</td>
                  <td><strong>{code}</strong><div class='muted'>{title}</div></td>
                  <td>{type}</td>
                  <td>{status_move}</td>
                  <td>{body}</td>
                </tr>
                """.format(
                    when=esc(fmt_datetime(row["created_at"])),
                    code=esc(row["complaint_code"]),
                    title=esc(row["title"]),
                    type=status_badge(row["update_type"]),
                    status_move=esc(f"{row['status_from'] or '-'} -> {row['status_to'] or '-'}"),
                    body=esc(row["message"]),
                )
                for row in complaint_updates
            )
            if complaint_updates
            else "<tr><td colspan='5' class='muted'>기간 내 민원 업데이트가 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    repeat_complaints_html = (
        "<table><thead><tr><th>번호</th><th>민원</th><th>민원인</th><th>분류</th><th>상태</th><th>접수일</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{code}</td>
                  <td>{title}</td>
                  <td>{requester}</td>
                  <td>{category}</td>
                  <td>{status}</td>
                  <td>{created_at}</td>
                </tr>
                """.format(
                    code=esc(row["complaint_code"]),
                    title=esc(row["title"]),
                    requester=esc(f"{row['requester_name'] or '-'} / {row['requester_phone'] or '-'}"),
                    category=esc(row["category_primary"] or "-"),
                    status=status_badge(row["status"]),
                    created_at=esc(fmt_datetime(row["created_at"])),
                )
                for row in repeat_complaint_rows
            )
            if repeat_complaint_rows
            else "<tr><td colspan='6' class='muted'>기간 내 반복 민원이 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    feedback_html = (
        "<table><thead><tr><th>번호</th><th>민원</th><th>만족도</th><th>후속 연락일</th><th>코멘트</th></tr></thead><tbody>"
        + (
            "".join(
                """
                <tr>
                  <td>{code}</td>
                  <td>{title}</td>
                  <td>{rating}</td>
                  <td>{follow_up_at}</td>
                  <td>{comment}</td>
                </tr>
                """.format(
                    code=esc(row["complaint_code"]),
                    title=esc(row["title"]),
                    rating=_complaint_feedback_badge(row["rating"]),
                    follow_up_at=esc(fmt_date(row["follow_up_at"] or row["updated_at"])),
                    comment=esc(row["comment"] or "-"),
                )
                for row in feedback_rows
            )
            if feedback_rows
            else "<tr><td colspan='5' class='muted'>기간 내 만족도 기록이 없습니다.</td></tr>"
        )
        + "</tbody></table>"
    )

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Reporting",
            "운영 보고서",
            "작업, 재고, 시설 상태를 같은 기간 축으로 묶어 현황 보고에 바로 사용할 수 있는 초안을 생성합니다.",
            actions=("<button class='btn secondary' type='button' onclick='window.print()'>화면 인쇄</button>"),
        )
        + "<section class='panel'><div class='split'><h2>보고 기간 설정</h2>"
        + "<form class='inline-form' method='get' action='/reports'>"
        + f"<div><label>시작일</label><input name='start' type='date' value='{esc(start)}'></div>"
        + f"<div><label>종료일</label><input name='end' type='date' value='{esc(end)}'></div>"
        + "<div><label>&nbsp;</label><button class='btn primary' type='submit'>보고서 갱신</button></div>"
        + "</form></div></section>"
        + metrics
        + "<div class='layout-2'>"
        + "<div class='stack'>"
        + "<section class='panel'><h2>자동 생성 보고문</h2><div class='report-box'>"
        + esc("\n".join(report_lines))
        + "</div></section>"
        + "<section class='panel'><h2>지연 민원</h2>"
        + overdue_complaints_html
        + "</section>"
        + "<section class='panel'><h2>반복 민원</h2>"
        + repeat_complaints_html
        + "</section>"
        + "<section class='panel'><h2>부족 재고</h2>"
        + low_stock_html
        + "</section></div>"
        + "<div class='stack'>"
        + "<section class='panel'><h2>우선 조치 대상</h2>"
        + high_priority_html
        + "</section>"
        + "<section class='panel'><h2>만족도 기록</h2>"
        + feedback_html
        + "</section>"
        + "<section class='panel'><h2>기간 내 민원 업데이트</h2>"
        + complaint_updates_html
        + "</section>"
        + "<section class='panel'><h2>기간 내 작업 업데이트</h2>"
        + updates_html
        + "</section></div></div>"
    )
    return HTMLResponse(layout(title="운영 보고서", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.get("/admin/database", response_class=HTMLResponse)
def database_page(request: Request):
    user, error = _authorize(request, "db:raw:view")
    if error:
        return error

    selected_table = _db_safe_table(request.query_params.get("table", DB_MANAGED_TABLES[0]))
    edit_id = _parse_int(request.query_params.get("edit", ""), 0)
    conn = get_conn()
    columns = _db_columns(conn, selected_table)
    rows = conn.execute(f"SELECT * FROM {selected_table} ORDER BY id DESC LIMIT 100").fetchall()
    edit_row = conn.execute(f"SELECT * FROM {selected_table} WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    table_cards = _db_table_cards(conn, selected_table)
    conn.close()

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Raw Database Admin",
            "DB 관리",
            "모든 운영 테이블을 raw DB 수준에서 직접 조회·등록·수정·삭제합니다.",
        )
        + "<div class='layout-2'>"
        + "<div class='stack'>"
        + _complaints_api_import_panel()
        + info_box("주의", "sessions 삭제는 즉시 로그아웃 효과를 낼 수 있고, attachments 삭제는 연결된 파일 참조를 제거합니다.")
        + info_box("입력 방식", "현재 화면은 공통 CRUD 화면이라 외래키는 숫자 id로 직접 입력합니다.")
        + _db_render_form(selected_table, columns, edit_row)
        + _db_render_rows(selected_table, columns, rows)
        + "</div>"
        + "<div class='stack'>"
        + table_cards
        + "</div></div>"
    )
    return HTMLResponse(layout(title="DB 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.post("/admin/complaints-api-import")
async def admin_complaints_api_import(request: Request):
    user, error = _authorize(request, "db:raw:edit")
    if error:
        return error

    form = await request.form()
    action = str(form.get("action", "dry_run")).strip().lower()
    base_url = str(form.get("base_url", "")).strip() or COMPLAINTS_API_IMPORT_DEFAULT_BASE_URL
    site = str(form.get("site", "")).strip() or COMPLAINTS_API_IMPORT_DEFAULT_SITE
    admin_token = str(form.get("admin_token", "")).strip() or COMPLAINTS_API_IMPORT_DEFAULT_TOKEN
    render_service_id = (
        str(form.get("render_service_id", "")).strip() or COMPLAINTS_API_IMPORT_DEFAULT_SERVICE_ID
    )
    update_existing = _bool_from_form(form.get("update_existing"))
    import_work_orders = _bool_from_form(form.get("import_work_orders"))

    try:
        from scripts import import_legacy_complaints_api as complaints_import  # pylint: disable=import-outside-toplevel

        backup_path = None
        if action == "inspect":
            source_token = complaints_import.resolve_source_admin_token(admin_token, render_service_id)
            summary = complaints_import.inspect_source_data(base_url, source_token, site)
        elif action == "apply":
            backup_path = _db_backup_snapshot("operations_pre_admin_legacy_import")
            source_token = complaints_import.resolve_source_admin_token(admin_token, render_service_id)
            summary = complaints_import.import_api_data(
                base_url,
                source_token,
                site,
                ops_db.DB_PATH.resolve(),
                dry_run=False,
                update_existing=update_existing,
                import_work_orders=import_work_orders,
                default_user_id=int(user["id"]),
            )
        else:
            source_token = complaints_import.resolve_source_admin_token(admin_token, render_service_id)
            summary = complaints_import.import_api_data(
                base_url,
                source_token,
                site,
                ops_db.DB_PATH.resolve(),
                dry_run=True,
                update_existing=update_existing,
                import_work_orders=import_work_orders,
                default_user_id=int(user["id"]),
            )
        message = _complaints_api_import_message(action, summary)
        if backup_path:
            message += f" / 백업 {backup_path.name}"
        return _with_flash("/admin/database", message, "ok")
    except SystemExit as exc:
        text = str(exc).strip() or "민원 API 이관 실행 중 종료되었습니다."
        return _with_flash("/admin/database", text, "error")
    except Exception as exc:
        return _with_flash("/admin/database", f"민원 API 이관에 실패했습니다: {exc}", "error")


@app.post("/admin/database/save")
async def database_save(request: Request):
    user, error = _authorize(request, "db:raw:edit")
    if error:
        return error

    form = await request.form()
    table = _db_safe_table(str(form.get("table", "")))
    row_id = _parse_int(form.get("row_id", ""), 0)
    conn = get_conn()
    columns = _db_columns(conn, table)

    try:
        if row_id:
            assignments = []
            values = []
            for column in columns:
                if column["pk"]:
                    continue
                converted = _db_convert_value(column, form.get(f"col_{column['name']}", ""), for_create=False)
                assignments.append(f"{column['name']} = ?")
                values.append(converted)
            conn.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?", [*values, row_id])
            conn.commit()
            conn.close()
            return _with_flash(f"/admin/database?table={table}&edit={row_id}", "행이 수정되었습니다.", "ok")

        insert_columns = []
        insert_values = []
        placeholders = []
        for column in columns:
            if column["pk"]:
                continue
            raw_value = form.get(f"col_{column['name']}", "")
            code_info = DB_CODE_FIELDS.get(table)
            if code_info and column["name"] == code_info[0] and str(raw_value or "").strip() == "":
                converted = f"__pending__{code_info[1].lower()}_{uuid.uuid4().hex}"
            else:
                converted = _db_convert_value(column, raw_value, for_create=True)
            if converted is _DB_OMIT:
                continue
            insert_columns.append(column["name"])
            insert_values.append(converted)
            placeholders.append("?")

        cursor = conn.execute(
            f"INSERT INTO {table} ({', '.join(insert_columns)}) VALUES ({', '.join(placeholders)})",
            insert_values,
        )
        conn.commit()
        new_id = cursor.lastrowid
        code_info = DB_CODE_FIELDS.get(table)
        if code_info:
            code_field, code_prefix = code_info
            current_code = conn.execute(
                f"SELECT {code_field} FROM {table} WHERE id = ?",
                (new_id,),
            ).fetchone()[code_field]
            if str(current_code).startswith("__pending__"):
                conn.execute(
                    f"UPDATE {table} SET {code_field} = ? WHERE id = ?",
                    (f"{code_prefix}-{new_id:04d}", new_id),
                )
                conn.commit()
        conn.close()
        return _with_flash(f"/admin/database?table={table}&edit={new_id}", "행이 등록되었습니다.", "ok")
    except Exception as exc:
        conn.rollback()
        conn.close()
        target = f"/admin/database?table={table}"
        if row_id:
            target += f"&edit={row_id}"
        return _with_flash(target, f"DB 저장에 실패했습니다: {exc}", "error")


@app.post("/admin/database/delete")
async def database_delete(request: Request):
    user, error = _authorize(request, "db:raw:edit")
    if error:
        return error

    form = await request.form()
    table = _db_safe_table(str(form.get("table", "")))
    row_id = _parse_int(form.get("row_id", ""), 0)
    if not row_id:
        return _with_flash(f"/admin/database?table={table}", "삭제할 행 id가 필요합니다.", "error")

    conn = get_conn()
    try:
        deleted_count, blocked_count, missing_count, file_paths = _db_delete_rows(conn, table, [row_id], int(user["id"]))
        if missing_count:
            conn.close()
            return _with_flash(f"/admin/database?table={table}", "삭제할 행을 찾지 못했습니다.", "error")
        if blocked_count and not deleted_count:
            conn.close()
            return _with_flash(f"/admin/database?table={table}", "현재 로그인한 사용자 행은 삭제할 수 없습니다.", "error")
        conn.commit()
        conn.close()
        for file_path in file_paths:
            target_file = UPLOAD_DIR / file_path
            try:
                if target_file.exists():
                    target_file.unlink()
            except OSError:
                pass
        return _with_flash(f"/admin/database?table={table}", "행이 삭제되었습니다.", "ok")
    except Exception as exc:
        conn.rollback()
        conn.close()
        return _with_flash(f"/admin/database?table={table}", f"DB 삭제에 실패했습니다: {exc}", "error")


@app.post("/admin/database/delete-selected")
async def database_delete_selected(request: Request):
    user, error = _authorize(request, "db:raw:edit")
    if error:
        return error

    form = await request.form()
    table = _db_safe_table(str(form.get("table", "")))
    row_ids = [_parse_int(value, 0) for value in form.getlist("row_ids")]
    row_ids = [row_id for row_id in row_ids if row_id > 0]
    if not row_ids:
        return _with_flash(f"/admin/database?table={table}", "삭제할 행을 하나 이상 선택해 주세요.", "error")

    conn = get_conn()
    try:
        deleted_count, blocked_count, missing_count, file_paths = _db_delete_rows(conn, table, row_ids, int(user["id"]))
        if not deleted_count:
            conn.close()
            if blocked_count:
                return _with_flash(f"/admin/database?table={table}", "현재 로그인한 사용자 행은 선택 삭제할 수 없습니다.", "error")
            return _with_flash(f"/admin/database?table={table}", "삭제 가능한 행이 없습니다.", "error")
        conn.commit()
        conn.close()
        for file_path in file_paths:
            target_file = UPLOAD_DIR / file_path
            try:
                if target_file.exists():
                    target_file.unlink()
            except OSError:
                pass

        message = f"{deleted_count}개 행이 삭제되었습니다."
        if blocked_count:
            message += f" 현재 로그인 사용자 {blocked_count}개는 제외했습니다."
        if missing_count:
            message += f" 찾지 못한 {missing_count}개는 건너뛰었습니다."
        return _with_flash(f"/admin/database?table={table}", message, "ok")
    except Exception as exc:
        conn.rollback()
        conn.close()
        return _with_flash(f"/admin/database?table={table}", f"선택 삭제에 실패했습니다: {exc}", "error")


@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request):
    user, error = _authorize(request, "users:manage")
    if error:
        return error

    edit_id = _parse_int(request.query_params.get("edit", ""), 0)
    conn = get_conn()
    users = conn.execute(
        "SELECT * FROM users ORDER BY is_active DESC, role ASC, full_name ASC, id ASC"
    ).fetchall()
    edit_row = conn.execute("SELECT * FROM users WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    conn.close()

    form_html = (
        "<section class='panel'><h2>사용자 등록 / 수정</h2><p class='muted'>역할 기반 권한을 부여해 9명 공동 운영을 지원합니다.</p>"
        "<form action='/admin/users/save' method='post' class='stack'>"
        f"<input type='hidden' name='user_id' value='{esc(edit_row['id'] if edit_row else '')}'>"
        + f"<div><label>아이디</label><input name='username' value='{esc(edit_row['username'] if edit_row else '')}' required></div>"
        + f"<div><label>이름</label><input name='full_name' value='{esc(edit_row['full_name'] if edit_row else '')}' required></div>"
        + f"<div><label>연락처</label><input name='phone' value='{esc(edit_row['phone'] if edit_row else '')}' inputmode='tel' placeholder='01012345678'></div>"
        + f"<div><label>역할</label><select name='role'>{render_options(auth.role_options(), edit_row['role'] if edit_row else 'viewer')}</select></div>"
        + f"<div><label>복구 질문</label><input name='recovery_question' value='{esc(edit_row['recovery_question'] if edit_row else '')}' placeholder='예: 가장 기억에 남는 근무지는?'></div>"
        + f"<div><label>복구 답변{' (변경 시에만 입력)' if edit_row else ''}</label><input name='recovery_answer' type='password' {'placeholder=\"변경하지 않으려면 비워두기\"' if edit_row else ''}></div>"
        + f"<div><label>비밀번호{' (변경 시에만 입력)' if edit_row else ''}</label><input name='password' type='password' {'placeholder=\"변경하지 않으려면 비워두기\"' if edit_row else 'required'}></div>"
        + (
            f"<label style='display:flex;align-items:center;gap:8px;'><input name='is_active' type='checkbox' value='1' {'checked' if edit_row['is_active'] else ''} style='width:auto;'>활성 사용자</label>"
            if edit_row
            else "<label style='display:flex;align-items:center;gap:8px;'><input name='is_active' type='checkbox' value='1' checked style='width:auto;'>활성 사용자</label>"
        )
        + "<div class='row-actions'><button class='btn primary' type='submit'>저장</button><a class='btn secondary' href='/admin/users'>새로 입력</a>"
        + (
            _post_action_button(f"/admin/users/delete/{edit_row['id']}", "삭제", "사용자를 삭제하시겠습니까?")
            if edit_row
            else ""
        )
        + "</div>"
        + "</form></section>"
    )

    if users:
        rows_html = []
        for row in users:
            rows_html.append(
                """
                <tr>
                  <td>{username}</td>
                  <td>{name}</td>
                  <td>{phone}</td>
                  <td>{role}</td>
                  <td>{status}</td>
                  <td>{recovery}</td>
                  <td>{created}</td>
                  <td>{actions}</td>
                </tr>
                """.format(
                    username=esc(row["username"]),
                    name=esc(row["full_name"]),
                    phone=esc(row["phone"] or "-"),
                    role=esc(auth.ROLE_LABELS.get(row["role"], row["role"])),
                    status=status_badge("활성" if row["is_active"] else "비활성"),
                    recovery=status_badge("설정됨" if row["recovery_answer_hash"] else "미설정"),
                    created=esc(fmt_datetime(row["created_at"])),
                    actions=f"<a class='btn secondary' href='/admin/users?edit={row['id']}'>수정</a>",
                )
            )
        list_html = (
            "<section class='panel'><h2>사용자 목록</h2><table>"
            "<thead><tr><th>아이디</th><th>이름</th><th>연락처</th><th>역할</th><th>상태</th><th>복구정보</th><th>등록일</th><th>관리</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></section>"
        )
    else:
        list_html = "<section class='panel'><h2>사용자 목록</h2>" + empty_state("등록된 사용자가 없습니다.") + "</section>"

    flash_message, flash_level = _flash_from_request(request)
    body = (
        page_header(
            "Access Governance",
            "권한 관리",
            "계정별 역할을 부여해 수정 권한과 조회 권한을 분리합니다.",
        )
        + "<div class='layout-2'>"
        + form_html
        + "<div class='stack'>"
        + info_box("복구 정보 운영", "연락처와 복구 질문/답변이 등록된 계정만 아이디 찾기와 비밀번호 재설정을 스스로 수행할 수 있습니다.")
        + list_html
        + "</div>"
        + "</div>"
    )
    return HTMLResponse(layout(title="권한 관리", body=body, user=user, flash_message=flash_message, flash_level=flash_level))


@app.post("/admin/users/save")
def users_save(
    request: Request,
    user_id: str = Form(""),
    username: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(""),
    role: str = Form(...),
    recovery_question: str = Form(""),
    recovery_answer: str = Form(""),
    password: str = Form(""),
    is_active: str = Form(""),
):
    current_user, error = _authorize(request, "users:manage")
    if error:
        return error

    if not auth.valid_role(role):
        return _with_flash("/admin/users", "유효하지 않은 역할입니다.", "error")

    user_id_i = _parse_int(user_id, 0)
    active_value = 1 if _bool_from_form(is_active) else 0
    phone_v = auth.normalize_phone(phone)
    recovery_question_v = _normalize_recovery_question(recovery_question)
    recovery_answer_v = recovery_answer.strip()
    conn = get_conn()
    try:
        if user_id_i:
            target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id_i,)).fetchone()
            if not target:
                conn.close()
                return _with_flash("/admin/users", "사용자를 찾을 수 없습니다.", "error")
            if target["id"] == current_user["id"] and not active_value:
                conn.close()
                return _with_flash(f"/admin/users?edit={user_id_i}", "현재 로그인한 계정은 비활성화할 수 없습니다.", "error")

            if recovery_answer_v and not recovery_question_v:
                conn.close()
                return _with_flash(f"/admin/users?edit={user_id_i}", "복구 답변을 바꾸려면 복구 질문도 입력해 주세요.", "error")
            if recovery_question_v != (target["recovery_question"] or "") and recovery_question_v and not recovery_answer_v:
                conn.close()
                return _with_flash(f"/admin/users?edit={user_id_i}", "복구 질문을 변경할 때는 새 복구 답변도 함께 입력해야 합니다.", "error")

            if recovery_question_v == "" and recovery_answer_v == "":
                recovery_answer_hash = ""
            elif recovery_answer_v:
                recovery_answer_hash = auth.hash_recovery_answer(recovery_answer_v)
            else:
                recovery_answer_hash = target["recovery_answer_hash"]

            password_rule_error = _password_error(password) if password.strip() else ""
            if password_rule_error:
                conn.close()
                return _with_flash(f"/admin/users?edit={user_id_i}", password_rule_error, "error")

            if password.strip():
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, full_name = ?, phone = ?, role = ?, recovery_question = ?, recovery_answer_hash = ?,
                        password_hash = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        username.strip(),
                        full_name.strip(),
                        phone_v,
                        role,
                        recovery_question_v,
                        recovery_answer_hash,
                        auth.hash_password(password.strip()),
                        active_value,
                        _now_text(),
                        user_id_i,
                    ),
                )
                conn.commit()
                conn.close()
                auth.invalidate_user_sessions(user_id_i)
                return _with_flash(
                    f"/admin/users?edit={user_id_i}",
                    "사용자 정보가 수정되었습니다. 기존 로그인 세션은 모두 종료되었습니다.",
                    "ok",
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, full_name = ?, phone = ?, role = ?, recovery_question = ?, recovery_answer_hash = ?,
                        is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        username.strip(),
                        full_name.strip(),
                        phone_v,
                        role,
                        recovery_question_v,
                        recovery_answer_hash,
                        active_value,
                        _now_text(),
                        user_id_i,
                    ),
                )
            conn.commit()
            conn.close()
            return _with_flash(f"/admin/users?edit={user_id_i}", "사용자 정보가 수정되었습니다.", "ok")

        if not password.strip():
            conn.close()
            return _with_flash("/admin/users", "신규 사용자는 비밀번호가 필요합니다.", "error")
        password_rule_error = _password_error(password)
        if password_rule_error:
            conn.close()
            return _with_flash("/admin/users", password_rule_error, "error")
        if bool(recovery_question_v) != bool(recovery_answer_v):
            conn.close()
            return _with_flash("/admin/users", "복구 질문과 답변은 둘 다 입력하거나 둘 다 비워 두어야 합니다.", "error")
        conn.execute(
            """
            INSERT INTO users(
                username, full_name, phone, role, password_hash, is_active,
                recovery_question, recovery_answer_hash, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username.strip(),
                full_name.strip(),
                phone_v,
                role,
                auth.hash_password(password.strip()),
                active_value,
                recovery_question_v,
                auth.hash_recovery_answer(recovery_answer_v) if recovery_answer_v else "",
                _now_text(),
                _now_text(),
            ),
        )
        conn.commit()
        conn.close()
        return _with_flash("/admin/users", "사용자가 등록되었습니다.", "ok")
    except Exception as exc:
        conn.close()
        return _with_flash("/admin/users", f"사용자 저장에 실패했습니다: {exc}", "error")


@app.post("/admin/users/delete/{user_id}")
def users_delete(request: Request, user_id: int):
    current_user, error = _authorize(request, "users:manage")
    if error:
        return error

    if user_id == current_user["id"]:
        return _with_flash(f"/admin/users?edit={user_id}", "현재 로그인한 계정은 삭제할 수 없습니다.", "error")

    conn = get_conn()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        conn.close()
        return _with_flash("/admin/users", "사용자를 찾을 수 없습니다.", "error")

    try:
        auth.invalidate_user_sessions(user_id)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return _with_flash("/admin/users", "사용자가 삭제되었습니다.", "ok")
    except Exception as exc:
        conn.rollback()
        conn.close()
        return _with_flash(f"/admin/users?edit={user_id}", f"사용자 삭제에 실패했습니다: {exc}", "error")


@app.get("/healthz")
def healthz():
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "service": "facility-operations", "db": "error", "detail": str(exc)},
        )
    return {"ok": True, "service": "facility-operations", "db": "ok"}
