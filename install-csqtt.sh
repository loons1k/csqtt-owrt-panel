#!/bin/sh
# ================================================================
#  CSQTT OpenWrt — auto-installer для OpenWrt.
#  Поддерживаются:
#    - OpenWrt 25.12.x и SNAPSHOT (apk-tools 3, пакеты .apk);
#    - OpenWrt 24.10.x (opkg, пакеты .ipk);
#    - архитектуры: aarch64_cortex-a53, aarch64_generic, x86_64,
#      arm_cortex-a7_neon-vfpv4 (см. ARCH_OK_LIST и скрипты сборки).
#
#  Формат публикации — ПРЯМЫЕ установочные пакеты (без внешних архивов), с
#  явным обозначением OpenWrt в имени. Прежние короткие теги (ow25/ow24/snapshot)
#  принимаются для уже опубликованных выпусков и их ссылок:
#    - OpenWrt 25.12.x: csqtt-<core>-openwrt-25.12-<arch>.apk
#    - OpenWrt 24.10.x: csqtt-<core>-openwrt-24.10-<arch>.ipk
#    - SNAPSHOT:        csqtt-<core>-openwrt-snapshot-<arch>.apk
#    - панель LuCI (архитектура all, общая для формата); русский интерфейс
#      встроен в панель (с v1.1.1), поэтому отдельный пакет перевода не требуется:
#        luci-app-csqtt-<pkg>-openwrt-25.12.apk / …-openwrt-24.10.ipk
#      Прежние выпуски могут дополнительно содержать (и он будет поставлен, если
#      присутствует в манифесте) отдельный пакет русификации:
#        luci-i18n-csqtt-ru-openwrt-25.12.apk / …-openwrt-24.10.ipk
#      Имена — ВНЕШНИЕ (ярлык релиза). ВНУТРЕННИЕ имя и версия пакета, а также
#      метаданные пакетного менеджера НЕ изменяются.
#      <core> — версия ядра; может содержать дефис (локальная редакция вида
#      `2.1.9-csqtt.4`), поэтому имя ядра разбирается по маркеру `-openwrt-`,
#      а не по первому дефису.
#    - установщик выбирает пакеты (ядро, панель, при наличии — отдельный перевод)
#      ПО МАНИФЕСТУ SHA256SUMS — имена не конструируются вручную.
#    - прежние форматы поддержаны для уже опубликованных релизов/ссылок: внешние
#      архивы наборов по архивному манифесту SHA256SUMS (csqtt-core-…tar.gz) и
#      плоский legacy-манифест самых ранних выпусков; промежуточный прямой
#      манифест SHA256SUMS.direct тоже читается.
#
#  Сетевые обращения (телеметрии нет, других хостов нет):
#    - api.github.com/repos/<owner>/<repo>/releases/latest — только чтобы
#      разрешить "latest" в тег, если CSQTT_VERSION не задан;
#    - github.com/<owner>/<repo>/releases/download/<tag>/SHA256SUMS и файлы;
#    - репозитории OpenWrt через менеджер пакетов ($PM update и установка
#      зависимостей: kmod-tun, luci-base, rpcd-mod-ucode, ucode-mod-socket,
#      coreutils-timeout).
#
#  Запуск:
#    sh install-csqtt.sh                 # последний выпуск (latest)
#    sh install-csqtt.sh --yes           # без интерактивного подтверждения
#    sh install-csqtt.sh --dry-run       # только план и проверки, без изменений
#    CSQTT_VERSION=v1.0 sh install-csqtt.sh            # конкретный выпуск
#    CSQTT_LOCAL_DIR=/path sh install-csqtt.sh            # из локального набора
#    CSQTT_CORE_ONLY=1 sh install-csqtt.sh                # только ядро (панель сохранить)
#    wget --no-proxy -qO- https://github.com/starkugz/csqtt-openwrt/raw/refs/heads/main/install-csqtt.sh | ash
#
#  Принципы:
#   - менеджер пакетов определяется по релизу прошивки: 25.12.x/SNAPSHOT —
#     apk (.apk), 24.10.x — opkg (.ipk); прочие релизы и архитектуры
#     отклоняются ДО изменений системы;
#   - набор выбирается по манифесту SHA256SUMS: прямые пакеты (ядро, панель,
#     перевод); для прежних выпусков — внешний архив или плоский манифест;
#   - контрольная сумма проверяется для архива и для каждого пакета (SHA-256);
#   - до установки выполняется сухой прогон всего набора (apk add --simulate /
#     opkg install --noaction): проверяются arch, зависимости и совместимость
#     kmod-tun с ядром;
#   - перед установкой показывается план (выпуск, версии ядра и пакета,
#     архитектура, пакеты, зависимости, что НЕ выполняется) и запрашивается
#     подтверждение (кроме --yes и неинтерактивного запуска); --dry-run
#     ничего не меняет;
#   - любая ошибка до установки (нет файла, суммы, платформа, менеджер,
#     совместимость, место, зависимости, резервная копия, параллельный запуск)
#     останавливает работу ДО изменений пакетов; массовое обновление прошивки
#     (apk upgrade / opkg upgrade) не запускается — только обновление индексов
#     (apk update / opkg update);
#   - понижение версий пакетов (осознанная перенумерация выпуска) выполняется
#     принудительно: opkg иначе печатает «Not downgrading …» и завершается
#     УСПЕХОМ, то есть молча ничего не меняет — поэтому при понижении к opkg
#     добавляется --force-downgrade. apk с явно указанным файлом понижает сам
#     (проверено на apk-tools 3.0.5), флаг ему не нужен;
#   - установщик НЕ удаляет пакеты при ошибке установки; если после установки
#     версия не изменилась (менеджер молча пропустил замену), сообщается об
#     ошибке, а не об успехе; удаление старой версии остаётся резервным путём и
#     возможно только при наличии проверенных пакетов для восстановления;
#   - конфигурация не заменяется на шаблон без резервной копии с правами 600.
# ================================================================
set -eu

REPO="${CSQTT_REPO:-loons1k/csqtt-openwrt}"
# Поддерживаемые архитектуры пакетов (каталоги сборки OpenWrt).
ARCH_OK_LIST="aarch64_cortex-a53 aarch64_generic x86_64 arm_cortex-a7_neon-vfpv4"
OW_APK="25.12"      # apk-tools 3, пакеты .apk
OW_IPK="24.10"      # opkg, пакеты .ipk
APK_TOOLS_MIN=3
REQUIRED_DEPS="kmod-tun luci-base rpcd-mod-ucode ucode-mod-socket coreutils-timeout"
NEED_KB_TMP=30000
NEED_KB_ROOT=10000
# Понижение версий пакетов: opkg без флага молча пропускает установку более
# старого пакета (печатает «Not downgrading …» и возвращает УСПЕХ), поэтому при
# понижении к opkg добавляется --force-downgrade. apk с явно указанным файлом
# понижает сам (проверено на apk-tools 3.0.5: «Downgrading …»), флаг не нужен.
# Значение задаётся ниже — после сравнения версий выпуска и установленных.
PM_FORCE_DOWNGRADE=""

