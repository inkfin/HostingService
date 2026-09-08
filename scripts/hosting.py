#!/usr/bin/env python3
"""Personal VPS service launcher. Python standard library only."""
import argparse
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reliability

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('website', 'gitea', 'teamspeak', 'uptime-kuma', 'it-tools')
PROFILES = (*SERVICES, 'china', 'overseas')


@contextmanager
def backup_lock():
    directory = ROOT / 'backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / '.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('已有备份或上传正在运行，请稍后再试。')
        yield


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def restic_env():
    if not shutil.which('restic'):
        raise ValueError('请先安装 restic，见 docs/object-storage.md。')
    path = ROOT / 'runtime/restic.json'
    if not path.is_file():
        raise ValueError('缺少 runtime/restic.json，请按 docs/object-storage.md 配置对象存储。')
    config = json.loads(path.read_text())
    repository = config.get('repository', '')
    password_file = ROOT / config.get('password_file', 'runtime/restic-password')
    if not repository or 'REPLACE' in repository:
        raise ValueError('请填写实际的 restic repository。')
    if not password_file.is_file() or not password_file.read_text().strip():
        raise ValueError('缺少非空 restic 密码文件；已有云备份必须使用原密码，不要重新生成。')
    for secret_path in (path, password_file):
        if secret_path.stat().st_mode & 0o077:
            raise ValueError(f'请执行 chmod 600 {secret_path} 保护备份凭据。')
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('RESTIC_', 'AWS_'))}
    allowed = {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_DEFAULT_REGION', 'AWS_SESSION_TOKEN'}
    for key, value in config.get('env', {}).items():
        if key not in allowed or not isinstance(value, str) or 'REPLACE' in value:
            raise ValueError('restic env 只允许已填写的 S3 访问密钥、region 和可选 session token。')
        environment[key] = value
    environment['RESTIC_REPOSITORY'] = repository
    environment['RESTIC_PASSWORD_FILE'] = str(password_file)
    environment['RESTIC_CACHE_DIR'] = str(ROOT / 'backups/.restic-cache')
    return environment


def restic(*args, environment=None, **kwargs):
    return subprocess.run(['restic', *args], cwd=ROOT,
                          env=environment if environment is not None else restic_env(),
                          check=True, **kwargs)


def upload_archive(archive, environment):
    archive = archive.resolve()
    if archive.parent != (ROOT / 'backups').resolve() or not archive.name.endswith('.tar.gz'):
        raise ValueError('只能上传本仓库 backups/ 内的 .tar.gz 归档。')
    checksum = archive.with_name(archive.name + '.sha256')
    expected = f'{sha256_file(archive)}  {archive.name}'
    if not checksum.is_file() or checksum.read_text().strip() != expected:
        raise ValueError('归档 SHA-256 校验不匹配或校验文件缺失，拒绝上传。')
    # Relative paths keep restored files independent of the old VPS checkout path.
    subprocess.run(['restic', 'backup', '--tag', 'hostingservice', '--', archive.name, checksum.name],
                   cwd=archive.parent, env=environment, check=True)
    reliability.update_health(ROOT, last_remote_success=reliability.now(),
                              archive=archive.name, last_error=None)
    print('加密上传完成。请用 ./hosting remote snapshots 查看快照 ID，并定期演练恢复。')


def remote_command(args):
    environment = restic_env()
    if args.action == 'upload':
        with backup_lock():
            upload_archive(Path(args.archive), environment)
    elif args.action == 'restore':
        if not re.fullmatch(r'[0-9a-fA-F]{8,64}', args.snapshot):
            raise ValueError('请使用 snapshots 中明确的快照 ID，避免误恢复其他服务器的 latest。')
        target = Path(args.target).expanduser().resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError('恢复目录必须为空或不存在，不能直接覆盖现有服务。')
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        restic('restore', args.snapshot, '--target', str(target), environment=environment)
        print(f'归档已下载到 {target}。先检查 .sha256，再按迁移指南解压；尚未启动或覆盖服务。')
    elif args.action == 'check':
        restic('check', *(['--read-data'] if args.read_data else []), environment=environment)
        reliability.update_health(ROOT, last_repository_check=reliability.now())
    else:
        restic(args.action, *(['--tag', 'hostingservice'] if args.action == 'snapshots' else []),
               environment=environment)


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(content)
    path.chmod(0o600)


def notify_backup(message):
    path = ROOT / 'runtime/backup-alert.json'
    if not path.is_file():
        print('未配置备份通知，详见 docs/operations.md。', file=sys.stderr)
        return False
    try:
        if path.stat().st_mode & 0o077:
            raise ValueError('通知配置权限必须为 600。')
        url = json.loads(path.read_text())['webhook_url']
        if not url.startswith('https://') or 'REPLACE' in url:
            raise ValueError('需要实际 HTTPS webhook URL。')
        request = urllib.request.Request(url, data=json.dumps({'text': message}).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ValueError('通知服务未接受请求。')
        return True
    except Exception as exc:
        # Do not leak URL credentials in exception strings.
        print('备份通知发送失败: ' + type(exc).__name__, file=sys.stderr)
        return False


def host(value):
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if len(value) <= 253 and re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', value):
            return value.lower()
        raise ValueError('请输入裸域名或 IP，不含协议、端口、空格。')


def env_read():
    path = ROOT / '.env'
    if not path.exists():
        raise ValueError('请先执行 ./hosting init')
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)


def compose(*args, capture=False, input=None):
    return run(['docker', 'compose', '--project-directory', str(ROOT),
                '--env-file', str(ROOT / '.env'), '-f', str(ROOT / 'compose.yaml'), *args],
               capture_output=capture, text=True, input=input)


def configure_backup():
    target = ROOT / 'runtime/restic.json'
    if target.exists():
        raise ValueError('备份配置已存在，不覆盖；请编辑 runtime/restic.json。')
    repository = input('S3 repository（s3:https://endpoint/bucket/prefix）: ').strip()
    if not repository.startswith('s3:https://') or 'REPLACE' in repository:
        raise ValueError('需要实际的 S3 HTTPS repository。')
    access = getpass.getpass('S3 access key（隐藏）: ').strip()
    secret = getpass.getpass('S3 secret key（隐藏）: ').strip()
    region = input('S3 region（按厂商要求，可留空）: ').strip()
    webhook = getpass.getpass('备份告警 HTTPS webhook（隐藏，接收 JSON text）: ').strip()
    if not access or not secret or not webhook.startswith('https://'):
        raise ValueError('访问密钥或告警 webhook 缺失。')
    password_path = ROOT / 'runtime/restic-password'
    if not password_path.exists():
        existing = input('连接已有 restic 备份库？yes/no: ').strip()
        if existing not in ('yes', 'no'):
            raise ValueError('请输入 yes 或 no。')
        password = getpass.getpass('原备份库密码（隐藏）: ') if existing == 'yes' else secrets.token_urlsafe(48)
        if not password:
            raise ValueError('备份密码不能为空。')
        private_write(password_path, password + '\n')
    environment = {'AWS_ACCESS_KEY_ID': access, 'AWS_SECRET_ACCESS_KEY': secret}
    if region:
        environment['AWS_DEFAULT_REGION'] = region
    private_write(target, json.dumps({'repository': repository, 'password_file': 'runtime/restic-password',
                                     'env': environment}, indent=2) + '\n')
    private_write(ROOT / 'runtime/backup-alert.json', json.dumps({'webhook_url': webhook}) + '\n')
    print('配置已保存。请将 runtime/restic-password 另存密码管理器，再测试备份库和通知。')


def gitea_admin(args):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,38}', args.username) or '@' not in args.email:
        raise ValueError('需要有效管理员用户名和 email。')
    users = compose('exec', '-T', '--user', '1000', 'gitea', 'gitea', 'admin', 'user', 'list', capture=True)
    if any(len(row.split()) > 1 and row.split()[1] == args.username for row in users.stdout.splitlines()):
        raise ValueError('同名账号已存在，未修改密码或权限。请检查其管理员状态。')
    secret_path = ROOT / 'runtime/gitea-admin.txt'
    if secret_path.exists():
        raise ValueError('初始凭据文件已存在，不覆盖；请核验先前创建结果。')
    password = secrets.token_urlsafe(32)
    # Save before creation so a network interruption cannot lose the generated credential.
    private_write(secret_path, f'Username: {args.username}\nEmail: {args.email}\nPassword: {password}\n')
    compose('exec', '-T', '--user', '1000', 'gitea', 'sh', '-c',
            'IFS= read -r password; exec gitea admin user create --admin --username "$1" --email "$2" --password "$password" --must-change-password',
            'sh', args.username, args.email, input=password + '\n', capture=True)
    print('Gitea 管理员已创建，初始密码保存在 runtime/gitea-admin.txt，请安全保存并首次登录更改。')


