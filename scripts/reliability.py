"""Offline Gitea validation and backup health records. No third-party dependencies."""
import configparser
import json
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
from datetime import datetime, timezone


def data_path(root, container_path):
    path = PurePosixPath(container_path)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to('/data'):
        raise ValueError('Gitea 使用了 /data 之外的路径；请为该存储配置专门的备份。')
    return root / 'data/gitea' / str(path.relative_to('/data'))


def verify_gitea(root):
    """Call only after the Gitea container has stopped. Fail closed on unsupported storage."""
    base = root / 'data/gitea'
    ini = base / 'gitea/conf/app.ini'
    if not ini.is_file():
        raise ValueError('Gitea 尚未初始化或 app.ini 缺失，拒绝将空目录当作完整备份。')
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read_string('[DEFAULT]\n' + ini.read_text())
    if config.get('database', 'DB_TYPE', fallback='').lower() != 'sqlite3':
        raise ValueError('当前完整备份支持 SQLite；外部 PostgreSQL/MySQL 需要数据库原生备份。')
    for section in config.sections():
        storage = config.get(section, 'STORAGE_TYPE', fallback='local')
        if storage.lower() != 'local':
            raise ValueError('检测到 Gitea 外部存储，当前本地目录备份不完整。')
        for key in ('PATH', 'ROOT', 'APP_DATA_PATH', 'ROOT_PATH'):
            value = config.get(section, key, fallback='')
            if value and value.startswith('/'):
                data_path(root, value)
    for path in base.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('Gitea 数据目录有指向外部的符号链接，拒绝不完整备份。')
    database = data_path(root, config.get('database', 'PATH', fallback='/data/gitea/gitea.db'))
    if not database.is_file():
        raise ValueError('Gitea SQLite 数据库缺失。')
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Gitea SQLite integrity_check 失败。')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'user', 'repository'}.issubset(tables):
            raise ValueError('数据库缺少 Gitea 核心表。')
        repositories = connection.execute('SELECT owner_name, lower_name FROM repository').fetchall()
    repo_root = data_path(root, config.get('repository', 'ROOT', fallback='/data/git/repositories'))
    for owner, name in repositories:
        repo = repo_root / str(owner).lower() / (str(name) + '.git')
        if not repo.resolve().is_relative_to(repo_root.resolve()) or not (repo / 'HEAD').is_file():
            raise ValueError('数据库中的代码仓库缺少对应的 Git 文件。')
    # Also check wiki and bare repositories not represented by the repository table.
    bare_repos = sorted(repo_root.rglob('*.git')) if repo_root.exists() else []
    for repo in bare_repos:
        if not repo.is_dir():
            continue
        subprocess.run(['git', '-c', f'safe.directory={repo}', '--git-dir', str(repo),
                        'fsck', '--full'], check=True, stdout=subprocess.DEVNULL)
    return {'database_integrity': 'ok', 'database_repositories': len(repositories),
            'git_repositories_checked': len(bare_repos), 'storage': 'sqlite-local'}


def now():
    return datetime.now(timezone.utc).isoformat()


def update_health(root, **fields):
    path = root / 'runtime/backup-status.json'
    existing = json.loads(path.read_text()) if path.exists() else {}
    existing.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(existing, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def health(root, max_age_hours=26):
    path = root / 'runtime/backup-status.json'
    status = json.loads(path.read_text()) if path.exists() else {}
    last = status.get('last_remote_success')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600 if last else None
    good = age is not None and 0 <= age <= max_age_hours and not status.get('last_error')
    return {'healthy': good, 'age_hours': age, **status}
