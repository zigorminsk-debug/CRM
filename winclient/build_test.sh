#!/usr/bin/env bash
# Консольный автотест клиентской логики (Linux/CI): проверяет валидацию формы,
# геокодирование адреса, отправку заявки и поиск в истории на работающем сервере.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist
g++ -std=c++17 -O1 -Isrc src/main_test.cpp src/api.cpp src/validate.cpp src/http_posix.cpp -o dist/crm_client_test
echo "Запуск автотеста против ${1:-http://127.0.0.1:8000}"
exec ./dist/crm_client_test "${1:-http://127.0.0.1:8000}"