DRY_RUN=0
ASSUME_YES=0
# [CORE-ONLY] Обновление только ядра: панель и перевод не скачиваются и не
# устанавливаются; конфигурация, секреты и device_id сохраняются. Совместимость
# с оставленной панелью проверяется до изменений (см. ниже).
CORE_ONLY="${CSQTT_CORE_ONLY:-0}"
# [CORE-ONLY] Разрешить несовпадающие версии ядра и панели (отключает проверку;
# только осознанно).
ALLOW_MIXED="${CSQTT_ALLOW_MIXED:-0}"
for arg in "$@"; do
	case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--yes|-y) ASSUME_YES=1 ;;
		--core-only) CORE_ONLY=1 ;;
		--help|-h)
			cat <<'HELP'
Использование: sh install-csqtt.sh [--yes] [--dry-run] [--core-only]
  --yes        не запрашивать подтверждение плана установки
  --dry-run    показать план и выполнить все проверки, ничего не устанавливая
  --core-only  обновить только ядро, сохранив панель LuCI и её встроенный перевод
Переменные окружения:
  CSQTT_VERSION   тег выпуска (например v1.0); по умолчанию — latest
  CSQTT_LOCAL_DIR каталог с манифестом SHA256SUMS и пакетами (вместо GitHub)
  CSQTT_ROOT      корень OpenWrt (для локальных тестов)
  CSQTT_ROLLBACK_DIR  каталог пакетов предыдущей версии (переход вниз)
  CSQTT_CORE_ONLY=1   то же, что --core-only (только ядро)
  CSQTT_ALLOW_MIXED=1 разрешить несовпадающие версии ядра и панели
HELP
			exit 0 ;;
		*) die "неизвестный аргумент: $arg (см. --help)" ;;
	esac
done

# --- абстракция менеджера пакетов ---------------------------------------------
# PM=apk|opkg определяется в check_platform() по релизу прошивки; где команда
# менеджера требует формата именно своего менеджера (apk --simulate /
# opkg --noaction), ветвление делается явно. Общие операции — через pm_*.
pm_update()      { "$PM" update "$@"; }

# --- переопределяемые пути (для локальных тестов; на роутере пусто) ----------
ROOT="${CSQTT_ROOT:-}"
OW_RELEASE="${ROOT}/etc/openwrt_release"
CONFIG="${ROOT}/etc/config/csqtt"
INITD="${ROOT}/etc/init.d/csqtt"
BIN="${ROOT}/usr/bin/csqtt"
RCD="${ROOT}/etc/rc.d"
TMPBASE="${CSQTT_TMPDIR:-${ROOT:+$ROOT/tmp}}"
[ -n "$TMPBASE" ] || TMPBASE=/tmp

VERSION="${CSQTT_VERSION:-}"
LOCAL_DIR="${CSQTT_LOCAL_DIR:-}"
ROLLBACK_DIR="${CSQTT_ROLLBACK_DIR:-}"
LOCK="$TMPBASE/csqtt-install.lock"
WORK="$TMPBASE/csqtt-install.$$"
SETDIR=""
BACKUP=""

log()  { printf '[+] %s\n' "$*"; }
info() { printf '[i] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

download() {
	# download <url> <out>  (не исполняет загруженные данные)
	if command -v uclient-fetch >/dev/null 2>&1; then
		uclient-fetch -q -O "$2" "$1"
	elif command -v wget >/dev/null 2>&1; then
		wget --no-proxy -q -O "$2" "$1" 2>/dev/null || wget -q -O "$2" "$1"
	elif command -v curl >/dev/null 2>&1; then
		curl -fsSL "$1" -o "$2"
	else
		return 1
	fi
}

# fetch <url-or-local> <out>
fetch() {
	if [ -n "$LOCAL_DIR" ]; then
		name=$(printf '%s' "$1" | sed 's#.*/##')
		[ -f "$LOCAL_DIR/$name" ] || return 1
		cp "$LOCAL_DIR/$name" "$2" || return 1
	else
		download "$1" "$2"
	fi
}

# --- разрешение latest в конкретный тег (один раз) ---------------------------
resolve_latest_tag() {
	json="$WORK/latest.json"
	if fetch "https://api.github.com/repos/$REPO/releases/latest" "$json"; then
		tag=$(sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' "$json" | head -n1)
		if [ -n "$tag" ]; then printf '%s' "$tag"; return 0; fi
	fi
	# запасной путь: редирект releases/latest -> /releases/tag/<tag>
	if command -v curl >/dev/null 2>&1; then
		url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null || true)
	else
		url=$(wget -q -S -O /dev/null "https://github.com/$REPO/releases/latest" 2>&1 | sed -n 's/.*[Ll]ocation: //p' | tail -n1 | tr -d '\r')
	fi
	tag="${url##*/}"
	case "$tag" in
		v[0-9]*) printf '%s' "$tag"; return 0 ;;
	esac
	return 1
}

# --- версии ----------------------------------------------------------------
# Полная установленная версия пакета <pkg> (как её видит менеджер), пусто — нет.
installed_version_of() {
	_pkg="$1"
	if [ "$PM" = apk ]; then
		apk list --installed 2>/dev/null | sed -n "s/^${_pkg}-\([0-9][^ ]*\) .*/\1/p" | head -n1
	else
		opkg list-installed 2>/dev/null | sed -n "s/^${_pkg} - \([0-9][^ ]*\).*/\1/p" | head -n1
	fi
}
installed_version() { installed_version_of csqtt; }

version_gt() {
	# 0, если $1 > $2 (сначала apk version -t, иначе числовой fallback)
	if [ "$PM" = apk ]; then
		res=$(apk version -t "$1" "$2" 2>/dev/null || true)
	else
		# opkg не имеет version -t: сравниваем через sort -V ниже.
		res=""
	fi
	if [ "$res" = ">" ]; then return 0; fi
	if [ "$res" = "<" ] || [ "$res" = "=" ]; then return 1; fi
	[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ] && [ "$1" != "$2" ]
}

