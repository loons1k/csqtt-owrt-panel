# CSQTT OpenWrt (v2.5+ Ready) 🛡️🚀

Современная интеграция протокола **CSQTT** (обход блокировок и «Белых Списков» / БС через видеозвонки VK/Одноклассники) для роутеров под управлением **OpenWrt** (23.05, 24.10, 25.12+).

Данный репозиторий полностью обновлен под новые релизы ядра [amurcanov/csqtt](https://github.com/amurcanov/csqtt) и решает проблему несовместимости старой панели управления LuCI (`starkugz/csqtt-openwrt`).

---

## ⚡ Ключевые особенности и доработки

1. **Встроенный WebCrypto дешифратор ссылок:**
   - Полная поддержка нового формата конфигураций `csqtt://`.
   - Клиентская расшифровка в браузере с использованием WebCrypto API (`PBKDF2 SHA-256` + `AES-GCM 128/256-bit`).
   - Работает прямо в LuCI без необходимости ставить на роутер тяжелые зависимости (Python, Node.js или openssl-bin).
2. **Надежное сохранение через Direct UCI:**
   - Прямая запись параметров в системную конфигурацию `/etc/config/csqtt` в обход ограничений RPC-демона LuCI.
   - Корректная обработка длинных base64-хешей VK (`vk`, `client_ids`, `device_id`).
3. **Управление современным ядром csqtt-core:**
   - Поддержка многопоточного режима UDP-воркеров (`workers: 1..16`).
   - Режимы обфускации (`video`, `turn`, `none`).
   - Выбор транспорта TURN (`udp` / `tcp`).
   - Настройка MTU виртуального интерфейса (`tun_mtu: 1280-1420`).
4. **Бесшовный Procd сервис и Watchdog:**
   - Чистый скрипт управления `/etc/init.d/csqtt` на базе OpenWrt procd.
   - Фоновый watchdog `/etc/csqtt/csqtt-watchdog.sh` с проверкой доступности VPS-пира через интерфейс туннеля и защитой от зависания сокетов при переключении WAN ↔ 4G/5G.
   - Светодиодная индикация статуса (синий — основной канал, красный — активен защищенный туннель CSQTT).
5. **Готовая связка с Clash / Mihomo:**
   - Прямой TPROXY-интерфейс `csqtt0`.
   - Автоматический `fallback`: когда проводной интернет отключается или мобильный оператор переходит в режим «Белых Списков» (БС), роутер мгновенно перенаправляет клиентский Wi-Fi трафик в CSQTT.

---

## 🚀 Быстрая установка

Подключитесь к роутеру по SSH и выполните команду:

```sh
wget -qO- https://raw.githubusercontent.com/loons1k/csqtt-openwrt/main/install.sh | sh
```

Скрипт автоматически:
- Установит недостающие пакеты (`kmod-tun`, `curl`, `ca-bundle`, `ucode`).
- Развернет обновленный интерфейс LuCI в `/www/luci-static/resources/view/csqtt/`.
- Настроит службы `/etc/init.d/csqtt` и watchdog в crontab.
- Очистит кэш LuCI для немедленного отображения в веб-интерфейсе.

---

## 🛠️ Сборка ядра csqtt-core под свою архитектуру

Исходный код ядра с нативной поддержкой TUN-интерфейса находится в каталоге `core/`.

### Сборка для MediaTek MT7981 (Filogic 820 / aarch64 Cortex-A53)

```sh
cd core
rustup target add aarch64-unknown-linux-musl

cargo build --release --target aarch64-unknown-linux-musl
```

Скомпилированный бинарный файл размещается на роутере по пути:
```sh
/usr/bin/csqtt-core
chmod +x /usr/bin/csqtt-core
```

---

## 🌐 Настройка резервирования в Clash / Mihomo

Для автоматического перехода на CSQTT при блокировках или отключении кабеля в `/opt/clash/config.yaml`:

```yaml
proxies:
  - name: '🛡️ CSQTT (VK Video Call Tunnel)'
    type: direct
    interface-name: csqtt0

proxy-groups:
  - name: '⚡️ Авто (Fallback)'
    type: fallback
    url: 'https://www.gstatic.com/generate_204'
    interval: 20
    proxies:
      - '⚡️ SkyPass Скорость'                 # Основной VLESS/Reality
      - '🛡️ CSQTT (VK Video Call Tunnel)'     # Резерв при БС
```

### DNS для режима «Белых Списков» (БС)
Во время блокировок мобильные операторы полностью отсекают зарубежные DNS (`8.8.8.8`, `1.1.1.1`). Используйте чистые российские резолверы без фильтрации:
```yaml
dns:
  enable: true
  listen: 0.0.0.0:7874
  default-nameserver:
    - 77.88.8.8
    - 77.88.8.1
  nameserver:
    - 77.88.8.8
    - 77.88.8.1
    - https://77.88.8.8/dns-query
```

---

## 📁 Структура репозитория

```
csqtt-openwrt/
├── install.sh                  # Автоматический установщик
├── README.md                   # Руководство пользователя
├── luci-app-csqtt/             # Пакет панели управления LuCI
│   ├── Makefile                # Сборка пакета для OpenWrt Buildroot
│   ├── root/                   # Системные конфигурации, init и rpcd
│   └── htdocs/                 # WebCrypto фронтенд (view/csqtt/profiles.js)
└── core/                       # Исходный код Rust-клиента (csqtt-core)
    ├── native_tun.rs           # Высокопроизводительный Linux TUN драйвер
    └── stats.rs                # Сбор телеметрии и метрик
```

---

## 📜 Лицензия
MIT / GPL-3.0
