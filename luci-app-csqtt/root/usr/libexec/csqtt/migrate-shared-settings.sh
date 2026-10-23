#!/bin/sh
# SPDX-FileCopyrightText: 2026 amurcanov
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
#
# migrate-shared-settings.sh — перенос ОБЩИХ настроек туннеля/VK из серверных
# профилей в единую секцию `config csqtt 'main'` (docs/panel-upgrade/
# SHARED_SETTINGS.md, DECISIONS D24/D25).
#
# Что делает:
#   * для каждого общего поля собирает значения всех профилей;
#   * если значение одинаково (или задано только в одном профиле) — переносит
#     его в `main` и удаляет из профилей;
#   * если значения различаются — фиксирует КОНФЛИКТ: показывает имена полей и
#     профилей (НЕ значения), не переносит поле, не очищает исходные данные;
#   * `device_id` при различии всегда «требует выбора» (авто-выбор запрещён, D25);
#   * ставит `schema_version '2'` только когда незакрытых конфликтов нет
#     (иначе остаётся legacy-режим чтения старой схемы);
#   * перед записью — резервная копия `<file>.bak.<UTC>` с правами 0600;
#   * по умолчанию dry-run; запись только по `--apply`;
#   * повторный запуск идемпотентен;
#   * секреты (vk, vk_js_token, device_id и любые значения) НЕ печатаются.
#
# Скрипт трогает только UCI-файл конфигурации; маршрутизацию, DNS, firewall,
# службу и сеть не меняет. На живом устройстве — только с отдельного разрешения.
#
# Использование:
#   sh scripts/migrate-shared-settings.sh                     # dry-run, /etc
#   sh scripts/migrate-shared-settings.sh --apply             # применить
#   sh scripts/migrate-shared-settings.sh --config-dir DIR --apply
#
# Коды выхода: 0 — успех/dry-run; 2 — ошибка аргументов/окружения;
#             3 — сбой резервного копирования/записи (выполнен откат).
set -u

CONFIG_DIR=/etc/config
TARGET=""
APPLY=0

# Порядок и состав общих полей (SHARED_SETTINGS.md §2/§3).
FIELDS="vk vk_js_token vk_hash_mode vk_auth_mode workers obfs turn_transport captcha_mode fingerprint client_ids device_id"

usage() {
	cat <<'USAGE'
migrate-shared-settings.sh — общие настройки туннеля/VK из профилей в main.

  -n, --dry-run        показать план, ничего не менять (по умолчанию)
  -a, --apply          применить изменения (резервная копия + правка конфига)
      --config-dir DIR каталог UCI-файлов (по умолчанию /etc/config)
      --target FILE    целевой конфиг (по умолчанию DIR/csqtt)
  -h, --help           эта справка
USAGE
}

while [ "$#" -gt 0 ]; do
	case "$1" in
		-h|--help) usage; exit 0 ;;
		-a|--apply) APPLY=1 ;;
		-n|--dry-run) APPLY=0 ;;
		--config-dir) shift; [ "$#" -gt 0 ] || { echo "ERROR: --config-dir без значения" >&2; exit 2; }; CONFIG_DIR=$1 ;;
		--target) shift; [ "$#" -gt 0 ] || { echo "ERROR: --target без значения" >&2; exit 2; }; TARGET=$1 ;;
		*) echo "ERROR: неизвестный аргумент: $1" >&2; usage >&2; exit 2 ;;
	esac
	shift
done

[ -n "$TARGET" ] || TARGET="$CONFIG_DIR/csqtt"
[ -f "$TARGET" ] || { echo "ERROR: нет целевого конфига $TARGET" >&2; exit 2; }
command -v awk >/dev/null 2>&1 || { echo "ERROR: нужен awk" >&2; exit 2; }

WORK=$(mktemp -d) || { echo "ERROR: не удалось создать временный каталог" >&2; exit 2; }
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

