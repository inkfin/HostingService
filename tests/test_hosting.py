import argparse
import importlib.util
import json
from pathlib import Path
import shutil
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
        values = dict(services='website,gitea,uptime-kuma,it-tools', mode='none',
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
