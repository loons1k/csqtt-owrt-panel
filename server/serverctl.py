#!/usr/bin/env python3
"""Local preparation of the official CSQTT server payload. No deployment."""
import argparse
import base64
import getpass
import hashlib
import http.client
import http.cookiejar
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import shutil
import stat
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
ARCHES = {'amd64': (2, 62), 'arm64': (2, 183), 'armv7': (1, 40)}
MAX_APK = 64 * 1024 * 1024
REMOTE_REPO = 'starkugz/csqtt-openwrt'
REMOTE_DEFAULT_COMMIT = '<SHA-коммита-после-публикации>'
REMOTE_DEFAULT_HOST = 'user@vps.example'
REMOTE_DEFAULT_WORK_DIR = '/root/csqtt-server-work/setup'


class ValidationError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_lock():
    return json.loads((ROOT / 'source-lock.json').read_text(encoding='utf-8'))


def read_bundle(apk, lock):
    apk = Path(apk)
    if not apk.is_file() or apk.stat().st_size > MAX_APK:
        raise ValidationError('APK отсутствует или превышает допустимый размер.')
    if sha256(apk.read_bytes()) != lock['apk_sha256']:
        raise ValidationError('Контрольная сумма APK не совпадает с зафиксированным выпуском.')
    payload = {}
    with zipfile.ZipFile(apk) as archive:
        for name, expected in lock['assets'].items():
            entries = [e for e in archive.infolist() if e.filename == name]
            if len(entries) != 1 or entries[0].file_size != expected['size']:
                raise ValidationError('Состав APK не соответствует зафиксированному выпуску.')
            data = archive.read(entries[0])
            if sha256(data) != expected['sha256']:
                raise ValidationError('Повреждён серверный компонент APK.')
            payload[name] = data
    deploy = payload['assets/deploy.sh']
    if b'readonly SCRIPT_VERSION="2.1.9"' not in deploy:
        raise ValidationError('Неожиданная версия установщика.')
    if ('readonly CSQTT_WIRE_PROTOCOL_REVISION="' + lock['protocol_revision'] + '"').encode() not in deploy:
        raise ValidationError('Неожиданная ревизия серверного протокола.')
    for arch in ARCHES:
        verify_elf(payload['assets/csqtt-linux-' + arch], arch)
    return payload


def verify_elf(data, arch):
    expected_class, machine = ARCHES[arch]
    if len(data) < 20 or data[:4] != b'\x7fELF' or data[4] != expected_class or data[5] != 1:
        raise ValidationError('Неверный формат ELF для выбранной архитектуры.')
    if struct.unpack_from('<H', data, 18)[0] != machine:
        raise ValidationError('Архитектура серверного бинарника не совпадает.')


