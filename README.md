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

로그인 후 즉시 비밀번호를 변경하는 것을 권장한다.

## 9인 기본 계정 생성

```powershell
python scripts/seed_team.py
```

## 문서

- `docs/operations_redesign_plan.md`
- `docs/team_and_deployment_runbook.md`
