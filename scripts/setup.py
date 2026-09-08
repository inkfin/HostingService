#!/usr/bin/env python3
"""Interactive, repeatable coordinator; secrets stay in existing private files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def ask(label, default=''):
    value = input(f'{label}' + (f' [{default}]' if default else '') + ': ').strip()
    return value or default


def confirm(label):
    if ask(label + '，完成后输入 yes（其他输入退出，之后可继续）') != 'yes':
        raise ValueError('尚未完成；重新执行 ./hosting setup 继续。')


def command(*args, capture=False):
    return subprocess.run([str(ROOT / 'hosting'), *args], cwd=ROOT, check=True,
                          text=True, capture_output=capture)


def reachable(url):
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise ValueError(f'服务尚未就绪：{url}；检查 ./hosting logs 后重试。')


def main():
    if not sys.stdin.isatty():
        raise ValueError('请从交互式 SSH 终端运行。')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('请在 Linux VPS 上用 sudo ./hosting setup 运行。')
    os.chdir(ROOT)
    os.umask(0o077)
    (ROOT / 'runtime/setup-report.json').unlink(missing_ok=True)
    print('HostingService 配置向导；Ctrl-C 退出，重新运行可继续。')
    print('新部署会创建服务；迁移请先按 docs/storage-and-migration.md 恢复配置与数据，再运行本向导。')
    if not (ROOT / '.env').exists():
        confirm('这是新部署（不是等待恢复数据的旧部署）')
        domain = ask('网站域名（留空使用 HTTP；域名需先设置 DNS）')
        command('init', *(['--domain', domain] if domain else []))
    command('doctor')
    command('check')
    command('up')
    # Re-read actual configuration on every run; do not trust stage markers.
    import hosting
    profiles = hosting.validate()
    env = hosting.env_read()
    print(f"\n在你的电脑运行：ssh -N -L 3000:127.0.0.1:3000 -L 3001:127.0.0.1:3001 -L 8080:127.0.0.1:8080 用户@{env['SERVER_HOST']}")
    if 'gitea' in profiles:
        reachable('http://127.0.0.1:3000/api/healthz')
        result = hosting.compose('exec', '-T', '--user', 'git', 'gitea',
                                 'gitea', 'admin', 'user', 'list', '--admin', capture=True)
        if len(result.stdout.strip().splitlines()) < 2:
            if (ROOT / 'runtime/gitea-admin.txt').exists():
                raise ValueError('管理员凭据文件已存在但未检测到管理员。请检查创建日志，保留文件，勿重复生成密码。')
            command('gitea-admin', '--username', ask('Gitea 管理员名'), '--email', ask('管理员邮箱'))
        print('打开 http://localhost:3000；首次密码在 runtime/gitea-admin.txt。添加 SSH 公钥并创建测试仓库。')
        confirm('已登录 Gitea、修改初始密码，并从自己电脑完成测试仓库 clone/push')
    for profile, url, instruction in (
        ('uptime-kuma', 'http://127.0.0.1:3001', '打开 http://localhost:3001 创建管理员和监控项'),
        ('it-tools', 'http://127.0.0.1:8080', '打开 http://localhost:8080 验证页面')):
        if profile in profiles:
            reachable(url)
            confirm(instruction)
    print('请按 README 的端口表配置云安全组；Docker 发布端口可能绕过 ufw。安装器不修改防火墙。')
    if 'website' in profiles:
        confirm('已从外网访问网站；使用域名时 HTTPS 证书正常')
    if 'teamspeak' in profiles:
        confirm('已使用 TeamSpeak 客户端连接，并保存管理员 token（见 ./hosting logs teamspeak）')
    if 'china' in profiles or 'overseas' in profiles:
        confirm('已按 README 导入 runtime 中的代理配置，并从客户端验证连接')
    if not (ROOT / 'runtime/restic.json').exists():
        print('准备私有对象存储 bucket、S3 endpoint、访问密钥、region 和 HTTPS 通知 webhook。')
        command('configure-backup')
    if subprocess.run([str(ROOT / 'hosting'), 'remote', 'snapshots'], cwd=ROOT).returncode:
        print('无法读取备份库。已有库请先检查凭据、网络和密码；不要初始化替代旧库。')
        confirm('已确认这是全新且空的备份前缀，需要初始化')
        command('remote', 'init')
    confirm('已将 runtime/restic-password 保存到服务器之外的密码管理器')
    command('alert-test')
    confirm('已在通知接收端实际收到测试消息')
    command('backup', '--remote')
    command('remote', 'check', '--read-data')
    print('恢复验收：按 docs/storage-and-migration.md，在隔离目录/服务器恢复这次快照；')
    print('有 Gitea 时还需验证账号、Issue、仓库内容，并完成 clone/push。校验通过不等于应用恢复通过。')
    confirm('已完成本次实际备份的隔离恢复验收')
    at = ask('每日备份时间 HH:MM', '03:30')
    timezone = ask('备份时区', 'Asia/Shanghai')
    command('schedule', 'install', '--at', at, '--timezone', timezone)
    command('schedule', 'status')
    command('backup-health')
    (ROOT / 'runtime/setup-report.json').write_text(json.dumps({
        'completed_at': time.time(), 'profiles': profiles,
        'application_access_and_restore': 'operator-confirmed',
        'remote_check': 'read-data-passed', 'schedule': {'at': at, 'timezone': timezone}
    }, indent=2) + '\n')
    print('配置完成：服务、异地备份和服务器定时任务已配置；人工验收记录保存在 runtime/setup-report.json。')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'未完成：{exc}', file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\n已退出；执行 sudo ./hosting setup 继续。', file=sys.stderr)
        sys.exit(130)
