from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from ops.auth import ROLE_LABELS, has_permission


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_datetime(value: str | None) -> str:
    if not value:
        return "-"
    return value.replace("T", " ")[:16]


def fmt_date(value: str | None) -> str:
    if not value:
        return "-"
    return value[:10]


def fmt_currency(value) -> str:
    try:
        return f"{int(value or 0):,}원"
    except Exception:
        return "0원"


def render_options(
    options: list[tuple[str, str]] | list[str],
    selected: str = "",
    *,
    blank_label: str | None = None,
) -> str:
    rendered = []
    if blank_label is not None:
        rendered.append(
            f"<option value='' {'selected' if selected == '' else ''}>{esc(blank_label)}</option>"
        )

    for option in options:
        if isinstance(option, tuple):
            value, label = option
        else:
            value, label = option, option
        rendered.append(
            f"<option value='{esc(value)}' {'selected' if value == selected else ''}>{esc(label)}</option>"
        )
    return "".join(rendered)


def status_badge(value: str) -> str:
    text = esc(value)
    tone = "neutral"
    if value in {"완료", "정상", "운영중", "활성", "회신완료", "종결", "만족"}:
        tone = "good"
    elif value in {"부족", "점검필요", "보류", "대기", "임박", "오늘 마감", "보통"}:
        tone = "warn"
    elif value in {"긴급", "고장", "폐기대기", "사용중지", "비활성", "지연", "불만", "종료"}:
        tone = "danger"
    return f"<span class='badge {tone}'>{text}</span>"


def metric_card(label: str, value: str | int, note: str = "") -> str:
    note_html = f"<div class='metric-note'>{esc(note)}</div>" if note else ""
    return (
        "<div class='metric-card'>"
        f"<div class='metric-label'>{esc(label)}</div>"
        f"<div class='metric-value'>{esc(value)}</div>"
        f"{note_html}</div>"
    )


def attachment_gallery(attachments, *, prefer_links: bool = False) -> str:
    if not attachments:
        return "<div class='muted'>첨부 없음</div>"
    image_items = []
    file_items = []
    for row in attachments:
        file_path = row["file_path"]
        href = f"/uploads/{quote(file_path)}"
        original_name = row["original_name"] or file_path
        extension = Path(str(original_name)).suffix.lower()
        if not prefer_links and extension in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            image_items.append(
                "<a class='thumb-link' target='_blank' href='"
                + href
                + "'><img class='thumb' src='"
                + href
                + "' alt='attachment'></a>"
            )
            continue
        file_items.append(
            "<a class='file-chip' target='_blank' href='"
            + href
            + "'>"
            + esc(original_name)
            + "</a>"
        )

    blocks = []
    if image_items:
        blocks.append("<div class='thumb-grid'>" + "".join(image_items) + "</div>")
    if file_items:
        blocks.append("<div class='file-list'>" + "".join(file_items) + "</div>")
    return "<div class='attachment-stack'>" + "".join(blocks) + "</div>"


def attachment_selector(attachments, *, field_name: str = "attachment_ids", prefer_links: bool = False) -> str:
    if not attachments:
        return "<div class='muted'>첨부 없음</div>"
    items = []
    for row in attachments:
        file_path = row["file_path"]
        href = f"/uploads/{quote(file_path)}"
        original_name = row["original_name"] or file_path
        extension = Path(str(original_name)).suffix.lower()
        if not prefer_links and extension in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            preview = (
                "<a class='thumb-link' target='_blank' href='"
                + href
                + "'><img class='thumb' src='"
                + href
                + "' alt='attachment'></a>"
            )
        else:
            preview = (
                "<a class='file-chip' target='_blank' href='"
                + href
                + "'>"
                + esc(original_name)
                + "</a>"
            )
        items.append(
            "<label class='attachment-select-item'>"
            + "<span class='attachment-select-head'>"
            + f"<input type='checkbox' name='{esc(field_name)}' value='{esc(row['id'])}' style='width:auto;'>"
            + "<span>선택</span>"
            + "</span>"
            + preview
            + "<span class='attachment-select-name'>"
            + esc(original_name)
            + "</span></label>"
        )
    return "<div class='attachment-select-grid'>" + "".join(items) + "</div>"