# --- проверки до изменений --------------------------------------------------
check_platform() {
	[ -f "$OW_RELEASE" ] || die "не найден $OW_RELEASE — это OpenWrt?"
	# shellcheck disable=SC1090  # файл релиза OpenWrt, не пользовательские данные
	. "$OW_RELEASE"
	ARCH="${DISTRIB_ARCH:-}"
	OW_REL="${DISTRIB_RELEASE:-}"
	OW_REV="${DISTRIB_REVISION:-}"
	OW_TARGET="${DISTRIB_TARGET:-}"
	info "OpenWrt ${OW_REL:-?}${OW_REV:+ ($OW_REV)}, arch ${ARCH:-?}${OW_TARGET:+, target $OW_TARGET}"
	case " $ARCH_OK_LIST " in
		*" $ARCH "*) : ;;
		*) die "поддерживаются архитектуры:${ARCH_OK_LIST} (обнаружено: ${ARCH:-неизвестно})." ;;
	esac
	# Определяем менеджер пакетов по релизу:
	#   stable(apk)  — ровно 25.12.x (apk-tools 3, пакеты .apk);
	#   stable(opkg) — ровно 24.10.x (opkg, пакеты .ipk);
	#   snapshot     — rolling-сборка, apk-tools 3; конкретная ревизия не
	#                  «угадывается», а проверяется по факту: ядро, kmod-tun и
	#                  зависимости должны разрешаться из репозиториев устройства.
	OW_REL_LC=$(printf '%s' "$OW_REL" | tr '[:upper:]' '[:lower:]')
	case "$OW_REL_LC" in
		snapshot) PM="apk"; PKG_EXT="apk"; FAMILY="snapshot"; OW_SERIES="snapshot" ;;
		"$OW_APK".*) PM="apk"; PKG_EXT="apk"; FAMILY="stable"; OW_SERIES="$OW_APK" ;;
		"$OW_IPK".*) PM="opkg"; PKG_EXT="ipk"; FAMILY="stable"; OW_SERIES="$OW_IPK" ;;
		*) die "поддерживается OpenWrt ${OW_APK}.x (apk), ${OW_IPK}.x (opkg) или SNAPSHOT (apk) (обнаружено: ${OW_REL:-неизвестно})." ;;
	esac
	info "семейство прошивки: $FAMILY, менеджер пакетов: $PM ($PKG_EXT)."
	# Менеджер пакетов обязан присутствовать и быть нужной генерации.
	command -v "$PM" >/dev/null 2>&1 || die "нужен $PM (OpenWrt ${OW_IPK}.x — opkg, ${OW_APK}.x/SNAPSHOT — apk)."
	if [ "$PM" = apk ]; then
		# apk-tools 3 обязателен: пакеты имеют v3-формат (apk v3). Если версию
		# определить не удалось — не блокируем (команда apk уже найдена), но
		# заведомо старые (2.x/opkg) отсекаем.
		APK_V=$(apk --version 2>/dev/null | sed -n 's/.*apk-tools[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n1)
		case "$APK_V" in
			''|*[!0-9]*) : ;;
			*) [ "$APK_V" -ge "$APK_TOOLS_MIN" ] || die "нужен apk-tools ${APK_TOOLS_MIN}+ (обнаружено: apk-tools $APK_V)." ;;
		esac
	fi
}

check_space() {
	# check_space <path> <need_kb>
	avail=$(df -k "$1" 2>/dev/null | awk 'NR==2 {print $4}')
	[ -n "$avail" ] || return 0
	[ "$avail" -ge "$2" ] || die "мало места в $1: нужно ${2}KiB, свободно ${avail}KiB."
}

check_dependencies() {
	missing=""
	# shellcheck disable=SC2086  # REQUIRED_DEPS — список через пробел без спецсимволов
	for dep in $REQUIRED_DEPS; do
		dep_ok "$dep" || missing="$missing $dep"
	done
	if [ -z "$missing" ]; then
		info "обязательные зависимости на месте."
		return 0
	fi
	info "не хватает зависимостей:$missing — проверяю возможность установки..."
	# сухой прогон: разрешимость зависимостей без изменения установленных пакетов
	# shellcheck disable=SC2086
	if pm_dry_run $REQUIRED_DEPS >/dev/null 2>&1; then
		info "зависимости разрешаются (будут установлены вместе с пакетами)."
	else
		# На SNAPSHOT самая частая причина — kmod-tun для другого ядра:
		# модуль должен соответствовать именно running kernel.
		if ! pm_dry_run kmod-tun >/dev/null 2>&1; then
			die "не разрешается kmod-tun для ядра $(uname -r): модуль должен совпадать с ядром устройства. Обновите индексы ($PM update) или прошивку и повторите; ничего не удалено и не изменено."
		fi
		die "не хватает зависимостей:$missing, и $PM не может их разрешить. Установите их и повторите."
	fi
}

# dep_ok <pkg>: установлен ли пакет (0 — да, 1 — нет).
dep_ok() {
	if [ "$PM" = apk ]; then
		apk info -e "$1" >/dev/null 2>&1
	else
		opkg list-installed 2>/dev/null | grep -q "^$1 - "
	fi
}

# pm_dry_run <files|pkgs...>: сухой прогон установки (0 — разрешается).
pm_dry_run() {
	if [ "$PM" = apk ]; then
		# shellcheck disable=SC2086
		apk add --simulate --allow-untrusted "$@" >/dev/null 2>&1
	else
		# shellcheck disable=SC2086
		opkg install --noaction "$@" >/dev/null 2>&1
	fi
}

# --- резервная копия --------------------------------------------------------
backup_config() {
	[ -f "$CONFIG" ] || { info "конфигурация отсутствует (чистая установка)."; return 0; }
	BACKUP="${CONFIG}.bak.$(date +%Y%m%d-%H%M%S)"
	if cp -p "$CONFIG" "$BACKUP" 2>/dev/null; then
		chmod 600 "$BACKUP" 2>/dev/null || true
		info "резервная копия конфигурации: $BACKUP"
	else
		BACKUP=""
		die "не удалось создать резервную копию $CONFIG — обновление остановлено, пакеты не изменялись."
	fi
}

service_enabled() { [ -e "$RCD/S99csqtt" ]; }