def port(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ValidationError('Порт должен быть целым числом.') from None
    if not 1 <= number <= 65535:
        raise ValidationError('Порт должен быть от 1 до 65535.')
    return number


def validate_credentials(credentials):
    required = {'main_password', 'device_id', 'web_user', 'web_password'}
    if not isinstance(credentials, dict) or set(credentials) != required:
        raise ValidationError('Нужны ровно четыре поля: main_password, device_id, web_user, web_password.')
    for key in required:
        value = credentials[key]
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValidationError('Пустое или слишком длинное поле конфигурации.')
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValidationError('Управляющие символы в конфигурации запрещены.')
    if len(credentials['main_password']) < 8 or len(credentials['web_password']) < 8:
        raise ValidationError('Пароли должны содержать не менее восьми символов.')
    return credentials


def open_private(path):
    if os.name != 'posix':
        raise ValidationError('Подготовка секретных файлов требует Linux/WSL с POSIX-правами.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValidationError('Файл настроек должен принадлежать текущему пользователю и иметь права 0600.')
    return os.fdopen(fd, 'rb')


def read_credentials(path):
    with open_private(path) as stream:
        data = stream.read(32769)
    if len(data) > 32768:
        raise ValidationError('Слишком большой файл настроек.')
    try:
        return validate_credentials(json.loads(data))
    except (ValueError, UnicodeError):
        raise ValidationError('Некорректный JSON настроек; содержимое скрыто.') from None


def prompt_credentials():
    if not sys.stdin.isatty():
        raise ValidationError('Для скрытого ввода нужен терминал или защищённый --credentials-file.')
    first = getpass.getpass('Пароль подключения к серверу: ')
    if first != getpass.getpass('Повторите пароль подключения: '):
        raise ValidationError('Пароли подключения не совпадают.')
    device = getpass.getpass('device_id клиента (сохраните существующее значение): ')
    user = input('Имя пользователя веб-панели: ')
    password = getpass.getpass('Пароль веб-панели: ')
    if password != getpass.getpass('Повторите пароль веб-панели: '):
        raise ValidationError('Пароли веб-панели не совпадают.')
    return validate_credentials(dict(main_password=first, device_id=device, web_user=user, web_password=password))


def request_device_id():
    """Скрытый ввод существующего устройства клиента. device_id не генерируется."""
    return getpass.getpass('device_id клиента (сохраните существующее значение): ')


def generated_credentials(device_id):
    """Случайный логин и два независимых пароля через криптографически стойкий
    secrets. Основой не служат время, IP, SSH-пароль или фиксированные значения.
    device_id передаётся извне (значение существующего клиента) и не рандомизируется."""
    web_user = 'csqtt_' + secrets.token_hex(8)
    main_password = secrets.token_urlsafe(24)
    web_password = secrets.token_urlsafe(24)
    while web_password == main_password:
        web_password = secrets.token_urlsafe(24)
    return validate_credentials(dict(main_password=main_password, device_id=device_id,
                                     web_user=web_user, web_password=web_password))


def read_setup_credentials(path):
    """Чтение настроек для setup из файла 0600. Допустимы два вида JSON:
    либо все четыре поля, либо только device_id (логин и пароли сгенерируются)."""
    with open_private(path) as stream:
        data = stream.read(32769)
    if len(data) > 32768:
        raise ValidationError('Слишком большой файл настроек.')
    try:
        values = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValidationError('Некорректный JSON настроек; содержимое скрыто.') from None
    if not isinstance(values, dict):
        raise ValidationError('Некорректный JSON настроек; содержимое скрыто.')
    allowed = {'main_password', 'device_id', 'web_user', 'web_password'}
    if not set(values).issubset(allowed):
        raise ValidationError('Некорректный JSON настроек; содержимое скрыто.')
    device_id = values.get('device_id')
    if not isinstance(device_id, str) or not device_id:
        raise ValidationError('В файле настроек нужен device_id существующего клиента.')
    missing = [name for name in allowed if not values.get(name)]
    if missing:
        if set(missing) != {'main_password', 'web_user', 'web_password'}:
            raise ValidationError('В файле настроек укажите либо только device_id, '
                                  'либо все четыре поля; содержимое скрыто.')
        return generated_credentials(device_id)
    return validate_credentials({name: values[name] for name in allowed})


def credentials_snapshot_path(work_dir):
    return Path(work_dir) / 'credentials.json'


def save_credentials_snapshot(work_dir, credentials):
    """Сохраняет данные доступа 0600 в рабочем каталоге 0700 для продолжения
    прерванного сценария: повторный запуск не генерирует новые значения молча."""
    path = credentials_snapshot_path(work_dir)
    if path.exists() or path.is_symlink():
        raise ValidationError('В рабочем каталоге уже сохранены данные доступа; повторите с ним же или используйте новый.')
    private_write(path, json.dumps(credentials, ensure_ascii=False).encode())
    return path


def load_credentials_snapshot(work_dir):
    path = credentials_snapshot_path(work_dir)
    with open_private(path) as stream:
        data = stream.read(32769)
    if len(data) > 32768:
        raise ValidationError('Слишком большой сохранённый файл данных доступа.')
    try:
        return validate_credentials(json.loads(data))
    except (ValueError, UnicodeError):
        raise ValidationError('Повреждён сохранённый файл данных доступа; содержимое скрыто.') from None


def systemd_value(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def assert_no_link_parents(path):
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValidationError('Путь подготовки не должен проходить через символические ссылки.')


def private_write(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
            raise ValidationError('Файловая система не обеспечивает нужные права доступа.')
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(fd)


def private_write_replace(path, data, mode=0o600):
    """Перезапись защищённого файла 0600 (для server-addr при возобновлении
    прерванного сценария). Не следует символическим ссылкам."""
    path = Path(path)
    if path.is_symlink():
        raise ValidationError('Нельзя перезаписывать символическую ссылку.')
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
            raise ValidationError('Файловая система не обеспечивает нужные права доступа.')
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(fd)


def prepare(apk, output, arch, credentials, ports, lock):
    if os.name != 'posix':
        raise ValidationError('Подготовка требует Linux/WSL; на Windows доступна inspect.')
    ports = validate_port_selection(ports)
    payload = read_bundle(apk, lock)
    credentials = validate_credentials(credentials)
    output = Path(os.path.abspath(output))
    assert_no_link_parents(output)
    if output.exists() or not output.parent.is_dir():
        raise ValidationError('Выходной каталог должен быть новым, а его родитель — существующим.')
    output.mkdir(mode=0o700)
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        output.rmdir()
        raise ValidationError('Нужна файловая система с POSIX-правами (например, ext4 в WSL).')
    created = []
    try:
        files = {
            'deploy.sh': payload['assets/deploy.sh'],
            '.csqtt-upload-server': payload['assets/csqtt-linux-' + arch],
            '.csqtt-upload-web.env': (
                'CSQTT_WEB_USER=' + systemd_value(credentials['web_user']) + '\n' +
                'CSQTT_WEB_PASS=' + systemd_value(credentials['web_password']) + '\n'
            ).encode(),
            '.csqtt-upload-overrides.json': json.dumps({
                'main_password': credentials['main_password'], 'device_id': credentials['device_id']
            }, ensure_ascii=False).encode(),
        }
        plan = {
            'schema_version': 1, 'state': 'prepared_not_installed',
            'upstream_version': lock['version'], 'protocol_revision': lock['protocol_revision'],
            'architecture': arch, 'deployment_mode': 'systemd', 'ports': ports,
            'source_apk_sha256': lock['apk_sha256'],
            'public_payload_sha256': {name: sha256(files[name]) for name in ['deploy.sh', '.csqtt-upload-server']},
            'requires_vps_test': True,
        }
        files['install-plan.json'] = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode()
        for name, data in files.items():
            target = output / name
            created.append(target)
            private_write(target, data, 0o700 if name == '.csqtt-upload-server' else 0o600)
    except Exception:
        for target in created:
            if target.is_file() and not target.is_symlink():
                target.unlink()
        output.rmdir()
        raise
    return plan


def check_runtime(output, lock):
    output = Path(output)
    assert_no_link_parents(output)
    with open_private(output / 'install-plan.json') as stream:
        plan = json.load(stream)
    native = {'x86_64': 'amd64', 'aarch64': 'arm64', 'armv7l': 'armv7'}.get(platform.machine())
    if plan.get('architecture') != native or os.name != 'posix':
        raise ValidationError('Для проверки запуска нужен Linux той же архитектуры, что бинарник.')
    binary = output / '.csqtt-upload-server'
    with open_private(binary) as stream:
        data = stream.read()
    expected = lock['assets']['assets/csqtt-linux-' + native]['sha256']
    if sha256(data) != expected:
        raise ValidationError('Серверный бинарник изменился после подготовки.')
    result = subprocess.run([str(binary.absolute()), '--protocol-revision'],
                            capture_output=True, timeout=5, check=False)
    if result.returncode or result.stdout.decode().strip() != lock['protocol_revision']:
        raise ValidationError('Проверка ревизии протокола не прошла; вывод бинарника скрыт.')


# ---------------------------------------------------------------------------
# Установка на VPS: предварительная проверка и запуск официального deploy.sh.
# Работает на целевом Linux-хосте от root. На рабочей машине (Windows/WSL)
# предварительная проверка вернёт ошибку для неподдерживаемой среды.
# ---------------------------------------------------------------------------

DEPLOY_MARKER_OK = 'CSQTT_DEPLOY_OK'
DEPLOY_MARKER_ERROR = 'CSQTT_DEPLOY_ERROR|'
DEPLOY_MARKER_ROLLED_BACK = 'CSQTT_DEPLOY_ROLLED_BACK'
DEPLOY_MARKER_ROLLBACK_FAILED = 'CSQTT_DEPLOY_ROLLBACK_FAILED'

# Строки официального установщика, которые гарантированно не содержат секретов.
# Только точное совпадение целиком, без широких префиксов ([OK], ✓, ⚠ и т.п.):
# текст прогресса с такими префиксами может содержать токены/пароли, поэтому
# неизвестные строки не выводятся на консоль автоматически.
CONSOLE_SAFE_EXACT = frozenset({
    DEPLOY_MARKER_OK,
    DEPLOY_MARKER_ROLLED_BACK,
    DEPLOY_MARKER_ROLLBACK_FAILED,
})

UPLOAD_NAMES_AND_MODES = {
    'deploy.sh': 0o600,
    '.csqtt-upload-server': 0o700,
    '.csqtt-upload-web.env': 0o600,
    '.csqtt-upload-overrides.json': 0o600,
}

ANSI_ESCAPE = re.compile(r'\x1B\[[;\d]*m')


def is_wsl():
    if os.environ.get('WSL_DISTRO_NAME') or os.environ.get('WSL_INTEROP'):
        return True
    try:
        release = Path('/proc/sys/kernel/osrelease').read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return False
    return 'microsoft' in release.lower()


def is_openwrt():
    return (Path('/etc/openwrt_release').is_file()
            or Path('/etc/openwrt_version').is_file()
            or Path('/etc/rc.common').is_file()
            or Path('/etc/openwrt_runtime').is_file())


def os_release_info():
    try:
        text = Path('/etc/os-release').read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return {}
    info = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        info[key.strip()] = value.strip().strip('"').strip("'")
    return info


def installed_marks():
    """Читаемые маркеры уже существующей установки CSQTT, без изменения системы."""
    marks = []
    for path in ['/usr/local/bin/csqtt', '/usr/local/lib/csqtt', '/etc/csqtt',
                 '/etc/systemd/system/csqtt.service',
                 '/etc/systemd/system/multi-user.target.wants/csqtt.service',
                 '/var/lib/csqtt', '/run/csqtt']:
        if os.path.lexists(path):
            marks.append(path)
    if Path('/sys/class/net/csqtt1').exists():
        marks.append('интерфейс csqtt1')
    return marks


def systemd_present():
    if not shutil.which('systemctl'):
        return False
    try:
        pid1 = Path('/proc/1/comm').read_text(encoding='utf-8', errors='ignore').strip()
    except OSError:
        return False
    return pid1 == 'systemd'


def tun_present():
    try:
        info = os.stat('/dev/net/tun')
    except OSError:
        return False
    return stat.S_ISCHR(info.st_mode)


def listening_ports():
    ports = set()
    for source in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            text = Path(source).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] != '0A':
                continue
            local = fields[1].split(':')
            if len(local) == 2:
                try:
                    ports.add(int(local[1], 16))
                except ValueError:
                    continue
    return ports


def listening_udp_ports():
    """Прослушиваемые UDP-порты: транспорт туннеля CSQTT — UDP, поэтому занятость
    надо проверять отдельно от TCP (например, против сервисов 3x-ui/Xray)."""
    ports = set()
    for source in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            text = Path(source).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3:
                continue
            # Прослушивающий UDP-сокет имеет нулевой удалённый адрес/порт;
            # состоянии (st) у UDP нет надёжного «LISTEN», как у TCP.
            remote = fields[2].split(':')
            if len(remote) != 2 or remote != ['00000000', '0000']:
                continue
            local = fields[1].split(':')
            if len(local) == 2:
                try:
                    ports.add(int(local[1], 16))
                except ValueError:
                    continue
    return ports


def native_arch():
    return {'x86_64': 'amd64', 'aarch64': 'arm64', 'armv7l': 'armv7'}.get(platform.machine())


def load_plan(source):
    with open_private(Path(source) / 'install-plan.json') as stream:
        return json.load(stream)


def check_source_bundle(source, lock, require_plan=True):
    """Целостность подготовленного каталога: состав, суммы, права, состояние."""
    source = Path(os.path.abspath(source))
    assert_no_link_parents(source)
    if not source.is_dir():
        raise ValidationError('Каталог подготовки не существует или не является каталогом.')
    if stat.S_IMODE(source.stat().st_mode) != 0o700:
        raise ValidationError('Каталог подготовки должен иметь права 0700.')
    plan = load_plan(source) if require_plan else None
    if plan is not None:
        if plan.get('state') != 'prepared_not_installed':
            raise ValidationError('Комплект не в состоянии готовой предустановленной подготовки.')
        if plan.get('deployment_mode') != 'systemd':
            raise ValidationError('Установка поддерживает только режим systemd.')
        if plan.get('upstream_version') != lock['version']:
            raise ValidationError('Версия комплекта не совпадает с зафиксированным выпуском.')
        expected = lock['assets']['assets/deploy.sh']['sha256']
        if plan.get('public_payload_sha256', {}).get('deploy.sh') != expected:
            raise ValidationError('install-plan.json повреждён: не совпадает сумма deploy.sh.')
    for name, expected_mode in UPLOAD_NAMES_AND_MODES.items():
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ValidationError('В комплекте отсутствует файл ' + name + '.')
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise ValidationError('Файл ' + name + ' должен иметь права ' + oct(expected_mode) + '.')
    deploy = source / 'deploy.sh'
    expected_deploy = lock['assets']['assets/deploy.sh']['sha256']
    if sha256(deploy.read_bytes()) != expected_deploy:
        raise ValidationError('deploy.sh изменился после подготовки: сумма не совпадает с закреплённым выпуском.')
    server = source / '.csqtt-upload-server'
    expected = lock['assets']['assets/csqtt-linux-' + (plan['architecture'] if plan else 'amd64')]['sha256']
    if sha256(server.read_bytes()) != expected:
        raise ValidationError('Серверный бинарник изменился после подготовки.')
    if plan is not None:
        arch = plan['architecture']
        if arch not in ('amd64', 'arm64', 'armv7'):
            raise ValidationError('Неизвестная архитектура в плане установки.')
    return plan


def verify_protocol_revision_safe(source):
    """Запускает только --protocol-revision родного бинарника комплекта."""
    check_source_bundle(source, load_lock())
    binary = Path(source) / '.csqtt-upload-server'
    if native_arch() != load_plan(source)['architecture']:
        raise ValidationError('Для проверки ревизии нужен бинарник родной архитектуры.')
    result = subprocess.run([str(binary.absolute()), '--protocol-revision'],
                            capture_output=True, timeout=5, check=False)
    if result.returncode or result.stdout.decode().strip() != load_lock()['protocol_revision']:
        raise ValidationError('Проверка ревизии протокола не прошла; вывод бинарника скрыт.')


def exists_csqtt_docker_container():
    if not shutil.which('docker'):
        return False
    try:
        result = subprocess.run(['docker', 'container', 'inspect', '-f', '{{.State.Running}}', 'csqtt'],
                                capture_output=True, timeout=10, check=False)
    except OSError:
        return False
    return result.returncode == 0 and b'true' in result.stdout


FOREIGN_SERVICE_MARKS = (
    'x-ui', '3x-ui', 'xray', 'nginx', 'apache2', 'apache', 'httpd', 'caddy',
    'v2ray', 'sing-box', 'singbox', 'hysteria', 'snell', 'shadowsocks',
    'mihomo', 'clash', 'tailscale', 'wg-quick',
)

FOREIGN_PATHS = (
    '/etc/x-ui', '/usr/local/x-ui', '/etc/3x-ui', '/usr/local/3x-ui', '/etc/xray',
    '/etc/nginx', '/etc/caddy', '/usr/local/nginx',
)

DEPLOY_FOREIGN_PATTERNS = (
    re.compile(r'\biptables\b[^;\n]*\s-F\b'),
    re.compile(r'\biptables\b[^;\n]*\b--flush\b'),
    re.compile(r'\bnft\b[^;\n]*\bflush ruleset\b'),
    re.compile(r':(INPUT|FORWARD|OUTPUT)\s+(\[0:0\]\s+)?DROP'),
    re.compile(r'\biptables\b[^;\n]*(?:-P\b|--policy\b)[^;\n]*(INPUT|FORWARD|OUTPUT)[^;\n]*(DROP|REJECT)'),
    re.compile(r'\bsystemctl\s+(stop|disable|kill|restart|mask|unmask)\s+\S*(x-ui|3x-ui|xray|nginx|apache|httpd|caddy|v2ray)\S*'),
    re.compile(r'\brm\s+-rf\s+[^\n;]*(/etc/x-ui|/usr/local/x-ui|/etc/3x-ui|/usr/local/3x-ui|/etc/xray|/etc/nginx|/etc/caddy|/usr/local/nginx)'),
)


def foreign_services():
    """Имена чужих серверных служб/процессов (3x-ui, Xray, nginx и др.) на хосте.
    Только чтение; ни одна из них не останавливается и не изменяется."""
    found = set()
    try:
        for unit_dir in ('/etc/systemd/system', '/lib/systemd/system'):
            try:
                entries = os.listdir(unit_dir)
            except OSError:
                continue
            for entry in entries:
                low = entry.lower()
                if any(mark in low for mark in FOREIGN_SERVICE_MARKS):
                    found.add(entry)
    except OSError:
        pass
    for path in FOREIGN_PATHS:
        if os.path.lexists(path):
            found.add(path)
    return sorted(found)


def audit_deploy_foreign_ops(deploy_bytes):
    """Находит в официальном deploy.sh операции, затрагивающие чужие ресурсы:
    общий сброс firewall, изменение глобальных политик, остановку/удаление чужих
    служб (3x-ui, Xray, nginx и т.п.). Возвращает список обнаруженных фрагментов."""
    text = deploy_bytes.decode('utf-8', errors='replace')
    hits = []
    for pattern in DEPLOY_FOREIGN_PATTERNS:
        for match in pattern.finditer(text):
            snippet = ' '.join(match.group(0).split())
            if snippet not in hits:
                hits.append(snippet[:140])
    return hits


def propose_free_port(busy, preferred, step=1):
    """Предлагает свободный порт рядом с preferred, не входящий в busy (не менее 1024)."""
    candidate = max(1024, preferred)
    while candidate in busy or candidate == 80:
        candidate += step
        if candidate > 65535:
            candidate = 1024
    return candidate


# ---------------------------------------------------------------------------
# Порты: значения по умолчанию, внутренние порты CSQTT и выбор в интерактиве.
# ---------------------------------------------------------------------------

DEFAULT_PORTS = {'peer': 46000, 'ssh': 22, 'web': 46002}

# Официальный deploy.sh использует 80/TCP для HTTP-01 проверки Let's Encrypt.
CSQTT_LE_HTTP_PORT = 80
# Внутренние порты CSQTT 2.1.9, которые пользователь не выбирает. Сверено с
# закреплённым deploy.sh 2.1.9 и строками серверного бинарника: deploy.sh
# запускает сервер как `csqtt --listen 0.0.0.0:PEER --web-port WEB --config-dir
# /etc/csqtt` и передаёт только порт туннеля и порт панели. Локальные сокеты DPI
# (127.0.0.1:46003) и syscalls (127.0.0.1:46004) фиксированы, WG-порт (46001) —
# значение по умолчанию отдельного поля устройства. Зависимости от выбранного
# порта туннеля нет, поэтому ничего не вычисляется из PEER и выхода за диапазон
# 65535 не возникает: резервируется фиксированный набор. Порт 80 занимает
# официальный TLS-установщик Let's Encrypt.
CSQTT_INTERNAL_FIXED_PORTS = frozenset({46001, 46003, 46004})
WILDCARD_ADDRS = frozenset({'0.0.0.0', '::'})


def csqtt_internal_ports():
    """Внутренние порты CSQTT, которые нельзя занимать под туннель/панель.
    Набор фиксирован и не зависит от выбранного порта туннеля (см. комментарий
    выше)."""
    reserved = set(CSQTT_INTERNAL_FIXED_PORTS)
    reserved.add(CSQTT_LE_HTTP_PORT)
    return reserved


def validate_port_selection(ports):
    """Проверяет диапазон 1..65535, различие выбранных портов, запрет порта 80
    (нужен официальному TLS-установщику) и совпадение с внутренними портами CSQTT."""
    ports = {key: port(value) for key, value in ports.items()}
    if len(set(ports.values())) != len(ports):
        raise ValidationError('Порты должны различаться.')
    if CSQTT_LE_HTTP_PORT in (ports['peer'], ports['web']):
        raise ValidationError('Порт 80 нужен официальному TLS-установщику; выберите другие порты туннеля и панели.')
    internal = csqtt_internal_ports()
    collide = sorted({ports['peer'], ports['web']} & internal)
    if collide:
        raise ValidationError('Порты совпадают с внутренними портами CSQTT ('
                              + ', '.join(map(str, collide)) + '); выберите другие.')
    return ports


def _proc_addr_v4(hexaddr):
    if len(hexaddr) != 8:
        return '?'
    return '.'.join(str(int(hexaddr[i:i + 2], 16)) for i in (6, 4, 2, 0))


def listening_sockets():
    """Занятые сокеты (proto, address, port) из /proc: TCP LISTEN и UDP-слушатели.
    Только чтение; нужна для проверки занятости с учётом протокола и адреса привязки."""
    sockets = []
    for proto, source in (('tcp', '/proc/net/tcp'), ('tcp', '/proc/net/tcp6'),
                          ('udp', '/proc/net/udp'), ('udp', '/proc/net/udp6')):
        try:
            text = Path(source).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4:
                continue
            local = fields[1].split(':')
            if len(local) != 2:
                continue
            if proto == 'tcp' and fields[3] != '0A':
                continue
            if proto == 'udp':
                remote = fields[2].split(':')
                if len(remote) != 2 or remote != ['00000000', '0000']:
                    continue
            try:
                value = int(local[1], 16)
            except ValueError:
                continue
            addr = _proc_addr_v4(local[0]) if source.endswith(proto) else '::'
            sockets.append((proto, addr, value))
    return sockets


def port_conflict(proto, addr, value, sockets):
    """Конфликт привязки (proto, addr, value) с наблюдаемыми сокетами: адрес
    wildcard конфликтует с любым адресом, иначе требуется совпадение адреса."""
    for s_proto, s_addr, s_port in sockets:
        if s_proto != proto or s_port != value:
            continue
        if addr in WILDCARD_ADDRS or s_addr in WILDCARD_ADDRS or s_addr == addr:
            return True
    return False


def _occupied_busy_set(sockets):
    return {value for _, _, value in sockets}


def choose_ports_interactive(defaults, ask=input, sockets=None):
    """Интерактивный выбор портов туннеля (UDP) и веб-панели (TCP). Enter
    сохраняет значение по умолчанию. Проверяются диапазон, дубли, внутренние
    порты CSQTT и занятость с учётом протокола и адреса привязки; при занятости
    сообщается причина и предлагается свободный порт."""
    sockets = listening_sockets() if sockets is None else sockets
    chosen = dict(defaults)
    for key, label, proto in (('peer', 'Порт туннеля PEER (UDP)', 'udp'),
                              ('web', 'Порт веб-панели WEB (TCP)', 'tcp')):
        while True:
            raw = ask('%s [%d]: ' % (label, defaults[key]))
            raw = (raw or '').strip()
            if raw == '':
                value = defaults[key]
            else:
                try:
                    value = port(raw)
                except ValidationError:
                    print('Порт должен быть целым числом от 1 до 65535.')
                    continue
            if value in {chosen[other] for other in chosen if other != key}:
                print('Порт %d уже выбран для другого параметра; укажите другой.' % value)
                continue
            if value == CSQTT_LE_HTTP_PORT:
                print('Порт 80 нужен официальному TLS-установщику; укажите другой.')
                continue
            internal = csqtt_internal_ports()
            if value in internal:
                print('Порт %d занят самим CSQTT (внутренний порт); укажите другой.' % value)
                continue
            if port_conflict(proto, '0.0.0.0', value, sockets):
                busy = (_occupied_busy_set(sockets) | internal
                        | {chosen[other] for other in chosen if other != key})
                print('%s/%d уже занят. Свободный вариант: %d'
                      % (proto.upper(), value, propose_free_port(busy, value)))
                continue
            chosen[key] = value
            break
    return chosen


def busy_selected_ports(ports):
    """Занятые TCP/UDP-порты среди выбранных (протокол туннеля — UDP, панели — TCP;
    занятость проверяется по обоим протоколам, чтобы не отбирать чужие службы)."""
    return sorted((listening_ports() | listening_udp_ports()) & {ports['peer'], ports['web']})


# ---------------------------------------------------------------------------
# Состояние установки (полная/неполная) — проверяется до генерации паролей.
# ---------------------------------------------------------------------------

CSQTT_BINARY = '/usr/local/bin/csqtt'
CSQTT_CONFIG_DIR = '/etc/csqtt'
CSQTT_UNIT_FILE = '/etc/systemd/system/csqtt.service'
CSQTT_CORE_MARKS = (CSQTT_BINARY, CSQTT_UNIT_FILE, CSQTT_CONFIG_DIR)
# Сохранённые настройки обычного удаления: серверных файлов уже нет, но данные
# (/etc/csqtt: csqtt.db, csqtt.env, сертификаты) остаются. Данные доступа при
# повторной установке берутся из прежнего рабочего каталога (credentials.json)
# или из --credentials-file, а не из настроек /etc/csqtt.
CSQTT_SETTINGS_MARKS = (CSQTT_CONFIG_DIR,)


def installation_state(marks=None):
    """'absent' — установки нет; 'complete' — полный состав; 'preserved' — сервер
    удалён, но сохранены только настройки /etc/csqtt; 'incomplete' — часть
    серверных маркеров без полного состава (повреждённая/незавершённая установка)."""
    marks = list(installed_marks()) if marks is None else list(marks)
    if not marks:
        return 'absent'
    if all(mark in marks for mark in CSQTT_CORE_MARKS):
        return 'complete'
    if set(marks) <= set(CSQTT_SETTINGS_MARKS):
        return 'preserved'
    return 'incomplete'


def guard_existing_install(marks=None):
    """Проверяет состояние CSQTT до генерации паролей и изменений. Полная
    установка — сообщение и выход без изменений. Только сохранённые настройки
    после обычного удаления — это не повреждённая установка: повторная установка
    разрешена, данные доступа берутся из прежнего рабочего каталога или
    --credentials-file (новые пароли автоматически не создаются). Неполная
    установка — понятный отказ без автоматического удаления или переустановки."""
    marks = list(installed_marks()) if marks is None else list(marks)
    state = installation_state(marks)
    if state == 'complete':
        print('CSQTT Server уже установлен. Повторная установка не выполнялась.')
    elif state == 'preserved':
        print('CSQTT Server не установлен, но сохранены его настройки (/etc/csqtt) '
              'от прежней установки: это не повреждённая установка. Повторная '
              'установка продолжится с данными доступа из прежнего рабочего каталога '
              'или --credentials-file.', file=sys.stderr)
    elif state == 'incomplete':
        message = ('Обнаружена неполная или повреждённая установка CSQTT '
                   '(существующей установки нет полного состава): ' + ', '.join(marks)
                   + '. Автоматическое удаление и переустановка не выполняются; '
                     'проверьте состояние службы и используйте команду удаления.')
        print(message, file=sys.stderr)
        raise ValidationError(message)
    return state


# ---------------------------------------------------------------------------
# Удаление установленного CSQTT Server (останавливает туннель и веб-панель).
# Использует официальный режим deploy.sh uninstall; по умолчанию настройки и
# сертификаты сохраняются в защищённой резервной копии.
# ---------------------------------------------------------------------------

CSQTT_UNINSTALL_STOPS = (
    'csqtt.service (и другие runtime-службы CSQTT) — остановка туннеля и панели',
    'csqtt-letsencrypt.service и csqtt-letsencrypt.timer',
    'docker-контейнер csqtt (если есть)',
)
CSQTT_UNINSTALL_REMOVES = (
    '/usr/local/bin/csqtt', '/usr/local/bin/csqtt-cascade',
    '/usr/local/lib/csqtt', '/run/csqtt', '/var/lib/csqtt', '/opt/csqtt-certbot',
    '/etc/systemd/system/csqtt.service',
    '/etc/systemd/system/csqtt-letsencrypt.service',
    '/etc/systemd/system/csqtt-letsencrypt.timer',
    '/etc/systemd/system/csqtt-docker-prereq.service',
    '/etc/sysctl.d/99-csqtt.conf', '/etc/sysctl.d/99-csqtt-udp-buffers.conf',
)
# Общие имена, которые официальный deploy.sh удаляет по одному совпадению имени.
# Их может использовать чужая служба, а deploy.sh uninstall сносит их безусловно
# (rm -f <имя>), поэтому гарантировать сохранность файла на всём протяжении
# удаления нельзя. Исключительную принадлежность CSQTT по внешним признакам
# установить невозможно (ссылка из службы CSQTT подтверждает лишь использование),
# поэтому при наличии любого из этих файлов удаление останавливается до изменений
# (см. shared_uninstall_blockers).
CSQTT_UNINSTALL_SHARED = ('/usr/local/bin/hev-socks5-tunnel',
                          '/usr/local/bin/hev-socks5-server')
# Каталоги systemd-служб, по которым ищутся ссылки на общий путь: своя служба
# CSQTT и чужая служба (обе лишь доказывают использование).
CSQTT_UNIT_DIRS = ('/etc/systemd/system', '/lib/systemd/system')
CSQTT_UNINSTALL_NETWORK = (
    'интерфейс csqtt1',
    'правила netfilter с меткой CSQTT_MANAGED/CSQTT_* (только правила CSQTT)',
)
CSQTT_UNINSTALL_PRESERVED = (
    '/etc/csqtt: база SQLite (csqtt.db и sidecars), csqtt.env, deploy-overrides.json, '
    'сертификаты web_cert.pem/web_key.pem',
    'рабочий каталог: данные доступа (credentials.json, access-*.txt) и комплект prepared/',
)
# Пути чужих служб, которые удаление затрагивать не должно (проверка в тестах).
CSQTT_FOREIGN_KEEP = ('/etc/x-ui', '/usr/local/x-ui', '/etc/nginx', '/etc/xray',
                      '/etc/caddy', '/etc/ssh')


def describe_removal(purge=False):
    """Состав изменений при удалении — для показа перед подтверждением."""
    lines = ['Будет остановлено:']
    lines += ['  - ' + item for item in CSQTT_UNINSTALL_STOPS]
    lines.append('Будет удалено (только принадлежащее CSQTT):')
    lines += ['  - ' + item for item in CSQTT_UNINSTALL_REMOVES]
    lines += ['  - ' + item for item in CSQTT_UNINSTALL_NETWORK]
    lines.append('Общие бинарники, которые официальный deploy.sh удаляет по одному '
                 'имени безусловно; при их наличии удаление останавливается до изменений, '
                 'так как исключительная принадлежность CSQTT не подтверждается:')
    lines += ['  - ' + item for item in CSQTT_UNINSTALL_SHARED]
    if purge:
        lines.append('Будет удалено безвозвратно (--purge):')
        lines.append('  - /etc/csqtt: настройки, база SQLite и сертификаты')
        lines.append('  - данные доступа в рабочем каталоге (--work-dir): '
                     'credentials.json, access-*.txt и комплект prepared/ с секретами')
        lines.append('Останется даже при --purge: сам рабочий каталог без данных доступа, '
                     'журналы logs/, файл server-addr, закреплённый APK и резервные копии '
                     '(в том числе созданные ранее).')
    else:
        lines.append('Будет сохранено:')
        lines += ['  - ' + item for item in CSQTT_UNINSTALL_PRESERVED]
        lines.append('  - защищённая резервная копия настроек и данных доступа')
    lines.append('Не затрагиваются: SSH, 3x-ui, Xray, nginx, общие сертификаты, '
                 'чужие настройки; ip_forward и общие sysctl не отключаются вслепую.')
    return lines


def service_is_active(name):
    if not shutil.which('systemctl'):
        return None
    try:
        probe = subprocess.run(['systemctl', 'is-active', name],
                               capture_output=True, timeout=10, check=False)
    except OSError:
        return None
    return probe.stdout.decode(errors='replace').strip() == 'active'


def _copy_file_private(source, target):
    data = Path(source).read_bytes()
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(fd)


def _copy_tree_private(source, target):
    target.mkdir(mode=0o700)
    for root, _dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        rel = root_path.relative_to(source)
        target_dir = target if rel == Path('.') else target / rel
        if rel != Path('.'):
            target_dir.mkdir(mode=0o700)
        for name in files:
            src_file = root_path / name
            if src_file.is_symlink() or not src_file.is_file():
                continue
            _copy_file_private(src_file, target_dir / name)


def backup_csqtt_state(config_dir, backup_root, extra_files=()):
    """Защищённая резервная копия настроек CSQTT и данных доступа (0700/0600).
    Только обычные файлы; символические ссылки не копируются."""
    config_dir = Path(config_dir)
    sources = []
    if config_dir.is_dir() and not config_dir.is_symlink():
        sources.append(config_dir)
    extra = [Path(path) for path in extra_files
             if Path(path).is_file() and not Path(path).is_symlink()]
    if not sources and not extra:
        return None
    backup_root = Path(backup_root)
    if backup_root.exists() or backup_root.is_symlink():
        raise ValidationError('Каталог резервной копии уже существует: ' + str(backup_root))
    backup_root.mkdir(parents=True, mode=0o700)
    if stat.S_IMODE(backup_root.stat().st_mode) != 0o700:
        raise ValidationError('Резервная копия требует файловой системы с правами 0700.')
    for source in sources:
        _copy_tree_private(source, backup_root / source.name)
    if extra:
        target_dir = backup_root / 'access'
        target_dir.mkdir(mode=0o700)
        for path in extra:
            _copy_file_private(path, target_dir / path.name)
    return backup_root


def _secure_rmtree(path):
    """Удаляет каталог CSQTT целиком (только для явного --purge). Не следует
    символическим ссылкам."""
    path = Path(path)
    if path.is_symlink() or not path.exists():
        return False
    shutil.rmtree(path)
    return True


def _remove_scratch_paths(paths):
    """Удаляет временные пути удаления (приватные каталоги/файлы). Не следует
    символическим ссылкам; ошибки не прерывают завершение."""
    for path in paths:
        path = Path(path)
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif os.path.lexists(str(path)):
                path.unlink()
        except OSError:
            pass


def _systemd_unit_contents(want_csqtt):
    """Содержимое systemd-служб (только чтение): want_csqtt=True — службы CSQTT,
    False — чужие службы. Чужие файлы не изменяются."""
    contents = []
    for base in CSQTT_UNIT_DIRS:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            if not name.endswith('.service'):
                continue
            path = Path(base) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                text = path.read_text(encoding='utf-8', errors='ignore')
            except OSError:
                continue
            if ('csqtt' in text.lower()) == want_csqtt:
                contents.append(text)
    return contents


def csqtt_unit_contents():
    """Содержимое systemd-служб, относящихся к CSQTT. Только чтение."""
    return _systemd_unit_contents(True)


def foreign_unit_contents():
    """Содержимое systemd-служб, не относящихся к CSQTT. Только чтение; нужно,
    чтобы обнаружить общий файл, используемый чужой службой."""
    return _systemd_unit_contents(False)


def shared_uninstall_blockers(paths=None, csqtt_contents=None, foreign_contents=None):
    """Препятствия к удалению из-за общих бинарников hev-socks5-*.

    Официальный deploy.sh uninstall удаляет эти имена безусловно (rm -f <имя>),
    поэтому сохранить чужой файл на всём протяжении удаления нельзя: схема
    «сохранить в памяти и вернуть после» теряет файл при аварийном завершении.
    Исключительную принадлежность CSQTT по внешним признакам установить нельзя —
    ссылка из службы CSQTT доказывает лишь использование, а не владение. Поэтому
    любой существующий общий путь считается препятствием, и удаление
    останавливается до внесения изменений.

    Возвращает список (путь, используется_чужой_службой, используется_CSQTT)."""
    if paths is None:
        paths = CSQTT_UNINSTALL_SHARED
    present = [str(Path(raw)) for raw in paths if os.path.lexists(str(Path(raw)))]
    if not present:
        return []
    csqtt_contents = csqtt_unit_contents() if csqtt_contents is None else list(csqtt_contents)
    foreign = foreign_unit_contents() if foreign_contents is None else list(foreign_contents)
    blockers = []
    for path in present:
        csqtt_refs = any(path in text for text in csqtt_contents)
        foreign_refs = any(path in text for text in foreign)
        blockers.append((path, foreign_refs, csqtt_refs))
    return blockers


def purge_work_dir_data(work_dir):
    """Полное удаление данных доступа CSQTT в рабочем каталоге (--purge).
    Удаляются только известные элементы CSQTT: credentials.json, access-*.txt и
    каталог prepared/ с секретами. Сам рабочий каталог и посторонние файлы не
    удаляются. Возвращает список удалённых путей."""
    removed = []
    work_dir = Path(work_dir)
    if work_dir.is_symlink() or not work_dir.is_dir():
        return removed
    snapshot = work_dir / 'credentials.json'
    if snapshot.is_file() and not snapshot.is_symlink():
        snapshot.unlink()
        removed.append(str(snapshot))
    for path in sorted(work_dir.glob('access-*.txt')):
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(str(path))
    prepared = work_dir / 'prepared'
    if prepared.is_dir() and not prepared.is_symlink():
        shutil.rmtree(prepared)
        removed.append(str(prepared))
    return removed


def run_uninstall_command(lock, source=None, apk=None, yes=False, purge=False,
                          config_dir=None, backup_dir=None, log_dir=None,
                          runner=None, tmp_base=None, work_dir=None):
    """Удаление установленного CSQTT Server вместе с веб-панелью.

    Останавливает и удаляет туннель и панель штатным deploy.sh uninstall. Перед
    удалением показывает состав изменений и требует подтверждения. По умолчанию
    настройки и сертификаты сохраняются в защищённой резервной копии 0700/0600;
    полное удаление данных — только с --purge и подтверждением (тогда удаляются
    /etc/csqtt и данные доступа в рабочем каталоге: credentials.json, access-*.txt
    и prepared/; сам рабочий каталог, журналы, server-addr, APK и резервные копии
    остаются). Общие бинарники hev-socks5-* официальный deploy.sh удаляет по имени
    безусловно; если они существуют, исключительную принадлежность CSQTT
    подтвердить нельзя, поэтому удаление останавливается до изменений. Чужие
    ресурсы (SSH, 3x-ui, Xray, nginx, общие сертификаты) не затрагиваются.
    Временные файлы (deploy.sh и скачанный APK) размещаются в приватном каталоге и
    убираются в любом случае, чтобы не сломать последующую установку."""
    scratch_paths = []
    try:
        return _run_uninstall_command(lock, scratch_paths, source=source, apk=apk,
                                      yes=yes, purge=purge, config_dir=config_dir,
                                      backup_dir=backup_dir, log_dir=log_dir,
                                      runner=runner, tmp_base=tmp_base, work_dir=work_dir)
    finally:
        _remove_scratch_paths(scratch_paths)


def _run_uninstall_command(lock, scratch_paths, source=None, apk=None, yes=False,
                           purge=False, config_dir=None, backup_dir=None,
                           log_dir=None, runner=None, tmp_base=None, work_dir=None):
    """Внутренняя часть удаления; временные пути складывает в scratch_paths."""
    if os.name != 'posix':
        raise ValidationError('Удаление выполняется только на целевом Linux-VPS.')
    if is_wsl() or is_openwrt():
        raise ValidationError('Удаление CSQTT нельзя выполнять в WSL или на OpenWrt.')
    if not (hasattr(os, 'geteuid') and os.geteuid() == 0):
        raise ValidationError('Для удаления требуются права root.')

    config_dir = Path(config_dir) if config_dir else Path(CSQTT_CONFIG_DIR)
    state = installation_state()
    if state == 'absent':
        print('CSQTT Server не установлен; удалять нечего. Изменения не выполнялись.')
        return None
    if state == 'incomplete':
        print('Обнаружена неполная или повреждённая установка CSQTT; состав изменений ниже.',
              file=sys.stderr)
    elif state == 'preserved':
        print('Найдены сохранённые настройки CSQTT (/etc/csqtt) без установленной службы; '
              'будут удалены только служебные файлы, если они есть.')

    tmp_base = Path(tmp_base) if tmp_base else Path('/tmp')
    if source:
        source = Path(os.path.abspath(source))
        check_source_bundle(source, lock, require_plan=True)
        deploy_path = source / 'deploy.sh'
        ports = load_plan(source)['ports']
    else:
        # Комплекта нет: официальный deploy.sh берётся из закреплённого APK
        # (--apk) или скачивается закреплённый APK, как в setup.
        tmp_base.mkdir(parents=True, exist_ok=True)
        # Приватный каталог: следы удаления не должны оставаться в /tmp под
        # фиксированным именем deploy.sh, иначе следующая установка остановится
        # на «во временном каталоге уже есть файл».
        work_tmp = Path(tempfile.mkdtemp(prefix='csqtt-uninstall-', dir=str(tmp_base)))
        scratch_paths.append(work_tmp)
        if apk:
            apk_path = Path(os.path.abspath(apk))
            read_bundle(apk_path, lock)
        else:
            apk_path = work_tmp / ('CSQTT-universal-' + lock['version'] + '.apk')
            fetch(lock, apk_path)
        payload = read_bundle(apk_path, lock)
        deploy_path = work_tmp / 'deploy.sh'
        fd = os.open(str(deploy_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(payload['assets/deploy.sh'])
        finally:
            os.close(fd)
        ports = dict(DEFAULT_PORTS)

    hits = audit_deploy_foreign_ops(deploy_path.read_bytes())
    if hits:
        raise ValidationError('В официальном deploy.sh обнаружены операции над чужими ресурсами: '
                              + '; '.join(hits) + '. Удаление отменено.')

    # Официальный deploy.sh uninstall удаляет общие бинарники hev-socks5-* по
    # имени безусловно (rm -f), поэтому убрать их временно и вернуть после
    # удаления нельзя: при аварийном завершении файл будет потерян. Ссылка из
    # службы CSQTT подтверждает лишь использование, а не исключительное владение.
    # Пока такой файл существует, удаление останавливается до изменений.
    blockers = shared_uninstall_blockers()
    if blockers:
        lines = ['Остановлено до изменений: общие бинарники hev-socks5-* существуют, а их '
                 'исключительная принадлежность CSQTT не подтверждена. Официальный '
                 'deploy.sh uninstall удаляет эти имена безусловно (rm -f), поэтому '
                 'сохранность файлов на протяжении удаления гарантировать нельзя:']
        for path, foreign_refs, csqtt_refs in blockers:
            if foreign_refs:
                note = 'используется чужой службой systemd'
            elif csqtt_refs:
                note = 'на него ссылается служба CSQTT (подтверждает использование, не владение)'
            else:
                note = 'ссылок в systemd-службах не найдено'
            lines.append('  - ' + path + ': ' + note)
        lines.append('Автоматическое удаление остановлено: обнаружены потенциально '
                     'общие файлы. Требуется отдельная проверка их использования; '
                     'файлы и службы не изменены.')
        raise ValidationError('\n'.join(lines))

    print('Удаление CSQTT Server. Будет остановлен туннель и веб-панель.')
    for line in describe_removal(purge):
        print(line)

    if not yes:
        if not sys.stdin.isatty():
            raise ValidationError('Нужно подтверждение: запустите с --yes или введите yes в терминале.')
        if input('Подтвердите удаление CSQTT (введите yes): ').strip().lower() != 'yes':
            raise ValidationError('Удаление отменено пользователем.')
    if purge:
        if not yes and input('Полное удаление данных CSQTT: введите purge: ').strip().lower() != 'purge':
            raise ValidationError('Полное удаление данных отменено.')

    access_files = []
    if work_dir:
        access_files = sorted(Path(work_dir).glob('access-*.txt'))
    # При полном удалении данных (--purge) резервная копия по умолчанию не
    # создаётся; её можно запросить явным --backup-dir.
    backup = None
    if not purge or backup_dir:
        if backup_dir:
            backup_root = Path(backup_dir)
        else:
            base = Path(work_dir) if work_dir else (tmp_base if tmp_base.name not in ('tmp',)
                                                    else Path('/root/csqtt-server-work'))
            backup_root = base / ('uninstall-backup-' + time.strftime('%Y%m%d-%H%M%S')
                                  + '-%06d' % int((time.time() % 1.0) * 1000000))
        backup = backup_csqtt_state(config_dir, backup_root, extra_files=access_files)

    log_dir = Path(log_dir) if log_dir else (Path(work_dir) if work_dir
                                             else (backup.parent if backup else tmp_base))
    if not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(log_dir, 0o700)
    if stat.S_IMODE(log_dir.stat().st_mode) & 0o077:
        raise ValidationError('Каталог журнала удаления должен иметь права 0700.')
    log_file = log_dir / ('uninstall-' + time.strftime('%Y%m%d-%H%M%S')
                          + '-%06d' % int((time.time() % 1.0) * 1000000) + '.log')

    env = dict(os.environ, CSQTT_DEPLOY_MODE='systemd',
               CSQTT_PEER_PORT=str(ports['peer']), CSQTT_SSH_PORT=str(ports['ssh']),
               CSQTT_WEB_PORT=str(ports['web']))
    cmd = ['bash', str(deploy_path), 'uninstall']

    print('Запуск официального удаления CSQTT ...')
    if runner is None:
        fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        _run_deploy_inplace(cmd, env, log_file)
    else:
        output_text = runner(cmd, env)
        fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(output_text.encode('utf-8', errors='replace'))
        finally:
            os.close(fd)

    purged_access = []
    if purge:
        if work_dir:
            purged_access = purge_work_dir_data(work_dir)
        _secure_rmtree(config_dir)

    leftover = [path for path in CSQTT_UNINSTALL_REMOVES if os.path.lexists(path)]
    active = service_is_active('csqtt')
    if leftover or active:
        print('Удаление завершено, но остались следы: '
              + (', '.join(leftover) if leftover else '')
              + ('; служба csqtt всё ещё активна' if active else '')
              + '. Проверьте журнал: ' + str(log_file), file=sys.stderr)
    else:
        print('CSQTT Server удалён: туннель и веб-панель остановлены и удалены.')
    if purge:
        print('Полное удаление данных выполнено: ' + str(config_dir))
        if work_dir:
            print('Данные доступа CSQTT удалены из рабочего каталога: '
                  + (', '.join(purged_access) if purged_access else 'не найдены'))
        else:
            print('Рабочий каталог не задан (--work-dir): credentials.json, access-файлы и '
                  'prepared/ в исходном каталоге не удалялись.', file=sys.stderr)
        print('Осталось (не удаляется автоматически): сам рабочий каталог без данных доступа '
              'CSQTT, журналы logs/, файл server-addr, закреплённый APK, резервные копии.')
    elif backup:
        print('Настройки и данные доступа сохранены в защищённой резервной копии: ' + str(backup))
    else:
        print('Настройки сохранены в ' + str(config_dir) + ' (резервная копия не требовалась).')
    print('Журнал удаления: ' + str(log_file))
    return backup


def precheck(source, lock):
    """Предварительная проверка без изменения системы. Возвращает список (статус, сообщение)."""
    source = Path(os.path.abspath(source))
    checks = []
    try:
        plan = check_source_bundle(source, lock, require_plan=True)
    except ValidationError as error:
        return [(False, 'комплект: ' + str(error))]
    arch = plan['architecture']
    ports = plan['ports']
    os_info = os_release_info()
    distro_id = os_info.get('ID', '').lower()
    distro_like = os_info.get('ID_LIKE', '').lower()
    supported = distro_id in ('ubuntu', 'debian') or 'debian' in distro_like.split()
    if arch != 'amd64':
        checks.append((False,
                       'архитектура комплекта ' + arch + ': установка ARM64/ARMv7 не проверена на тестовом VPS и не объявляется поддерживаемой'))
    checks.append((os.name == 'posix', 'необходима Linux-система'))
    checks.append((not is_wsl(), 'серверный deploy.sh нельзя запускать в WSL'))
    checks.append((not is_openwrt(), 'deploy.sh нельзя запускать на OpenWrt (меняет NAT/firewall шлюза)'))
    checks.append((platform.machine() == 'x86_64' and native_arch() == 'amd64',
                   'архитектура VPS x86-64'))
    checks.append((supported, 'дистрибутив Ubuntu/Debian' + (': ' + os_info.get('PRETTY_NAME', '') if os_info.get('PRETTY_NAME') else '')))
    checks.append((hasattr(os, 'geteuid') and os.geteuid() == 0, 'права root'))
    checks.append((systemd_present(), 'systemd является init (PID 1)'))
    checks.append((tun_present(), 'устройство /dev/net/tun доступно'))

    # Совместимость с уже установленными 3x-ui / Xray и другими службами.
    foreign = foreign_services()
    checks.append((True, 'чужие службы (3x-ui/Xray/nginx и др.): ' +
                   (', '.join(foreign) if foreign else 'не обнаружены') +
                   '; их работа не останавливается и конфигурация не изменяется'))

    # Официальный deploy.sh не должен затрагивать чужие правила firewall/службы.
    audit = audit_deploy_foreign_ops((source / 'deploy.sh').read_bytes())
    if audit:
        checks.append((False, 'в официальном deploy.sh обнаружены операции над чужими ресурсами: '
                       + '; '.join(audit) + '. Установка отменена до проверки установщика.'))
    else:
        checks.append((True, 'deploy.sh затрагивает только ресурсы CSQTT (маркеры CSQTT_MANAGED, интерфейс csqtt1)'))

    # Внутренние порты CSQTT (фиксированные локальные сокеты DPI/syscalls и
    # WG-порт) не должны совпадать с выбранными портами; их занятость чужими
    # службами учитывается. Набор не зависит от выбранного порта туннеля.
    internal = csqtt_internal_ports()
    selected_internal = sorted({ports['peer'], ports['web']} & internal)
    checks.append((not selected_internal,
                   'выбранные порты не совпадают с внутренними портами CSQTT'
                   + (': ' + ', '.join(map(str, selected_internal)) if selected_internal else '')))
    internal_busy = sorted(listening_ports() & (internal - {CSQTT_LE_HTTP_PORT}))
    checks.append((not internal_busy,
                   'внутренние порты CSQTT свободны'
                   + (': заняты ' + ', '.join(map(str, internal_busy)) if internal_busy else '')))

    tcp_busy = listening_ports() & set([ports['peer'], ports['web']])
    udp_busy = listening_udp_ports() & set([ports['peer'], ports['web']])
    busy = sorted(tcp_busy | udp_busy)
    if busy:
        proposal = ('; порты заняты; предложены свободные: peer=%d, web=%d (проверьте на VPS и переподготовьте при необходимости)'
                    % (propose_free_port(udp_busy | tcp_busy, ports['peer']),
                       propose_free_port(udp_busy | tcp_busy, ports['web'])))
    else:
        proposal = ''
    checks.append((not busy, 'порты туннеля(peer) и WEB не заняты' +
                   (': ' + ', '.join(map(str, busy)) if busy else '') + proposal))
    marks = installed_marks()
    state = installation_state(marks)
    if state == 'preserved':
        checks.append((True, 'найдены только сохранённые настройки CSQTT (/etc/csqtt) без '
                             'установленной службы: повторная установка разрешена, данные '
                             'доступа берутся из прежнего рабочего каталога или --credentials-file'))
    else:
        checks.append((state == 'absent', 'нет существующей установки CSQTT' +
                       (': ' + ', '.join(marks) if marks else '')))
    return checks


def deploy_console_lines(output):
    """Показывает на консоли только безусловно безопасные строки официального
    установщика: точные финальные маркеры и структурированные строки прогресса
    CSQTT_PROGRESS. Широкие префиксы ([OK], [✓], [►], ⚠ и т.п.) не считаются
    гарантией безопасности: под ними может идти токен или пароль. Вся сырая
    диагностика сохраняется в защищённый журнал 0600."""
    result = []
    for raw in output.splitlines():
        line = ANSI_ESCAPE.sub('', raw).strip()
        if not line:
            continue
        if line in CONSOLE_SAFE_EXACT:
            result.append(line)
            continue
        if line.startswith('CSQTT_PROGRESS|'):
            parts = line.split('|', 2)
            if len(parts) == 3:
                result.append('Этап: ' + parts[2])
            continue
        # Всё остальное (включая [OK], ✓, ⚠ и свободный текст) — только в журнал.
    return result


def _run_deploy_inplace(cmd, env, log_file):
    """Построчно читает вывод официального installer, пишет полный вывод в лог 0600,
    на консоль выводит только доверенные строки. Возвращает полный текст вывода."""
    full = []
    process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert log_file is not None
    try:
        with open(log_file, 'a', encoding='utf-8') as stream:
            stream.write('=== CSQTT install ' + time.strftime('%Y-%m-%d %H:%M:%S') + ' ===\n')
            for raw in iter(process.stdout.readline, ''):
                if not raw:
                    break
                stream.write(raw)
                full.append(raw)
                for line in deploy_console_lines(raw):
                    print(line)
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.SubprocessError:
            process.kill()
            process.wait()
        raise
    if process.returncode:
        raise subprocess.SubprocessError('официальный установщик завершился с кодом ' + str(process.returncode))
    return ''.join(full)


def run_install_command(source, lock, yes=False, log_dir=None, runner=None, tmp_base=None):
    """Основная последовательность установки на VPS."""
    lock = dict(lock)
    source = Path(os.path.abspath(source))
    plan = check_source_bundle(source, lock, require_plan=True)
    arch = plan['architecture']
    if arch != 'amd64':
        raise ValidationError('Установка поддерживает только amd64 на этом этапе; '
                              + arch + ' требует отдельного испытания на тестовом VPS.')
    if guard_existing_install() == 'complete':
        return None
    checks = precheck(source, lock)
    failed = [message for status, message in checks if not status]
    if failed:
        raise ValidationError('Предварительная проверка не пройдена: ' + '; '.join(failed) + '.')
    print('Предварительная проверка пройдена: чистая Ubuntu/Debian x86-64, root, systemd, TUN, порты свободны.')
    print('Серверный deploy.sh меняет NAT/firewall, sysctl и устанавливает службу systemd на этом VPS.')

    if not yes:
        if not sys.stdin.isatty():
            raise ValidationError('Нужно явное подтверждение: запустите с --yes или введите yes в терминале.')
        answer = input('Подтвердите установку на этом сервере (введите yes): ').strip().lower()
        if answer != 'yes':
            raise ValidationError('Установка отменена пользователем.')

    # Перед любыми изменениями проверяем ревизию протокола родного бинарника.
    verify_protocol_revision_safe(source)

    # Как и официальное приложение, копируем входные файлы в /tmp с фиксированными именами.
    tmp_base = Path(tmp_base) if tmp_base else Path('/tmp')
    tmp_base.mkdir(parents=True, exist_ok=True)
    upload_targets = {}
    for name in UPLOAD_NAMES_AND_MODES:
        target = tmp_base / name
        if os.path.lexists(str(target)):
            raise ValidationError('Во временном каталоге уже есть файл ' + str(target)
                                  + '; удалите его вручную и повторите.')
        upload_targets[name] = target
    log_file = None
    try:
        for name, mode in UPLOAD_NAMES_AND_MODES.items():
            source_path = source / name
            target = upload_targets[name]
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(source_path.read_bytes())
            finally:
                os.close(fd)
        env = dict(os.environ,
                   CSQTT_PEER_PORT=str(plan['ports']['peer']),
                   CSQTT_SSH_PORT=str(plan['ports']['ssh']),
                   CSQTT_WEB_PORT=str(plan['ports']['web']),
                   CSQTT_DEPLOY_MODE='systemd')
        log_dir = Path(log_dir) if log_dir else source.parent
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(log_dir, 0o700)
        if stat.S_IMODE(log_dir.stat().st_mode) & 0o077:
            raise ValidationError('Каталог журнала установки должен иметь права 0700.')
        log_file = log_dir / ('install-' + time.strftime('%Y%m%d-%H%M%S')
                      + '-%06d' % int((time.time() % 1.0) * 1000000) + '.log')
        cmd = ['bash', str(tmp_base / 'deploy.sh'), 'install']

        print('Запуск официального установщика CSQTT ' + lock['version'] + ' ...')
        if runner is None:
            fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            output_text = _run_deploy_inplace(cmd, env, log_file)
        else:
            output_text = runner(cmd, env)
            fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(output_text.encode('utf-8', errors='replace'))
            finally:
                os.close(fd)

        success = DEPLOY_MARKER_OK in output_text
        rolled_back = DEPLOY_MARKER_ROLLED_BACK in output_text
        rollback_failed = DEPLOY_MARKER_ROLLBACK_FAILED in output_text
        error_detail = None
        for line in reversed(output_text.splitlines()):
            if line.startswith(DEPLOY_MARKER_ERROR):
                error_detail = line.split('|', 2)[-1].strip()
                break
        if success:
            print('CSQTT_DEPLOY_OK: установка завершена успешно. Журнал установки: ' + str(log_file))
        elif rolled_back or rollback_failed:
            raise ValidationError('Установка не прошла проверку; автоматический откат не реализован'
                                  + ('. ' + error_detail if error_detail else ''))
        elif error_detail:
            raise ValidationError('Установщик сообщил об ошибке: ' + error_detail
                                  + '. Журнал установки: ' + str(log_file))
        else:
            raise ValidationError('Установщик завершился без маркера успеха. Журнал установки: ' + str(log_file))
        return log_file
    finally:
        for target in upload_targets.values():
            try:
                if target.is_file() and not target.is_symlink():
                    target.unlink()
            except OSError:
                pass


def read_web_env(source):
    with open_private(source / '.csqtt-upload-web.env') as stream:
        web_env = stream.read(32769).decode('utf-8', errors='replace')
    values = {}
    for line in web_env.splitlines():
        if line.startswith('CSQTT_WEB_USER='):
            values['web_user'] = line.split('=', 1)[1].strip().strip('"').replace('\\"', '"')
        elif line.startswith('CSQTT_WEB_PASS='):
            values['web_password'] = line.split('=', 1)[1].strip().strip('"').replace('\\"', '"')
    return values


def connection_info(source, lock, server_addr=None):
    """Печатает параметры подключения клиента и панели. Содержит пароли;
    вызывается только явно (connection-info)."""
    source = Path(os.path.abspath(source))
    plan = check_source_bundle(source, lock, require_plan=True)
    with open_private(source / '.csqtt-upload-overrides.json') as stream:
        overrides = json.load(stream)
    web_env = read_web_env(source)
    addr = server_addr or resolved_server_addr(source)
    print('Параметры подключения клиента CSQTT (не передавайте этот вывод третьим лицам):')
    print('  Сервер:       ' + addr + ':' + str(plan['ports']['peer']))
    print('  Пароль:       ' + overrides.get('main_password', ''))
    print('  device_id:    ' + overrides.get('device_id', ''))
    print('  Веб-панель:   https://' + addr + ':' + str(plan['ports']['web']) + '/')
    print('  Логин панели: ' + web_env.get('web_user', ''))
    print('  Пароль панели: ' + web_env.get('web_password', ''))
    return plan


def resolved_server_addr(source, server_addr=None):
    """Адрес VPS: переданный явно, сохранённый автосценарием рядом с комплектом
    (файл server-addr 0600 в рабочем каталоге 0700), иначе заглушка."""
    if server_addr:
        return server_addr
    sibling = Path(source).parent / 'server-addr'
    if sibling.is_file():
        try:
            with open_private(sibling) as stream:
                value = stream.read(256).decode('utf-8', errors='ignore').strip()
            if value:
                return value
        except (ValidationError, OSError):
            pass
    return '<IP-адрес или домен VPS>'


def detect_public_ipv4(timeout=5):
    """Определяет внешний IPv4 VPS (только чтение). Возвращает адрес или None."""
    for endpoint in ('https://api.ipify.org', 'https://ipv4.icanhazip.com'):
        try:
            with urllib.request.urlopen(endpoint, timeout=timeout) as response:
                text = response.read(64).decode('ascii', errors='ignore').strip()
            if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', text):
                return text
        except OSError:
            continue
    return None


def panel_https_status(port, timeout=5):
    """Read-only HTTPS-запрос к локальной веб-панели без входа (проверка отклика).
    Возвращает HTTP-код или None, если панель недоступна/ошибка соединения."""
    context = ssl._create_unverified_context()
    request = urllib.request.Request('https://127.0.0.1:%d/' % port)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, ValueError):
        return None


def caesar_encode(value):
    """Кодирование полей формы панели как в её клиентском JS (caesarEncode):
    каждый байт UTF-8 сдвигается на +47 (по модулю 256), результат переводится в
    base64 и получает префикс 'c1:'. Нужно только для локальной проверки входа на
    127.0.0.1; возвращаемое значение не журналируется."""
    shifted = bytes((byte + 47) & 0xFF for byte in value.encode('utf-8'))
    return 'c1:' + base64.b64encode(shifted).decode('ascii')


def panel_login_status(port, credentials, timeout=5):
    """Read-only проверка входа в локальную веб-панель её фактическим механизмом:
    POST /api/login с JSON-полями в кодировке caesarEncode и сессионной cookie,
    затем GET / с этой cookie. Возвращает True (вход подтверждён), False (панель
    отклонила созданные учётные данные) или None (панель недоступна/ошибка).
    Пароли не журналируются и не передаются в argv. Basic-auth панель не
    поддерживает, поэтому прежняя проверка давала ложный отказ."""
    if not credentials or not credentials.get('web_user'):
        return None
    context = ssl._create_unverified_context()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(jar))
    payload = json.dumps({
        'user': caesar_encode(credentials['web_user']),
        'pass': caesar_encode(credentials['web_password']),
    }).encode('utf-8')
    login_request = urllib.request.Request(
        'https://127.0.0.1:%d/api/login' % port, data=payload,
        headers={'Content-Type': 'application/json'})
    try:
        with opener.open(login_request, timeout=timeout) as response:
            if response.status != 200:
                return False
        panel_request = urllib.request.Request('https://127.0.0.1:%d/' % port)
        with opener.open(panel_request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code in (400, 401, 403, 429):
            return False
        return None
    except (OSError, ValueError):
        return None


def check_installed_service_and_panel(plan, credentials=None):
    """Read-only проверка после установки: служба active и панель отвечает по HTTPS.

    Возвращает {service, panel, login}: service/panel — None если проверить нельзя;
    login — текст о проверке входа (или что вход не проверялся автоматически)."""
    result = {'service': None, 'panel': None, 'login': ''}
    if shutil.which('systemctl'):
        try:
            probe = subprocess.run(['systemctl', 'is-active', 'csqtt'],
                                   capture_output=True, timeout=10, check=False)
            status = probe.stdout.decode(errors='replace').strip()
            # Точное сравнение: 'active' не должно совпадать с 'inactive'/'activating'.
            result['service'] = probe.returncode == 0 and status == 'active'
        except OSError:
            result['service'] = None
    plain = panel_https_status(plan['ports']['web'])
    result['panel'] = plain in (200, 301, 302, 401, 403) if plain is not None else None
    if result['panel'] and credentials and credentials.get('web_user'):
        login = panel_login_status(plan['ports']['web'], credentials)
        if login is True:
            result['login'] = 'вход с созданными учётными данными подтверждён (POST /api/login + сессия)'
        elif login is False:
            result['login'] = 'вход с созданными учётными данными не подтверждён (/api/login отклонил) — проверьте в браузере'
        else:
            result['login'] = 'вход с созданными учётными данными автоматически не проверялся — проверьте в браузере'
    else:
        result['login'] = 'вход с созданными учётными данными автоматически не проверялся — проверьте в браузере'
    return result


def build_access_info(plan, credentials, server_addr, verification, install_log=None):
    lines = [
        'Данные доступа CSQTT Server (секретно)',
        'Создано: ' + time.strftime('%Y-%m-%d %H:%M:%S'),
        '',
        'Веб-панель (HTTPS):',
        '  Адрес:  https://' + server_addr + ':' + str(plan['ports']['web']) + '/',
        '  Логин:  ' + credentials['web_user'],
        '  Пароль: ' + credentials['web_password'],
        '  Проверка службы: ' + ({True: 'ок', False: 'не подтверждена', None: 'не проверялась'}.get(verification.get('service'))),
        '  Проверка панели: ' + ({True: 'ок', False: 'не подтверждена', None: 'не проверялась'}.get(verification.get('panel'))),
        '  ' + (verification.get('login') or 'вход автоматически не проверялся'),
        '',
        'Подключение клиента (OpenWrt: LuCI \u2192 CSQTT, добавление профиля):',
        '  Сервер (Peer): ' + server_addr + ':' + str(plan['ports']['peer']),
        '  Пароль подключения: ' + credentials['main_password'],
        '  device_id: ' + credentials['device_id'],
        '',
        'Ссылка csqtt:// не выводится: формат импорта в этом выпуске не реализован и не проверен;',
        'параметры вводятся вручную (адрес, порт, пароль, device_id).',
    ]
    if install_log:
        lines.append('')
        lines.append('Журнал установки: ' + str(install_log))
    return '\n'.join(lines) + '\n'


def _print_access_console(addr, plan, credentials):
    """Печать данных доступа в интерактивном терминале после успешной установки."""
    print('=== Данные доступа CSQTT Server ===')
    print('Веб-панель (HTTPS): https://' + addr + ':' + str(plan['ports']['web']) + '/')
    print('Логин панели:       ' + credentials['web_user'])
    print('Пароль панели:      ' + credentials['web_password'])
    print('Сервер (Peer):      ' + addr + ':' + str(plan['ports']['peer']))
    print('Пароль туннеля:     ' + credentials['main_password'])
    print('device_id:          ' + credentials['device_id'])


def run_setup_command(lock, work_dir, apk=None, arch='amd64', credentials=None,
                      ports=None, yes=False, log_dir=None, runner=None, tmp_base=None,
                      server_addr=None, manual_credentials=False, prompt_ports=False):
    """Автоматический сценарий установки серверной части CSQTT без Android.

    Последовательность: проверка системы → проверка наличия установленного CSQTT
    (до генерации паролей) → выбор/проверка портов → fetch закреплённого APK →
    verify → prepare (генерация настроек через secrets или защищённый файл;
    защищённые файлы) → precheck → явное подтверждение → install → проверка
    службы/панели → интерактивный показ данных доступа и файл 0600 в каталоге 0700.

    Порты туннеля (UDP) и веб-панели (TCP) выбираются в интерактиве
    (`prompt_ports=True`), Enter сохраняет значения по умолчанию 46000/46002;
    проверяются диапазон 1–65535, дубли, внутренние порты CSQTT и занятость.
    При полной установке CSQTT повторный запуск сообщает об этом и ничего не
    меняет; неполная установка не удаляется и не переустанавливается автоматически.
    Если от прежней установки остались только сохранённые настройки /etc/csqtt
    (обычное удаление), это не считается повреждённой установкой: повторная
    установка разрешена, но новые пароли автоматически не создаются — данные
    доступа берутся из прежнего рабочего каталога (credentials.json) или из
    --credentials-file; без них установка останавливается до изменений.

    По умолчанию логин администратора и два независимых пароля генерируются
    через secrets; device_id берётся у существующего клиента и не рандомизируется.
    Сгенерированные данные сохраняются снапшотом 0600 в рабочем каталоге: при
    продолжении прерванного сценария те же данные переиспользуются, новые молча
    не создаются. Запускать на целевом чистом Ubuntu/Debian x86-64 от root.
    """
    if os.name != 'posix':
        raise ValidationError('Автосценарий выполняется только на целевом Linux-VPS.')
    os_info = os_release_info()
    distro_id = os_info.get('ID', '').lower()
    distro_like = os_info.get('ID_LIKE', '').lower()
    supported = distro_id in ('ubuntu', 'debian') or 'debian' in distro_like.split()
    env_checks = [
        (not is_wsl(), 'сценарий нельзя запускать в WSL'),
        (not is_openwrt(), 'сценарий нельзя запускать на OpenWrt'),
        (platform.machine() == 'x86_64', 'архитектура x86-64 (amd64)'),
        (supported, 'дистрибутив Ubuntu/Debian'),
        (hasattr(os, 'geteuid') and os.geteuid() == 0, 'права root'),
        (systemd_present(), 'systemd является init (PID 1)'),
        (tun_present(), 'устройство /dev/net/tun доступно'),
    ]
    failed = [message for status, message in env_checks if not status]
    if failed:
        raise ValidationError('Система не подходит для автосценария: ' + '; '.join(failed) + '.')

    work_dir = Path(os.path.abspath(work_dir))
    assert_no_link_parents(work_dir)

    # Наличие установленного CSQTT проверяется до генерации паролей и любых
    # изменений: полная установка — сообщение и выход без изменений; только
    # сохранённые настройки после обычного удаления — это не повреждённая
    # установка, но новые пароли автоматически не создаются: нужен существующий
    # файл данных доступа; неполная установка — понятный отказ без
    # автоматического удаления или переустановки.
    state = guard_existing_install()
    if state == 'complete':
        return None
    resumed = work_dir.exists()

    # После обычного удаления /etc/csqtt сохраняется, но штатная установка
    # (проверено на VPS) не оставляет там deploy-overrides.json, поэтому
    # рассчитывать на сохранённые в /etc/csqtt пароли нельзя. В новый рабочий
    # каталог без --credentials-file установка не продолжается и ничего не
    # создаёт: нужен существующий файл данных доступа. Проверка стоит до
    # создания рабочего каталога и любых изменений системы.
    if state == 'preserved' and not resumed \
            and credentials is None and not manual_credentials:
        raise ValidationError(
            'Сохранены настройки CSQTT (/etc/csqtt), но файла данных доступа нет. '
            'Новые пароли не создаются: повторите установку в прежний --work-dir '
            '(там сохранён credentials.json 0600) или передайте --credentials-file '
            'с существующим файлом данных доступа.')

    if not resumed and credentials is None and not manual_credentials and not sys.stdin.isatty():
        raise ValidationError('Для генерации данных доступа нужен терминал '
                              '(device_id существующего клиента) или --credentials-file.')
    prepared = work_dir / 'prepared'

    # Порты проверяются до создания рабочего каталога и изменений системы: в
    # интерактиве предлагаются туннель (UDP) и веб-панель (TCP), Enter сохраняет
    # значения по умолчанию; проверяются диапазон, дубли, внутренние порты CSQTT
    # и занятость. В неинтерактиве конфликт — понятная ошибка; чужие службы не
    # останавливаются.
    if not (resumed and prepared.is_dir()):
        selected = dict(ports) if ports else dict(DEFAULT_PORTS)
        if prompt_ports and sys.stdin.isatty():
            selected = choose_ports_interactive(selected)
        ports = validate_port_selection(selected)
        busy = busy_selected_ports(ports)
        if busy:
            raise ValidationError('Порты туннеля/панели уже заняты на этом VPS: '
                                  + ', '.join(str(value) for value in busy)
                                  + '. Выберите свободные порты (--peer-port/--web-port) '
                                    'и повторите; чужие службы не останавливаются.')

    if resumed:
        if not work_dir.is_dir() or stat.S_IMODE(work_dir.stat().st_mode) != 0o700:
            raise ValidationError('Рабочий каталог сценария должен быть каталогом 0700.')
        if not credentials_snapshot_path(work_dir).is_file():
            raise ValidationError('Рабочий каталог уже существует без снапшота данных доступа '
                                  '(credentials.json 0600); используйте новый --work-dir.')
        if credentials is not None:
            raise ValidationError('Каталог уже содержит данные доступа; не передавайте --credentials-file '
                                  'при возобновлении — используются сохранённые данные.')
        credentials = load_credentials_snapshot(work_dir)
    else:
        if work_dir.parent.exists() and not work_dir.parent.is_dir():
            raise ValidationError('На пути рабочего каталога не каталог: ' + str(work_dir.parent))
        # Создаём недостающие родительские каталоги (0700): команда из README
        # задаёт вложенный путь .../csqtt-server-work/setup на чистом VPS.
        for missing in reversed([path for path in work_dir.parents
                                 if path != path.parent and not path.exists()]):
            missing.mkdir(mode=0o700)
        work_dir.mkdir(mode=0o700)
        if stat.S_IMODE(work_dir.stat().st_mode) != 0o700:
            work_dir.rmdir()
            raise ValidationError('Нужна файловая система с POSIX-правами (например, ext4 в Linux).')

    if not resumed:
        if credentials is None:
            if manual_credentials:
                credentials = prompt_credentials()
            else:
                if not sys.stdin.isatty():
                    raise ValidationError('Для генерации данных доступа нужен терминал '
                                          '(device_id существующего клиента) или --credentials-file.')
                credentials = generated_credentials(request_device_id())
        save_credentials_snapshot(work_dir, credentials)

    if prepared.is_dir():
        plan = check_source_bundle(prepared, lock, require_plan=True)
    else:
        apk_path = Path(os.path.abspath(apk)) if apk else None
        if apk_path is None:
            apk_path = work_dir / ('CSQTT-universal-' + lock['version'] + '.apk')
            if not apk_path.is_file():
                fetch(lock, apk_path)
            else:
                read_bundle(apk_path, lock)
        read_bundle(apk_path, lock)
        ports = ports or dict(DEFAULT_PORTS)
        plan = prepare(apk_path, prepared, arch, credentials, ports, lock)

    checks = precheck(prepared, lock)
    failed = [message for status, message in checks if not status]
    if failed:
        raise ValidationError('Предварительная проверка не пройдена: ' + '; '.join(failed) + '.')

    if not yes:
        if not sys.stdin.isatty():
            raise ValidationError('Нужно явное подтверждение: запустите с --yes или введите yes в терминале.')
        answer = input('Подтвердите установку на этом сервере (введите yes): ').strip().lower()
        if answer != 'yes':
            raise ValidationError('Установка отменена пользователем.')

    logs = Path(log_dir) if log_dir else work_dir / 'logs'
    install_log = run_install_command(prepared, lock, yes=True, log_dir=logs,
                                      runner=runner, tmp_base=tmp_base)

    verification = check_installed_service_and_panel(plan, credentials)
    addr = server_addr or detect_public_ipv4() or '<IP-адрес или домен VPS>'
    access = work_dir / ('access-' + time.strftime('%Y%m%d-%H%M%S')
                     + '-%06d' % int((time.time() % 1.0) * 1000000) + '.txt')
    private_write(access, build_access_info(plan, credentials, addr, verification, install_log).encode('utf-8'))
    private_write_replace(work_dir / 'server-addr', (addr + '\n').encode('utf-8'), 0o600)

    if verification.get('service') and verification.get('panel'):
        print('Установка завершена: служба активна, веб-панель отвечает.')
        if sys.stdin.isatty():
            _print_access_console(addr, plan, credentials)
        login = verification.get('login')
        print(login or 'вход автоматически не проверялся — см. файл доступа.')
    else:
        print('Установка выполнена, но проверка службы/панели не подтвердила готовность;',
              file=sys.stderr)
        print('детали — в журнале: ' + str(install_log), file=sys.stderr)
    print('Данные доступа: ' + str(access))
    return access


def remote_bootstrap_command(work_dir=REMOTE_DEFAULT_WORK_DIR, commit=None):
    """Внутренняя команда установки на VPS: сохраняет файлы установщика из
    одного публичного коммита starkugz/csqtt-openwrt на диск, затем запускает
    штатный install-server.sh (он сам добавляет подкоманду setup) с доступным
    терминалом. Скрипт по stdin не передаётся, поэтому интерактивный ввод
    (пароль, подтверждение) не ломается. Команда не завершается через exec:
    последний шаг выполняется как обычная команда, после чего trap EXIT убирает
    временный каталог, а код завершения установщика сохраняется (set -eu).
    Возвращает одну строку команды shell (без внешнего форматирования).
    """
    commit = commit or REMOTE_DEFAULT_COMMIT
    if not commit:
        raise ValidationError('Не задан SHA-коммита для удалённой установки.')
    if commit != REMOTE_DEFAULT_COMMIT and not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValidationError('SHA-коммита должен быть 40-символьным шестнадцатеричным значением.')
    archive = 'https://github.com/%s/archive/%s.tar.gz' % (REMOTE_REPO, commit)
    return (
        'set -eu; '
        "command -v curl >/dev/null 2>&1 || { echo 'curl отсутствует' >&2; exit 1; }; "
        "command -v tar >/dev/null 2>&1 || { echo 'tar отсутствует' >&2; exit 1; }; "
        "command -v python3 >/dev/null 2>&1 || { echo 'python3 отсутствует' >&2; exit 1; }; "
        'd="$(mktemp -d)"; '
        "trap 'rm -rf \"$d\"' EXIT; "
        'curl -fsSL -o "$d/csqtt.tgz" "' + archive + '"; '
        'tar -xzf "$d/csqtt.tgz" -C "$d" --strip-components=1; '
        'cd "$d"; '
        './install-server.sh --work-dir "' + work_dir + '"'
    )


def remote_setup_vps_command(work_dir=REMOTE_DEFAULT_WORK_DIR, commit=None):
    """Вариант 1: одна команда, выполняемая на самом VPS после подключения по SSH."""
    return remote_bootstrap_command(work_dir=work_dir, commit=commit)


def remote_setup_ssh_command(user_host=REMOTE_DEFAULT_HOST,
                             work_dir=REMOTE_DEFAULT_WORK_DIR, commit=None):
    """Вариант 2: команда со своего компьютера. Подключается через ssh -t
    (выделенный терминал), проверка ключа SSH остаётся включённой, пароль
    вводится штатно. Запускает тот же установщик, что и вариант 1."""
    inner = remote_bootstrap_command(work_dir=work_dir, commit=commit)
    return 'ssh -t ' + user_host + ' -- ' + shlex.quote(inner)


def fetch(lock, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValidationError('Файл загрузки уже существует.')
    url = lock['apk_url']
    if not url.startswith('https://github.com/amurcanov/csqtt/releases/download/'):
        raise ValidationError('В lock-файле указан неожиданный источник.')
    request = urllib.request.Request(url, headers={'User-Agent': 'CSQTT-server-local-prototype'})
    partial = output.with_name(output.name + '.part')
    assert_no_link_parents(partial)
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream, urllib.request.urlopen(request, timeout=30) as response:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_APK:
                    raise ValidationError('Слишком большой ответ сервера загрузки.')
                stream.write(chunk)
        read_bundle(partial, lock)
        os.link(partial, output)
    finally:
        partial.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='CSQTT Server: подготовка и установка серверной части без Android. Установка VPS — только штатным deploy.sh на чистом Ubuntu/Debian x86-64.')
    commands = parser.add_subparsers(dest='command', required=True)
    get = commands.add_parser('fetch', help='скачать зафиксированный официальный APK')
    get.add_argument('--output', required=True)
    inspect = commands.add_parser('inspect', help='проверить APK, установщик и три серверные архитектуры')
    inspect.add_argument('--apk', required=True)
    prep = commands.add_parser('prepare', help='подготовить серверные файлы; ничего не устанавливать')
    prep.add_argument('--apk', required=True)
    prep.add_argument('--arch', choices=ARCHES, required=True)
    prep.add_argument('--output', required=True)
    prep.add_argument('--credentials-file', help='существующий JSON с правами 0600; значения не передавать аргументами')
    prep.add_argument('--peer-port', default=46000, type=int)
    prep.add_argument('--ssh-port', default=22, type=int)
    prep.add_argument('--web-port', default=46002, type=int)
    runtime = commands.add_parser('check-runtime', help='запустить только --protocol-revision, без сервера')
    runtime.add_argument('--output', required=True)
    precheck_cmd = commands.add_parser('precheck', help='предварительная проверка VPS и комплекта без изменения системы')
    precheck_cmd.add_argument('--source', required=True)
    install_cmd = commands.add_parser('install', help='установить на этот чистый Ubuntu/Debian x86-64 через официальный deploy.sh')
    install_cmd.add_argument('--source', required=True)
    install_cmd.add_argument('--yes', action='store_true', help='подтвердить установку без интерактивного ввода')
    install_cmd.add_argument('--log-dir', help='каталог для защищённого журнала установки')
    conn_cmd = commands.add_parser('connection-info', help='показать параметры подключения клиента и панели (пароли; только явно)')
    conn_cmd.add_argument('--source', required=True)
    conn_cmd.add_argument('--server-addr', help='адрес VPS, если не сохранён автосценарием')
    setup_cmd = commands.add_parser('setup', help='автосценарий установки на чистом Ubuntu/Debian x86-64: система → fetch → verify → prepare → precheck → подтверждение → install → проверка службы/панели → файл доступа 0600')
    setup_cmd.add_argument('--work-dir', required=True, help='новый рабочий каталог 0700 (создаётся)')
    setup_cmd.add_argument('--apk', help='заранее скачанный закреплённый APK (иначе fetch)')
    setup_cmd.add_argument('--arch', choices=ARCHES, default='amd64')
    setup_cmd.add_argument('--credentials-file', help='существующий JSON с правами 0600; значения не передавать аргументами')
    setup_cmd.add_argument('--manual-credentials', action='store_true',
                           help='ввести свои значения через скрытый ввод вместо генерации через secrets')
    setup_cmd.add_argument('--peer-port', default=46000, type=int)
    setup_cmd.add_argument('--ssh-port', default=22, type=int)
    setup_cmd.add_argument('--web-port', default=46002, type=int)
    setup_cmd.add_argument('--yes', action='store_true', help='подтвердить установку без интерактивного ввода')
    setup_cmd.add_argument('--log-dir', help='каталог для защищённого журнала установки')
    setup_cmd.add_argument('--server-addr', help='внешний IP/домен VPS для адреса панели (иначе определяется автоматически)')
    uninstall_cmd = commands.add_parser('uninstall', help='удалить установленный CSQTT Server вместе с веб-панелью (останавливает туннель); настройки по умолчанию сохраняются в защищённой резервной копии')
    uninstall_cmd.add_argument('--source', help='подготовленный комплект с официальным deploy.sh закреплённого выпуска')
    uninstall_cmd.add_argument('--apk', help='закреплённый APK с официальным deploy.sh (иначе --source или автоскачивание закреплённого APK)')
    uninstall_cmd.add_argument('--yes', action='store_true', help='подтвердить удаление без интерактивного ввода')
    uninstall_cmd.add_argument('--purge', action='store_true', help='полностью удалить данные CSQTT (по умолчанию настройки, база и сертификаты сохраняются)')
    uninstall_cmd.add_argument('--backup-dir', help='каталог защищённой резервной копии настроек (0700/0600)')
    uninstall_cmd.add_argument('--work-dir', help='рабочий каталог установщика: оттуда сохраняются данные доступа')
    uninstall_cmd.add_argument('--log-dir', help='каталог для защищённого журнала удаления')
    args = parser.parse_args(argv)
    lock = load_lock()
    try:
        if args.command == 'fetch':
            fetch(lock, args.output)
            print('Официальный APK скачан; контрольные суммы и компоненты проверены.')
        elif args.command == 'inspect':
            read_bundle(args.apk, lock)
            print('PASS: APK, deploy.sh, ELF amd64/arm64/armv7; CSQTT ' + lock['version'])
        elif args.command == 'prepare':
            credentials = read_credentials(args.credentials_file) if args.credentials_file else prompt_credentials()
            plan = prepare(args.apk, args.output, args.arch, credentials,
                           dict(peer=args.peer_port, ssh=args.ssh_port, web=args.web_port), lock)
            print('Комплект подготовлен для ' + plan['architecture'] + '. Секреты записаны с правами 0600.')
            print('Сервер не запускался; требуется отдельное испытание на тестовом VPS.')
        elif args.command == 'check-runtime':
            check_runtime(args.output, lock)
            print('PASS: серверный бинарник подтвердил ревизию протокола. Служба не запускалась.')
        elif args.command == 'precheck':
            checks = precheck(args.source, lock)
            for status, message in checks:
                print(('PASS' if status else 'FAIL') + ': ' + message)
            if not all(status for status, _ in checks):
                print('Предварительная проверка не пройдена; установка не выполнялась.', file=sys.stderr)
                return 1
            print('PASS: VPS готов к установке; deploy.sh не запускался.')
        elif args.command == 'install':
            run_install_command(args.source, lock, yes=args.yes, log_dir=args.log_dir)
        elif args.command == 'setup':
            credentials = read_setup_credentials(args.credentials_file) if args.credentials_file else None
            ports = dict(peer=args.peer_port, ssh=args.ssh_port, web=args.web_port)
            access = run_setup_command(lock, args.work_dir, apk=args.apk, arch=args.arch,
                                       credentials=credentials, ports=ports, yes=args.yes,
                                       log_dir=args.log_dir, server_addr=args.server_addr,
                                       manual_credentials=args.manual_credentials,
                                       prompt_ports=sys.stdin.isatty())
            if access is None:
                return 0
            print('Автосценарий завершён; данные доступа: ' + str(access))
        elif args.command == 'uninstall':
            run_uninstall_command(lock, source=args.source, apk=args.apk, yes=args.yes,
                                  purge=args.purge, backup_dir=args.backup_dir,
                                  work_dir=args.work_dir, log_dir=args.log_dir)
        else:
            plan = connection_info(args.source, lock,
                                   server_addr=getattr(args, 'server_addr', None))
            print('Эти данные при установке в общий журнал не записываются.')
        return 0
    except (ValidationError, OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError):
        # Never echo exceptions: upstream/filesystem errors can contain private values.
        print('Ошибка: подготовка, проверка или установка не прошла. Секреты и подробности скрыты; журнал установки — в защищённом файле 0600.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
