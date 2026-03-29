$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (!(Test-Path $python)) {
    throw "가상환경 Python을 찾지 못했습니다: $python"
}

$distDir = Join-Path $root "dist"
$buildDir = Join-Path $root "build"
$appName = "FacilityOpsServer"
$targetDir = Join-Path $distDir $appName

Write-Host "PyInstaller 설치 확인"
& $python -m pip install pyinstaller | Out-Host

Write-Host "기존 빌드 정리"
if (Test-Path $targetDir) {
    Remove-Item $targetDir -Recurse -Force
}
if (Test-Path $buildDir) {
    Remove-Item $buildDir -Recurse -Force
}

Write-Host "실행 파일 빌드"
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name $appName `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    --hidden-import fastapi `
    --hidden-import python_multipart `
    ops_launcher.py | Out-Host

$runtimeDataDir = Join-Path $targetDir "runtime_data"
$runtimeUploadsDir = Join-Path $runtimeDataDir "uploads"
$runtimeCertsDir = Join-Path $runtimeDataDir "certs"
New-Item -ItemType Directory -Force -Path $runtimeUploadsDir | Out-Null
New-Item -ItemType Directory -Force -Path $runtimeCertsDir | Out-Null

$dbSource = Join-Path $root "operations.db"
if (Test-Path $dbSource) {
    Write-Host "현재 운영 DB 복사"
    Copy-Item $dbSource (Join-Path $runtimeDataDir "operations.db") -Force
}

$uploadSource = Join-Path $root "uploads"
if (Test-Path $uploadSource) {
    Write-Host "현재 업로드 파일 복사"
    Copy-Item (Join-Path $uploadSource "*") $runtimeUploadsDir -Recurse -Force -ErrorAction SilentlyContinue
}

$certSource = Join-Path $root "runtime_data\\certs"
if (Test-Path $certSource) {
    Write-Host "HTTPS 인증서 복사"
    Copy-Item (Join-Path $certSource "*") $runtimeCertsDir -Recurse -Force -ErrorAction SilentlyContinue
}

$readmePath = Join-Path $targetDir "README_RUN.txt"
@"
시설 운영 시스템 실행 방법

1. FacilityOpsServer.exe 를 실행합니다.
2. runtime_data\certs\local-network-cert.pem 과 local-network-key.pem 이 있으면 HTTPS(기본 8443)로 실행됩니다.
3. 같은 PC에서는 실행 콘솔에 표시된 https://127.0.0.1:8443 또는 http://127.0.0.1:8000 으로 접속합니다.
4. 다른 PC나 스마트폰에서는 실행 콘솔에 표시된 주소로 접속합니다.
5. 스마트폰 PWA 설치용이면 rootCA.pem 을 먼저 기기에 설치해 신뢰 처리합니다.
6. Windows 방화벽 창이 뜨면 개인 네트워크 허용을 선택합니다.

기본 관리자
- 아이디: admin
- 비밀번호: admin1234

데이터 저장 위치
- runtime_data\operations.db
- runtime_data\uploads
"@ | Set-Content -Path $readmePath -Encoding UTF8

Write-Host ""
Write-Host "빌드 완료: $targetDir"
Write-Host "실행 파일: $(Join-Path $targetDir "$appName.exe")"
