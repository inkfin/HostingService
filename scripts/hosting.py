#!/usr/bin/env python3
"""Personal VPS service launcher. Python standard library only."""
import argparse
import getpass
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
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('website', 'gitea', 'teamspeak', 'uptime-kuma', 'it-tools')
PROFILES = (*SERVICES, 'china', 'overseas')


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(content)
    path.chmod(0o600)


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


def compose(*args, capture=False):
    return run(['docker', 'compose', '--project-directory', str(ROOT),
                '--env-file', str(ROOT / '.env'), '-f', str(ROOT / 'compose.yaml'), *args],
               capture_output=capture, text=True)


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


def backup():
    validate()
    running = compose('ps', '--status', 'running', '--services', capture=True).stdout.split()
    directory = ROOT / 'backups'
    directory.mkdir(mode=0o700, exist_ok=True)
    archive = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
    paths = [p for p in ('.env', 'runtime', 'data', 'website', 'compose.yaml') if (ROOT / p).exists()]
    try:
        if running:
            compose('stop', *running)
        run(['tar', '-czf', str(archive), *paths])
        archive.chmod(0o600)
        print(f'备份完成: {archive}，包含密码及私钥，请离机保管。')
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    finally:
        if running:
            compose('start', *running)


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
    for command in ('up', 'down', 'check', 'status', 'backup'):
        sub.add_parser(command)
    logs = sub.add_parser('logs')
    logs.add_argument('service', nargs='?')
    raw = sub.add_parser('compose', help='透传 Docker Compose 参数')
    raw.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args)
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
        backup()
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
