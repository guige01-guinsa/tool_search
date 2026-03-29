# 시설 운영 시스템

기존 `tool_search`를 대체하는 운영용 웹 시스템이다.

## 포함 기능

- 시설 관리
- 재고 관리
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

## DB 관리

- 관리자 로그인 후 상단 `DB관리` 메뉴에서만 모든 운영 테이블을 raw DB 수준으로 조회·등록·수정·삭제 가능
- 대상 테이블: `users`, `sessions`, `facilities`, `inventory_items`, `inventory_transactions`, `work_orders`, `work_order_updates`, `attachments`

## 권한 분리

- `admin`: 사용자 관리, raw DB 관리, 전체 업무 데이터 관리
- `manager`: 시설/재고/작업지시 전체 관리, raw DB 직접 접근 불가
- `technician`: 재고 수불 처리, 작업지시 등록, 본인 생성 또는 본인 배정 작업지시 업데이트
- `viewer`: 조회 전용

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
- `docs/team_and_deployment_runbook.md`
