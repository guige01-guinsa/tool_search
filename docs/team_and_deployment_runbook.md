# 9인 운영 계정 및 배포 런북

## 권장 9인 계정 구성

실제 이름으로 바꾸기 전 기본 구조는 아래처럼 잡는 편이 안정적이다.

| 아이디 | 역할 | 권장 사용자 |
| --- | --- | --- |
| `admin` | 관리자 | 시스템 총괄 1명 |
| `ops_lead` | 운영관리 | 관리소장 또는 총괄 책임자 |
| `facility_mgr` | 운영관리 | 시설 파트 리더 |
| `electric_1` | 작업자 | 전기 담당 1 |
| `electric_2` | 작업자 | 전기 담당 2 |
| `mechanical_1` | 작업자 | 기계 담당 1 |
| `mechanical_2` | 작업자 | 기계 담당 2 |
| `fire_safety` | 작업자 | 소방 담당 |
| `viewer_audit` | 조회전용 | 회계/감사/보고용 계정 |

## 역할 사용 원칙

### 관리자

- 사용자 생성/비활성화
- 모든 화면 접근
- 최종 운영 정책 변경

### 운영관리

- 시설, 재고, 작업지시 생성/수정/삭제
- 보고서 검토
- 현장 운영 조정

### 작업자

- 재고 수불 처리
- 작업지시 등록 및 진행 업데이트
- 보고서 조회

### 조회전용

- 대시보드, 시설, 재고, 작업지시, 보고서 조회만 허용

## 초기 계정 자동 생성

아래 스크립트는 권장 9개 계정을 한 번에 만든다.

```powershell
python scripts/seed_team.py
```

초기 임시 비밀번호는 스크립트에 정의된 `ChangeMe!2026` 이다.

운영 전환 직후 해야 할 일:

1. `admin` 계정으로 로그인한다.
2. 모든 계정 비밀번호를 즉시 변경한다.
3. 실사용자 이름으로 `full_name`을 수정한다.
4. 퇴사/휴직 인원은 삭제보다 `비활성화`를 사용한다.

## Windows 배포

### 1. 패키지 설치

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 앱 실행

```powershell
$env:PORT=8000
uvicorn ops_main:app --host 0.0.0.0 --port $env:PORT
```

같은 네트워크 사용자들은 `http://서버IP:8000` 으로 접속한다.

## Linux 서버 배포

### 1. 패키지 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 실행

```bash
PORT=8000 ./start.sh
```

## 데이터 파일

운영 시스템의 실제 저장 대상은 다음 두 가지다.

- `operations.db`
- `uploads/`

둘 중 하나라도 빠지면 운영 데이터가 완전하지 않다.

## 백업 정책

최소 권장 주기:

1. 매일 1회 `operations.db` 백업
2. 매일 1회 `uploads/` 백업
3. 주 1회 외부 저장소 복제

## 복구 절차

1. 앱 중지
2. `operations.db` 복원
3. `uploads/` 복원
4. 앱 재시작
5. `admin` 로그인 후 대시보드/재고/작업지시 확인

## 레거시 DB 재이관이 필요한 경우

기존 `tool_search` 백업 DB가 외부에 남아 있다면 첫 실행 전에만 환경변수로 지정한다.

```powershell
$env:LEGACY_DB_PATH='D:\backup\tools.db'
uvicorn ops_main:app --host 0.0.0.0 --port 8000
```

이미 `operations.db`가 생성된 뒤에는 중복 이관 방지를 위해 자동 재이관하지 않는다.
