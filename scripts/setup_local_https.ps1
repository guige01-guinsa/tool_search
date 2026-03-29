$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $root "runtime_data\certs"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null

function Resolve-MkcertPath {
    $cmd = Get-Command mkcert -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidate = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter mkcert*.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if ($candidate) {
        return $candidate
    }

    throw "mkcert 실행 파일을 찾지 못했습니다."
}

$mkcert = Resolve-MkcertPath

Write-Host "mkcert 루트 인증서를 설치합니다."
& $mkcert -install | Out-Host

$hosts = New-Object System.Collections.Generic.List[string]
foreach ($fixed in @("localhost", "127.0.0.1", "::1", $env:COMPUTERNAME)) {
    if ($fixed -and -not $hosts.Contains($fixed)) {
        $hosts.Add($fixed)
    }
}

$ipList = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -and
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.SkipAsSource -ne $true
    } |
    Select-Object -ExpandProperty IPAddress -Unique

foreach ($ip in $ipList) {
    if (-not $hosts.Contains($ip)) {
        $hosts.Add($ip)
    }
}

$certFile = Join-Path $certDir "local-network-cert.pem"
$keyFile = Join-Path $certDir "local-network-key.pem"

Write-Host "HTTPS 인증서를 생성합니다."
Write-Host ("대상 호스트: " + ($hosts -join ", "))
& $mkcert -cert-file $certFile -key-file $keyFile @hosts | Out-Host

$caRoot = (& $mkcert -CAROOT).Trim()
$rootCA = Join-Path $caRoot "rootCA.pem"
$copiedRootCA = Join-Path $certDir "rootCA.pem"
$copiedRootCACrt = Join-Path $certDir "rootCA.crt"
if (Test-Path $rootCA) {
    Copy-Item $rootCA $copiedRootCA -Force
    Copy-Item $rootCA $copiedRootCACrt -Force
}

Write-Host ""
Write-Host "완료:"
Write-Host "  인증서: $certFile"
Write-Host "  개인키: $keyFile"
if (Test-Path $copiedRootCA) {
    Write-Host "  루트 CA: $copiedRootCA"
    Write-Host "  루트 CA(crt): $copiedRootCACrt"
}
Write-Host ""
Write-Host "스마트폰에서 PWA 설치형으로 쓰려면:"
Write-Host "1. rootCA.crt 또는 rootCA.pem 을 스마트폰에 복사합니다."
Write-Host "2. Android는 인증서 설치, iPhone/iPad는 프로파일 설치 후 '전체 신뢰'를 켭니다."
Write-Host "3. 그 다음 https://<이 PC의 IP>:8443 으로 접속합니다."