def doctor():
    checks = {name: bool(shutil.which(name)) for name in ('docker', 'python3', 'openssl', 'git', 'restic', 'systemctl')}
    checks['linux'] = sys.platform == 'linux'
    checks['architecture'] = platform.machine()
    checks['disk_free_gib'] = round(shutil.disk_usage(ROOT).free / 1024**3, 2)
    for key, cmd in [('docker_daemon', ['docker', 'info']), ('compose', ['docker', 'compose', 'version'])]:
        try:
            checks[key] = subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            checks[key] = False
    print(json.dumps(checks, indent=2))
    if not all(checks[key] for key in ('linux', 'docker', 'python3', 'openssl', 'git', 'restic', 'systemctl', 'docker_daemon', 'compose')):
        raise ValueError('部署依赖尚不完整，请先修复 false 项。')


def generate_proxy(mode, server, subscription=None):
    runtime = ROOT / 'runtime'
    if mode == 'china':
        target = runtime / 'mihomo.yaml'
        if target.exists():
            raise ValueError('Mihomo 配置已存在，保留原文件；请手动编辑 runtime/mihomo.yaml。')
        if not subscription or not subscription.startswith('https://'):
            raise ValueError('国内模式需要 HTTPS 的 Clash/Mihomo 格式订阅地址。')
        password = secrets.token_urlsafe(32)
        # JSON is valid YAML and safely escapes untrusted subscription URLs.
        config = {'mixed-port': 7890, 'allow-lan': True, 'bind-address': '*',
                  'mode': 'rule', 'log-level': 'info', 'authentication': ['personal:' + password],
                  'proxy-providers': {'upstream': {'type': 'http', 'url': subscription,
                      'path': './providers/upstream.yaml', 'interval': 3600}},
                  'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'use': ['upstream']}],
                  'rules': ['MATCH,PROXY']}
        private_write(target, json.dumps(config, indent=2) + '\n')
        private_write(runtime / 'mihomo-access.txt',
                      f'HTTP / SOCKS5: 127.0.0.1:7890\nUsername: personal\nPassword: {password}\n')
    elif mode == 'overseas':
        directory = runtime / 'hysteria'
        if directory.exists():
            raise ValueError('Hysteria 配置已存在，保留原证书和密码；请手动编辑 runtime/hysteria。')
        if not shutil.which('openssl'):
            raise ValueError('海外模式需要 openssl。')
        directory.mkdir(parents=True, mode=0o700)
        password = secrets.token_urlsafe(32)
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes',
             '-keyout', str(directory / 'server.key'), '-out', str(directory / 'server.crt'),
             '-days', '3650', '-subj', '/CN=personal-proxy'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        (directory / 'server.key').chmod(0o600)
        fingerprint = run(['openssl', 'x509', '-in', str(directory / 'server.crt'),
                           '-noout', '-fingerprint', '-sha256'], capture_output=True, text=True)
        pin = fingerprint.stdout.strip().split('=', 1)[1]
        private_write(directory / 'config.json', json.dumps({
            'listen': ':8443', 'tls': {'cert': '/etc/hysteria/server.crt', 'key': '/etc/hysteria/server.key'},
            'auth': {'type': 'password', 'password': password}}, indent=2) + '\n')
        address = f'[{server}]' if ':' in server else server
        private_write(runtime / 'hysteria-client.yaml', json.dumps({
            'server': f'{address}:8443', 'auth': password,
            'tls': {'sni': 'personal-proxy', 'insecure': True, 'pinSHA256': pin},
            'socks5': {'listen': '127.0.0.1:1080'},
            'http': {'listen': '127.0.0.1:8081'}}, indent=2) + '\n')