def page_header(eyebrow: str, title: str, description: str, actions: str = "") -> str:
    action_block = f"<div class='page-actions'>{actions}</div>" if actions else ""
    return (
        "<section class='hero'>"
        f"<div class='eyebrow'>{esc(eyebrow)}</div>"
        f"<h1>{esc(title)}</h1>"
        f"<p>{esc(description)}</p>"
        f"{action_block}</section>"
    )


def info_box(title: str, body: str) -> str:
    return f"<section class='panel'><h2>{esc(title)}</h2><div class='muted'>{body}</div></section>"


def empty_state(message: str) -> str:
    return f"<div class='empty'>{esc(message)}</div>"


def nav_for_user(user) -> str:
    if not user:
        return ""

    links = [("/", "대시보드", "dashboard:view")]
    links.extend(
        [
            ("/facilities", "시설", "facilities:view"),
            ("/inventory", "재고", "inventory:view"),
            ("/contacts", "연락처", "contacts:view"),
            ("/office-records", "행정업무", "office_records:view"),
            ("/complaints", "민원", "complaints:view"),
            ("/work-orders", "작업지시", "work_orders:view"),
            ("/reports", "보고서", "reports:view"),
        ]
    )
    if has_permission(user["role"], "users:manage"):
        links.append(("/admin/users", "권한관리", "users:manage"))
    if has_permission(user["role"], "db:raw:view"):
        links.append(("/admin/database", "DB관리", "db:raw:view"))

    items = []
    for href, label, permission in links:
        if has_permission(user["role"], permission):
            items.append(f"<a class='nav-link' href='{href}'>{esc(label)}</a>")
    return "<nav class='nav-bar'>" + "".join(items) + "</nav>"


def flash_block(message: str, level: str = "info") -> str:
    if not message:
        return ""
    return f"<div class='flash {esc(level)}'>{esc(message)}</div>"


def user_chip(user) -> str:
    if not user:
        return ""
    role_label = ROLE_LABELS.get(user["role"], user["role"])
    return (
        "<div class='user-chip'>"
        f"<div><strong>{esc(user['full_name'])}</strong></div>"
        f"<div class='muted'>{esc(user['username'])} · {esc(role_label)}</div></div>"
    )


