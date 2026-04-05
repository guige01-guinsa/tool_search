from __future__ import annotations

import gc
import io
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

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


def build_sample_pdf() -> bytes:
    font_name = "Helvetica"
    for candidate in ("HYGothic-Medium", "HYSMyeongJo-Medium"):
        try:
            if candidate not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(candidate))
            font_name = candidate
            break
        except Exception:
            continue

    def write_page(pdf: canvas.Canvas, lines: list[str]) -> None:
        text = pdf.beginText(36, 806)
        text.setFont(font_name, 9)
        for line in lines:
            text.textLine(line)
        pdf.drawText(text)
        pdf.showPage()

    first_page_lines = [
        "KA",
        "테스트더샵",
        "Field Operations Reporting",
        "시공사 샘플건설",
        "세대 민원 처리 현황 보고서",
        "테스트 PDF 이관 검증용 요약입니다.",
        "최근 접수 2026-04-05 09:30",
        "제출처",
        "테스트더샵 관리사무소",
        "보고일",
        "2026년 04월 05일",
        "문서구분",
        "전체 보고",
        "단지명",
        "테스트더샵",
        "제출사",
        "테스트더샵",
        "시공사",
        "샘플건설",
        "공사명",
        "테스트더샵 외벽 보수 세대 민원 처리",
        "민원건수",
        "3",
        "세대수",
        "120",
        "미처리",
        "3",
        "종결",
        "0",
        "재민원",
        "0",
        "보고기준 2026-04-05 12:00:00 · 단지 테스트더샵 · 범위 전체",
        "진행률",
        "100%",
        "3/3",
        "완성률",
        "0%",
        "0/3",
        "상태 분포",
        "접수",
        "1",
        "배정완료",
        "2",
        "상세 목록",
        "민원ID",
        "동",
        "호수",
        "민원유형",
        "상태",
        "담당자",
        "접수일시",
        "연락처",
        "민원내용",
        "동: 101동",
        "1001",
        "101동",
        "1203호",
        "복합 민원",
        "배정완료",
        "현장A",
        "2026-04-05",
        "09:00",
        "010-1111-2222",
        "거실 창문 및 방충망 오염",
        "1002",
        "101동",
        "1502호",
        "유리/창문",
        "오염",
        "접수",
        "미배정",
        "2026-04-05",
        "09:30",
        "작은방 창문 오염",
        "동 소계 · 101동: 2건",
        "KA Facility OS · 세대 민원관리 보고서",
        "1 page",
    ]
    second_page_lines = [
        "세대 민원관리 전체 상세목록",
        "테스트더샵 · 전체",
        "민원ID",
        "동",
        "호수",
        "민원유형",
        "상태",
        "담당자",
        "접수일시",
        "연락처",
        "민원내용",
        "동: 102동",
        "1003",
        "102동",
        "804호",
        "기타 마감불량",
        "배정완료",
        "현장B",
        "2026-04-04",
        "17:15",
        "010-3333-4444",
        "외벽 도색 마감 불량",
        "동 소계 · 102동: 1건",
        "KA Facility OS · 세대 민원관리 보고서",
        "2 page",
    ]

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    write_page(pdf, first_page_lines)
    write_page(pdf, second_page_lines)
    pdf.save()
    return buf.getvalue()


def main() -> None:
    tmp_path = ROOT_DIR / f"tmp_ops_pdf_import_{uuid.uuid4().hex[:8]}"
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
        from ops.db import get_conn

        client = TestClient(ops_main.app)
        login = client.post(
            "/login",
            data={"username": "admin", "password": "admin1234"},
            follow_redirects=False,
        )
        expect(login.status_code in {302, 303}, "관리자 로그인에 실패했습니다.")

        pdf_bytes = build_sample_pdf()

        dry_run = client.post(
            "/admin/complaints-pdf-import",
            data={"action": "dry_run", "create_work_orders": "1"},
            files={"pdf_file": ("sample.pdf", pdf_bytes, "application/pdf")},
            follow_redirects=True,
        )
        expect(
            dry_run.status_code == 200 and "PDF 드라이런" in dry_run.text,
            "PDF 드라이런 플로우가 비정상입니다.",
        )

        apply_resp = client.post(
            "/admin/complaints-pdf-import",
            data={"action": "apply", "create_work_orders": "1"},
            files={"pdf_file": ("sample.pdf", pdf_bytes, "application/pdf")},
            follow_redirects=True,
        )
        expect(
            apply_resp.status_code == 200 and "PDF 이관 완료" in apply_resp.text,
            "PDF 실제 이관 플로우가 비정상입니다.",
        )

        conn = get_conn()
        batch_count = conn.execute(
            "SELECT COUNT(*) AS count FROM complaint_import_batches WHERE source_type = 'pdf_report'"
        ).fetchone()["count"]
        complaint_count = conn.execute(
            "SELECT COUNT(*) AS count FROM complaints WHERE source_type = 'pdf_report'"
        ).fetchone()["count"]
        facility_count = conn.execute(
            "SELECT COUNT(*) AS count FROM facilities WHERE source_type = 'pdf_report'"
        ).fetchone()["count"]
        work_count = conn.execute(
            "SELECT COUNT(*) AS count FROM work_orders WHERE source_type = 'pdf_report'"
        ).fetchone()["count"]
        sample_row = conn.execute(
            """
            SELECT complaint_code, site_name, building_label, unit_number, external_assignee_name, source_reference
            FROM complaints
            WHERE source_reference = 'pdf_report:complaint:테스트더샵:1001'
            """
        ).fetchone()
        conn.close()

        expect(batch_count == 1, "PDF 이관 배치가 생성되지 않았습니다.")
        expect(complaint_count == 3, "PDF 민원 건수가 올바르지 않습니다.")
        expect(facility_count == 2, "PDF 기준 시설 생성 수가 올바르지 않습니다.")
        expect(work_count == 3, "PDF 기준 작업지시 생성 수가 올바르지 않습니다.")
        expect(sample_row is not None, "PDF 원본 reference 기반 민원이 저장되지 않았습니다.")
        expect(sample_row["site_name"] == "테스트더샵", "단지명이 민원에 저장되지 않았습니다.")
        expect(sample_row["building_label"] == "101동", "동 정보가 민원에 저장되지 않았습니다.")
        expect(sample_row["external_assignee_name"] == "현장A", "외부 담당자명이 저장되지 않았습니다.")

        complaint_page = client.get("/complaints", params={"site": "테스트더샵", "building": "101동"})
        expect(
            complaint_page.status_code == 200 and "전체 동" in complaint_page.text and "테스트더샵" in complaint_page.text,
            "민원 화면의 단지/동 필터가 비정상입니다.",
        )

        complaint_pdf = client.get("/complaints/pdf", params={"site": "테스트더샵", "building": "101동"})
        expect(
            complaint_pdf.status_code == 200
            and complaint_pdf.headers.get("content-type", "").startswith("application/pdf")
            and len(complaint_pdf.content) > 1000,
            "PDF 이관 후 민원 PDF 출력이 비정상입니다.",
        )

        print("OK: pdf import flows verified")
    finally:
        if client is not None:
            client.close()
        remove_tree(tmp_path)


if __name__ == "__main__":
    main()
