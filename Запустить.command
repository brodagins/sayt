#!/bin/bash
# Двойной щелчок по этому файлу запускает программу «База клиентов».
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import sys" >/dev/null 2>&1; then
    echo "Python 3 не найден."
    echo "Откройте программу «Терминал», введите python3 и нажмите Enter —"
    echo "macOS предложит установить нужные инструменты. После установки"
    echo "запустите этот файл ещё раз."
    read -r -p "Нажмите Enter, чтобы закрыть окно..."
    exit 1
fi

python3 app.py
read -r -p "Программа завершила работу. Нажмите Enter, чтобы закрыть окно..."
