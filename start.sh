#!/usr/bin/env bash
set -euo pipefail

: "${PORT:=10000}"

exec uvicorn ops_main:app --host 0.0.0.0 --port "$PORT"