# --- выбор строк "hash name" из файла SHA256SUMS -----------------------------
# pick_asset <файл> <regexp>: возвращает "hash name" первой подходящей строки.
pick_asset() {
	awk -v pat="$2" '
		{
			name = $2
			sub(/^\.\//, "", name)
			if (name ~ pat) { print $1, name; exit }
		}' "$1"
}

# --- выбор внешнего архива по манифесту выпуска ------------------------------
# Имя архива НЕ конструируется вручную, а выбирается из опубликованного
# SHA256SUMS по семейству прошивки, архитектуре и формату (по манифесту):
#   csqtt-core-<core>_pkg-<pkg>-r<rel>_openwrt-<серия>_<arch>-<fmt>.tar.gz
# Сегменты разделены подчёркиванием: csqtt-core-* / pkg-* / openwrt-* / <arch>-<fmt>.
# Если опубликовано несколько наборов серии — берётся старший (sort -V).
pick_archive() {
	awk -v arch="$ARCH" -v ext="$PKG_EXT" -v series="$OW_SERIES" '
		{
			name = $2
			sub(/^\.\//, "", name)
			if (name ~ "^csqtt-core-[^_ ]*_pkg-[^_ ]*_openwrt-" series "[._0-9]*_" arch "-" ext "[.]tar[.]gz$")
				print $1, name
		}' "$WORK/SHA256SUMS" | sort -k2 -V | tail -n1
}

# --- выбор прямых установочных пакетов (новый формат, SHA256SUMS) ============
# Короткие внешние имена: ядро несёт версию ядра и явный тег OpenWrt
# (openwrt-25.12 | openwrt-24.10 | openwrt-snapshot), панель — версию пакета и
# тег, перевод — тег. Имена берутся ИЗ МАНИФЕСТА (pick_asset), а не
# конструируются. Для уже опубликованных выпусков принимаются и прежние
# короткие теги (ow25 | ow24 | snapshot). Путь к манифесту передаётся
# параметром: единый SHA256SUMS или промежуточный SHA256SUMS.direct.
direct_core_tags() {
	# по одному тегу в строке: сначала новый, затем прежний (совместимость)
	if [ "$FAMILY" = snapshot ]; then
		printf '%s\n' openwrt-snapshot snapshot
	elif [ "$OW_SERIES" = "$OW_APK" ]; then
		printf '%s\n' openwrt-25.12 ow25
	else
		printf '%s\n' openwrt-24.10 ow24
	fi
}
direct_ui_tags() {
	# панель и перевод: общий .apk для 25.12 и SNAPSHOT, .ipk для 24.10
	if [ "$OW_SERIES" = "$OW_IPK" ]; then
		printf '%s\n' openwrt-24.10 ow24
	else
		printf '%s\n' openwrt-25.12 ow25
	fi
}
# Первое совпадение по тегам (новый формат, затем прежний) — из манифеста.
pick_direct_core() {
	for t in $(direct_core_tags); do
		r=$(pick_asset "$1" "^csqtt-[0-9][^ ]*-${t}-${ARCH}[.]${PKG_EXT}\$")
		if [ -n "$r" ]; then printf '%s' "$r"; return 0; fi
	done
	return 0
}
pick_direct_panel() {
	for t in $(direct_ui_tags); do
		r=$(pick_asset "$1" "^luci-app-csqtt-[0-9][^-]*-${t}[.]${PKG_EXT}\$")
		if [ -n "$r" ]; then printf '%s' "$r"; return 0; fi
	done
	return 0
}
pick_direct_i18n() {
	for t in $(direct_ui_tags); do
		r=$(pick_asset "$1" "^luci-i18n-csqtt-ru-${t}[.]${PKG_EXT}\$")
		if [ -n "$r" ]; then printf '%s' "$r"; return 0; fi
	done
	return 0
}

# --- legacy: выбор отдельных пакетов из плоского манифеста (старые выпуски) --
pick_core() {
	a=$(pick_asset "$WORK/SHA256SUMS" "^csqtt_[0-9][^/]*_${ARCH}-${FAMILY}[.]${PKG_EXT}\$")
	[ -n "$a" ] || a=$(pick_asset "$WORK/SHA256SUMS" "^csqtt_[0-9][^/]*_${ARCH}[.]${PKG_EXT}\$")
	printf '%s' "$a"
}

pick_panel() {
	a=$(pick_asset "$WORK/SHA256SUMS" "^luci-app-csqtt[^/]*_all-${FAMILY}[.]${PKG_EXT}\$")
	[ -n "$a" ] || a=$(pick_asset "$WORK/SHA256SUMS" "^luci-app-csqtt[^/]*_all[.]${PKG_EXT}\$")
	printf '%s' "$a"
}

pick_i18n() {
	a=$(pick_asset "$WORK/SHA256SUMS" "^luci-i18n-csqtt-ru[^/]*-${FAMILY}[.]${PKG_EXT}\$")
	[ -n "$a" ] || a=$(pick_asset "$WORK/SHA256SUMS" "^luci-i18n-csqtt-ru[^/]*[.]${PKG_EXT}\$")
	printf '%s' "$a"
}

# --- безопасная распаковка (без path traversal) ------------------------------
safe_extract() {
	# safe_extract <archive> <dest>
	bad=$(tar -tzf "$1" 2>/dev/null | grep -E '(^|/)\.\.(/|$)|^/|^[A-Za-z]:' | head -n1)
	[ -z "$bad" ] || die "архив содержит недопустимый путь: $bad"
	tar -xzf "$1" -C "$2"
}

# --- скачивание и проверка SHA-256 ------------------------------------------
download_and_verify() {
	# download_and_verify <name> <expected_sha>
	fetch "$BASE/$1" "$WORK/$1" || die "не удалось скачать $1 из выпуска $TAG."
	actual=$(sha256sum "$WORK/$1" | awk '{print $1}')
	[ "$actual" = "$2" ] || die "SHA256 $1 не совпала (получено $actual, ожидалось $2) — установка остановлена."
}

verify_local_sha() {
	# verify_local_sha <path> <expected_sha>
	got=$(sha256sum "$1" | awk '{print $1}')
	[ "$got" = "$2" ] || die "SHA256 $(basename "$1") не совпала (получено $got, ожидалось $2) — установка остановлена."
}

# --- проверка совместимости всего набора ДО установки -----------------------
# Сухой прогон `apk add --simulate` / `opkg install --noaction` по локальным
# файлам: менеджер проверяет arch, разрешает зависимости (включая kmod-tun для
# running kernel) и ничего не меняет в системе. Это и есть реальная проверка
# совместимости со SNAPSHOT конкретной ревизии — без «флага разрешения» и без
# установки модулей ядра от другой сборки.
verify_package_set() {
	# shellcheck disable=SC2086  # имена файлов без пробелов
	if [ "$PM" = apk ]; then
		# shellcheck disable=SC2046  # имена файлов без пробелов
		plan=$(apk add --simulate --allow-untrusted $(target_files) 2>&1) && {
			info "набор совместим: arch, зависимости и kmod-tun разрешаются."
			return 0
		}
	else
		# opkg install --noaction: локальные .ipk — сухой прогон без изменений.
		# opkg проверяет arch и зависимости (kmod-tun для running kernel)
		# так же, как и реальная установка.
		# shellcheck disable=SC2046,SC2086  # имена файлов без пробелов; флаг опционален
		if opkg install $PM_FORCE_DOWNGRADE --noaction $(target_files) >/dev/null 2>&1; then
			info "набор совместим: arch, зависимости и kmod-tun разрешаются."
			return 0
		fi
		# shellcheck disable=SC2046,SC2086  # имена файлов без пробелов; флаг опционален
		plan=$(opkg install $PM_FORCE_DOWNGRADE --noaction $(target_files) 2>&1 || true)
	fi
	if ! pm_dry_run kmod-tun >/dev/null 2>&1; then
		die "набор несовместим: kmod-tun не разрешается для ядра $(uname -r). На SNAPSHOT модуль должен соответствовать ядру и цели; обновите индексы ($PM update) или прошивку. $PM: $(printf '%s' "$plan" | tail -n1)"
	fi
	die "набор несовместим с этой системой (arch/зависимости). $PM: $(printf '%s' "$plan" | tail -n1)"
}

# --- установка --------------------------------------------------------------
# Файлы для установки: ядро + панель (русский словарь встроен в панель).
# Отдельный пакет русификации больше не требуется и ставится только если он
# присутствует в манифесте прежнего выпуска (legacy). CORE_ONLY=1 — только ядро
# (панель LuCI, встроенный словарь и, если стоял, отдельный перевод сохраняются).
target_files() {
	if [ "${CORE_ONLY:-0}" = 1 ]; then
		printf '%s\n' "$SETDIR/$CORE_NAME"
	elif [ -n "${I18N_NAME:-}" ]; then
		printf '%s %s %s\n' "$SETDIR/$CORE_NAME" "$SETDIR/$PANEL_NAME" "$SETDIR/$I18N_NAME"
	else
		printf '%s %s\n' "$SETDIR/$CORE_NAME" "$SETDIR/$PANEL_NAME"
	fi
}

install_packages() {
	# shellcheck disable=SC2046,SC2086  # имена файлов без пробелов; флаг опционален
	if [ "$PM" = apk ]; then
		# apk с явным файлом понижает сам, отдельного флага нет.
		apk add --allow-untrusted $(target_files)
	else
		opkg install $PM_FORCE_DOWNGRADE $(target_files)
	fi
}

# Пакеты для восстановления установленной версии: ищем в CSQTT_ROLLBACK_DIR
# или в $TMPBASE/csqtt-rollback. Без них переход (удаление) запрещён.
find_rollback() {
	dir="$ROLLBACK_DIR"
	[ -n "$dir" ] || dir="$TMPBASE/csqtt-rollback"
	[ -d "$dir" ] || return 1
	# Ядро и панель обязательны; отдельный перевод (legacy) — если он был.
	for f in csqtt luci-app-csqtt; do
		found=$(find "$dir" -maxdepth 1 -name "${f}_*.${PKG_EXT}" 2>/dev/null | head -n1)
		[ -n "$found" ] || return 1
	done
	printf '%s' "$dir"
}

restore_rollback() {
	dir="$1"
	info "восстановление предыдущей установки из $dir ..."
	# shellcheck disable=SC2046  # имена файлов без пробелов
	if [ "$PM" = apk ]; then
		apk add --allow-untrusted $(find "$dir" -maxdepth 1 -name '*.apk') \
			|| warn "восстановление пакетов не удалось — см. состояние ниже."
	else
		opkg install $(find "$dir" -maxdepth 1 -name '*.ipk') \
			|| warn "восстановление пакетов не удалось — см. состояние ниже."
	fi
	if [ -n "$BACKUP" ] && [ -f "$BACKUP" ] && [ ! -f "$CONFIG" ]; then
		cp -p "$BACKUP" "$CONFIG" 2>/dev/null || true
	fi
}

# --- план и подтверждение ---------------------------------------------------
show_plan() {
	cat <<EOF

[+] ПЛАН:
    Выпуск:      $TAG
    OpenWrt:     ${OW_REL:-?}${OW_REV:+ ($OW_REV)}
    Архитектура: ${ARCH:-?}${OW_TARGET:+, target $OW_TARGET}
    Менеджер:    $PM, формат .$PKG_EXT (семейство $FAMILY)
    Источник:    $SRC_DESC
    Режим:       ${MODE_DESC:-полный набор (ядро + панель)}${DOWNGRADE_LINE}
    Пакеты (имена из манифеста):
      $CORE_NAME   — ядро CSQTT ${CORE_VER:-?}, пакет ${PKG_VER:-?}
      $PANEL_NAME  — панель LuCI ${PANEL_VER:-?} (русский интерфейс встроен)
      ${I18N_NAME:+$I18N_NAME   — отдельный перевод ${I18N_VER:-?} (legacy, не требуется)}
    Зависимости из репозиториев OpenWrt:$REQUIRED_DEPS
    НЕ выполняется: массовое обновление (upgrade), перезагрузка, изменение
    маршрутов/WAN/DNS/firewall/NAT. Туннель поднимается только на интерфейсе
    csqtt0; трафик в туннель направляется пользовательским прокси.
EOF
	if [ "$DRY_RUN" = 1 ]; then
		echo "[i] dry-run: план и проверки выполнены, изменения не производились."
		exit 0
	fi
	if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
		printf '[?] Продолжить установку? [Y/n] '
		read -r ans || ans="y"
		case "$ans" in
			y|Y|yes|YES|"") : ;;
			*) die "установка отменена пользователем." ;;
		esac
	fi
}