def initialize(args):
    if (ROOT / '.env').exists():
        raise ValueError('.env 已存在，初始化不会覆盖已有部署；直接执行 ./hosting up。')
    selected = args.services.split(',') if args.services else input(
        '服务 [website,gitea,uptime-kuma,it-tools]，可加 teamspeak: ').strip().split(',')
    if selected == ['']:
        selected = ['website', 'gitea', 'uptime-kuma', 'it-tools']
    selected = list(dict.fromkeys(s.strip() for s in selected))
    if not selected or set(selected) - set(SERVICES):
        raise ValueError('服务只能是 ' + ','.join(SERVICES))
    mode = args.mode or input('代理模式 none / china / overseas [none]: ').strip() or 'none'
    if mode not in ('none', 'china', 'overseas'):
        raise ValueError('未知代理模式。')
    server = host(args.host or input('VPS 公网 IP 或域名 [localhost]: ').strip() or 'localhost')
    if mode == 'overseas' and server == 'localhost':
        raise ValueError('海外模式需要客户端能够访问的公网 IP 或域名。')
    domain = host(args.domain) if args.domain else None
    if domain:
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            if '.' not in domain:
                raise ValueError('--domain 需要真实完整域名。')
        else:
            raise ValueError('--domain 需要指向此 VPS 的域名。')
    if 'teamspeak' in selected:
        if platform.machine().lower() not in ('x86_64', 'amd64'):
            raise ValueError('TeamSpeak 官方镜像仅支持 amd64；请移除 teamspeak 或使用 x86_64 VPS。')
        if not args.accept_teamspeak_license:
            answer = input('阅读 https://www.teamspeak.com/en/privacy-and-terms/ 后，接受 TeamSpeak 许可？输入 accept: ')
            if answer != 'accept':
                raise ValueError('未接受 TeamSpeak 许可。')
    subscription = None
    if mode == 'china':
        subscription = getpass.getpass('Clash/Mihomo HTTPS 订阅 URL（输入隐藏）: ')
    (ROOT / 'runtime').mkdir(mode=0o700, exist_ok=True)
    generate_proxy(mode, server, subscription)
    if mode != 'none':
        selected.append(mode)
    # The Gitea web UI is accessed through an SSH tunnel until explicitly published.
    values = {'COMPOSE_PROFILES': ','.join(selected), 'SERVER_HOST': server,
              'GITEA_ROOT_URL': 'http://localhost:3000/', 'GITEA_SSH_PORT': '2222',
              'ADMIN_BIND': '127.0.0.1', 'WEB_BIND': '0.0.0.0', 'HTTP_PORT': '80',
              'HTTPS_PORT': '443', 'HYSTERIA_PORT': '8443',
              'TS3SERVER_LICENSE': 'accept' if 'teamspeak' in selected else 'view'}
    site = domain or ':80'
    private_write(ROOT / 'runtime' / 'Caddyfile',
                  f'{site} {{\n    root * /srv\n    encode zstd gzip\n    file_server\n}}\n')
    private_write(ROOT / '.env', ''.join(f'{k}={v}\n' for k, v in values.items()))
    print('已生成配置。执行 ./hosting up 启动；runtime/ 包含私密凭据，不要提交。')


