#!/bin/sh
DIR=$(dirname "$0")
CONF="$DIR/csqtt.conf"
[ -f "$CONF" ] || exit 0
. "$CONF"
umask 077

LOG="/var/log/csqtt-watchdog.log"
MAX_SIZE_KB=512
TUN_IFACE="${TUN_IFACE:-}"
INIT_CMD="/etc/init.d/csqtt"
PING_TARGET="10.66.67.1"

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

# 1. LED State Synchronizer with Clash
FALLBACK_NOW=$(curl -s 'http://127.0.0.1:9090/proxies/%E2%9A%A1%EF%B8%8F%20%D0%90%D0%B2%D1%82%D0%BE%20(Fallback)' 2>/dev/null | grep -o '"now":"[^"]*"' | cut -d'"' -f4)
VPN_NOW=$(curl -s 'http://127.0.0.1:9090/proxies/%F0%9F%9B%A1%EF%B8%8F%20VPN' 2>/dev/null | grep -o '"now":"[^"]*"' | cut -d'"' -f4)

if [ "$VPN_NOW" = "🛡️ CSQTT (VK Video Call Tunnel)" ] || [ "$FALLBACK_NOW" = "🛡️ CSQTT (VK Video Call Tunnel)" ]; then
    echo none > /sys/class/leds/blue:status/trigger 2>/dev/null
    echo 0 > /sys/class/leds/blue:status/brightness 2>/dev/null
    echo default-on > /sys/class/leds/red:status/trigger 2>/dev/null
    echo 1 > /sys/class/leds/red:status/brightness 2>/dev/null
else
    echo none > /sys/class/leds/red:status/trigger 2>/dev/null
    echo 0 > /sys/class/leds/red:status/brightness 2>/dev/null
    echo default-on > /sys/class/leds/blue:status/trigger 2>/dev/null
    echo 1 > /sys/class/leds/blue:status/brightness 2>/dev/null
fi

# 2. Log Truncation in RAM tmpfs
for log in "$LOG" /var/log/csqtt-client.log; do
    [ -f "$log" ] || continue
    FILE_SIZE=$(du -k "$log" 2>/dev/null | awk '{print $1}')
    if [ -n "$FILE_SIZE" ] && [ "$FILE_SIZE" -gt "$MAX_SIZE_KB" ]; then
        tail -n 200 "$log" > "${log}.cut" 2>/dev/null \
            && cat "${log}.cut" > "$log" \
            && rm -f "${log}.cut" \
            && echo "$(stamp) [WATCHDOG] Log cut" >> "$LOG"
    fi
done

[ -f "/var/run/csqtt/stopped" ] && exit 0
[ -f "/var/run/csqtt/restarting" ] && exit 0

# 3. Process & Route Watchdog
IS_RUNNING=0
if pgrep -f "/usr/bin/csqtt" >/dev/null 2>&1 || pidof csqtt >/dev/null 2>&1 || pgrep csqtt-client >/dev/null 2>&1; then
    IS_RUNNING=1
fi

IS_UP=1
if [ -n "$TUN_IFACE" ]; then
    IS_UP=0
    if ip link show "$TUN_IFACE" 2>/dev/null | grep -q "UP"; then
        IS_UP=1
        # ip route disabled for mngr (kept on WAN)
    fi
fi

if [ "$IS_RUNNING" -eq 0 ] || [ "$IS_UP" -eq 0 ]; then
    echo "$(stamp) [WATCHDOG] Restarting csqtt..." >> "$LOG"
    rm -f "/var/run/csqtt/stopped"
    "$INIT_CMD" restart >> "$LOG" 2>&1
    exit 0
fi

if [ -n "$TUN_IFACE" ] && command -v ping >/dev/null 2>&1; then
    if ! ping -c 2 -W 3 -I "$TUN_IFACE" "$PING_TARGET" >/dev/null 2>&1; then
        echo "$(stamp) [WATCHDOG] Ping failed, restarting..." >> "$LOG"
        rm -f "/var/run/csqtt/stopped"
        "$INIT_CMD" restart >> "$LOG" 2>&1
    fi
fi
