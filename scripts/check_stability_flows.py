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

        import ops_main
        from ops import auth

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

        dashboard = client.get("/")
        expect(dashboard.status_code == 200, "대시보드 접근에 실패했습니다.")
        expect("행정업무" in dashboard.text, "대시보드에 행정업무 진입점이 없습니다.")
        expect("연락처" in dashboard.text, "대시보드에 연락처 진입점이 없습니다.")

        facilities = client.get("/facilities")
        expect(facilities.status_code == 200, "시설 화면 접근에 실패했습니다.")
        expect("첨부 이미지 (최대 6장)" in facilities.text, "시설 화면의 첨부 이미지 제한 안내가 없습니다.")

        inventory = client.get("/inventory")
        expect(inventory.status_code == 200, "재고 화면 접근에 실패했습니다.")
        expect("첨부 이미지 (최대 6장)" in inventory.text, "재고 화면의 첨부 이미지 제한 안내가 없습니다.")

        contacts = client.get("/contacts")
        expect(contacts.status_code == 200, "연락처 화면 접근에 실패했습니다.")
        expect("업체연락처, 근무자연락처, 계약업체, 관공서" in contacts.text, "연락처 화면 설명이 비정상입니다.")

        office_records = client.get("/office-records")
        expect(office_records.status_code == 200, "행정업무 화면 접근에 실패했습니다.")
        expect(
            "기안지, 공문서, 정기점검" in office_records.text
            and "연결 연락처" in office_records.text
            and "첨부 파일 / 이미지 (최대 6개)" in office_records.text,
            "행정업무 화면 설명이 비정상입니다.",
        )

        reports = client.get("/reports")
        expect(reports.status_code == 200, "보고서 화면 접근에 실패했습니다.")
        expect("연락처 분류 현황" in reports.text and "기간 내 행정업무 업데이트" in reports.text, "보고서의 연락처/행정업무 섹션이 비정상입니다.")

        complaints_pdf = client.get("/complaints/pdf")
        expect(
            complaints_pdf.status_code == 200
            and complaints_pdf.headers.get("content-type", "").startswith("application/pdf")
            and len(complaints_pdf.content) > 1000,
            "민원 PDF 출력 응답이 비정상입니다.",
        )

        admin_page = client.get("/admin/database")
        expect(admin_page.status_code == 200, "관리자 DB 화면 접근에 실패했습니다.")
        expect("세대 민원 API 이관" not in admin_page.text, "관리자 DB 화면에 숨겨야 할 세대 민원 API 이관 패널이 보입니다.")
        expect("민원 화면에서 검색 버튼 옆 'PDF 출력'" in admin_page.text, "DB 화면의 PDF 안내가 없습니다.")
        expect("민원 PDF 이관" in admin_page.text, "관리자 DB 화면에 민원 PDF 이관 패널이 없습니다.")

        print("OK: stability flows verified")
    finally:
        if client is not None:
            client.close()
        remove_tree(tmp_path)


if __name__ == "__main__":
    main()