def layout(
    *,
    title: str,
    body: str,
    user=None,
    flash_message: str = "",
    flash_level: str = "info",
) -> str:
    nav = nav_for_user(user)
    chip = user_chip(user)
    install_button = "<button class='btn secondary' id='install-app-btn' type='button' hidden>앱 설치</button>"
    auth_actions = install_button
    if user:
        auth_actions = (
            install_button
            + "<form action='/logout' method='post'>"
            "<button class='btn secondary' type='submit'>로그아웃</button>"
            "</form>"
        )

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="theme-color" content="#1f5a55"/>
  <meta name="mobile-web-app-capable" content="yes"/>
  <meta name="apple-mobile-web-app-capable" content="yes"/>
  <meta name="apple-mobile-web-app-status-bar-style" content="default"/>
  <meta name="apple-mobile-web-app-title" content="시설운영"/>
  <meta name="application-name" content="시설운영"/>
  <link rel="manifest" href="/manifest.webmanifest"/>
  <link rel="icon" href="/assets/pwa/icon-192.png" sizes="192x192" type="image/png"/>
  <link rel="apple-touch-icon" href="/assets/pwa/apple-touch-icon.png"/>
  <title>{esc(title)}</title>
  <style>
    :root {{
      --bg: #f2f4ef;
      --surface: #fffdf7;
      --surface-2: #ffffff;
      --ink: #17212b;
      --muted: #60707d;
      --line: #d8dfd5;
      --brand: #1f5a55;
      --brand-2: #d98f39;
      --good: #1b6b3c;
      --warn: #a86115;
      --danger: #a0362f;
      --shadow: 0 14px 38px rgba(23, 33, 43, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans KR", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(217, 143, 57, 0.12), transparent 30%),
        linear-gradient(180deg, #eef3eb 0%, var(--bg) 35%, #f7f5ef 100%);
      min-height: 100vh;
    }}
    a {{ color: inherit; }}
    .shell {{ max-width: 1320px; margin: 0 auto; padding: 18px; }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 14px;
    }}
    .brand {{
      display: flex;
      gap: 14px;
      align-items: center;
      background: var(--surface);
      border: 1px solid rgba(31, 90, 85, 0.12);
      border-radius: 22px;
      padding: 14px 18px;
      box-shadow: var(--shadow);
    }}
    .brand-mark {{
      width: 52px;
      height: 52px;
      border-radius: 18px;
      background: linear-gradient(135deg, var(--brand), #12353d);
      color: white;
      display: grid;
      place-items: center;
      font-size: 22px;
      font-weight: 900;
      letter-spacing: 1px;
    }}
    .brand h1 {{ margin: 0; font-size: 20px; }}
    .brand p {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}
    .top-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
    }}
    .nav-bar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }}
    .nav-link {{
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(31, 90, 85, 0.08);
      border: 1px solid rgba(31, 90, 85, 0.14);
      color: var(--brand);
      font-weight: 700;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(31, 90, 85, 0.97), rgba(17, 53, 61, 0.96));
      color: white;
      padding: 24px;
      border-radius: 28px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
      margin-bottom: 16px;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -30px -60px auto;
      width: 260px;
      height: 260px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(217, 143, 57, 0.34), transparent 70%);
    }}
    .hero h1 {{ margin: 8px 0 8px; font-size: 32px; line-height: 1.15; max-width: 720px; }}
    .hero p {{ margin: 0; color: rgba(255,255,255,0.86); max-width: 760px; line-height: 1.6; }}
    .eyebrow {{ font-size: 12px; letter-spacing: 0.18em; text-transform: uppercase; color: rgba(255,255,255,0.7); }}
    .page-actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; position: relative; z-index: 1; }}
    .flash {{
      padding: 13px 16px;
      border-radius: 16px;
      margin-bottom: 16px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      box-shadow: var(--shadow);
      font-weight: 700;
    }}
    .flash.ok {{ border-color: rgba(27, 107, 60, 0.22); color: var(--good); }}
    .flash.warn {{ border-color: rgba(168, 97, 21, 0.22); color: var(--warn); }}
    .flash.error {{ border-color: rgba(160, 54, 47, 0.22); color: var(--danger); }}
    .flash.info {{ border-color: rgba(31, 90, 85, 0.2); color: var(--brand); }}
    .flash.install-tip {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }}
    .metric-card {{
      background: var(--surface);
      border: 1px solid rgba(23, 33, 43, 0.08);
      border-radius: 22px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}
    .metric-label {{ color: var(--muted); font-size: 13px; }}
    .metric-value {{ font-size: 34px; font-weight: 900; margin-top: 8px; }}
    .metric-note {{ color: var(--muted); font-size: 12px; margin-top: 10px; line-height: 1.5; }}
    .layout-2 {{
      display: grid;
      grid-template-columns: 380px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid rgba(23, 33, 43, 0.08);
      border-radius: 24px;
      padding: 18px;
      box-shadow: var(--shadow);
    }}
    .panel h2 {{ margin: 0 0 8px; font-size: 19px; }}
    .panel h3 {{ margin: 0 0 10px; font-size: 16px; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .grid {{
      display: grid;
      gap: 12px;
    }}
    .grid.two {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    label {{
      display: block;
      margin: 0 0 6px;
      font-size: 12px;
      font-weight: 700;
      color: var(--muted);
      letter-spacing: 0.03em;
    }}
    input, select, textarea, button {{
      font: inherit;
    }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fffeff;
      border-radius: 15px;
      padding: 11px 12px;
      color: var(--ink);
    }}
    textarea {{ min-height: 110px; resize: vertical; }}
    .btn {{
      display: inline-flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      border: none;
      border-radius: 999px;
      padding: 11px 16px;
      cursor: pointer;
      font-weight: 800;
      white-space: nowrap;
    }}
    .btn.primary {{ background: var(--brand); color: white; }}
    .btn.secondary {{ background: rgba(23, 33, 43, 0.05); color: var(--ink); }}
    .btn.warn {{ background: rgba(168, 97, 21, 0.12); color: var(--warn); }}
    .btn.danger {{ background: rgba(160, 54, 47, 0.12); color: var(--danger); }}
    .row-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
      table {{
        width: 100%;
        border-collapse: collapse;
        background: #fffefb;
        border-radius: 20px;
        overflow: hidden;
      }}
      .db-table {{
        table-layout: fixed;
      }}
      th, td {{
        padding: 12px 10px;
        border-bottom: 1px solid rgba(23, 33, 43, 0.08);
        text-align: left;
        vertical-align: top;
        font-size: 14px;
        overflow-wrap: anywhere;
        word-break: break-word;
      }}
    th {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: rgba(31, 90, 85, 0.04);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid rgba(23, 33, 43, 0.08);
      background: rgba(23, 33, 43, 0.04);
    }}
    .badge.good {{ color: var(--good); background: rgba(27, 107, 60, 0.09); border-color: rgba(27, 107, 60, 0.18); }}
    .badge.warn {{ color: var(--warn); background: rgba(168, 97, 21, 0.1); border-color: rgba(168, 97, 21, 0.18); }}
    .badge.danger {{ color: var(--danger); background: rgba(160, 54, 47, 0.1); border-color: rgba(160, 54, 47, 0.18); }}
    .badge.neutral {{ color: var(--brand); background: rgba(31, 90, 85, 0.08); border-color: rgba(31, 90, 85, 0.18); }}
    .split {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .inline-form {{
      display: flex;
      gap: 8px;
      align-items: end;
      flex-wrap: wrap;
    }}
    .inline-form > * {{ flex: 1 1 120px; }}
    .db-check-cell {{
      width: 56px;
      text-align: center;
    }}
    .db-check-cell input {{
      margin: 0 auto;
    }}
    .db-action-cell .btn {{
      margin-right: 6px;
      margin-bottom: 6px;
    }}
    .thumb-grid {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 6px;
    }}
    .thumb {{
      width: 72px;
      height: 72px;
      border-radius: 14px;
      object-fit: cover;
      border: 1px solid rgba(23, 33, 43, 0.08);
      background: #f7f5ef;
    }}
    .thumb-link {{ display: inline-flex; }}
    .attachment-stack {{
      display: grid;
      gap: 8px;
      margin-top: 6px;
    }}
    .file-list {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .file-chip {{
      display: inline-flex;
      align-items: center;
      text-decoration: none;
      border-radius: 999px;
      padding: 7px 10px;
      background: rgba(31, 90, 85, 0.08);
      border: 1px solid rgba(31, 90, 85, 0.14);
      color: var(--brand);
      font-size: 12px;
      font-weight: 700;
    }}
    .attachment-select-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }}
    .attachment-select-item {{
      display: grid;
      gap: 8px;
      padding: 10px;
      border-radius: 16px;
      border: 1px solid rgba(23, 33, 43, 0.1);
      background: rgba(247, 245, 239, 0.9);
      cursor: pointer;
    }}
    .attachment-select-head {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      font-weight: 700;
      color: var(--ink);
    }}
    .attachment-select-name {{
      font-size: 11px;
      color: var(--muted);
      word-break: break-all;
      line-height: 1.4;
    }}
    .stack {{
      display: grid;
      gap: 12px;
    }}
    .empty {{
      padding: 26px 18px;
      border-radius: 18px;
      background: rgba(23, 33, 43, 0.03);
      border: 1px dashed rgba(23, 33, 43, 0.12);
      color: var(--muted);
      text-align: center;
    }}
    .report-box {{
      background: #132731;
      color: #edf4f1;
      border-radius: 22px;
      padding: 18px;
      white-space: pre-wrap;
      line-height: 1.7;
      font-family: "Consolas", "Noto Sans KR", monospace;
      font-size: 13px;
      border: 1px solid rgba(255,255,255,0.08);
    }}
    .user-chip {{
      padding: 12px 14px;
      border-radius: 18px;
      background: var(--surface);
      border: 1px solid rgba(23, 33, 43, 0.08);
      box-shadow: var(--shadow);
      min-width: 200px;
    }}
    .pill-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .pill {{
      display: inline-flex;
      border-radius: 999px;
      padding: 5px 10px;
      background: rgba(31, 90, 85, 0.08);
      color: var(--brand);
      font-size: 12px;
      font-weight: 700;
    }}
    @media (max-width: 1060px) {{
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .layout-2 {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 680px) {{
      .shell {{ padding: 10px; }}
      .brand {{ width: 100%; }}
      .hero h1 {{ font-size: 26px; }}
      .metrics {{ grid-template-columns: 1fr; }}
      .grid.two {{ grid-template-columns: 1fr; }}
      .panel {{ padding: 14px; border-radius: 18px; }}
      th, td {{ padding: 10px 8px; }}
      .top-actions {{ width: 100%; justify-content: stretch; }}
      .top-actions form, .top-actions a {{ flex: 1 1 auto; }}
      .top-actions .btn {{ width: 100%; }}
      .nav-link {{ flex: 1 1 auto; text-align: center; }}
      .flash.install-tip {{ align-items: flex-start; }}
      .responsive-table thead {{
        display: none;
      }}
      .responsive-table,
      .responsive-table tbody,
      .responsive-table tr,
      .responsive-table td {{
        display: block;
        width: 100%;
      }}
      .responsive-table {{
        border-radius: 18px;
      }}
      .responsive-table tr {{
        border-bottom: 1px solid rgba(23, 33, 43, 0.08);
        padding: 12px 0;
      }}
      .responsive-table tr:last-child {{
        border-bottom: none;
      }}
        .responsive-table td {{
          border-bottom: none;
          padding: 6px 0;
          font-size: 13px;
          overflow-wrap: anywhere;
          word-break: break-word;
        }}
      .responsive-table td::before {{
        content: attr(data-label);
        display: block;
        margin-bottom: 4px;
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 0.05em;
        text-transform: uppercase;
      }}
      .responsive-table .db-check-cell {{
        text-align: left;
      }}
      .responsive-table .db-check-cell input {{
        margin: 0;
      }}
      .responsive-table .db-action-cell {{
        padding-top: 10px;
      }}
      .responsive-table .db-action-cell .btn {{
        width: 100%;
        margin-right: 0;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="brand">
        <div class="brand-mark">FM</div>
        <div>
          <h1>시설 운영 시스템</h1>
          <p>시설, 재고, 작업지시, 보고서, 권한을 한 화면에서 관리합니다.</p>
        </div>
      </div>
      <div class="top-actions">
        {chip}
        {auth_actions}
      </div>
    </div>
    {nav}
    {flash_block(flash_message, flash_level)}
    <div class="flash install-tip" id="ios-install-tip" hidden>
      <div>
        <strong>홈 화면에 추가</strong>
        <div class="muted">iPhone/iPad Safari에서는 공유 버튼을 누른 뒤 "홈 화면에 추가"를 선택하세요.</div>
      </div>
      <button class="btn secondary" id="ios-install-dismiss" type="button">닫기</button>
    </div>
    {body}
  </div>
  <script>
    (() => {{
      let deferredInstallPrompt = null;
      const installButton = document.getElementById("install-app-btn");
      const iosTip = document.getElementById("ios-install-tip");
      const iosDismiss = document.getElementById("ios-install-dismiss");
      const isIos = /iphone|ipad|ipod/i.test(window.navigator.userAgent);
      const isStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

      if ("serviceWorker" in navigator) {{
        window.addEventListener("load", () => {{
          navigator.serviceWorker.register("/sw.js").catch((error) => console.warn("sw register failed", error));
        }});
      }}

      window.addEventListener("beforeinstallprompt", (event) => {{
        event.preventDefault();
        deferredInstallPrompt = event;
        if (installButton) {{
          installButton.hidden = false;
        }}
      }});

      window.addEventListener("appinstalled", () => {{
        deferredInstallPrompt = null;
        if (installButton) {{
          installButton.hidden = true;
        }}
      }});

      if (installButton) {{
        installButton.addEventListener("click", async () => {{
          if (!deferredInstallPrompt) {{
            if (isIos && !isStandalone && iosTip) {{
              iosTip.hidden = false;
            }}
            return;
          }}
          deferredInstallPrompt.prompt();
          await deferredInstallPrompt.userChoice;
          deferredInstallPrompt = null;
          installButton.hidden = true;
        }});
      }}

      if (iosDismiss) {{
        iosDismiss.addEventListener("click", () => {{
          if (iosTip) {{
            iosTip.hidden = true;
          }}
        }});
      }}

      if (isIos && !isStandalone && iosTip) {{
        iosTip.hidden = false;
      }}
    }})();
  </script>
</body>
</html>"""
