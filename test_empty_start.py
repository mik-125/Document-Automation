"""Fresh installations must not inherit a developer's local uploads or setup."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class EmptyStartTests(unittest.TestCase):
    def test_fresh_process_and_restart_have_no_uploaded_data(self):
        # Import server in a separate process with its data location established
        # first. Never clear or patch the developer's real local database.
        check = """
from pathlib import Path
from unittest.mock import patch
import server

with patch.object(server.GoogleWorkspace, 'from_env', side_effect=AssertionError('No automatic Google connection')):
    state = server.app_state()
for key in ('classes', 'students', 'files', 'issues', 'periods', 'reports', 'class_templates'):
    assert state[key] == [], key
assert not state['selected_period_id']
assert not state['template']['ready']
for connection in state['connections'].values():
    assert connection['status'] != 'connected'
with server.DB.connect() as db:
    for table in ('classes', 'students', 'imports', 'periods', 'homework_periods',
                  'period_exams', 'exams', 'reports', 'report_archive', 'template_versions'):
        assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0, table
for folder in (server.EXAMPLE_DIR, server.PDF_TEMPLATE_DIR, server.REPORT_DIR, server.EXPORT_DIR):
    assert not folder.exists() or not any(p.is_file() for p in folder.rglob('*')), folder.name
assert not (server.DATA_DIR / 'google-oauth-client.json').exists()
print('Empty installation verified')
"""
        with tempfile.TemporaryDirectory() as folder:
            env = os.environ.copy()
            env.update(BRIGHTLINE_DATA_DIR=folder, GOOGLE_OAUTH_CLIENT_FILE='',
                       GOOGLE_OAUTH_TOKEN_FILE='')
            for _ in range(2):
                result = subprocess.run([sys.executable, '-c', check],
                    cwd=Path(__file__).resolve().parent, env=env, capture_output=True,
                    text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Empty installation verified', result.stdout)


if __name__ == '__main__':
    unittest.main()
