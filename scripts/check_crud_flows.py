from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    tmp_path = Path(tempfile.mkdtemp(prefix="ops-crud-check-"))
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
            follow_redirects=False,
        )
        expect(facility_create.status_code in {302, 303}, "시설 등록 요청이 실패했습니다.")
        facility_row = fetchone("SELECT * FROM facilities WHERE name = ?", (facility_name,))
        expect(facility_row is not None, "시설이 생성되지 않았습니다.")
        facility_id = facility_row["id"]

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
            follow_redirects=False,
        )
        expect(inventory_create.status_code in {302, 303}, "재고 등록 요청이 실패했습니다.")
        inventory_row = fetchone("SELECT * FROM inventory_items WHERE name = ?", (inventory_name,))
        expect(inventory_row is not None, "재고가 생성되지 않았습니다.")
        inventory_id = inventory_row["id"]

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

        work_page = client.get(f"/work-orders?edit={work_id}")
        expect(work_page.status_code == 200 and "formaction='/work-orders/delete/" in work_page.text, "작업지시 수정 화면의 삭제 버튼 구조가 올바르지 않습니다.")

        work_update = client.post(
            "/work-orders/save",
            data={
                "work_order_id": str(work_id),
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

        work_delete = client.post(f"/work-orders/delete/{work_id}", follow_redirects=False)
        expect(work_delete.status_code in {302, 303}, "작업지시 삭제 요청이 실패했습니다.")
        expect(fetchone("SELECT * FROM work_orders WHERE id = ?", (work_id,)) is None, "작업지시가 삭제되지 않았습니다.")

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

        print("OK: CRUD flows verified")
    finally:
        if client is not None:
            client.close()
        shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    main()
