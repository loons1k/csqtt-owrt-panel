# Установка на роутер

> **Все команды этой страницы выполняются на роутере по SSH**
> (`ssh root@<IP-роутера>`). Что подготовить заранее — в README, раздел
> [«Что понадобится»](../README.md#что-понадобится-и-как-проверить-совместимость-роутера).

Репозиторий **публичный**: файлы выпуска доступны всем без авторизации. Прямые ссылки и
автоустановщик работают анонимно, без токена GitHub.

## Что устанавливается

Для ручной установки нужны **два** пакета:

- **Ядро** (`csqtt`) — сам туннель и служба. Выбирается под архитектуру роутера.
- **Панель LuCI** (`luci-app-csqtt`) — веб-интерфейс. Одинакова для всех архитектур и
  **всегда на русском**: перевод встроен в панель (начиная с `luci-app-csqtt`
  `1.1.0-r3`), отдельный пакет `luci-i18n-csqtt-ru` не нужен.

Имена файлов содержат `openwrt`, выпуск и архитектуру:
`csqtt-<ядро>-openwrt-<выпуск>-<архитектура>.<apk|ipk>`; панель —
`luci-app-csqtt-<пакет>-openwrt-<выпуск>.<apk|ipk>`. Прямые пакеты собраны из SDK
**OpenWrt 25.12.5** (выпуск 25.12) и **OpenWrt 24.10.8** (выпуск 24.10); SNAPSHOT —
ревизия **r36953-0ab51fb9c1**.

## Автоматически (скрипт)

На роутере:

```sh
wget --no-proxy -qO /tmp/csqtt-install.sh https://raw.githubusercontent.com/starkugz/csqtt-openwrt/main/install-csqtt.sh \
  && ash /tmp/csqtt-install.sh && /etc/init.d/csqtt restart
```

> Скрипт сначала скачивается целиком и запускается **только после успешной загрузки**
> (`wget … && ash …`): при обрыве связи установка не начинается. Короткая форма
> `wget --no-proxy -qO- … | ash` короче, но при обрыве связи может выполнить неполный
> файл — если она нужна, применяйте её с осторожностью.
>
> `/etc/init.d/csqtt restart` после успешной установки **обязателен при обновлении**:
> менеджер пакетов не перезапускает уже работающий процесс, и без перезапуска
> продолжило бы работать прежнее ядро из памяти (`ls -l /proc/$(pidof csqtt)/exe`).
> При первой установке служба и так стартует с новым бинарником.

Скрипт определяет менеджер пакетов по релизу прошивки (24.10.x — `opkg`,
25.12.x/SNAPSHOT — `apk`), скачивает единый манифест `SHA256SUMS` и выбирает **два прямых
пакета** — ядро и панель — под семейство и архитектуру устройства **по манифесту**, а не по
догадке. Русский перевод встроен в панель и отдельно не скачивается. Затем проверяет
SHA-256, делает сухой прогон зависимостей (`opkg install --noaction` /
`apk add --simulate`), сохраняет резервную копию конфигурации и только затем устанавливает.
Прочие релизы и архитектуры отклоняются до изменений системы. Установщик **не заменяет**
конфигурацию, токены, пароли и `device_id` при обновлении.

### Варианты скрипта

Сначала скачайте скрипт на роутер **безопасно** — запускайте его только после успешного
скачивания (обрыв связи не должен приводить к запуску неполного файла):

```sh
f=$(mktemp "${TMPDIR:-/tmp}/csqtt-install.XXXXXX") || exit 1
if wget --no-proxy -qO "$f" https://raw.githubusercontent.com/starkugz/csqtt-openwrt/main/install-csqtt.sh \
   && head -n 1 "$f" | grep -q '^#!'; then
    ash "$f" --dry-run              # только план и проверки, без изменений
    ash "$f" --yes                  # без интерактивного подтверждения
    CSQTT_VERSION=v1.0 ash "$f"   # конкретный тег выпуска
    ash "$f" --core-only            # обновить только ядро (см. update.md)
    rm -f "$f"
else
    rm -f "$f"
    echo "CSQTT: скрипт не скачан (или это не скрипт) — команды не запускаются" >&2
fi
```

Переменная `f` используется и в примерах ниже; `$f` — путь к скачанному скрипту.

`CSQTT_LOCAL_DIR=/путь ash /tmp/install-csqtt.sh` — установка из уже скачанного набора
пакетов, без обращения к сети (когда сети нет или нужен свой набор).

Контроль целостности: единый манифест
[SHA256SUMS](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/SHA256SUMS)
— SHA-256 всех пакетов выпуска,
[MANIFEST.txt](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/MANIFEST.txt)
— полные версии, выпуск OpenWrt/SDK и ревизия SNAPSHOT.

## Вручную (прямые пакеты)

Выберите строку по значению `DISTRIB_ARCH` вашего роутера (как его узнать — в README).

<details>
<summary><strong>OpenWrt 25.12.x — APK (apk-tools 3)</strong></summary>

| `DISTRIB_ARCH` | Ядро | Панель |
|---|---|---|
| `aarch64_cortex-a53` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-25.12-aarch64_cortex-a53.apk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-25.12.apk) |
| `aarch64_generic` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-25.12-aarch64_generic.apk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-25.12.apk) |
| `x86_64` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-25.12-x86_64.apk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-25.12.apk) |
| `arm_cortex-a7_neon-vfpv4` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-25.12-arm_cortex-a7_neon-vfpv4.apk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-25.12.apk) |