# --- 1. окружение -----------------------------------------------------------
[ "$(id -u)" = 0 ] || die "нужны права root."
check_platform   # определяет PM/PKG_EXT/FAMILY/OW_SERIES и требует нужный менеджер
# Блокировка параллельной установки (атомарный mkdir; не требует flock).
if ! mkdir "$LOCK" 2>/dev/null; then
	die "уже выполняется установка ($LOCK). Дождитесь завершения и повторите."
fi
mkdir -p "$WORK"
trap 'rm -rf "$WORK" "$LOCK"' EXIT INT TERM

# --- 2. выбор выпуска (latest -> тег один раз) ------------------------------
if [ -n "$VERSION" ]; then
	TAG="$VERSION"
	case "$TAG" in v*) : ;; *) TAG="v$TAG" ;; esac
	info "выбран выпуск: $TAG"
elif [ -n "$LOCAL_DIR" ]; then
	TAG="local"
	info "локальный каталог: $LOCAL_DIR"
else
	info "определяю последний выпуск..."
	TAG=$(resolve_latest_tag) || die "не удалось определить последний выпуск (нет сети или API недоступен). Укажите CSQTT_VERSION=<тег>."
	info "последний выпуск: $TAG"
fi
case "$TAG" in
	*[!A-Za-z0-9._-]*) die "некорректное имя тега: $TAG" ;;
esac
BASE="https://github.com/$REPO/releases/download/$TAG"

# --- 3. манифест и выбор пакетов -------------------------------------------
# Единый манифест выпуска — SHA256SUMS. Приоритет:
#   1) прямой манифест (короткие имена трёх пакетов) — новый формат: ядро,
#      панель и перевод скачиваются и ставятся напрямую, без архива;
#   2) промежуточный прямой манифест SHA256SUMS.direct (уже опубликованные
#      выпуски, где он шёл рядом с архивным SHA256SUMS);
#   3) архивный SHA256SUMS — прежний формат наборов *.tar.gz;
#   4) плоский legacy-манифест самых ранних выпусков.
MANIFEST="$WORK/SHA256SUMS"
MANIFEST_INTERIM="$WORK/SHA256SUMS.direct"
DIRECT_FILE=""
SET_KIND=""
ARCHIVE=""
ARCH_NAME=""
SRC_DESC=""
PKG_VER=""
CORE_VER=""
PANEL_VER=""
I18N_VER=""

# Есть ли в манифесте строки прямых пакетов (csqtt-<ver>-<тег>-…).
manifest_is_direct() {
	grep -qE 'csqtt-[0-9]' "$1" 2>/dev/null
}

