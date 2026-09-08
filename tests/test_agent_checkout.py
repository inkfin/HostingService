import importlib.util
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent_checkout', Path(__file__).resolve().parents[1] / 'scripts/agent_checkout.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AgentCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'hosting').write_text('public')
        subprocess.run(['git', '-C', str(self.root), 'add', 'hosting'], check=True)
        self.username = pwd.getpwuid(os.getuid()).pw_name

    def test_private_untracked_data_never_changes_owner(self):
        for name in ('runtime', 'data', 'backups'):
            (self.root / name).mkdir()
            (self.root / name / 'secret').write_text('private')
        (self.root / '.env').write_text('private')
        with patch.object(module.os, 'chown') as chown:
            module.prepare(self.root, self.username)
        touched = {call.args[0] for call in chown.call_args_list}
        self.assertIn(self.root / 'hosting', touched)
        for path in touched:
            self.assertFalse(path.name == '.env' or any(name in path.relative_to(self.root).parts for name in ('runtime', 'data', 'backups')))

    def test_tracked_private_file_fails_before_chown(self):
        (self.root / '.env').write_text('private')
        subprocess.run(['git', '-C', str(self.root), 'add', '.env'], check=True)
        with patch.object(module.os, 'chown') as chown:
            with self.assertRaises(ValueError):
                module.prepare(self.root, self.username)
            chown.assert_not_called()

    def test_symlink_target_is_not_chowned(self):
        (self.root / 'link').symlink_to('hosting')
        subprocess.run(['git', '-C', str(self.root), 'add', 'link'], check=True)
        with patch.object(module.os, 'chown') as chown:
            module.prepare(self.root, self.username)
        calls = [c for c in chown.call_args_list if c.args[0] == self.root / 'link']
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].kwargs['follow_symlinks'])
