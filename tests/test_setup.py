"""Failure and resume boundaries for the interactive coordinator."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/setup.py'
spec = importlib.util.spec_from_file_location('setup_wizard', SCRIPT)
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class SetupTests(unittest.TestCase):
    def test_noninteractive_refused_before_mutation(self):
        with patch.object(wizard.sys.stdin, 'isatty', return_value=False), patch.object(wizard, 'command') as command:
            with self.assertRaises(ValueError):
                wizard.main()
            command.assert_not_called()

    def test_confirmation_cannot_be_skipped(self):
        for response in ('', 'no', 'y'):
            with patch('builtins.input', return_value=response):
                with self.assertRaises(ValueError):
                    wizard.confirm('restored')

    def test_existing_configuration_is_not_initialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('original secret')
            failure = subprocess.CalledProcessError(1, 'doctor')
            with patch.object(wizard, 'ROOT', root), patch.object(wizard.sys.stdin, 'isatty', return_value=True), patch.object(wizard.sys, 'platform', 'linux'), patch.object(wizard.os, 'geteuid', return_value=0), patch.object(wizard.os, 'chdir'), patch.object(wizard.os, 'umask'), patch.object(wizard, 'command', side_effect=failure) as command:
                with self.assertRaises(subprocess.CalledProcessError):
                    wizard.main()
                command.assert_called_once_with('doctor')
            self.assertEqual((root / '.env').read_text(), 'original secret')
            self.assertFalse((root / 'runtime/setup-report.json').exists())

    def test_failed_http_probe_cannot_pass(self):
        with patch.object(wizard.urllib.request, 'urlopen', side_effect=OSError), patch.object(wizard.time, 'sleep'):
            with self.assertRaises(ValueError):
                wizard.reachable('http://localhost:3000')