</details>

<details>
<summary><strong>OpenWrt 24.10.x — IPK (opkg)</strong></summary>

| `DISTRIB_ARCH` | Ядро | Панель |
|---|---|---|
| `aarch64_cortex-a53` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-24.10-aarch64_cortex-a53.ipk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-24.10.ipk) |
| `aarch64_generic` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-24.10-aarch64_generic.ipk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-24.10.ipk) |
| `x86_64` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-24.10-x86_64.ipk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-24.10.ipk) |
| `arm_cortex-a7_neon-vfpv4` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-24.10-arm_cortex-a7_neon-vfpv4.ipk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-24.10.ipk) |

</details>

<details>
<summary><strong>OpenWrt SNAPSHOT — APK, ревизия r36953-0ab51fb9c1</strong></summary>

Только `aarch64_cortex-a53` (панель — тот же APK, что для 25.12.x):

| `DISTRIB_ARCH` | Ядро | Панель |
|---|---|---|
| `aarch64_cortex-a53` | [Ядро](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/csqtt-2.1.9-openwrt-snapshot-aarch64_cortex-a53.apk) | [Панель](https://github.com/starkugz/csqtt-openwrt/releases/download/v1.0/luci-app-csqtt-1.0-openwrt-snapshot.apk) |

SNAPSHOT — rolling-сборка: совместимость гарантируется только для указанной ревизии.
Установщик проверяет сборку на месте и останавливается при несовпадении ядра и `kmod-tun`.

</details>

Установка скачанных файлов — **на роутере**, из каталога с этими файлами. Вместо `<arch>`
подставьте вашу архитектуру, например `aarch64_cortex-a53`:

```sh
# OpenWrt 25.12.x / SNAPSHOT (apk):
apk update
apk add --allow-untrusted \
  ./csqtt-2.1.9-openwrt-25.12-<arch>.apk \
  ./luci-app-csqtt-1.0-openwrt-25.12.apk
```

```sh
# OpenWrt 24.10.x (opkg):
opkg update
opkg install \
  ./csqtt-2.1.9-openwrt-24.10-<arch>.ipk \
  ./luci-app-csqtt-1.0-openwrt-24.10.ipk
```

> **Обновление с прежнего выпуска (v1.1.0/v1.1.1).** Номера пакетов в `v1.0`
> (`csqtt` `1.0`, панель `1.0`) **меньше**, чем в ранее опубликованных (`csqtt`
> `1.1.0-r2`, панель `1.1.1-r1`), поэтому обновление — это **понижение версии**.
> `apk` с явно указанным файлом понижает сам; `opkg` по умолчанию печатает
> `Not downgrading package …` и **ничего не меняет** (при этом завершается успешно),
> поэтому для 24.10 к команде нужно добавить `--force-downgrade`:
>
> ```sh
> opkg install --force-downgrade \
>   ./csqtt-2.1.9-openwrt-24.10-<arch>.ipk \
>   ./luci-app-csqtt-1.0-openwrt-24.10.ipk
> ```
>
> Установщик (`install-csqtt.sh`) делает это сам и дополнительно проверяет, что
> версия действительно заменилась. Настройки, профили, пароли, токены и `device_id`
> при обновлении сохраняются.

`--allow-untrusted` для `apk` нужен, потому что пакеты собраны локально и не подписаны
ключом репозитория OpenWrt. Зависимости (`kmod-tun`, `luci-base`, `rpcd-mod-ucode`,
`ucode-mod-socket`, `coreutils-timeout`) менеджер подтянет из официального репозитория.

## Состав выпуска

Выпуск содержит только прямые пакеты (ядро и панель), единый `SHA256SUMS`, `MANIFEST.txt`,
`install-csqtt.sh` и `uninstall-csqtt.sh`. Отдельного пакета перевода `luci-i18n-csqtt-ru`
в выпуске нет — русский язык встроен в панель; внешних архивов `.tar.gz` и их отдельных
`.sha256` нет.

## Удаление

Удаление описано в разделе
[«Удаление»](../README.md#удаление) README и в документе
[«Обновление и удаление»](update.md#удаление).

---

[← Назад к README](../README.md)
