#!/bin/sh
# CSQTT Server — установка и удаление серверной части без Android (лаунчер основного репозитория).
# Установка: выполняет автосценарий setup из server/serverctl.py на целевом Ubuntu/Debian x86-64.
# Удаление:  ./install-server.sh uninstall … — останавливает и удаляет туннель и веб-панель CSQTT.
# Этот скрипт НЕ является установщиком для OpenWrt и не изменяет сам роутер.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "${1:-}" in
  uninstall|remove|--uninstall|--remove)
    shift
    exec python3 "$SCRIPT_DIR/server/serverctl.py" uninstall "$@"
    ;;
  *)
    exec python3 "$SCRIPT_DIR/server/serverctl.py" setup "$@"
    ;;
esac
