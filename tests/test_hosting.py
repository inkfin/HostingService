import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('hosting', Path(__file__).resolve().parents[1] / 'scripts/hosting.py')
hosting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hosting)
SOURCE = hosting.ROOT


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patcher = patch.object(hosting, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        shutil.copy(SOURCE / 'compose.yaml', self.root / 'compose.yaml')
        (self.root / 'website').mkdir()

    def init(self, **kwargs):
        values = dict(services='website,uptime-kuma,it-tools', mode='none',
                      host='vps.example.com', domain=None, accept_teamspeak_license=False)
        values.update(kwargs)
        hosting.initialize(argparse.Namespace(**values))

    def test_repeat_init_preserves_credentials(self):
        self.init()
        before = (self.root / '.env').read_bytes()
        with self.assertRaises(ValueError):
            self.init(mode='overseas')
        self.assertEqual(before, (self.root / '.env').read_bytes())
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)

    def test_reject_host_injection(self):
        for host in ('$(id)', 'abc\nX=evil', 'https://example.com', 'foo;bar', 'a b'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.init(host=host)
        self.assertFalse((self.root / '.env').exists())

    def test_china_subscription_and_no_direct_fallback(self):
        url = 'https://example.com/sub?token=a"b&x=$HOME'
        with patch('getpass.getpass', return_value=url):
            self.init(mode='china')
        config = json.loads((self.root / 'runtime/mihomo.yaml').read_text())
        self.assertEqual(config['proxy-providers']['upstream']['url'], url)
        self.assertEqual(config['rules'], ['MATCH,PROXY'])
        self.assertGreater(len(config['authentication'][0]), 40)
        with self.assertRaises(ValueError):
            hosting.generate_proxy('china', 'example.com', url)

    def test_overseas_certificate_and_pinned_client(self):
        self.init(mode='overseas', host='2001:db8::1')
        client = json.loads((self.root / 'runtime/hysteria-client.yaml').read_text())
        server = json.loads((self.root / 'runtime/hysteria/config.json').read_text())
        self.assertEqual(client['auth'], server['auth']['password'])
        self.assertEqual(client['server'], '[2001:db8::1]:8443')
        cert = self.root / 'runtime/hysteria/server.crt'
        pin = subprocess.check_output(['openssl', 'x509', '-in', str(cert), '-noout',
                                       '-fingerprint', '-sha256'], text=True).strip().split('=', 1)[1]
        self.assertEqual(client['tls']['pinSHA256'], pin)
        key = self.root / 'runtime/hysteria/server.key'
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_teamspeak_rejects_arm_before_writing(self):
        with patch('platform.machine', return_value='aarch64'), self.assertRaises(ValueError):
            self.init(services='teamspeak', accept_teamspeak_license=True)
        self.assertFalse((self.root / '.env').exists())

    def test_validate_missing_config(self):
        self.init()
        (self.root / 'runtime/Caddyfile').unlink()
        with self.assertRaises(ValueError):
            hosting.validate()

    def test_backup_restarts_services_on_archive_failure(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='gitea\nwebsite\n')
        with patch.object(hosting, 'compose', return_value=result) as compose, \
             patch.object(hosting, 'run', side_effect=subprocess.CalledProcessError(1, 'tar')):
            with self.assertRaises(subprocess.CalledProcessError):
                hosting.backup()
        self.assertEqual(compose.call_args_list[-1].args, ('start', 'gitea', 'website'))
        self.assertFalse(list((self.root / 'backups').iterdir()))

    def test_backup_round_trip_preserves_database_secrets_and_modes(self):
        self.init()
        database = self.root / 'data/gitea/gitea.db'
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE notes (text TEXT)')
            connection.execute('INSERT INTO notes VALUES (?)', ('我的数据',))
        secret = self.root / 'runtime/test.key'
        hosting.private_write(secret, 'example private key\n')
        shutil.copy(SOURCE / 'hosting', self.root / 'hosting')
        (self.root / 'hosting').chmod(0o755)
        result = subprocess.CompletedProcess([], 0, stdout='gitea\n')
        with patch.object(hosting, 'compose', return_value=result) as compose:
            hosting.backup()
        self.assertEqual(compose.call_args_list[-1].args, ('start', 'gitea'))
        archive, = (self.root / 'backups').glob('*.tar.gz')
        checksum = archive.with_name(archive.name + '.sha256')
        self.assertEqual(checksum.read_text().split()[0], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(checksum.stat().st_mode & 0o777, 0o600)
        restored = self.root / 'restored'
        restored.mkdir()
        subprocess.run(['tar', '-xzpf', str(archive), '-C', str(restored)], check=True)
        with sqlite3.connect(restored / 'data/gitea/gitea.db') as connection:
            self.assertEqual(connection.execute('SELECT text FROM notes').fetchone()[0], '我的数据')
        self.assertEqual((restored / '.env').read_bytes(), (self.root / '.env').read_bytes())
        self.assertEqual((restored / 'runtime/test.key').stat().st_mode & 0o777, 0o600)
        self.assertEqual((restored / 'hosting').stat().st_mode & 0o777, 0o755)
        self.assertFalse((restored / 'backups').exists())

    def test_migration_backup_does_not_restart_old_services(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='gitea\nwebsite\n')
        with patch.object(hosting, 'compose', return_value=result) as compose:
            hosting.backup(keep_stopped=True)
        self.assertEqual(compose.call_args_list[-1].args, ('stop', '--timeout', '120', 'gitea', 'website'))
        self.assertEqual(len(list((self.root / 'backups').glob('*.sha256'))), 1)

    def test_migration_backup_failure_still_restarts_services(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='gitea\n')
        with patch.object(hosting, 'compose', return_value=result) as compose, \
             patch.object(hosting, 'private_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                hosting.backup(keep_stopped=True)
        self.assertEqual(compose.call_args_list[-1].args, ('start', 'gitea'))
        self.assertFalse(list((self.root / 'backups').iterdir()))

    def test_backup_does_not_start_previously_stopped_services(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='')
        with patch.object(hosting, 'compose', return_value=result) as compose:
            hosting.backup()
        self.assertEqual(len(compose.call_args_list), 1)

    def test_backup_lock_rejects_concurrent_backup(self):
        with hosting.backup_lock():
            with self.assertRaises(ValueError):
                with hosting.backup_lock():
                    self.fail('must not acquire a second lock')

    def test_upload_rejects_corrupted_archive_before_network(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='')
        with patch.object(hosting, 'compose', return_value=result):
            archive = hosting.backup()
        archive.write_bytes(archive.read_bytes() + b'corruption')
        with patch('subprocess.run') as run, self.assertRaises(ValueError):
            hosting.upload_archive(archive, {})
        run.assert_not_called()

    def test_remote_restore_refuses_occupied_directory(self):
        target = self.root / 'live'
        target.mkdir()
        (target / 'database').write_text('keep me')
        args = argparse.Namespace(action='restore', snapshot='12345678', target=str(target))
        with patch.object(hosting, 'restic_env', return_value={}), \
             patch.object(hosting, 'restic') as restic, self.assertRaises(ValueError):
            hosting.remote_command(args)
        restic.assert_not_called()
        self.assertEqual((target / 'database').read_text(), 'keep me')

    def test_remote_preflight_failure_never_stops_services(self):
        with patch('sys.argv', ['hosting', 'backup', '--remote']), \
             patch.object(hosting, 'restic_env', return_value={}), \
             patch.object(hosting, 'restic', side_effect=subprocess.CalledProcessError(1, 'restic')), \
             patch.object(hosting, 'backup') as backup:
            with self.assertRaises(subprocess.CalledProcessError):
                hosting.main()
        backup.assert_not_called()

    def test_failed_upload_preserves_archive_and_restarts_daily_services(self):
        self.init()
        result = subprocess.CompletedProcess([], 0, stdout='gitea\n')
        with patch('sys.argv', ['hosting', 'backup', '--remote']), \
             patch.object(hosting, 'restic_env', return_value={}), \
             patch.object(hosting, 'restic'), \
             patch.object(hosting, 'compose', return_value=result) as compose, \
             patch.object(hosting, 'upload_archive', side_effect=subprocess.CalledProcessError(1, 'restic')):
            with self.assertRaises(subprocess.CalledProcessError):
                hosting.main()
        self.assertEqual(compose.call_args_list[-1].args, ('start', 'gitea'))
        self.assertEqual(len(list((self.root / 'backups').glob('*.tar.gz'))), 1)
        self.assertEqual(len(list((self.root / 'backups').glob('*.sha256'))), 1)

    @unittest.skipUnless(shutil.which('restic'), 'Install restic to run encrypted backup/restore integration')
    def test_restic_encrypted_backup_restore_round_trip(self):
        self.init()
        hosting.private_write(self.root / 'runtime/restic-password', 'integration-password-only\n')
        hosting.private_write(self.root / 'runtime/restic.json', json.dumps({
            'repository': str(self.root / 'test-remote-repository'),
            'password_file': 'runtime/restic-password', 'env': {}}))
        source = self.root / 'data/gitea/important.txt'
        source.parent.mkdir(parents=True)
        source.write_text('persistent service data\n')
        environment = hosting.restic_env()
        hosting.restic('init', environment=environment, stdout=subprocess.DEVNULL)
        result = subprocess.CompletedProcess([], 0, stdout='')
        with patch.object(hosting, 'compose', return_value=result):
            archive = hosting.backup()
        hosting.upload_archive(archive, environment)
        snapshots = hosting.restic('snapshots', '--json', environment=environment,
                                   capture_output=True, text=True)
        snapshot_id = json.loads(snapshots.stdout)[0]['id']
        target = self.root / 'restore-download'
        hosting.remote_command(argparse.Namespace(action='restore', snapshot=snapshot_id, target=str(target)))
        downloaded = target / archive.name
        self.assertEqual(downloaded.read_bytes(), archive.read_bytes())
        extract = self.root / 'restored-service'
        extract.mkdir()
        subprocess.run(['tar', '-xzpf', str(downloaded), '-C', str(extract)], check=True)
        self.assertEqual((extract / 'data/gitea/important.txt').read_text(), source.read_text())
        hosting.restic('check', '--read-data', environment=environment, stdout=subprocess.DEVNULL)
        hosting.private_write(self.root / 'runtime/restic-password', 'wrong-password\n')
        with self.assertRaises(subprocess.CalledProcessError):
            hosting.restic('snapshots', environment=environment, capture_output=True)

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose unavailable')
    def test_compose_all_profiles_without_daemon(self):
        self.init()
        result = subprocess.run(['docker', 'compose', '--project-directory', str(self.root),
                                 '--env-file', str(self.root / '.env'), '-f', str(self.root / 'compose.yaml'),
                                 '--profile', '*', 'config', '--format', 'json'],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout)
        self.assertEqual(len(config['services']), 7)
        for name in ('gitea', 'uptime-kuma', 'it-tools', 'mihomo'):
            for port in config['services'][name]['ports']:
                if port['target'] != 22:
                    self.assertEqual(port['host_ip'], '127.0.0.1')
        self.assertEqual(config['services']['hysteria']['ports'][0]['protocol'], 'udp')


if __name__ == '__main__':
    unittest.main()
