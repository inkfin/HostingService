#!/usr/bin/env python3
"""Real Docker Gitea backup/restore test; only disposable projects and loopback ports."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]


def run(args, cwd, **kw):
    return subprocess.run(args, cwd=cwd, check=True, text=True, **kw)


def dc(root, *args, **kw):
    return run(['docker', 'compose', '-f', str(root / 'compose.yaml'), *args], root, **kw)


def port(root):
    return dc(root, 'port', 'gitea', '3000', capture_output=True).stdout.strip().rsplit(':', 1)[1]


def request(url, path, auth=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode(auth.encode()).decode()
    req = urllib.request.Request(url + path, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait(root):
    url = 'http://127.0.0.1:' + port(root)
    for _ in range(90):
        try:
            request(url, '/api/v1/version')
            return url
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(1)
    raise RuntimeError('Gitea did not become ready')


def write_compose(root, name):
    config = {'name': name, 'services': {'gitea': {
        'image': 'docker.gitea.com/gitea:1.27.3', 'profiles': ['gitea'],
        'environment': {'USER_UID': '1000', 'USER_GID': '1000',
                        'GITEA__database__DB_TYPE': 'sqlite3', 'GITEA__security__INSTALL_LOCK': 'true',
                        'GITEA__service__DISABLE_REGISTRATION': 'true',
                        'GITEA__server__ROOT_URL': 'http://localhost:3000/'},
        'ports': ['127.0.0.1::3000'], 'volumes': ['./data/gitea:/data']}}}
    (root / 'compose.yaml').write_text(json.dumps(config))
    (root / '.env').write_text('COMPOSE_PROFILES=gitea\n')


def main():
    (SOURCE / '.e2e').mkdir(exist_ok=True)
    # Shared home path is accessible to local Docker Desktop / Colima too.
    temp = Path(tempfile.mkdtemp(prefix='gitea-', dir=SOURCE / '.e2e'))
    old, new = temp / 'old', temp / 'new'
    old.mkdir(); new.mkdir()
    name = 'hosting-e2e-' + secrets.token_hex(4)
    write_compose(old, name + '-old')
    shutil.copytree(SOURCE / 'scripts', old / 'scripts')
    shutil.copy(SOURCE / 'hosting', old / 'hosting')
    write_compose(new, name + '-new')
    password = secrets.token_urlsafe(32)
    auth = 'tester:' + password
    git_environment = os.environ.copy()
    git_environment.update({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                            'GIT_CONFIG_VALUE_0': 'Authorization: Basic ' + base64.b64encode(auth.encode()).decode()})
    try:
        dc(old, 'up', '-d')
        url = wait(old)
        run(['python3', 'scripts/hosting.py', 'gitea-admin', '--username', 'bootstrap',
             '--email', 'bootstrap@example.com'], old)
        initial_credentials = (old / 'runtime/gitea-admin.txt').read_bytes()
        repeated = subprocess.run(['python3', 'scripts/hosting.py', 'gitea-admin', '--username', 'bootstrap',
                                   '--email', 'bootstrap@example.com'], cwd=old, capture_output=True)
        assert repeated.returncode != 0
        assert (old / 'runtime/gitea-admin.txt').read_bytes() == initial_credentials
        dc(old, 'exec', '-T', '--user', '1000', 'gitea', 'sh', '-c',
           'IFS= read -r password; gitea admin user create --admin --username tester --email test@example.com --password "$password" --must-change-password=false',
           input=password + '\n', capture_output=True)
        request(url, '/api/v1/user/repos', auth, {'name': 'demo', 'private': True})
        issue = request(url, '/api/v1/repos/tester/demo/issues', auth, {'title': 'must survive restore'})
        work = temp / 'work'
        work.mkdir()
        run(['git', 'init', '-b', 'main'], work, capture_output=True)
        (work / 'proof.txt').write_text('persistent commit\n')
        run(['git', 'add', '.'], work)
        run(['git', '-c', 'user.name=E2E', '-c', 'user.email=e2e@example.com', '-c', 'commit.gpgsign=false',
             'commit', '-m', 'Test data'], work, capture_output=True)
        # Disabling signing applies only to this disposable synthetic test repository.
        commit = run(['git', 'rev-parse', 'HEAD'], work, capture_output=True).stdout.strip()
        run(['git', 'push', url + '/tester/demo.git', 'main'], work, env=git_environment, capture_output=True)
        run(['python3', 'scripts/hosting.py', 'backup'], old)
        archive, = (old / 'backups').glob('*.tar.gz')
        checksum = archive.with_name(archive.name + '.sha256').read_text().split()[0]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum
        dc(old, 'down')
        run(['tar', '-xzpf', str(archive), '-C', str(new)], new)
        # Restore original data/scripts but isolate container/project and host port.
        write_compose(new, name + '-new')
        manifest = json.loads((new / 'runtime/backup-manifest.json').read_text())
        assert manifest['gitea']['database_repositories'] == 1
        assert manifest['gitea']['git_repositories_checked'] >= 1
        dc(new, 'up', '-d')
        restored_url = wait(new)
        assert request(restored_url, '/api/v1/user', auth)['login'] == 'tester'
        assert request(restored_url, f'/api/v1/repos/tester/demo/issues/{issue["number"]}', auth)['title'] == 'must survive restore'
        clone = temp / 'cloned'
        run(['git', 'clone', '--branch', 'main', restored_url + '/tester/demo.git', str(clone)], temp,
            env=git_environment, capture_output=True)
        assert run(['git', 'rev-parse', 'HEAD'], clone, capture_output=True).stdout.strip() == commit
        (clone / 'proof.txt').write_text('push after restore\n')
        run(['git', 'add', '.'], clone)
        run(['git', '-c', 'user.name=E2E', '-c', 'user.email=e2e@example.com', '-c', 'commit.gpgsign=false',
             'commit', '-m', 'After restore'], clone, capture_output=True)
        run(['git', 'push', 'origin', 'main'], clone, env=git_environment, capture_output=True)
        print('PASS: Gitea user, private repository, issue, commit, backup integrity, restore, clone and push')
    finally:
        for root in (old, new):
            dc(root, 'down', '--remove-orphans')
        try:
            shutil.rmtree(temp)
        except PermissionError:
            print('Containers removed. Root-owned test data remains at:', temp)


if __name__ == '__main__':
    main()
