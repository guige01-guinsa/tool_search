from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시설 운영 시스템용 세대 민원 PDF 이관")
    parser.add_argument("pdf_path", help="이관할 PDF 파일 경로")
    parser.add_argument("--db", dest="db_path", default="", help="대상 SQLite DB 경로")
    parser.add_argument("--apply", action="store_true", help="실제 DB에 반영")
    parser.add_argument("--update-existing", action="store_true", help="같은 source_reference 레코드도 다시 반영")
    parser.add_argument("--skip-work-orders", action="store_true", help="작업지시 자동 생성을 끔")
    parser.add_argument("--user-id", type=int, default=1, help="created_by/updated_by에 기록할 사용자 id")
    parser.add_argument("--json", action="store_true", help="요약 결과를 JSON으로 출력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf_path).expanduser()
    if not pdf_path.exists():
        raise SystemExit(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    if args.db_path:
        os.environ["OPS_DB_PATH"] = str(Path(args.db_path).expanduser())

    from ops.db import get_conn, init_db
    from ops import pdf_import

    init_db()
    conn = get_conn()
    try:
        summary = pdf_import.import_complaints_pdf_bytes(
            conn,
            pdf_path.read_bytes(),
            source_name=pdf_path.name,
            dry_run=not args.apply,
            update_existing=args.update_existing,
            create_work_orders=not args.skip_work_orders,
            default_user_id=args.user_id,
        )
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    report = summary.get("report", {})
    counts = summary.get("counts", {})
    batch = summary.get("batch", {})
    mode_label = "실제 이관" if args.apply else "드라이런"
    print(f"[{mode_label}] {report.get('site_name') or '단지 미상'} / {pdf_path.name}")
    print(
        "민원 "
        f"{counts.get('parsed_complaints', 0)}건 해석, "
        f"신규 {counts.get('complaints_inserted', 0)}건, "
        f"수정 {counts.get('complaints_updated', 0)}건, "
        f"유지 {counts.get('complaints_skipped', 0)}건"
    )
    print(
        "시설 "
        f"{counts.get('facilities_created', 0)}건 생성, "
        f"작업지시 {counts.get('work_orders_inserted', 0)}건 생성, "
        f"배치 {batch.get('batch_code') or '-'}"
    )
    warnings = report.get("warnings", [])
    if warnings:
        print(f"경고 {len(warnings)}건")
        for item in warnings[:10]:
            print(f"- {item}")


if __name__ == "__main__":
    main()
