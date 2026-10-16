#!/bin/sh
# =============================================================================
#  CSQTT Server — загрузчик серверного установщика.
#
#  Это НЕ установщик и НЕ часть серверной части: файл только доставляет
#  опубликованный установщик на целевой сервер.
#
#  Что делает:
#    1. проверяет, есть ли чем скачивать (curl или wget) и есть ли python3;
#    2. скачивает три файла установщика во временный каталог, созданный им самим:
#         install-server.sh, server/serverctl.py, server/source-lock.json
#    3. проверяет, что каждый файл скачался и не пуст, а сценарий похож на сценарий;
#    4. запускает существующий install-server.sh, передавая ему все аргументы
#       без изменений (setup и uninstall — режимы самого установщика);
#    5. удаляет **только созданный им** временный каталог.
#
#  Код возврата установщика возвращается загрузчиком без изменений.
#  Серверная часть, её код и работающий VPS загрузчиком не изменяются.
#
#  Запуск на целевом VPS (Ubuntu/Debian x86-64, root):
#    sh install-server-online.sh --work-dir /root/csqtt-server-work/setup
#    sh install-server-online.sh uninstall --work-dir /root/csqtt-server-work/setup
#    sh install-server-online.sh --help        # справка самого установщика
#
#  Переменные окружения:
#    CSQTT_RAW_BASE — откуда берутся файлы установщика (по умолчанию ветка main
#                     репозитория starkugz/csqtt-openwrt); задаётся для зеркал
#                     и проверок.
# =============================================================================
set -u

REPO_RAW=${CSQTT_RAW_BASE:-https://raw.githubusercontent.com/starkugz/csqtt-openwrt/main}
INSTALLER=install-server.sh
INSTALLER_FILES='install-server.sh server/serverctl.py server/source-lock.json'
TMP_BASE=${TMPDIR:-/tmp}
WORK=

msg() { printf 'CSQTT: %s\n' "$*" >&2; }
die() { msg "$*"; exit 1; }

usage() {
	cat >&2 <<EOF
CSQTT: загрузчик серверного установщика.

  sh $0 --work-dir /root/csqtt-server-work/setup
  sh $0 uninstall --work-dir /root/csqtt-server-work/setup
  sh $0 --help

Все аргументы передаются серверному установщику $INSTALLER.
EOF
}

# --- проверки инструментов: до создания временного каталога -------------------
if [ "$#" -eq 0 ]; then
	usage
	exit 2
fi
DOWNLOADER=
if command -v curl >/dev/null 2>&1; then
	DOWNLOADER=curl
elif command -v wget >/dev/null 2>&1; then
	DOWNLOADER=wget
else
	die "нужен curl или wget, чтобы скачать установщик (например: apt-get install -y curl)."
fi
command -v python3 >/dev/null 2>&1 \
	|| die "нужен python3: его запускает установщик (например: apt-get install -y python3)."

download() { # download <адрес> <файл>
	if [ "$DOWNLOADER" = curl ]; then
		curl -fsSL --retry 2 --connect-timeout 20 -o "$2" "$1"
	else
		wget --no-proxy -q -O "$2" "$1"
	fi
}

# --- временный каталог: создаёт и удаляет только этот скрипт -------------------
cleanup() {
	[ -n "$WORK" ] || return 0
	case "$WORK" in
		"$TMP_BASE"/csqtt-server-online.*) rm -rf -- "$WORK" ;;
	esac
	WORK=
	return 0
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

WORK=$(mktemp -d "$TMP_BASE/csqtt-server-online.XXXXXX") \
	|| die "не удалось создать временный каталог в $TMP_BASE."
[ -d "$WORK" ] || die "не удалось создать временный каталог в $TMP_BASE."

msg "скачиваю установщик серверной части…"
for file in $INSTALLER_FILES; do
	subdir=${file%/*}
	if [ "$subdir" != "$file" ]; then
		mkdir -p "$WORK/$subdir" || die "не удалось создать каталог для $file."
	fi
	download "$REPO_RAW/$file" "$WORK/$file" \
		|| die "не удалось скачать $file из $REPO_RAW — проверьте сеть и адрес."
	[ -s "$WORK/$file" ] || die "скачанный файл $file пуст."
done

# загрузка могла «удаться», вернув страницу ошибки: сценарий должен быть сценарием
first_line=$(head -n 1 "$WORK/$INSTALLER" 2>/dev/null || :)
case "$first_line" in
	'#!'*) ;;
	*) die "скачанный $INSTALLER не похож на сценарий — возможно, вместо файла получена страница ошибки." ;;
esac

# --- запуск установщика: аргументы и код возврата — без изменений --------------
sh "$WORK/$INSTALLER" "$@"
rc=$?
cleanup
exit "$rc"
