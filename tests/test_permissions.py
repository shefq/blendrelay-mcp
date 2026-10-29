import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from blendrelay_runtime import permissions
from blendrelay_mcp.cli import _run_doctor
from blendrelay_blender.version import __version__


class PermissionTests(unittest.TestCase):
    def test_windows_uses_process_sid_even_with_wrong_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audit.jsonl'
            target.touch()
            with patch.object(permissions, '_WINDOWS', True), patch.object(
                permissions, '_windows_user_sid', return_value='S-1-5-21-123-1001'
            ), patch.object(permissions.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 0, '', '')
            ) as run, patch.dict(os.environ, {'USERNAME': 'wrong', 'USERDOMAIN': 'wrong'}):
                permissions.secure_private(target)
            self.assertEqual(run.call_args.args[0][-1], '*S-1-5-21-123-1001:(F)')
            self.assertIn('/inheritance:r', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['errors'], 'replace')

    def test_windows_failure_preserves_details_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audit.jsonl'
            target.touch()
            with patch.object(permissions, '_WINDOWS', True), patch.object(
                permissions, '_windows_user_sid', return_value='S-1-5-21-123-1001'
            ), patch.object(permissions.subprocess, 'run', return_value=
                subprocess.CompletedProcess([], 1332, 'Failed processing 1 files',
                                            'No mapping between account names and security IDs')):
                with self.assertRaises(OSError) as caught:
                    permissions.secure_private(target)
            message = str(caught.exception)
            for expected in ('1332', 'No mapping', 'Failed processing', 'audit.jsonl'):
                self.assertIn(expected, message)

    def test_sid_lookup_failure_never_silently_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audit.jsonl'
            target.touch()
            with patch.object(permissions, '_WINDOWS', True), patch.object(
                permissions, '_windows_user_sid', side_effect=OSError('token lookup failed')
            ), patch.object(permissions.subprocess, 'run') as run:
                with self.assertRaisesRegex(OSError, 'token lookup failed'):
                    permissions.secure_private(target)
                run.assert_not_called()

    def test_icacls_timeout_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audit.jsonl'
            target.touch()
            with patch.object(permissions, '_WINDOWS', True), patch.object(
                permissions, '_windows_user_sid', return_value='S-1-5-21-123-1001'
            ), patch.object(permissions.subprocess, 'run',
                            side_effect=subprocess.TimeoutExpired('icacls', 10)):
                with self.assertRaisesRegex(OSError, 'icacls.*timed out'):
                    permissions.secure_private(target)

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL integration')
    def test_real_acl_can_be_applied_twice_and_file_remains_writable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audit.jsonl'
            target.touch()
            with patch.dict(os.environ, {'USERNAME': 'wrong', 'USERDOMAIN': 'wrong'}):
                permissions.secure_private(target)
                permissions.secure_private(target)
            target.write_text('test\n', encoding='utf-8')
            result = subprocess.run(['icacls', str(target)], capture_output=True,
                                    text=True, errors='replace', check=True)
            self.assertNotIn('(I)', result.stdout)


class DoctorPermissionTests(unittest.TestCase):
    def test_doctor_reports_acl_failure_without_changing_existing_audit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir()
            audit = runtime / 'audit.jsonl'
            audit.write_text('existing audit\n', encoding='utf-8')
            version = root / 'blender' / '5.2'
            extension = version / 'extensions' / 'user_default' / 'blendrelay_mcp'
            extension.mkdir(parents=True)
            (extension / 'blender_manifest.toml').write_text(
                f'id="blendrelay_mcp"\nversion="{__version__}"\n', encoding='utf-8')
            output = io.StringIO()
            with patch('blendrelay_mcp.addon_installer.compatible_blender_versions',
                       return_value=[((5, 2), version)]), patch.object(
                permissions, 'secure_private', side_effect=OSError('ACL denied (exit 5)')
            ), contextlib.redirect_stdout(output):
                self.assertEqual(_run_doctor(str(runtime)), 1)
            self.assertIn('ACL denied (exit 5)', output.getvalue())
            self.assertIn('RESULT: FAIL', output.getvalue())
            self.assertEqual(audit.read_text(encoding='utf-8'), 'existing audit\n')
            self.assertEqual(list(runtime.iterdir()), [audit])


if __name__ == '__main__':
    unittest.main()
