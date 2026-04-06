# 시설 운영 시스템

기존 `tool_search`를 대체하는 운영용 웹 시스템이다.

## 포함 기능

- 시설 관리
- 재고 관리
- 연락처 관리 (`업체연락처 / 근무자연락처 / 계약업체 / 관공서`)
- 행정업무 관리 (`기안지 / 공문서 / 정기점검`)
- 민원 관리
- 세대 민원 PDF 이관
- 민원 목록 PDF 출력
- 민원 만족도 기록
- 민원 SLA 경고 / 반복 민원 감지
- 민원 회신 템플릿
- 재고 수불 이력
- 작업지시 및 진행 업데이트
- 운영 보고서
- 사용자/권한 관리
- 관리자용 DB관리 화면
- 회원가입 자동 승인
- 아이디 찾기 / 비밀번호 재설정

## 실행

```bash
PORT=8000 ./start.sh
```

Windows PowerShell:

```powershell
$env:PORT=8000
uvicorn ops_main:app --host 0.0.0.0 --port $env:PORT
```

## 점검 / 테스트

- 문법 점검: `python -m py_compile ops_main.py scripts\check_crud_flows.py scripts\check_stability_flows.py scripts\check_pdf_import_flows.py`
- CRUD 회귀 점검: `python scripts/check_crud_flows.py`
- 안정화 스모크 점검: `python scripts/check_stability_flows.py`
- PDF 이관 점검: `python scripts/check_pdf_import_flows.py`

## 초기 관리자

- 아이디: `admin`
- 비밀번호: `admin1234`

로컬 기본값이며, 배포 환경에서는 `OPS_ADMIN_PASSWORD` 환경변수로 바꾸는 편이 안전하다.

## 계정 복구

- `회원가입`: 기본 `조회전용 + 활성`으로 생성되고, 가입 직후 바로 로그인 가능
- `아이디 찾기`: 이름, 연락처, 복구 질문/답변이 일치할 때 아이디 일부 표시
- `비밀번호 재설정`: 아이디, 이름, 연락처, 복구 질문/답변이 일치할 때 새 비밀번호로 변경

## PWA 설치

- Android Chrome: 주소 접속 후 상단 `앱 설치` 버튼 또는 브라우저 메뉴의 `앱 설치`를 사용
- iPhone/iPad Safari: 공유 버튼을 누른 뒤 `홈 화면에 추가`를 선택
- 홈 화면에 추가한 뒤에는 전체화면 앱처럼 실행되고, 같은 네트워크의 서버 주소로 접속한다.
- `http://127.0.0.1` 같은 로컬 PC에서는 바로 동작하지만, 스마트폰 등 다른 기기에서 설치형 PWA로 쓰려면 보통 `HTTPS` 주소가 필요하다.

## 로컬 HTTPS

- 인증서 생성: `powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_https.ps1`
- exe 실행: `FacilityOpsServer.exe` 또는 개발 실행: `python .\ops_launcher.py`
- 인증서가 `runtime_data\certs\local-network-cert.pem` / `local-network-key.pem`에 있으면 런처가 자동으로 `https://<PC-IP>:8443` 로 실행한다.
- Android 스마트폰은 `runtime_data\certs\rootCA.crt` 또는 `rootCA.pem` 설치 후 인증서를 신뢰해야 하고, iPhone/iPad는 프로파일 설치 후 `설정 > 일반 > 정보 > 인증서 신뢰 설정`에서 전체 신뢰를 켜야 한다.

## DB 관리

- 관리자 로그인 후 상단 `DB관리` 메뉴에서만 모든 운영 테이블을 raw DB 수준으로 조회·등록·수정·삭제 가능
- 관리자 `DB관리` 화면에서 `민원 PDF 이관` 패널로 세대 민원 처리 현황 PDF를 바로 업로드해 민원/시설/작업지시로 변환 가능
- 목록 화면에서 체크박스로 여러 행을 선택한 뒤 `선택 삭제`로 일괄 삭제 가능
- 대상 테이블: `users`, `sessions`, `facilities`, `contacts`, `office_records`, `office_record_updates`, `inventory_items`, `inventory_transactions`, `complaints`, `complaint_updates`, `complaint_feedback`, `complaint_response_templates`, `work_orders`, `work_order_updates`, `attachments`

