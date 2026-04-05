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
                data={"action": "inspect", "render_service_id": "srv-stub", "site": "연산더샵"},
                follow_redirects=True,
            )
            expect(
                inspect_resp.status_code == 200
                and "원본 확인: 민원 5건, 이력 11건, 문자 2건, 첨부 0건" in inspect_resp.text,
                "민원 API 원본 확인 플로우가 비정상입니다.",
            )

            dry_run_resp = client.post(
                "/admin/complaints-api-import",
                data={"action": "dry_run", "render_service_id": "srv-stub", "site": "연산더샵"},
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
