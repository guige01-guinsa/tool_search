from __future__ import annotations

import gc
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def remove_tree(path: Path) -> None:
    for _ in range(5):
        if not path.exists():
            return
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        gc.collect()
        time.sleep(0.2)


def main() -> None:
    tmp_path = ROOT_DIR / f"tmp_ops_stability_check_{uuid.uuid4().hex[:8]}"
    if tmp_path.exists():
        remove_tree(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = None
    try:
        os.environ["OPS_DB_PATH"] = str(tmp_path / "operations.db")
        os.environ["OPS_UPLOAD_DIR"] = str(tmp_path / "uploads")
        os.environ.pop("LEGACY_DB_PATH", None)
        os.environ.pop("OPS_ADMIN_USERNAME", None)
        os.environ.pop("OPS_ADMIN_PASSWORD", None)
        os.environ.pop("OPS_ADMIN_NAME", None)
        os.environ.pop("LEGACY_COMPLAINTS_API_BASE_URL", None)
        os.environ.pop("LEGACY_COMPLAINTS_SITE", None)
        os.environ.pop("LEGACY_ADMIN_TOKEN", None)
        os.environ.pop("LEGACY_COMPLAINTS_RENDER_SERVICE_ID", None)
        os.environ.pop("LEGACY_RENDER_SERVICE_ID", None)

        import ops_main
        from ops import auth
        from ops.db import get_conn
        from scripts import import_legacy_complaints_api as complaints_import

        client = TestClient(ops_main.app)

        health = client.get("/healthz")
        expect(health.status_code == 200, "healthz 응답이 비정상입니다.")
        health_payload = health.json()
        expect(health_payload.get("ok") is True, "healthz ok 플래그가 비정상입니다.")
        expect(health_payload.get("db") == "ok", "healthz DB 상태가 비정상입니다.")

        manifest = client.get("/manifest.webmanifest")
        expect(manifest.status_code == 200, "manifest 응답이 비정상입니다.")
        manifest_payload = manifest.json()
        expect(manifest_payload.get("display") == "standalone", "manifest display 설정이 올바르지 않습니다.")
        expect(any(icon.get("src") == "/assets/pwa/icon-192.png" for icon in manifest_payload.get("icons", [])), "manifest 아이콘 정의가 부족합니다.")

        sw = client.get("/sw.js")
        expect(sw.status_code == 200 and "CACHE_NAME" in sw.text, "service worker 응답이 비정상입니다.")

        login = client.post(
            "/login",
            data={"username": auth.DEFAULT_ADMIN_USERNAME, "password": auth.DEFAULT_ADMIN_PASSWORD},
            follow_redirects=False,
        )
        expect(login.status_code in {302, 303}, "관리자 로그인에 실패했습니다.")

        complaints_pdf = client.get("/complaints/pdf")
        expect(
            complaints_pdf.status_code == 200
            and complaints_pdf.headers.get("content-type", "").startswith("application/pdf")
            and len(complaints_pdf.content) > 1000,
            "민원 PDF 출력 응답이 비정상입니다.",
        )

        admin_page = client.get("/admin/database")
        expect(admin_page.status_code == 200, "관리자 DB 화면 접근에 실패했습니다.")
        expect("세대 민원 API 이관" in admin_page.text, "관리자 DB 화면에 세대 민원 API 이관 패널이 없습니다.")
        expect("민원 화면에서 검색 버튼 옆 'PDF 출력'" in admin_page.text, "DB 화면의 PDF 안내가 없습니다.")
        expect("소스 API 재호출 허용" in admin_page.text, "이관 보호 모드 체크박스가 없습니다.")

        original_resolve = complaints_import.resolve_source_admin_token
        original_inspect = complaints_import.inspect_source_data
        original_import = complaints_import.import_api_data
        try:
            complaints_import.resolve_source_admin_token = lambda explicit_token, render_service_id: "stub-token"
            complaints_import.inspect_source_data = lambda base_url, admin_token, site: {
                "base_url": base_url,
                "site": site,
                "cases": 5,
                "events": 11,
                "attachments": 0,
                "messages": 2,
                "cost_items": 0,
                "status_counts": {"assigned": 4, "received": 1},
            }

            def fake_import(
                base_url,
                admin_token,
                site,
                target_db,
                *,
                dry_run,
                update_existing,
                import_work_orders,
                default_user_id=None,
            ):
                return {
                    "source": {"base_url": base_url, "site": site},
                    "target_db": str(target_db),
                    "dry_run": dry_run,
                    "import_work_orders": import_work_orders,
                    "update_existing": update_existing,
                    "counts": {
                        "complaints_inserted": 5 if dry_run else 3,
                        "updates_inserted": 11 if dry_run else 7,
                        "message_updates_inserted": 2 if dry_run else 1,
                        "work_orders_inserted": 5 if import_work_orders else 0,
                    },
                }

            complaints_import.import_api_data = fake_import

            inspect_resp = client.post(
                "/admin/complaints-api-import",
                data={"action": "inspect", "render_service_id": "srv-stub", "site": "연산더샵", "allow_source_call": "1"},
                follow_redirects=True,
            )
            expect(
                inspect_resp.status_code == 200
                and "원본 확인: 민원 5건, 이력 11건, 문자 2건, 첨부 0건" in inspect_resp.text,
                "민원 API 원본 확인 플로우가 비정상입니다.",
            )

            dry_run_resp = client.post(
                "/admin/complaints-api-import",
                data={"action": "dry_run", "render_service_id": "srv-stub", "site": "연산더샵", "allow_source_call": "1"},
                follow_redirects=True,
            )
            expect(
                dry_run_resp.status_code == 200
                and "드라이런: 민원 5건, 이력 11건, 문자이력 2건, 작업지시 0건 예정" in dry_run_resp.text,
                "민원 API 드라이런 플로우가 비정상입니다.",
            )

            apply_resp = client.post(
                "/admin/complaints-api-import",
                data={
                    "action": "apply",
                    "render_service_id": "srv-stub",
                    "site": "연산더샵",
                    "allow_source_call": "1",
                    "import_work_orders": "1",
                },
                follow_redirects=True,
            )
            expect(
                apply_resp.status_code == 200
                and "이관 완료: 민원 3건, 이력 7건, 문자이력 1건, 작업지시 5건" in apply_resp.text,
                "민원 API 실제 이관 플로우가 비정상입니다.",
            )
            expect((tmp_path / "backups").exists(), "민원 API 실제 이관 전 백업이 생성되지 않았습니다.")

            conn = get_conn()
            conn.execute(
                """
                INSERT INTO complaints(
                    complaint_code, channel, category_primary, category_secondary, facility_id, unit_label, location_detail,
                    requester_name, requester_phone, requester_email, title, description, priority, status, response_due_at,
                    resolved_at, closed_at, assignee_user_id, created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "API-CM-000001",
                    "전화",
                    "민원",
                    "",
                    None,
                    "101동 101호",
                    "연산더샵",
                    "홍길동",
                    "01012345678",
                    "",
                    "기존 이관 데이터",
                    "소스 재호출 방지 검증",
                    "보통",
                    "배정완료",
                    "",
                    "",
                    "",
                    None,
                    1,
                    1,
                    "2026-04-05 09:00:00",
                    "2026-04-05 09:00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO complaint_updates(
                    complaint_id, update_type, status_from, status_to, message, is_public_note, created_by, created_at
                ) VALUES (
                    (SELECT id FROM complaints WHERE complaint_code = 'API-CM-000001'),
                    '내부메모', '', '배정완료', '기존 이관 이력', 0, 1, '2026-04-05 09:00:00'
                )
                """
            )
            conn.commit()
            conn.close()

            complaints_import.resolve_source_admin_token = lambda explicit_token, render_service_id: (_ for _ in ()).throw(
                AssertionError("이미 이관된 경우 소스 토큰을 다시 읽으면 안 됩니다.")
            )
            complaints_import.inspect_source_data = lambda base_url, admin_token, site: (_ for _ in ()).throw(
                AssertionError("이미 이관된 경우 소스 inspect를 호출하면 안 됩니다.")
            )
            complaints_import.import_api_data = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("이미 이관된 경우 소스 import를 호출하면 안 됩니다.")
            )

            locked_resp = client.post(
                "/admin/complaints-api-import",
                data={"action": "inspect", "render_service_id": "srv-stub", "site": "연산더샵"},
                follow_redirects=True,
            )
            expect(
                locked_resp.status_code == 200 and "보호 모드로 소스 API 재호출을 막았습니다." in locked_resp.text,
                "이미 이관된 데이터 보호 플로우가 비정상입니다.",
            )
        finally:
            complaints_import.resolve_source_admin_token = original_resolve
            complaints_import.inspect_source_data = original_inspect
            complaints_import.import_api_data = original_import

        print("OK: stability flows verified")
    finally:
        if client is not None:
            client.close()
        remove_tree(tmp_path)


if __name__ == "__main__":
    main()