## 권한 분리

- `admin`: 사용자 관리, raw DB 관리, 민원/시설/재고/연락처/작업지시/행정업무 전체 관리
- `manager`: 민원/시설/재고/연락처/작업지시/행정업무 전체 관리, raw DB 직접 접근 불가
- `technician`: 민원 접수, 본인 민원 업데이트, 만족도 기록, 재고 수불 처리, 작업지시 등록, 본인 생성 또는 본인 배정 작업지시 업데이트, 본인 연락처 등록, 본인 행정업무 등록/업데이트
- `viewer`: 대시보드, 민원, 시설, 재고, 연락처, 작업지시, 행정업무, 보고서 조회만 허용

## 민원 2차 기능

- `SLA 자동 계산`: 회신 목표일을 비워 두면 우선도 기준(`긴급 0일`, `높음 1일`, `보통 3일`, `낮음 5일`)으로 자동 입력
- `반복 민원 감지`: 같은 연락처 기준 최근 90일 내 유사 위치/분류 민원을 상세 화면과 보고서에서 표시
- `만족도 기록`: 민원별 1~5점 만족도와 후속 연락일, 코멘트를 기록
- `회신 템플릿`: 공통/분류별 회신 템플릿을 기본 제공하고 `DB관리 > complaint_response_templates`에서 수정 가능

## 연락처 / 행정업무 연계

- 연락처는 `업체연락처`, `근무자연락처`, `계약업체`, `관공서`로 구분해 저장
- 행정업무 등록 시 `연결 연락처`를 선택하면 대상 / 수신처와 연락수단을 함께 조회 가능
- 연락처 상세 화면에서 해당 연락처와 연결된 최근 행정업무를 바로 확인 가능
- 대시보드와 운영 보고서에서 연락처 현황과 행정업무 연계 상태를 함께 확인 가능
- 시설 관리와 재고 관리의 첨부 이미지는 각각 최대 6장까지 누적 저장 가능하며, 기존 첨부를 선택해 일괄 삭제 가능
- 행정업무는 문서 파일과 이미지 첨부를 함께 관리할 수 있고, 기존 첨부 포함 최대 6개까지 누적 저장 및 선택 삭제 가능

## 세대 민원 PDF 이관

- 관리자 화면에서도 `DB관리 > 민원 PDF 이관` 패널에서 `드라이런`, `실제 이관`을 실행할 수 있다.
- 로컬 파일 기준 CLI 실행: `python scripts/import_complaints_pdf.py c:\guige\pdf\분류_동호.pdf`
- 실제 DB 반영: `python scripts/import_complaints_pdf.py c:\guige\pdf\분류_동호.pdf --apply`
- 다른 DB 파일로 검증: `python scripts/import_complaints_pdf.py c:\guige\pdf\분류_동호.pdf --db .\tmp\operations.db --json`
- PDF에서 `단지명`, `동/호`, `민원유형`, `상태`, `담당자`, `접수일시`, `연락처`, `민원내용`을 읽어 민원/시설/작업지시로 자동 매핑한다.

## Render 배포

이 저장소에는 `render.yaml`이 포함되어 있다.

- Build Command: `pip install -r requirements.txt`
- Start Command: `bash start.sh`
- Health Check: `/healthz`
- `OPS_ADMIN_PASSWORD`, `OPS_COOKIE_SECURE=true`, `OPS_DB_PATH=/opt/render/project/src/data/operations.db`, `OPS_UPLOAD_DIR=/opt/render/project/src/data/uploads`를 권장한다.

Render에서 자동배포가 실패하면서 `pipeline_minutes_exhausted` 메시지가 보이면, 빌드 분이 소진된 상태라서 코드 문제가 아니라 요금제/월간 분량 문제다.

## 9인 기본 계정 생성

```powershell
python scripts/seed_team.py
```

## 문서

- `docs/operations_redesign_plan.md`
- `docs/complaint_integration_plan.md`
- `docs/team_and_deployment_runbook.md`