def validate():
    values = env_read()
    selected = values.get('COMPOSE_PROFILES', '').split(',')
    if not selected or set(selected) - set(PROFILES):
        raise ValueError('COMPOSE_PROFILES 包含未知服务或为空。')
    if 'china' in selected and 'overseas' in selected:
        raise ValueError('每台 VPS 选择 china 或 overseas 之一。')
    files = {'website': 'Caddyfile', 'china': 'mihomo.yaml', 'overseas': 'hysteria/config.json'}
    for profile, filename in files.items():
        if profile in selected and not (ROOT / 'runtime' / filename).is_file():
            raise ValueError(f'缺少 runtime/{filename}，请先初始化对应服务。')
    if 'teamspeak' in selected:
        if values.get('TS3SERVER_LICENSE') != 'accept':
            raise ValueError('TeamSpeak 需要 TS3SERVER_LICENSE=accept。')
        if platform.machine().lower() not in ('x86_64', 'amd64'):
            raise ValueError('TeamSpeak 官方镜像仅支持 amd64。')
    return selected


def backup(keep_stopped=False):
    selected = validate()
    running = compose('ps', '--status', 'running', '--services', capture=True).stdout.split()
    directory = ROOT / 'backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    archive = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
    checksum = archive.with_name(archive.name + '.sha256')
    completed = False
    try:
        if running:
            compose('stop', '--timeout', '120', *running)
        gitea_report = None
        if 'gitea' in selected:
            ids = compose('ps', '--all', '--quiet', 'gitea', capture=True).stdout.split()
            if ids:
                states = json.loads(run(['docker', 'inspect', *ids], capture_output=True, text=True).stdout)
                for container in states:
                    state = container['State']
                    if state.get('Running') or state.get('OOMKilled') or state.get('ExitCode') not in (0, 143):
                        raise ValueError('Gitea 未正常停止，拒绝标记为一致性备份。')
                    mounts = [m for m in container['Mounts'] if m['Destination'] == '/data']
                    if len(mounts) != 1 or mounts[0]['Type'] != 'bind' or Path(mounts[0]['Source']).resolve() != (ROOT / 'data/gitea').resolve():
                        raise ValueError('Gitea 实际 /data 挂载不在本仓库，当前备份不完整。')
                    if any(m['Destination'].startswith('/data/') for m in container['Mounts']):
                        raise ValueError('Gitea /data 内存在额外挂载，需单独确认备份覆盖。')
            gitea_report = reliability.verify_gitea(ROOT)
        private_write(ROOT / 'runtime/backup-manifest.json', json.dumps({
            'created_at': reliability.now(), 'profiles': selected, 'gitea': gitea_report,
            'consistency': 'services-stopped', 'restored_application_test': 'not-performed'
        }, indent=2) + '\n')
        paths = [p for p in ('.env', 'runtime', 'data', 'website', 'compose.yaml',
                            'hosting', 'scripts', 'config') if (ROOT / p).exists()]
        run(['tar', '-czf', str(archive), *paths])
        archive.chmod(0o600)
        # Read the entire archive before calling it a usable backup.
        run(['tar', '-tzf', str(archive)], stdout=subprocess.DEVNULL)
        private_write(checksum, f'{sha256_file(archive)}  {archive.name}\n')
        completed = True
        print(f'备份完成: {archive}，包含密码及私钥，请离机保管。')
        print(f'校验文件: {checksum}')
        if keep_stopped and running:
            print('迁移模式：原服务保持停止，避免新数据写入。不要在旧机执行 up。')
            print('若取消迁移，可执行 ./hosting compose start ' + ' '.join(running))
    except BaseException:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
        raise
    finally:
        if running and not (keep_stopped and completed):
            compose('start', *running)
    return archive


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='个人 VPS 服务管理')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='首次交互式配置，不覆盖已有配置')
    init.add_argument('--services', help=','.join(SERVICES))
    init.add_argument('--mode', choices=['none', 'china', 'overseas'])
    init.add_argument('--host')
    init.add_argument('--domain', help='可选，网站自动 HTTPS 域名，需提前解析')
    init.add_argument('--accept-teamspeak-license', action='store_true')
    proxy = sub.add_parser('proxy-init', help='为已有部署生成代理配置，随后手动添加 profile')
    proxy.add_argument('mode', choices=['china', 'overseas'])
    for command in ('up', 'down', 'check', 'status'):
        sub.add_parser(command)
    backup_parser = sub.add_parser('backup', help='停服备份、读取校验并生成 SHA-256 校验文件')
    backup_parser.add_argument('--keep-stopped', action='store_true',
                               help='用于换机：备份成功后保持服务停止；失败时仍尝试恢复')
    backup_parser.add_argument('--remote', action='store_true', help='用 restic 加密上传到配置的对象存储')
    remote = sub.add_parser('remote', help='管理加密异地备份，不会自动删除历史快照')
    actions = remote.add_subparsers(dest='action', required=True)
    actions.add_parser('init', help='仅首次创建 restic 备份仓库时使用')
    actions.add_parser('snapshots', help='查看备份快照 ID')
    remote_check = actions.add_parser('check', help='检查备份仓库结构')
    remote_check.add_argument('--read-data', action='store_true', help='完整读取远端数据校验，会产生下载流量')
    upload = actions.add_parser('upload', help='重试上传已生成的本地备份，不停服')
    upload.add_argument('--archive', required=True)
    restore = actions.add_parser('restore', help='下载某个快照到空目录，不覆盖线上数据')
    restore.add_argument('--snapshot', required=True)
    restore.add_argument('--target', required=True)
    schedule = sub.add_parser('schedule', help='服务器 systemd 定时备份')
    schedule.add_argument('args', nargs=argparse.REMAINDER)
    health_parser = sub.add_parser('backup-health', help='检查最近一次异地备份是否成功且未过期')
    health_parser.add_argument('--max-age-hours', type=float, default=26)
    health_parser.add_argument('--notify', action='store_true')
    sub.add_parser('alert-test', help='向已配置的备份通知地址发送测试通知')
    sub.add_parser('configure-backup', help='隐藏输入对象存储密钥并生成备份配置')
    sub.add_parser('doctor', help='检查部署所需依赖，缺失时返回非零')
    admin = sub.add_parser('gitea-admin', help='创建初始管理员，随机密码保存到私有文件')
    admin.add_argument('--username', required=True)
    admin.add_argument('--email', required=True)
    logs = sub.add_parser('logs')
    logs.add_argument('service', nargs='?')
    raw = sub.add_parser('compose', help='透传 Docker Compose 参数')
    raw.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args)
    elif args.command == 'configure-backup':
        configure_backup()
    elif args.command == 'gitea-admin':
        gitea_admin(args)
    elif args.command == 'doctor':
        doctor()
    elif args.command == 'proxy-init':
        server = host(env_read()['SERVER_HOST'])
        if args.mode == 'overseas' and server == 'localhost':
            raise ValueError('请先修改 .env 的 SERVER_HOST 为公网 IP 或域名。')
        subscription = getpass.getpass('HTTPS 订阅 URL: ') if args.mode == 'china' else None
        generate_proxy(args.mode, server, subscription)
        print('配置已生成，请把 ' + args.mode + ' 添加到 .env 的 COMPOSE_PROFILES，移除另一代理模式。')
    elif args.command in ('check', 'up'):
        validate()
        compose('config', '--quiet')
        if args.command == 'up':
            compose('up', '-d')
            compose('ps')
        else:
            print('配置检查通过。')
    elif args.command == 'backup':
        with backup_lock():
            try:
                environment = restic_env() if args.remote else None
                if args.remote:
                    restic('snapshots', '--json', environment=environment, stdout=subprocess.DEVNULL)
                archive = backup(keep_stopped=args.keep_stopped)
                if args.remote:
                    upload_archive(archive, environment)
            except Exception:
                try:
                    reliability.update_health(ROOT, last_error='backup-failed', last_failure=reliability.now())
                except OSError:
                    print('无法写入备份状态，请检查磁盘空间和权限。', file=sys.stderr)
                notify_backup('HostingService 备份或上传失败，请检查日志；已有本地归档不会因上传失败删除。')
                if args.keep_stopped:
                    print('迁移失败：请检查旧服务状态并保留旧 VPS。', file=sys.stderr)
                raise
    elif args.command == 'remote':
        try:
            remote_command(args)
        except Exception:
            if args.action == 'check':
                try:
                    reliability.update_health(ROOT, last_error='repository-check-failed')
                except OSError:
                    print('无法写入远端检查状态。', file=sys.stderr)
                notify_backup('HostingService 远端备份检查失败，请查看服务器日志。')
            raise
    elif args.command == 'schedule':
        run([sys.executable, str(ROOT / 'scripts/scheduler.py'), *args.args])
    elif args.command == 'backup-health':
        status = reliability.health(ROOT, args.max_age_hours)
        print(json.dumps(status, indent=2))
        if not status['healthy']:
            if args.notify:
                notify_backup('HostingService 备份失败、缺失或超过允许时间，请检查服务器。')
            raise ValueError('异地备份健康检查失败。')
    elif args.command == 'alert-test':
        if not notify_backup('HostingService 备份通知测试，请确认能收到本消息。'):
            raise ValueError('备份通知测试失败。')
        reliability.update_health(ROOT, alert_test_accepted=reliability.now())
    elif args.command == 'logs':
        compose('logs', '--tail', '100', '-f', *([args.service] if args.service else []))
    elif args.command == 'compose':
        compose(*args.args)
    else:
        compose('ps' if args.command == 'status' else 'down')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'错误: {exc}', file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。', file=sys.stderr)
        sys.exit(130)
