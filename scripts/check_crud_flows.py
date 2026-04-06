from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


SAMPLE_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xd9\x8f\x9b"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def image_files(prefix: str, count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"{prefix}-{idx}.png", SAMPLE_PNG, "image/png")) for idx in range(count)]


def main() -> None:
    tmp_path = ROOT_DIR / f"tmp_ops_crud_check_{uuid.uuid4().hex[:8]}"
    if tmp_path.exists():
        shutil.rmtree(tmp_path, ignore_errors=True)
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
        from ops.db import get_conn

        client = TestClient(ops_main.app)

        login = client.post(
            "/login",
            data={"username": "admin", "password": "admin1234"},
            follow_redirects=False,
        )
        expect(login.status_code in {302, 303}, "관리자 로그인에 실패했습니다.")

        def fetchone(query: str, params: tuple = ()):
            conn = get_conn()
            row = conn.execute(query, params).fetchone()
            conn.close()
            return row

        suffix = uuid.uuid4().hex[:8]

        facility_name = f"검증시설-{suffix}"
        facility_create = client.post(
            "/facilities/save",
            data={
                "category": "전기",
                "name": facility_name,
                "building": "본관",
                "floor": "B1",
                "zone": "분전반 앞",
                "status": "운영중",
                "manager_user_id": "",
                "note": "CRUD 검증",
            },
            files=image_files(f"facility-{suffix}", 6),
            follow_redirects=False,
        )
        expect(facility_create.status_code in {302, 303}, "시설 등록 요청이 실패했습니다.")
        facility_row = fetchone("SELECT * FROM facilities WHERE name = ?", (facility_name,))
        expect(facility_row is not None, "시설이 생성되지 않았습니다.")
        facility_id = facility_row["id"]
        facility_attachment_count = fetchone(
            "SELECT COUNT(*) AS count FROM attachments WHERE entity_type = 'facility' AND entity_id = ?",
            (facility_id,),
        )["count"]
        expect(facility_attachment_count == 6, "시설 첨부 이미지 저장 개수가 올바르지 않습니다.")

        facility_page = client.get(f"/facilities?edit={facility_id}")
        expect(facility_page.status_code == 200 and "formaction='/facilities/delete/" in facility_page.text, "시설 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        facility_update = client.post(
            "/facilities/save",
            data={
                "facility_id": str(facility_id),
                "category": "기계",
                "name": f"{facility_name}-수정",
                "building": "별관",
                "floor": "1F",
                "zone": "기계실",
                "status": "점검중",
                "manager_user_id": "",
                "note": "수정 완료",
            },
            follow_redirects=False,
        )
        expect(facility_update.status_code in {302, 303}, "시설 수정 요청이 실패했습니다.")
        facility_row = fetchone("SELECT * FROM facilities WHERE id = ?", (facility_id,))
        expect(facility_row["name"].endswith("-수정"), "시설 수정이 반영되지 않았습니다.")

        facility_over_limit = client.post(
            "/facilities/save",
            data={
                "facility_id": str(facility_id),
                "category": "기계",
                "name": f"{facility_name}-초과시도",
                "building": "별관",
                "floor": "1F",
                "zone": "기계실",
                "status": "점검중",
                "manager_user_id": "",
                "note": "첨부 초과 시도",
            },
            files=image_files(f"facility-over-{suffix}", 1),
            follow_redirects=False,
        )
        expect(facility_over_limit.status_code in {302, 303}, "시설 첨부 초과 제한 응답이 비정상입니다.")
        facility_row = fetchone("SELECT * FROM facilities WHERE id = ?", (facility_id,))
        expect(facility_row["name"].endswith("-수정"), "시설 첨부 초과 시 기본정보가 변경되면 안 됩니다.")
        facility_attachment_count = fetchone(
            "SELECT COUNT(*) AS count FROM attachments WHERE entity_type = 'facility' AND entity_id = ?",
            (facility_id,),
        )["count"]
        expect(facility_attachment_count == 6, "시설 첨부 초과 시 이미지가 추가 저장되면 안 됩니다.")

        contact_name = f"검증연락처-{suffix}"
        contact_create = client.post(
            "/contacts/save",
            data={
                "contact_type": "계약업체",
                "name": contact_name,
                "organization": "검증설비주식회사",
                "department": "유지보수팀",
                "position": "대리",
                "phone": "02-1234-5678",
                "email": "contact@example.com",
                "address": "서울시 테스트구 점검로 1",
                "status": "활성",
                "note": "연락처 생성 검증",
            },
            follow_redirects=False,
        )
        expect(contact_create.status_code in {302, 303}, "연락처 등록 요청이 실패했습니다.")
        contact_row = fetchone("SELECT * FROM contacts WHERE name = ?", (contact_name,))
        expect(contact_row is not None, "연락처가 생성되지 않았습니다.")
        contact_id = contact_row["id"]

        contact_page = client.get(f"/contacts?edit={contact_id}")
        expect(
            contact_page.status_code == 200 and f"formaction='/contacts/delete/{contact_id}'" in contact_page.text,
            "연락처 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.",
        )

        contact_update = client.post(
            "/contacts/save",
            data={
                "contact_id": str(contact_id),
                "contact_type": "관공서",
                "name": f"{contact_name}-수정",
                "organization": "테스트구청",
                "department": "시설관리과",
                "position": "주무관",
                "phone": "02-9876-5432",
                "email": "gov@example.com",
                "address": "서울시 테스트구 청사로 10",
                "status": "활성",
                "note": "연락처 수정 검증",
            },
            follow_redirects=False,
        )
        expect(contact_update.status_code in {302, 303}, "연락처 수정 요청이 실패했습니다.")
        contact_row = fetchone("SELECT * FROM contacts WHERE id = ?", (contact_id,))
        expect(
            contact_row["name"].endswith("-수정") and contact_row["contact_type"] == "관공서" and contact_row["organization"] == "테스트구청",
            "연락처 수정이 반영되지 않았습니다.",
        )

        office_title = f"검증행정업무-{suffix}"
        office_create = client.post(
            "/office-records/save",
            data={
                "record_type": "정기점검",
                "title": office_title,
                "facility_id": str(facility_id),
                "contact_id": str(contact_id),
                "target_name": "",
                "priority": "높음",
                "status": "작성중",
                "description": "행정업무 생성 검증",
                "owner_user_id": "",
                "due_date": "2026-03-31",
            },
            follow_redirects=False,
        )
        expect(office_create.status_code in {302, 303}, "행정업무 등록 요청이 실패했습니다.")
        office_row = fetchone("SELECT * FROM office_records WHERE title = ?", (office_title,))
        expect(office_row is not None, "행정업무가 생성되지 않았습니다.")
        expect(office_row["contact_id"] == contact_id, "행정업무에 연락처 연결이 저장되지 않았습니다.")
        expect("테스트구청" in str(office_row["target_name"]), "행정업무 대상명이 연결 연락처 기준으로 자동 입력되지 않았습니다.")
        office_id = office_row["id"]

        office_page = client.get(f"/office-records?edit={office_id}")
        expect(
            office_page.status_code == 200
            and f"formaction='/office-records/delete/{office_id}'" in office_page.text
            and "gov@example.com" in office_page.text,
            "행정업무 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.",
        )

        office_update = client.post(
            "/office-records/save",
            data={
                "record_id": str(office_id),
                "record_type": "공문서",
                "title": f"{office_title}-수정",
                "facility_id": str(facility_id),
                "contact_id": str(contact_id),
                "target_name": "구청 시설관리팀",
                "priority": "긴급",
                "status": "결재대기",
                "description": "행정업무 수정 검증",
                "owner_user_id": "",
                "due_date": "2026-04-01",
            },
            follow_redirects=False,
        )
        expect(office_update.status_code in {302, 303}, "행정업무 수정 요청이 실패했습니다.")
        office_row = fetchone("SELECT * FROM office_records WHERE id = ?", (office_id,))
        expect(
            office_row["title"].endswith("-수정")
            and office_row["record_type"] == "공문서"
            and office_row["priority"] == "긴급"
            and office_row["contact_id"] == contact_id,
            "행정업무 수정이 반영되지 않았습니다.",
        )

        office_progress = client.post(
            f"/office-records/update/{office_id}",
            data={"update_type": "상태변경", "body": "결재 상신 후 완료 처리", "status": "완료"},
            follow_redirects=False,
        )
        expect(office_progress.status_code in {302, 303}, "행정업무 업데이트 요청이 실패했습니다.")
        office_row = fetchone("SELECT * FROM office_records WHERE id = ?", (office_id,))
        expect(office_row["status"] == "완료" and office_row["completed_at"], "행정업무 상태 업데이트가 반영되지 않았습니다.")
        office_update_count = fetchone("SELECT COUNT(*) AS count FROM office_record_updates WHERE office_record_id = ?", (office_id,))["count"]
        expect(office_update_count >= 3, "행정업무 이력이 충분히 생성되지 않았습니다.")

        contact_detail = client.get(f"/contacts?edit={contact_id}")
        expect(
            contact_detail.status_code == 200 and office_title in contact_detail.text,
            "연락처 상세 화면에 연계 행정업무가 표시되지 않습니다.",
        )

        complaint_title = f"검증민원-{suffix}"
        complaint_create = client.post(
            "/complaints/save",
            data={
                "channel": "전화",
                "category_primary": "전기",
                "category_secondary": "조명",
                "facility_id": str(facility_id),
                "unit_label": "101동 1203호",
                "location_detail": "거실 천장",
                "requester_name": "검증민원인",
                "requester_phone": "01011112222",
                "requester_email": "test@example.com",
                "title": complaint_title,
                "description": "민원 생성 검증",
                "priority": "높음",
                "status": "접수",
                "response_due_at": "2026-03-31",
                "assignee_user_id": "",
            },
            follow_redirects=False,
        )
        expect(complaint_create.status_code in {302, 303}, "민원 등록 요청이 실패했습니다.")
        complaint_row = fetchone("SELECT * FROM complaints WHERE title = ?", (complaint_title,))
        expect(complaint_row is not None, "민원이 생성되지 않았습니다.")
        complaint_id = complaint_row["id"]

        complaint_page = client.get(f"/complaints?edit={complaint_id}")
        expect(complaint_page.status_code == 200 and "formaction='/complaints/delete/" in complaint_page.text, "민원 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        complaint_update = client.post(
            "/complaints/save",
            data={
                "complaint_id": str(complaint_id),
                "channel": "모바일",
                "category_primary": "기계",
                "category_secondary": "환기",
                "facility_id": str(facility_id),
                "unit_label": "101동 1203호",
                "location_detail": "욕실 환풍기",
                "requester_name": "검증민원인",
                "requester_phone": "01011113333",
                "requester_email": "updated@example.com",
                "title": f"{complaint_title}-수정",
                "description": "민원 수정 검증",
                "priority": "긴급",
                "status": "접수",
                "response_due_at": "2026-04-01",
                "assignee_user_id": "",
            },
            follow_redirects=False,
        )
        expect(complaint_update.status_code in {302, 303}, "민원 수정 요청이 실패했습니다.")
        complaint_row = fetchone("SELECT * FROM complaints WHERE id = ?", (complaint_id,))
        expect(complaint_row["title"].endswith("-수정") and complaint_row["priority"] == "긴급", "민원 수정이 반영되지 않았습니다.")
        template_row = fetchone("SELECT * FROM complaint_response_templates WHERE name = ?", ("접수 안내",))
        expect(template_row is not None, "민원 회신 템플릿 기본값이 생성되지 않았습니다.")

        repeat_title = f"반복민원-{suffix}"
        repeat_create = client.post(
            "/complaints/save",
            data={
                "channel": "전화",
                "category_primary": "기계",
                "category_secondary": "환기",
                "facility_id": str(facility_id),
                "unit_label": "101동 1203호",
                "location_detail": "욕실 환풍기",
                "requester_name": "검증민원인",
                "requester_phone": "01011113333",
                "requester_email": "repeat@example.com",
                "title": repeat_title,
                "description": "반복 민원 감지 검증",
                "priority": "보통",
                "status": "접수",
                "response_due_at": "",
                "assignee_user_id": "",
            },
            follow_redirects=False,
        )
        expect(repeat_create.status_code in {302, 303}, "반복 민원 등록 요청이 실패했습니다.")
        repeat_row = fetchone("SELECT * FROM complaints WHERE title = ?", (repeat_title,))
        expect(repeat_row is not None, "반복 민원이 생성되지 않았습니다.")
        repeat_id = repeat_row["id"]

        complaint_detail = client.get(f"/complaints?edit={complaint_id}")
        expect(
            complaint_detail.status_code == 200 and "반복 민원 감지" in complaint_detail.text and repeat_title in complaint_detail.text,
            "반복 민원 감지 화면이 올바르게 표시되지 않습니다.",
        )
        expect("PDF 출력" in complaint_detail.text, "민원 화면에 PDF 출력 버튼이 보이지 않습니다.")
        complaint_pdf = client.get("/complaints/pdf", params={"q": complaint_title})
        expect(
            complaint_pdf.status_code == 200
            and complaint_pdf.headers.get("content-type", "").startswith("application/pdf")
            and len(complaint_pdf.content) > 1000,
            "민원 PDF 출력이 정상 동작하지 않습니다.",
        )

        inventory_name = f"검증재고-{suffix}"
        inventory_create = client.post(
            "/inventory/save",
            data={
                "category": "전기",
                "name": inventory_name,
                "specification": "10A",
                "quantity": "5",
                "unit": "개",
                "location": "창고 A",
                "status": "정상",
                "min_quantity": "2",
                "purchase_date": "2026-03-29",
                "purchase_amount": "10000",
                "note": "CRUD 검증",
            },
            files=image_files(f"inventory-{suffix}", 6),
            follow_redirects=False,
        )
        expect(inventory_create.status_code in {302, 303}, "재고 등록 요청이 실패했습니다.")
        inventory_row = fetchone("SELECT * FROM inventory_items WHERE name = ?", (inventory_name,))
        expect(inventory_row is not None, "재고가 생성되지 않았습니다.")
        inventory_id = inventory_row["id"]
        inventory_attachment_count = fetchone(
            "SELECT COUNT(*) AS count FROM attachments WHERE entity_type = 'inventory' AND entity_id = ?",
            (inventory_id,),
        )["count"]
        expect(inventory_attachment_count == 6, "재고 첨부 이미지 저장 개수가 올바르지 않습니다.")

        inventory_page = client.get(f"/inventory?edit={inventory_id}")
        expect(inventory_page.status_code == 200 and "formaction='/inventory/delete/" in inventory_page.text, "재고 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        inventory_update = client.post(
            "/inventory/save",
            data={
                "item_id": str(inventory_id),
                "category": "기계",
                "name": f"{inventory_name}-수정",
                "specification": "20A",
                "quantity": "8",
                "unit": "개",
                "location": "창고 B",
                "status": "정상",
                "min_quantity": "3",
                "purchase_date": "2026-03-30",
                "purchase_amount": "12000",
                "note": "수정 완료",
            },
            follow_redirects=False,
        )
        expect(inventory_update.status_code in {302, 303}, "재고 수정 요청이 실패했습니다.")
        inventory_row = fetchone("SELECT * FROM inventory_items WHERE id = ?", (inventory_id,))
        expect(inventory_row["name"].endswith("-수정") and inventory_row["quantity"] == 8, "재고 수정이 반영되지 않았습니다.")

        inventory_over_limit = client.post(
            "/inventory/save",
            data={
                "item_id": str(inventory_id),
                "category": "기계",
                "name": f"{inventory_name}-초과시도",
                "specification": "20A",
                "quantity": "8",
                "unit": "개",
                "location": "창고 B",
                "status": "정상",
                "min_quantity": "3",
                "purchase_date": "2026-03-30",
                "purchase_amount": "12000",
                "note": "첨부 초과 시도",
            },
            files=image_files(f"inventory-over-{suffix}", 1),
            follow_redirects=False,
        )
        expect(inventory_over_limit.status_code in {302, 303}, "재고 첨부 초과 제한 응답이 비정상입니다.")
        inventory_row = fetchone("SELECT * FROM inventory_items WHERE id = ?", (inventory_id,))
        expect(inventory_row["name"].endswith("-수정"), "재고 첨부 초과 시 기본정보가 변경되면 안 됩니다.")
        inventory_attachment_count = fetchone(
            "SELECT COUNT(*) AS count FROM attachments WHERE entity_type = 'inventory' AND entity_id = ?",
            (inventory_id,),
        )["count"]
        expect(inventory_attachment_count == 6, "재고 첨부 초과 시 이미지가 추가 저장되면 안 됩니다.")

        inventory_tx = client.post(
            f"/inventory/tx/{inventory_id}",
            data={"tx_type": "반출", "quantity": "2", "reason": "검증 사용"},
            follow_redirects=False,
        )
        expect(inventory_tx.status_code in {302, 303}, "재고 수불 요청이 실패했습니다.")
        inventory_row = fetchone("SELECT * FROM inventory_items WHERE id = ?", (inventory_id,))
        expect(inventory_row["quantity"] == 6, "재고 수불 결과 수량이 올바르지 않습니다.")
        tx_count = fetchone("SELECT COUNT(*) AS count FROM inventory_transactions WHERE item_id = ?", (inventory_id,))["count"]
        expect(tx_count >= 2, "재고 수불 이력이 충분히 생성되지 않았습니다.")

        work_title = f"검증작업-{suffix}"
        work_create = client.post(
            "/work-orders/save",
            data={
                "complaint_id": str(complaint_id),
                "category": "전기",
                "title": work_title,
                "facility_id": str(facility_id),
                "requester_name": "검증요청자",
                "priority": "보통",
                "status": "접수",
                "description": "작업 생성 검증",
                "assignee_user_id": "",
                "due_date": "2026-03-31",
            },
            follow_redirects=False,
        )
        expect(work_create.status_code in {302, 303}, "작업지시 등록 요청이 실패했습니다.")
        work_row = fetchone("SELECT * FROM work_orders WHERE title = ?", (work_title,))
        expect(work_row is not None, "작업지시가 생성되지 않았습니다.")
        work_id = work_row["id"]
        expect(work_row["complaint_id"] == complaint_id, "작업지시에 민원 연결이 저장되지 않았습니다.")
        complaint_row = fetchone("SELECT * FROM complaints WHERE id = ?", (complaint_id,))
        expect(complaint_row["status"] == "배정완료", "민원 상태가 작업지시 연결에 맞게 갱신되지 않았습니다.")

        work_page = client.get(f"/work-orders?edit={work_id}")
        expect(work_page.status_code == 200 and "formaction='/work-orders/delete/" in work_page.text, "작업지시 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        work_update = client.post(
            "/work-orders/save",
            data={
                "work_order_id": str(work_id),
                "complaint_id": str(complaint_id),
                "category": "기계",
                "title": f"{work_title}-수정",
                "facility_id": str(facility_id),
                "requester_name": "검증요청자",
                "priority": "높음",
                "status": "진행중",
                "description": "작업 수정 검증",
                "assignee_user_id": "",
                "due_date": "2026-04-01",
            },
            follow_redirects=False,
        )
        expect(work_update.status_code in {302, 303}, "작업지시 수정 요청이 실패했습니다.")
        work_row = fetchone("SELECT * FROM work_orders WHERE id = ?", (work_id,))
        expect(work_row["title"].endswith("-수정") and work_row["status"] == "진행중", "작업지시 수정이 반영되지 않았습니다.")

        complaint_progress = client.post(
            f"/complaints/update/{complaint_id}",
            data={"update_type": "상태변경", "message": "현장 확인 후 처리중으로 전환", "status": "처리중", "is_public_note": "1"},
            follow_redirects=False,
        )
        expect(complaint_progress.status_code in {302, 303}, "민원 업데이트 요청이 실패했습니다.")
        complaint_row = fetchone("SELECT * FROM complaints WHERE id = ?", (complaint_id,))
        expect(complaint_row["status"] == "처리중", "민원 상태 업데이트가 반영되지 않았습니다.")
        complaint_update_count = fetchone("SELECT COUNT(*) AS count FROM complaint_updates WHERE complaint_id = ?", (complaint_id,))["count"]
        expect(complaint_update_count >= 3, "민원 이력이 충분히 생성되지 않았습니다.")

        complaint_feedback = client.post(
            f"/complaints/feedback/{complaint_id}",
            data={"rating": "4", "comment": "조치 속도가 빨랐습니다.", "follow_up_at": "2026-04-02"},
            follow_redirects=False,
        )
        expect(complaint_feedback.status_code in {302, 303}, "민원 만족도 저장 요청이 실패했습니다.")
        feedback_row = fetchone("SELECT * FROM complaint_feedback WHERE complaint_id = ?", (complaint_id,))
        expect(
            feedback_row is not None and feedback_row["rating"] == 4 and feedback_row["comment"] == "조치 속도가 빨랐습니다.",
            "민원 만족도 저장이 반영되지 않았습니다.",
        )
        complaint_update_count = fetchone("SELECT COUNT(*) AS count FROM complaint_updates WHERE complaint_id = ?", (complaint_id,))["count"]
        expect(complaint_update_count >= 4, "민원 만족도 기록 이력이 생성되지 않았습니다.")

        work_progress = client.post(
            f"/work-orders/update/{work_id}",
            data={"update_type": "진행보고", "body": "현장 확인 완료", "status": "완료"},
            follow_redirects=False,
        )
        expect(work_progress.status_code in {302, 303}, "작업지시 업데이트 요청이 실패했습니다.")
        work_row = fetchone("SELECT * FROM work_orders WHERE id = ?", (work_id,))
        expect(work_row["status"] == "완료", "작업지시 상태 업데이트가 반영되지 않았습니다.")
        work_update_count = fetchone("SELECT COUNT(*) AS count FROM work_order_updates WHERE work_order_id = ?", (work_id,))["count"]
        expect(work_update_count >= 3, "작업지시 이력이 충분히 생성되지 않았습니다.")

        user_name = f"user_{suffix}"
        user_create = client.post(
            "/admin/users/save",
            data={
                "username": user_name,
                "full_name": "검증 사용자",
                "phone": "01099998888",
                "role": "viewer",
                "recovery_question": "",
                "recovery_answer": "",
                "password": "Test1234!",
                "is_active": "1",
            },
            follow_redirects=False,
        )
        expect(user_create.status_code in {302, 303}, "사용자 등록 요청이 실패했습니다.")
        user_row = fetchone("SELECT * FROM users WHERE username = ?", (user_name,))
        expect(user_row is not None, "사용자가 생성되지 않았습니다.")
        user_id = user_row["id"]

        user_edit_page = client.get(f"/admin/users?edit={user_id}")
        expect(user_edit_page.status_code == 200 and f"formaction='/admin/users/delete/{user_id}'" in user_edit_page.text, "사용자 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        user_update = client.post(
            "/admin/users/save",
            data={
                "user_id": str(user_id),
                "username": user_name,
                "full_name": "검증 사용자 수정",
                "phone": "01099997777",
                "role": "technician",
                "recovery_question": "",
                "recovery_answer": "",
                "password": "",
                "is_active": "1",
            },
            follow_redirects=False,
        )
        expect(user_update.status_code in {302, 303}, "사용자 수정 요청이 실패했습니다.")
        user_row = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        expect(user_row["full_name"] == "검증 사용자 수정" and user_row["role"] == "technician", "사용자 수정이 반영되지 않았습니다.")

        raw_name = f"raw-item-{suffix}"
        raw_create = client.post(
            "/admin/database/save",
            data={
                "table": "inventory_items",
                "row_id": "",
                "col_item_code": "",
                "col_category": "기타",
                "col_name": raw_name,
                "col_specification": "",
                "col_quantity": "1",
                "col_unit": "개",
                "col_location": "",
                "col_status": "정상",
                "col_min_quantity": "0",
                "col_purchase_date": "",
                "col_purchase_amount": "0",
                "col_note": "",
                "col_legacy_tool_id": "",
                "col_created_by": "1",
                "col_updated_by": "1",
                "col_created_at": "",
                "col_updated_at": "",
            },
            follow_redirects=False,
        )
        expect(raw_create.status_code in {302, 303}, "DB관리 행 등록 요청이 실패했습니다.")
        raw_row = fetchone("SELECT * FROM inventory_items WHERE name = ?", (raw_name,))
        expect(raw_row is not None, "DB관리로 재고 행이 생성되지 않았습니다.")
        raw_id = raw_row["id"]

        raw_update = client.post(
            "/admin/database/save",
            data={
                "table": "inventory_items",
                "row_id": str(raw_id),
                "col_item_code": raw_row["item_code"],
                "col_category": "기타",
                "col_name": f"{raw_name}-수정",
                "col_specification": raw_row["specification"],
                "col_quantity": "9",
                "col_unit": raw_row["unit"],
                "col_location": raw_row["location"],
                "col_status": raw_row["status"],
                "col_min_quantity": str(raw_row["min_quantity"]),
                "col_purchase_date": raw_row["purchase_date"],
                "col_purchase_amount": str(raw_row["purchase_amount"]),
                "col_note": raw_row["note"],
                "col_legacy_tool_id": "" if raw_row["legacy_tool_id"] is None else str(raw_row["legacy_tool_id"]),
                "col_created_by": "" if raw_row["created_by"] is None else str(raw_row["created_by"]),
                "col_updated_by": "" if raw_row["updated_by"] is None else str(raw_row["updated_by"]),
                "col_created_at": raw_row["created_at"],
                "col_updated_at": raw_row["updated_at"],
            },
            follow_redirects=False,
        )
        expect(raw_update.status_code in {302, 303}, "DB관리 행 수정 요청이 실패했습니다.")
        raw_row = fetchone("SELECT * FROM inventory_items WHERE id = ?", (raw_id,))
        expect(raw_row["name"].endswith("-수정") and raw_row["quantity"] == 9, "DB관리 수정이 반영되지 않았습니다.")

        inventory_delete = client.post(f"/inventory/delete/{inventory_id}", follow_redirects=False)
        expect(inventory_delete.status_code in {302, 303}, "재고 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM inventory_items WHERE id = ?", (inventory_id,)) is None, "재고가 삭제되지 않았습니다.")

        complaint_delete = client.post(f"/complaints/delete/{complaint_id}", follow_redirects=False)
        expect(complaint_delete.status_code in {302, 303}, "민원 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM complaints WHERE id = ?", (complaint_id,)) is None, "민원이 삭제되지 않았습니다.")
        work_row = fetchone("SELECT * FROM work_orders WHERE id = ?", (work_id,))
        expect(work_row is not None and work_row["complaint_id"] is None, "민원 삭제 시 연결 작업지시 해제가 반영되지 않았습니다.")

        work_delete = client.post(f"/work-orders/delete/{work_id}", follow_redirects=False)
        expect(work_delete.status_code in {302, 303}, "작업지시 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM work_orders WHERE id = ?", (work_id,)) is None, "작업지시가 삭제되지 않았습니다.")

        contact_delete = client.post(f"/contacts/delete/{contact_id}", follow_redirects=False)
        expect(contact_delete.status_code in {302, 303}, "연락처 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM contacts WHERE id = ?", (contact_id,)) is None, "연락처가 삭제되지 않았습니다.")
        office_row = fetchone("SELECT * FROM office_records WHERE id = ?", (office_id,))
        expect(office_row is not None and office_row["contact_id"] is None, "연락처 삭제 시 행정업무 연결 해제가 반영되지 않았습니다.")

        office_delete = client.post(f"/office-records/delete/{office_id}", follow_redirects=False)
        expect(office_delete.status_code in {302, 303}, "행정업무 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM office_records WHERE id = ?", (office_id,)) is None, "행정업무가 삭제되지 않았습니다.")

        facility_delete = client.post(f"/facilities/delete/{facility_id}", follow_redirects=False)
        expect(facility_delete.status_code in {302, 303}, "시설 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM facilities WHERE id = ?", (facility_id,)) is None, "시설이 삭제되지 않았습니다.")

        user_delete = client.post(f"/admin/users/delete/{user_id}", follow_redirects=False)
        expect(user_delete.status_code in {302, 303}, "사용자 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM users WHERE id = ?", (user_id,)) is None, "사용자가 삭제되지 않았습니다.")

        raw_delete = client.post(
            "/admin/database/delete",
            data={"table": "inventory_items", "row_id": str(raw_id)},
            follow_redirects=False,
        )
        expect(raw_delete.status_code in {302, 303}, "DB관리 행 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM inventory_items WHERE id = ?", (raw_id,)) is None, "DB관리 삭제가 반영되지 않았습니다.")

        bulk_name_a = f"bulk-item-a-{suffix}"
        bulk_name_b = f"bulk-item-b-{suffix}"
        for bulk_name in (bulk_name_a, bulk_name_b):
            bulk_create = client.post(
                "/admin/database/save",
                data={
                    "table": "inventory_items",
                    "row_id": "",
                    "col_item_code": "",
                    "col_category": "기타",
                    "col_name": bulk_name,
                    "col_specification": "",
                    "col_quantity": "2",
                    "col_unit": "개",
                    "col_location": "",
                    "col_status": "정상",
                    "col_min_quantity": "0",
                    "col_purchase_date": "",
                    "col_purchase_amount": "0",
                    "col_note": "",
                    "col_legacy_tool_id": "",
                    "col_created_by": "1",
                    "col_updated_by": "1",
                    "col_created_at": "",
                    "col_updated_at": "",
                },
                follow_redirects=False,
            )
            expect(bulk_create.status_code in {302, 303}, "DB관리 bulk 삭제용 행 등록 요청이 실패했습니다.")

        bulk_row_a = fetchone("SELECT * FROM inventory_items WHERE name = ?", (bulk_name_a,))
        bulk_row_b = fetchone("SELECT * FROM inventory_items WHERE name = ?", (bulk_name_b,))
        expect(bulk_row_a is not None and bulk_row_b is not None, "DB관리 bulk 삭제용 행이 생성되지 않았습니다.")

        raw_page = client.get("/admin/database?table=inventory_items")
        expect(
            raw_page.status_code == 200 and "/admin/database/delete-selected" in raw_page.text and "data-db-select-all" in raw_page.text,
            "DB관리 화면에 bulk 삭제 UI가 반영되지 않았습니다.",
        )

        bulk_delete = client.post(
            "/admin/database/delete-selected",
            data={
                "table": "inventory_items",
                "row_ids": [str(bulk_row_a["id"]), str(bulk_row_b["id"])],
            },
            follow_redirects=False,
        )
        expect(bulk_delete.status_code in {302, 303}, "DB관리 bulk 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM inventory_items WHERE id = ?", (bulk_row_a["id"],)) is None, "DB관리 bulk 삭제 첫 번째 행이 반영되지 않았습니다.")
        expect(fetchone("SELECT * FROM inventory_items WHERE id = ?", (bulk_row_b["id"],)) is None, "DB관리 bulk 삭제 두 번째 행이 반영되지 않았습니다.")

        repeat_delete = client.post(f"/complaints/delete/{repeat_id}", follow_redirects=False)
        expect(repeat_delete.status_code in {302, 303}, "반복 민원 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM complaints WHERE id = ?", (repeat_id,)) is None, "반복 민원이 삭제되지 않았습니다.")

        print("OK: CRUD flows verified")
    finally:
        if client is not None:
            client.close()
        shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    main()
