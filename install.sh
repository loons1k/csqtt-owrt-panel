#!/bin/sh
# CSQTT-OWRT-Panel Automated Installer
# Supports OpenWrt 23.05, 24.10, 25.10.5, 25.12+
# Tested on Huasifei WH3000Pro NAND (MT7981) + Fibocom FM350-GL
set -e

REPO_NAME="csqtt-owrt-panel"
REPO_RAW="https://raw.githubusercontent.com/loons1k/csqtt-owrt-panel/main"

# Fallback to csqtt-openwrt if repository has not yet been renamed
if ! curl -sfI "${REPO_RAW}/install.sh" >/dev/null 2>&1; then
    REPO_RAW="https://raw.githubusercontent.com/loons1k/csqtt-openwrt/main"
fi

echo "=== Установка CSQTT-OWRT-Panel для OpenWrt ==="

# 1. Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "Ошибка: Запустите скрипт от имени root!" >&2
    exit 1
fi

# 2. Install dependencies
echo "[1/5] Проверка и установка зависимостей..."
opkg update >/dev/null 2>&1 || true
opkg install kmod-tun curl ca-bundle ucode ucode-mod-fs ucode-mod-uci gzip 2>/dev/null || true

# 3. Create directories
echo "[2/5] Создание каталогов..."
mkdir -p /www/luci-static/resources/view/csqtt
mkdir -p /usr/share/luci/menu.d
mkdir -p /usr/share/rpcd/acl.d
mkdir -p /usr/share/rpcd/ucode
mkdir -p /usr/libexec/csqtt
mkdir -p /etc/csqtt
mkdir -p /etc/config
mkdir -p /etc/init.d
mkdir -p /var/run/csqtt

# 4. Download / Install LuCI components
echo "[3/5] Установка компонентов панели csqtt-owrt-panel..."
LUCI_VIEWS="about.js captcha.js i18n.js i18n.ru.json logs.js profiles.js settings.js status.js tunnel.js"
for view in $LUCI_VIEWS; do
    echo "  -> view/csqtt/$view"
    curl -sSL "${REPO_RAW}/luci-app-csqtt/htdocs/luci-static/resources/view/csqtt/$view" -o "/www/luci-static/resources/view/csqtt/$view"
done

echo "  -> menu.d/luci-app-csqtt.json"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/usr/share/luci/menu.d/luci-app-csqtt.json" -o "/usr/share/luci/menu.d/luci-app-csqtt.json"

echo "  -> acl.d/luci-app-csqtt.json"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/usr/share/rpcd/acl.d/luci-app-csqtt.json" -o "/usr/share/rpcd/acl.d/luci-app-csqtt.json"

echo "  -> rpcd/ucode/csqtt"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/usr/share/rpcd/ucode/csqtt" -o "/usr/share/rpcd/ucode/csqtt"

echo "  -> libexec/csqtt/migrate-shared-settings.sh"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/usr/libexec/csqtt/migrate-shared-settings.sh" -o "/usr/libexec/csqtt/migrate-shared-settings.sh"
chmod +x /usr/libexec/csqtt/migrate-shared-settings.sh

echo "  -> init.d/csqtt"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/etc/init.d/csqtt" -o "/etc/init.d/csqtt"
chmod +x /etc/init.d/csqtt

echo "  -> csqtt-watchdog.sh"
curl -sSL "${REPO_RAW}/luci-app-csqtt/root/etc/csqtt/csqtt-watchdog.sh" -o "/etc/csqtt/csqtt-watchdog.sh"
chmod +x /etc/csqtt/csqtt-watchdog.sh

# Install default config if missing
if [ ! -f /etc/config/csqtt ]; then
    echo "  -> config/csqtt"
    curl -sSL "${REPO_RAW}/luci-app-csqtt/root/etc/config/csqtt" -o "/etc/config/csqtt"
fi

# 5. Install csqtt-core binary
echo "[4/5] Установка бинарного ядра csqtt-core..."
ARCH="$(uname -m)"
if [ "$ARCH" = "aarch64" ]; then
    echo "  -> Загрузка предсобранного csqtt-core для aarch64 (MT7981 / Cortex-A53)..."
    curl -sSL "${REPO_RAW}/bin/csqtt-core-aarch64.gz" | gzip -d > /usr/bin/csqtt-core
    chmod +x /usr/bin/csqtt-core
    echo "  -> csqtt-core успешно установлен в /usr/bin/csqtt-core"
elif [ -x /usr/bin/csqtt-core ]; then
    echo "  -> Обнаружен существующий /usr/bin/csqtt-core"
else
    echo "Предупреждение: для архитектуры $ARCH соберите бинарник вручную из каталога core/."
fi

# 6. Setup cron watchdog & finalize LuCI
echo "[5/5] Финализация и перезапуск служб..."
if ! crontab -l 2>/dev/null | grep -q 'csqtt-watchdog.sh'; then
    (crontab -l 2>/dev/null; echo "* * * * * /etc/csqtt/csqtt-watchdog.sh >/dev/null 2>&1") | crontab -
    echo "  -> Watchdog добавлен в crontab"
fi

/etc/init.d/rpcd reload 2>/dev/null || true
rm -rf /tmp/luci-indexcache /tmp/luci-modulecache
/etc/init.d/csqtt enable 2>/dev/null || true

echo ""
echo "=== Установка CSQTT-OWRT-Panel успешно завершена! ==="
echo "Откройте веб-интерфейс LuCI: раздел 'Службы' -> 'CSQTT'"
