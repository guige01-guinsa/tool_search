# 시설 운영 시스템

기존 `tool_search`를 대체하는 운영용 웹 시스템이다.

## 포함 기능

- 시설 관리
- 재고 관리
- 재고 수불 이력
- 작업지시 및 진행 업데이트
- 운영 보고서
- 사용자/권한 관리

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