# 3a. Прямые пакеты по единому манифесту SHA256SUMS.
if fetch "$BASE/SHA256SUMS" "$MANIFEST" 2>/dev/null && [ -s "$MANIFEST" ]; then
	if manifest_is_direct "$MANIFEST"; then
		CORE=$(pick_direct_core "$MANIFEST")
		PANEL=$(pick_direct_panel "$MANIFEST")
		I18N=$(pick_direct_i18n "$MANIFEST")
		if [ -n "$CORE" ] && [ -n "$PANEL" ]; then
			SET_KIND="direct"
			DIRECT_FILE="$MANIFEST"
		else
			die "выпуск $TAG: в SHA256SUMS нет прямого набора (ядро, панель) для ${ARCH}/${PKG_EXT}. Проверьте архитектуру и выпуск OpenWrt."
		fi
	fi
fi

# 3a'. Промежуточный прямой манифест SHA256SUMS.direct (прежние выпуски).
if [ "$SET_KIND" != direct ] \
	&& fetch "$BASE/SHA256SUMS.direct" "$MANIFEST_INTERIM" 2>/dev/null && [ -s "$MANIFEST_INTERIM" ]; then
	CORE=$(pick_direct_core "$MANIFEST_INTERIM")
	PANEL=$(pick_direct_panel "$MANIFEST_INTERIM")
	I18N=$(pick_direct_i18n "$MANIFEST_INTERIM")
	if [ -n "$CORE" ] && [ -n "$PANEL" ]; then
		SET_KIND="direct"
		DIRECT_FILE="$MANIFEST_INTERIM"
	fi
fi