# --- 1. Сбор значений общих полей -------------------------------------------
COLLECT="$WORK/collect.awk"
cat >"$COLLECT" <<'AWK'
BEGIN {
	n = split(fields, F, " ")
	for (i = 1; i <= n; i++) want[F[i]] = 1
	inmain = 0; inserver = 0
}
/^[[:space:]]*config[[:space:]]/ {
	inmain = ($0 ~ /^[[:space:]]*config[[:space:]]+csqtt[[:space:]]+['"]main['"]/)
	inserver = ($0 ~ /^[[:space:]]*config[[:space:]]+server([[:space:]]|$)/)
	secid = ""
	if (inserver) {
		s = $0
		sub(/^[[:space:]]*config[[:space:]]+server[[:space:]]*/, "", s)
		if (s ~ /^'/) { sub(/^'/, "", s); sub(/'.*$/, "", s) }
		else if (s ~ /^"/) { sub(/^"/, "", s); sub(/".*$/, "", s) }
		else { sub(/[[:space:]].*$/, "", s) }
		secid = s
	}
	next
}
/^[[:space:]]*option[[:space:]]+/ {
	line = $0
	sub(/^[[:space:]]*option[[:space:]]+/, "", line)
	f = line; sub(/[[:space:]].*$/, "", f)
	if (!(f in want)) next
	if (inmain) { print "M\t" f; next }
	if (!inserver) next
	v = $0
	sub(/^[[:space:]]*option[[:space:]]+[^[:space:]]+[[:space:]]+/, "", v)
	sub(/[[:space:]]+$/, "", v)
	if (v != "") print "V\t" f "\t" v "\t" secid
}
AWK

COLLECTED="$WORK/collected"
awk -v fields="$FIELDS" -f "$COLLECT" "$TARGET" >"$COLLECTED"

# Поля, уже присутствующие в main (уже перенесены — пропускаем).
present=$(awk -F'\t' '$1 == "M" { print $2 }' "$COLLECTED" | sort -u | tr '\n' ' ')

RESOLVED="$WORK/resolved"
: >"$RESOLVED"
conflicts=""
moves=0

echo "Миграция общих настроек: $TARGET"
for f in $FIELDS; do
	case " $present " in *" $f "*) continue ;; esac
	vals=$(awk -F'\t' -v f="$f" '$1 == "V" && $2 == f { print $3 }' "$COLLECTED" | sort -u)
	n=$(printf '%s\n' "$vals" | grep -c .)
	if [ "$n" -eq 0 ]; then
		continue
	fi
	if [ "$n" -eq 1 ]; then
		printf '%s\t%s\n' "$f" "$vals" >>"$RESOLVED"
		moves=$((moves + 1))
		echo "  - $f: переносится в main"
	else
		names=$(awk -F'\t' -v f="$f" '$1 == "V" && $2 == f { print $4 }' "$COLLECTED" | sort -u | tr '\n' ' ')
		[ "$f" = "device_id" ] && note=" (device_id: требуется выбор пользователя, D25)" || note=""
		echo "  - КОНФЛИКТ $f: профили ${names% } — значения не переносятся$note"
		conflicts="$conflicts $f"
	fi
done

if [ "$moves" -eq 0 ] && [ -z "$conflicts" ]; then
	echo "  (общие поля уже в main или их нет — миграция не требуется)"
fi
if [ -n "$conflicts" ]; then
	echo "незакрытые конфликты:${conflicts}. Разрешите их в панели и повторите."
fi

if [ "$APPLY" -eq 0 ]; then
	echo "[dry-run] файл не изменён. Применить: --apply"
	exit 0
fi

# --- 2. Проверка и подготовка новой версии ----------------------------------
add_schema=1
[ -n "$conflicts" ] && add_schema=0

REWRITE="$WORK/rewrite.awk"
cat >"$REWRITE" <<'AWK'
function key_of(line,   k) {
	k = line
	sub(/^[[:space:]]*option[[:space:]]+/, "", k)
	sub(/[[:space:]].*$/, "", k)
	return k
}
function flush_main(   i, k) {
	if (main_done) return
	for (i = 1; i <= nf; i++) {
		k = ord[i]
		if ((k in resolved) && !(k in present)) print "\toption " k " " resolved[k]
	}
	if (add_schema == 1) print "\toption schema_version '2'"
	main_done = 1
}
BEGIN {
	nf = split(order, ord, " ")
	n = split(present_list, P, " ")
	for (i = 1; i <= n; i++) if (P[i] != "") present[P[i]] = 1
	while ((getline line < resfile) > 0) {
		k = line; sub(/\t.*$/, "", k)
		v = line; sub(/^[^\t]*\t/, "", v)
		if (k != "") resolved[k] = v
	}
	close(resfile)
	main_seen = 0; inmain = 0; inserver = 0; main_done = 0
}
/^[[:space:]]*config[[:space:]]/ {
	if (inmain) flush_main()
	inmain = ($0 ~ /^[[:space:]]*config[[:space:]]+csqtt[[:space:]]+['"]main['"]/)
	inserver = ($0 ~ /^[[:space:]]*config[[:space:]]+server([[:space:]]|$)/)
	if (inmain) { main_seen = 1; main_done = 0 }
	print
	next
}
/^[[:space:]]*#/ {
	if (inmain) flush_main()
	inmain = 0; inserver = 0
	print
	next
}
{
	if (inmain && /^[[:space:]]*option[[:space:]]+/) {
		k = key_of($0)
		if (add_schema == 1 && k == "schema_version") next
		present[k] = 1
	}
	if (inserver && /^[[:space:]]*option[[:space:]]+/) {
		k = key_of($0)
		if (k in resolved) next
	}
	print
}
END {
	if (inmain) flush_main()
	if (!main_seen) {
		print ""
		print "config csqtt 'main'"
		for (i = 1; i <= nf; i++) if (ord[i] in resolved) print "\toption " ord[i] " " resolved[ord[i]]
		if (add_schema == 1) print "\toption schema_version '2'"
	}
}
AWK

NEW="$WORK/new.uci"
awk -v resfile="$RESOLVED" -v present_list="$present" -v add_schema="$add_schema" \
	-v order="$FIELDS" -f "$REWRITE" "$TARGET" >"$NEW" || { echo "ERROR: не удалось построить новый конфиг" >&2; exit 3; }

# Проверка результата ДО применения: ровно одна секция main, файл не пуст.
main_count=$(grep -c "^[[:space:]]*config[[:space:]]*csqtt[[:space:]]*['\"]main['\"]" "$NEW" 2>/dev/null || true)
if [ -s "$NEW" ] && [ "$main_count" -eq 1 ]; then
	:
else
	echo "ERROR: проверка результата не пройдена (main=$main_count) — изменения отменены" >&2
	exit 3
fi

# --- 3. Резервная копия и запись --------------------------------------------
ts=$(date -u +%Y%m%dT%H%M%SZ)
bak="$TARGET.bak.$ts"
cp -p "$TARGET" "$bak" || { echo "ERROR: не удалось создать $bak" >&2; exit 3; }
chmod 600 "$bak" 2>/dev/null || true

if cp "$NEW" "$TARGET"; then
	echo "записано: общих полей перенесено: $moves; schema_version=$add_schema"
	echo "резервная копия: $bak"
	if [ -n "$conflicts" ]; then
		echo "незакрытые конфликты:${conflicts} — legacy-режим сохранён, данные профилей не тронуты"
	else
		echo "включите нужный профиль в панели и выполните reload службы csqtt"
	fi
else
	cp -p "$bak" "$TARGET" && echo "ERROR: запись не удалась, конфиг восстановлен из $bak" >&2
	exit 3
fi
