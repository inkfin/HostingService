#!/usr/bin/env python3
"""Install system-level timers on a Linux VPS; render mode is side-effect-free."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def units(root, at='03:30', timezone='Asia/Shanghai'):
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
        raise ValueError('时间必须为 HH:MM。')
    if not re.fullmatch(r'[A-Za-z0-9_+/-]+', timezone) or '..' in timezone or timezone.startswith('/'):
        raise ValueError('无效时区。')
    if not (Path('/usr/share/zoneinfo') / timezone).is_file():
        raise ValueError('系统未安装该时区。')
    if any(c in str(root) for c in '\n\r\x00$\\'):
        raise ValueError('部署路径不能包含换行、反斜杠或 $。')
    runner = f'{quote(sys.executable)} {quote(root / "scripts/hosting.py")}'
    # WorkingDirectory does not use ExecStart's shell-like quoting rules.
    common = f'WorkingDirectory={str(root).replace("%", "%%")}\nEnvironment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\nUMask=0077\n'
    return {
        'hostingservice-backup.service': '[Unit]\nDescription=HostingService encrypted backup\nWants=network-online.target\nAfter=network-online.target docker.service\n\n[Service]\nType=oneshot\n' + common + f'ExecStart={runner} backup --remote\nTimeoutStartSec=infinity\n',
        'hostingservice-backup.timer': f'[Unit]\nDescription=Daily HostingService backup\n\n[Timer]\nOnCalendar=*-*-* {at}:00 {timezone}\nPersistent=true\nRandomizedDelaySec=5m\n\n[Install]\nWantedBy=timers.target\n',
        'hostingservice-backup-watch.service': '[Unit]\nDescription=HostingService backup freshness check\n\n[Service]\nType=oneshot\n' + common + f'ExecStart={runner} backup-health --max-age-hours 26 --notify\n',
        'hostingservice-backup-watch.timer': '[Unit]\nDescription=Hourly HostingService backup freshness check\n\n[Timer]\nOnBootSec=15m\nOnUnitActiveSec=1h\n\n[Install]\nWantedBy=timers.target\n',
        'hostingservice-backup-check.service': '[Unit]\nDescription=Weekly restic repository check\nWants=network-online.target\nAfter=network-online.target\n\n[Service]\nType=oneshot\n' + common + f'ExecStart={runner} remote check\nTimeoutStartSec=infinity\n',
        'hostingservice-backup-check.timer': f'[Unit]\nDescription=Weekly backup repository check\n\n[Timer]\nOnCalendar=Sun *-*-* 05:30:00 {timezone}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['render', 'install', 'status', 'disable'])
    parser.add_argument('--at', default='03:30')
    parser.add_argument('--timezone', default='Asia/Shanghai')
    args = parser.parse_args()
    files = units(ROOT, args.at, args.timezone)
    if args.action == 'render':
        print(json.dumps(files, indent=2))
        return
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        raise ValueError('定时部署需要 Linux systemd；当前环境仅支持 render。')
    timers = [name for name in files if name.endswith('.timer')]
    if args.action == 'status':
        subprocess.run(['systemctl', 'list-timers', '--all', 'hostingservice-*'], check=True)
        return
    if os.geteuid() != 0:
        raise ValueError('请用 sudo 安装或停用系统定时器。')
    if args.action == 'disable':
        subprocess.run(['systemctl', 'disable', '--now', *timers], check=True)
        return
    # Refuse automatic backup until one real offsite backup has succeeded.
    status_file = ROOT / 'runtime/backup-status.json'
    status = json.loads(status_file.read_text()) if status_file.exists() else {}
    if not status.get('last_remote_success') or status.get('last_error'):
        raise ValueError('先完成一次 ./hosting backup --remote，再安装定时器。')
    if not (ROOT / 'runtime/backup-alert.json').exists() or not status.get('alert_test_accepted'):
        raise ValueError('先配置备份故障通知并运行 ./hosting alert-test，见部署指南。')
    subprocess.run(['systemd-analyze', 'calendar', f'*-*-* {args.at}:00 {args.timezone}'], check=True,
                   stdout=subprocess.DEVNULL)
    directory = Path('/etc/systemd/system')
    for name, content in files.items():
        target = directory / name
        if target.exists() and str(ROOT) not in target.read_text() and not name.endswith('.timer'):
            raise ValueError('已有其他部署使用同名定时服务，拒绝覆盖。')
    for name, content in files.items():
        (directory / name).write_text(content)
        (directory / name).chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', *timers], check=True)
    subprocess.run(['systemctl', 'list-timers', '--all', 'hostingservice-*'], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'错误: {exc}', file=sys.stderr)
        sys.exit(1)