if [ "$SET_KIND" = "direct" ]; then
	CORE_NAME=${CORE#* }
	CORE_SHA=${CORE%% *}
	PANEL_NAME=${PANEL#* }
	PANEL_SHA=${PANEL%% *}
	I18N_NAME=${I18N#* }
	I18N_SHA=${I18N%% *}
	SETDIR="$WORK"
	SRC_DESC="прямые пакеты (манифест $(basename "$DIRECT_FILE"))"
	info "формат выпуска: прямые установочные пакеты."
	info "ядро:   $CORE_NAME"
	info "панель: $PANEL_NAME"
	if [ -n "$I18N_NAME" ]; then info "перевод (legacy): $I18N_NAME"; else info "перевод: встроен в панель"; fi
	# Полные версии — из MANIFEST.txt выпуска, если он опубликован (необязателен).
	if fetch "$BASE/MANIFEST.txt" "$WORK/MANIFEST.txt" 2>/dev/null && [ -s "$WORK/MANIFEST.txt" ]; then
		CORE_VER=$(sed -n 's/^core_version:[[:space:]]*//p' "$WORK/MANIFEST.txt" | head -n1)
		PV=$(sed -n 's/^pkg_version:[[:space:]]*//p' "$WORK/MANIFEST.txt" | head -n1)
		REL=$(sed -n 's/^pkg_release:[[:space:]]*//p' "$WORK/MANIFEST.txt" | head -n1)
		[ -n "$PV" ] && PKG_VER="$PV${REL:+ (release $REL)}"
		# Полная версия пакета (как у менеджера: <version>-r<release>) — для
		# сравнения с установленной, чтобы не считать ту же версию понижением.
		[ -n "$PV" ] && PKG_FULL="$PV${REL:+-r$REL}"
		PANEL_VER=$(sed -n 's/^luci_version:[[:space:]]*//p' "$WORK/MANIFEST.txt" | head -n1)
		I18N_VER=$(sed -n 's/^i18n_version:[[:space:]]*//p' "$WORK/MANIFEST.txt" | head -n1)
	fi
	# Версия ядра из имени файла: до разделителя -openwrt-<серия>-; версия может
	# содержать дефис (прежние выпуски с локальной редакцией: 2.1.9-csqtt.4),
	# поэтому режем по последнему маркеру -openwrt-, а не по первому дефису. Для
	# прежних коротких тегов (ow25/ow24/snapshot) маркера нет — запасной разбор
	# по первому дефису.
	if [ -z "$CORE_VER" ]; then
		CORE_VER=$(printf '%s' "$CORE_NAME" | sed -n 's/^csqtt-\(.*\)-openwrt-.*/\1/p')
		[ -n "$CORE_VER" ] || CORE_VER=$(printf '%s' "$CORE_NAME" | sed -n 's/^csqtt-\([^-]*\)-.*/\1/p')
	fi
	[ -n "$PANEL_VER" ] || PANEL_VER=$(printf '%s' "$PANEL_NAME" | sed -n 's/^luci-app-csqtt-\([^-]*\)-.*/\1/p')
	[ -n "$I18N_VER" ] || I18N_VER="(см. MANIFEST.txt выпуска)"
	if [ -z "$PKG_VER" ]; then PKG_VER="${PANEL_VER:-?}"; fi
	if [ "$CORE_ONLY" = 1 ]; then
		log "Загрузка и проверка ядра (режим «только ядро») ..."
		download_and_verify "$CORE_NAME" "$CORE_SHA"
		info "контрольная сумма ядра совпала."
	else
		log "Загрузка и проверка прямых пакетов ..."
		download_and_verify "$CORE_NAME" "$CORE_SHA"
		download_and_verify "$PANEL_NAME" "$PANEL_SHA"
		if [ -n "$I18N_NAME" ]; then
			download_and_verify "$I18N_NAME" "$I18N_SHA"
			info "контрольные суммы ядра, панели и перевода (legacy) совпали."
		else
			info "контрольные суммы ядра и панели совпали (русский словарь встроен в панель)."
		fi
	fi
else
	# 3b. Прежний формат: архивный или плоский манифест SHA256SUMS.
	warn "в выпуске нет полного прямого набора для ${ARCH} — перехожу к прежнему формату."
	log "Читаю SHA256SUMS выпуска $TAG ..."
	[ -s "$MANIFEST" ] || fetch "$BASE/SHA256SUMS" "$MANIFEST" \
		|| die "выпуск $TAG недоступен: нет ни прямых пакетов, ни манифеста SHA256SUMS — установка остановлена."
	ARCHIVE=$(pick_archive)
	if [ -n "$ARCHIVE" ]; then
		# Прежний формат v1.2.0: один внешний архив набора по манифесту.
		SET_KIND="archive"
		ARCH_NAME=${ARCHIVE#* }
		ARCH_SHA=${ARCHIVE%% *}
		SRC_DESC="внешний архив $ARCH_NAME"
		info "архив набора: $ARCH_NAME"
		fetch "$BASE/$ARCH_NAME" "$WORK/$ARCH_NAME" || die "не удалось скачать $ARCH_NAME из выпуска $TAG."
		verify_local_sha "$WORK/$ARCH_NAME" "$ARCH_SHA"
		info "контрольная сумма архива совпала."
		mkdir -p "$WORK/set"
		safe_extract "$WORK/$ARCH_NAME" "$WORK/set"
		SETDIR=$(find "$WORK/set" -mindepth 1 -maxdepth 2 -type f -name SHA256SUMS -printf '%h\n' | head -n1)
		[ -n "$SETDIR" ] || die "в архиве $ARCH_NAME нет SHA256SUMS набора."
		info "набор распакован: $SETDIR"

		CORE=$(pick_asset "$SETDIR/SHA256SUMS" "^csqtt_[0-9][^/]*_${ARCH}[.]${PKG_EXT}\$")
		PANEL=$(pick_asset "$SETDIR/SHA256SUMS" "^luci-app-csqtt[^/]*_all[.]${PKG_EXT}\$")
		I18N=$(pick_asset "$SETDIR/SHA256SUMS" "^luci-i18n-csqtt-ru[^/]*[.]${PKG_EXT}\$")
		[ -n "$CORE" ] || die "в наборе нет пакета csqtt_*_${ARCH}.${PKG_EXT}."
		[ -n "$PANEL" ] || die "в наборе нет пакета luci-app-csqtt_*_all.${PKG_EXT}."
		# Отдельный перевод — необязателен: словарь встроен в панель (с v1.1.1).

		CORE_NAME=${CORE#* }
		CORE_SHA=${CORE%% *}
		PANEL_NAME=${PANEL#* }
		PANEL_SHA=${PANEL%% *}
		I18N_NAME=${I18N#* }
		I18N_SHA=${I18N%% *}

		# версии для плана — из BUILD.txt набора (проверенные метаданные сборки)
		if [ -f "$SETDIR/BUILD.txt" ]; then
			CORE_VER=$(sed -n 's/^core_version:[[:space:]]*//p' "$SETDIR/BUILD.txt" | head -n1)
			PV=$(sed -n 's/^pkg_version:[[:space:]]*//p' "$SETDIR/BUILD.txt" | head -n1)
			REL=$(sed -n 's/^pkg_release:[[:space:]]*//p' "$SETDIR/BUILD.txt" | head -n1)
			[ -n "$PV" ] && PKG_VER="$PV${REL:+ (release $REL)}"
			[ -n "$PV" ] && PKG_FULL="$PV${REL:+-r$REL}"
			LV=$(sed -n 's/^luci_version:[[:space:]]*//p' "$SETDIR/BUILD.txt" | head -n1)
			[ -n "$LV" ] && PANEL_VER="$LV" || PANEL_VER="${PANEL_NAME#luci-app-csqtt_}"
			PANEL_VER=${PANEL_VER%_all.*}
			IV=$(sed -n 's/^i18n_version:[[:space:]]*//p' "$SETDIR/BUILD.txt" | head -n1)
			[ -n "$IV" ] && I18N_VER="$IV" || I18N_VER="(в имени пакета)"
		fi
	else
		# Legacy: плоский манифест со штатными именами пакетов (самые ранние выпуски).
		SET_KIND="legacy"
		SRC_DESC="пакеты напрямую (legacy-манифест)"
		info "в манифесте нет внешнего архива — выбираю пакеты напрямую (legacy-формат)."
		CORE=$(pick_core)
		PANEL=$(pick_panel)
		I18N=$(pick_i18n)
		[ -n "$CORE" ] || die "в выпуске $TAG нет пакета csqtt_*_${ARCH}.${PKG_EXT} (family=$FAMILY)."
		[ -n "$PANEL" ] || die "в выпуске $TAG нет пакета luci-app-csqtt_*_all."
		# Отдельный перевод — необязателен: словарь встроен в панель (с v1.1.1).

		CORE_NAME=${CORE#* }
		CORE_SHA=${CORE%% *}
		PANEL_NAME=${PANEL#* }
		PANEL_SHA=${PANEL%% *}
		I18N_NAME=${I18N#* }
		I18N_SHA=${I18N%% *}
		PKG_VER=$(printf '%s' "$CORE_NAME" | sed -n 's/^csqtt_\([0-9][^_]*\)_.*/\1/p')
		SETDIR="$WORK"

		log "Загрузка и проверка пакетов ..."
		download_and_verify "$CORE_NAME" "$CORE_SHA"
		download_and_verify "$PANEL_NAME" "$PANEL_SHA"
		if [ -n "$I18N_NAME" ]; then
			download_and_verify "$I18N_NAME" "$I18N_SHA"
			info "контрольные суммы ядра, панели и перевода (legacy) совпали."
		else
			info "контрольные суммы ядра и панели совпали (русский словарь встроен в панель)."
		fi
	fi
fi
if [ -n "${I18N_NAME:-}" ]; then
	info "пакеты выпуска: $CORE_NAME, $PANEL_NAME, $I18N_NAME (перевод отдельным пакетом, legacy)"
else
	info "пакеты выпуска: $CORE_NAME, $PANEL_NAME (русский встроен)"
fi

# --- 5. проверки до изменений -----------------------------------------------
# Сравнение версий — ДО сухого прогона: при понижении и прогон, и установка
# должны идти с одним и тем же набором флагов (иначе прогон проверяет не то,
# что будет выполнено на самом деле).
CUR_VER=$(installed_version || true)
if [ -n "$CUR_VER" ]; then info "установлено сейчас: $CUR_VER"; else info "CSQTT ещё не установлен."; fi
NEW_VER=$(printf '%s' "${PKG_FULL:-$PKG_VER}" | sed 's/ (release .*//')
[ -n "$NEW_VER" ] || NEW_VER="$CUR_VER"
DOWNGRADE=0
# Строка плана: пустая, если понижения нет; иначе начинается с перевода строки,
# чтобы в heredoc не появлялась лишняя пустая строка.
DOWNGRADE_LINE=""
if [ -n "$CUR_VER" ] && [ -n "$NEW_VER" ] && [ "$NEW_VER" != "$CUR_VER" ] && version_gt "$CUR_VER" "$NEW_VER"; then
	DOWNGRADE=1
	info "обнаружено понижение версии пакетов: $CUR_VER -> $NEW_VER (перенумерация выпуска)."
	if [ "$PM" = opkg ]; then
		PM_FORCE_DOWNGRADE="--force-downgrade"
		info "opkg: установка пойдёт с --force-downgrade (без него opkg печатает «Not downgrading …» и ничего не меняет)."
	fi
	DOWNGRADE_LINE="
    Внимание:    понижение версий пакетов $CUR_VER -> $NEW_VER: замена принудительная${PM_FORCE_DOWNGRADE:+ ($PM_FORCE_DOWNGRADE)}."
fi
log "Проверка свободного места ..."
check_space "$TMPBASE" "$NEED_KB_TMP"
check_space "${ROOT}/" "$NEED_KB_ROOT"
# Только индексы: apk upgrade / opkg upgrade (массовое обновление прошивки)
# НЕ вызываются.
pm_update >/dev/null 2>&1 || warn "$PM update не удался (нет сети?) — зависимости могут не установиться."
check_dependencies
log "Проверка совместимости набора ($PM --simulate/--noaction) ..."
verify_package_set
if [ "$DRY_RUN" != 1 ]; then
	log "Резервная копия конфигурации ..."
	backup_config
fi
ENABLED_BEFORE=0
if service_enabled; then ENABLED_BEFORE=1; fi

# [CORE-ONLY] Режим + проверка совместимости с оставленной панелью ДО изменений.
# Панель и ядро одного выпуска согласованы; при обновлении только ядра панель
# другой версии может не работать. Сравниваем базовые версии (без суффикса -rN).
if [ "$CORE_ONLY" = 1 ]; then
	MODE_DESC="только ядро (панель LuCI и встроенный перевод сохраняются)"
	inst_panel=$(installed_version_of luci-app-csqtt || true)
	rel_panel=$(printf '%s' "${PANEL_VER:-}" | sed 's/ (release .*//; s/-r[0-9][0-9]*$//')
	inst_panel_base=$(printf '%s' "$inst_panel" | sed 's/-r[0-9][0-9]*$//')
	if [ -n "$inst_panel_base" ] && [ -n "$rel_panel" ] && [ "$inst_panel_base" != "$rel_panel" ]; then
		if [ "$ALLOW_MIXED" = 1 ]; then
			warn "панель $inst_panel не совпадает с панелью выпуска ${PANEL_VER:-?} — продолжаю (CSQTT_ALLOW_MIXED=1)."
		else
			die "обновление только ядра остановлено: установленная панель $inst_panel не совпадает с панелью выпуска ${PANEL_VER:-?}. Ядро и панель выпускаются согласованно; несовместимая панель может не работать. Обновите панель и перевод вместе (запуск без CSQTT_CORE_ONLY) или повторите с CSQTT_ALLOW_MIXED=1, если уверены в совместимости. Пакеты не изменялись."
		fi
	fi
else
	MODE_DESC="полный набор (ядро + панель; русский интерфейс встроен)"
fi

# План + подтверждение (после всех проверок, до установки).
show_plan

# --- 6. установка (без удаления при ошибке) ---------------------------------
log "Установка пакетов ..."
if install_packages; then
	info "пакеты установлены."
else
	add_rc=$?
	# НИКАКОГО удаления по общим ошибкам (зависимости/место/повреждение).
	if [ "$DOWNGRADE" != 1 ]; then
		die "$PM установка не удалась (код $add_rc). Пакеты не изменялись. Проверьте место, зависимости и целостность пакетов."
	fi
	info "прямая замена не удалась при понижении версии."
	RBDIR=$(find_rollback || true)
	if [ -z "$RBDIR" ]; then
		die "для перехода $CUR_VER -> $NEW_VER нужны проверенные пакеты установленной версии. Поместите их в $TMPBASE/csqtt-rollback (или задайте CSQTT_ROLLBACK_DIR) и повторите. Пакеты не удалялись."
	fi
	log "Переход: удаление $CUR_VER и установка $NEW_VER (восстановление из $RBDIR) ..."
	if [ "$PM" = apk ]; then
		apk del luci-i18n-csqtt-ru luci-app-csqtt csqtt >/dev/null 2>&1 || true
	else
		opkg remove luci-i18n-csqtt-ru luci-app-csqtt csqtt >/dev/null 2>&1 || true
	fi
	if ! install_packages; then
		warn "установка нового выпуска не удалась — восстанавливаю предыдущую установку."
		restore_rollback "$RBDIR"
		warn "фактическое состояние: $(installed_version || echo 'пакеты отсутствуют')"
		[ -n "$BACKUP" ] && warn "резервная копия конфигурации: $BACKUP"
		die "переход не завершён; выполнено восстановление предыдущей установки."
	fi
fi
# Если менеджер удалил conffile при переходе — восстановить сохранённую копию.
if [ ! -f "$CONFIG" ] && [ -n "$BACKUP" ] && [ -f "$BACKUP" ]; then
	cp -p "$BACKUP" "$CONFIG" || true
	info "конфигурация восстановлена из $BACKUP"
fi

# --- 7. проверка результата -------------------------------------------------
# Понижение могло быть молча пропущено менеджером (opkg без --force-downgrade
# печатает «Not downgrading …» и возвращает 0). Успешный код возврата — не
# доказательство замены: сравниваем фактическую установленную версию.
if [ "$DOWNGRADE" = 1 ]; then
	AFTER_VER=$(installed_version || true)
	if [ "$AFTER_VER" = "$CUR_VER" ]; then
		die "понижение версии не выполнено: установлен прежний пакет $CUR_VER, ожидался $NEW_VER. Изменений нет, служба и конфигурация не тронуты${BACKUP:+, резервная копия: $BACKUP}."
	fi
	info "версия заменена: $CUR_VER -> ${AFTER_VER:-?}"
fi
missing=""
# shellcheck disable=SC2086  # REQUIRED_DEPS — список через пробел без спецсимволов
for dep in $REQUIRED_DEPS; do
	dep_ok "$dep" || missing="$missing $dep"
done
[ -z "$missing" ] || die "после установки не хватает зависимостей:$missing"
[ -x "$BIN" ] || die "после установки нет $BIN."
NEW_INSTALLED=$(installed_version || true)
info "установлено: ${NEW_INSTALLED:-csqtt}"
info "ядро: $("$BIN" version 2>/dev/null | head -n1 || echo '?')"
if [ "$ENABLED_BEFORE" = 1 ] && ! service_enabled; then
	if [ -x "$INITD" ]; then "$INITD" enable >/dev/null 2>&1 || true; fi
	info "автозапуск службы сохранён."
elif service_enabled; then
	info "автозапуск службы включён."
else
	info "автозапуск не включён (включите при необходимости)."
fi

# --- 8. дальнейшие шаги -----------------------------------------------------
cat <<EOF

[+] Готово: CSQTT OpenWrt установлен (пакет ${PKG_VER:-?} + панель LuCI с встроенным русским интерфейсом + ядро CSQTT ${CORE_VER:-?}).
    OpenWrt ${OW_REL}${OW_REV:+ ($OW_REV)}, семейство ${FAMILY}, arch ${ARCH}.
    Источник: $SRC_DESC

Дальше в LuCI (Службы → star-panel-csqtt):
  1. Профили → Импортировать ссылку: вставьте ссылку csqtt://… (без отметки «Активировать»).
  2. Откройте профиль (Изменить): Режим авторизации VK = Auto JS, Режим хешей VK = Auto JS.
  3. В поле VK JS token вставьте сам токен или полный OAuth redirect-URL.
  4. Сохраните и включите профиль.
  5. Включите службу: Настройки → CSQTT включён (или uci set csqtt.main.enabled='1'; uci commit csqtt).
  6. Проверьте соединение: csqtt status; csqtt doctor.
  7. Трафик: направьте прокси на csqtt0 (Mihomo/ssclash, interface-name: csqtt0)
     либо выполните явный тест: curl --interface csqtt0 https://1.1.1.1/cdn-cgi/trace.

ВАЖНО: interface-only — установка сама по себе НЕ направляет весь трафик в
туннель; нужен пользовательский прокси, привязанный к csqtt0.

Обновление/откат:
  - конфигурация сохраняется; копия для этого запуска: ${BACKUP:-нет}
  - переустановить конкретный выпуск: CSQTT_VERSION=<тег> sh install-csqtt.sh
  - резервная копия одного конфига НЕ является откатом пакетов.
EOF