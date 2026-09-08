#!/usr/bin/env python3
"""Give the operator public source access without chowning container data."""
import os
from pathlib import Path
import pwd
import subprocess
import sys

PRIVATE = {'.env', 'runtime', 'data', 'backups'}


def prepare(root, username):
    root = Path(root).resolve()
    gitdir = root / '.git'
    if not gitdir.is_dir() or gitdir.is_symlink():
        raise ValueError('需要普通 Git clone，不能使用链接的 gitdir/worktree。')
    user = pwd.getpwnam(username)
    files = subprocess.check_output(['git', '-c', f'safe.directory={root}', '-C', str(root), 'ls-files', '-z']).decode().split('\0')
    paths = {root}
    for name in filter(None, files):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Git 路径越界。')
        if relative.parts[0] in PRIVATE or relative.parts[0].startswith('.env.'):
            if name != '.env.example':
                raise ValueError('私密路径被跟踪，先检查仓库，不能移交所有权。')
        path = root / relative
        parents = [p for p in path.parents if p == root or root in p.parents]
        if any(p.is_symlink() for p in parents):
            raise ValueError('源文件父目录是符号链接。')
        paths.add(path)
        paths.update(parents)
    for path in paths:
        if path.exists() or path.is_symlink():
            os.chown(path, user.pw_uid, user.pw_gid, follow_symlinks=False)
    for directory, dirs, names in os.walk(gitdir, followlinks=False):
        for path in [Path(directory), *(Path(directory) / n for n in dirs + names)]:
            os.chown(path, user.pw_uid, user.pw_gid, follow_symlinks=False)


if __name__ == '__main__':
    prepare(*sys.argv[1:])
