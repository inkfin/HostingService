import importlib.util
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'

def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

reliability = load('reliability')
scheduler = load('scheduler')


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ini = self.root / 'data/gitea/gitea/conf/app.ini'
        self.ini.parent.mkdir(parents=True)
        self.ini.write_text('APP_NAME = Gitea\n[database]\nDB_TYPE=sqlite3\nPATH=/data/gitea/gitea.db\n[repository]\nROOT=/data/git/repositories\n')
        self.database = self.root / 'data/gitea/gitea/gitea.db'
        with sqlite3.connect(self.database) as connection:
            connection.execute('CREATE TABLE user (id INTEGER)')
            connection.execute('CREATE TABLE repository (owner_name TEXT, lower_name TEXT)')

    def test_missing_repository_is_rejected(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO repository VALUES ('owner', 'missing')")
        with self.assertRaises(ValueError):
            reliability.verify_gitea(self.root)

    def test_real_bare_repository_fsck(self):
        repo = self.root / 'data/gitea/git/repositories/owner/test.git'
        repo.parent.mkdir(parents=True)
        subprocess.run(['git', 'init', '--bare', str(repo)], check=True, capture_output=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO repository VALUES ('owner', 'test')")
        report = reliability.verify_gitea(self.root)
        self.assertEqual(report['git_repositories_checked'], 1)
        self.assertEqual(report['database_repositories'], 1)

    def test_corrupt_database_is_rejected(self):
        self.database.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            reliability.verify_gitea(self.root)

    def test_external_storage_and_symlinks_rejected(self):
        self.ini.write_text(self.ini.read_text() + '[storage]\nSTORAGE_TYPE=minio\n')
        with self.assertRaises(ValueError):
            reliability.verify_gitea(self.root)
        self.ini.write_text('[database]\nDB_TYPE=sqlite3\n')
        (self.root / 'data/gitea/external').symlink_to(self.root)
        with self.assertRaises(ValueError):
            reliability.verify_gitea(self.root)

    def test_external_database_rejected(self):
        self.ini.write_text('[database]\nDB_TYPE=postgres\n')
        with self.assertRaises(ValueError):
            reliability.verify_gitea(self.root)

    def test_health_rejects_stale_and_failed_backup(self):
        self.assertFalse(reliability.health(self.root)['healthy'])
        reliability.update_health(self.root, last_remote_success=(datetime.now(timezone.utc) - timedelta(hours=27)).isoformat())
        self.assertFalse(reliability.health(self.root)['healthy'])
        reliability.update_health(self.root, last_remote_success=reliability.now(), last_error=None)
        self.assertTrue(reliability.health(self.root)['healthy'])
        reliability.update_health(self.root, last_error='failed')
        self.assertFalse(reliability.health(self.root)['healthy'])

    def test_timer_rejects_injection_and_has_explicit_timezone(self):
        for at in ('99:99', '03:30\nExecStart=evil'):
            with self.assertRaises(ValueError):
                scheduler.units(self.root, at)
        with self.assertRaises(ValueError):
            scheduler.units(self.root, timezone='../../etc/passwd')
        files = scheduler.units(self.root)
        self.assertIn('03:30:00 Asia/Shanghai', files['hostingservice-backup.timer'])
        self.assertIn('Persistent=true', files['hostingservice-backup.timer'])
        self.assertIn('backup --remote', files['hostingservice-backup.service'])
        self.assertNotIn('--keep-stopped', files['hostingservice-backup.service'])

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'Requires Linux systemd tools')
    def test_systemd_accepts_generated_units(self):
        files = scheduler.units(self.root)
        paths = []
        for name, content in files.items():
            path = self.root / name
            path.write_text(content)
            paths.append(str(path))
        subprocess.run(['systemd-analyze', 'verify', *paths], check=True, capture_output=True)

if __name__ == '__main__':
    unittest.main()
