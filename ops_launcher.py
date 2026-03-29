from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _data_root(runtime_root: Path) -> Path:
    root = runtime_root / "runtime_data" if getattr(sys, "frozen", False) else runtime_root
    root.mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    return root


def _cert_root(runtime_root: Path) -> Path:
    root = runtime_root / "runtime_data" / "certs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _choose_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("사용 가능한 포트를 찾지 못했습니다. 8000-8019 포트를 확인해 주세요.")


def _local_ips() -> list[str]:
    ips: set[str] = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for result in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = result[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass

    return sorted(ips)


def _ssl_paths(cert_root: Path) -> tuple[Path | None, Path | None]:
    cert_path = Path(os.getenv("OPS_SSL_CERTFILE", "")).expanduser() if os.getenv("OPS_SSL_CERTFILE") else cert_root / "local-network-cert.pem"
    key_path = Path(os.getenv("OPS_SSL_KEYFILE", "")).expanduser() if os.getenv("OPS_SSL_KEYFILE") else cert_root / "local-network-key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    return None, None


def _open_browser_with_scheme(scheme: str, port: int) -> None:
    try:
        webbrowser.open(f"{scheme}://127.0.0.1:{port}/login")
    except Exception:
        pass


def main() -> None:
    runtime_root = _runtime_root()
    data_root = _data_root(runtime_root)
    cert_root = _cert_root(runtime_root)
    cert_path, key_path = _ssl_paths(cert_root)
    scheme = "https" if cert_path and key_path else "http"
    default_port = "8443" if scheme == "https" else "8000"
    preferred_port = int(str(os.getenv("PORT", default_port)).strip() or default_port)
    port = _choose_port(preferred_port)

    os.environ.setdefault("OPS_DB_PATH", str(data_root / "operations.db"))
    os.environ.setdefault("OPS_UPLOAD_DIR", str(data_root / "uploads"))
    os.environ.setdefault("OPS_COOKIE_SECURE", "true" if scheme == "https" else "false")
    os.chdir(runtime_root)

    print("시설 운영 시스템 서버를 시작합니다.")
    print(f"실행 위치: {runtime_root}")
    print(f"데이터 위치: {data_root}")
    if scheme == "https":
        print(f"HTTPS 인증서: {cert_path}")
        print(f"HTTPS 개인키: {key_path}")
    else:
        print("HTTPS 인증서가 없어 HTTP 모드로 실행합니다.")
    print("접속 주소:")
    for ip in _local_ips():
        print(f"  {scheme}://{ip}:{port}")
    print("같은 Wi-Fi 또는 같은 사내망의 PC/스마트폰에서 위 주소로 접속할 수 있습니다.")
    print("Windows 방화벽 경고가 뜨면 개인 네트워크 허용을 선택해 주세요.")

    threading.Timer(1.5, _open_browser_with_scheme, args=(scheme, port)).start()
    uvicorn.run(
        "ops_main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        ssl_certfile=str(cert_path) if cert_path else None,
        ssl_keyfile=str(key_path) if key_path else None,
    )


if __name__ == "__main__":
    main()
